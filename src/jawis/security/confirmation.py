"""Single-use 10s confirmation gate with PID create_time binding and TH/EN negation-safe parser."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import time
import uuid

_INTERROGATIVE_RE = re.compile(r"(\?|ไหม|มั้ย|หรือเปล่า|รึเปล่า|หรือไม่|หรือยัง|รึยัง|เหรอ)")
_EN_CANCEL_RE = re.compile(r"\b(?:no|nope|not|cancel|abort|stop|don'?t|do\s+not|never\s*mind)\b")
_EN_APPROVE_RE = re.compile(r"\b(?:yes|confirm|confirmed|approve|approved|proceed)\b")
_TH_CANCEL_PREFIXES = (
    "ไม่ยืนยัน",
    "อย่ายืนยัน",
    "อย่าปิด",
    "อย่าเพิ่ง",
    "อย่าพึ่ง",
    "อย่าเลย",
    "ไม่ต้อง",
    "ไม่ใช่",
    "ไม่ปิด",
    "ไม่เอา",
    "ไม่ตกลง",
    "ไม่โอเค",
    "ไม่ได้",
    "ยกเลิก",
    "หยุด",
)
_TH_EN_CANCEL_RE = re.compile(r"(?:^|\s)(?:ไม่|อย่า)\s*(?:yes|confirm|confirmed|approve|approved|proceed)\b")
_TH_APPROVE_EXACT = ("ยืนยัน", "ตกลง", "ปิดเลย", "ใช่", "ได้เลย", "โอเค")


@dataclass(frozen=True)
class TargetProcess:
    """Process identity snapshot binding a confirmation to PID and start timestamp."""

    pid: int
    create_time: float
    process_name: str


@dataclass(frozen=True)
class PendingConfirmation:
    """Active single-use confirmation request awaiting user decision."""

    confirm_id: str
    action: str
    app_id: int
    app_key: str
    display_name: str
    target_processes: list[TargetProcess]
    expires_at: float


@dataclass(frozen=True)
class ConfirmationResult:
    """Resolved outcome and origin for a confirmation request."""

    decision: str  # "approve" | "cancel" | "timeout"
    source: str    # "hud" | "voice" | "timer" | "hud_text"


class ConfirmationManager:
    """Manages one active pending confirmation with deterministic 10s expiry."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: dict[str, PendingConfirmation] = {}
        self._futures: dict[str, asyncio.Future[ConfirmationResult]] = {}

    def create_request(
        self,
        *,
        action: str,
        app_id: int,
        app_key: str,
        display_name: str,
        target_processes: list[TargetProcess],
    ) -> PendingConfirmation:
        """Create a new single-use confirmation request, superseding any existing pending one."""
        for old_id in list(self._pending.keys()):
            self.resolve(old_id, "cancel", source="superseded")

        done_orphans = [cid for cid, fut in self._futures.items() if fut.done() and cid not in self._pending]
        for orphan_id in done_orphans[:-16]:
            self._futures.pop(orphan_id, None)

        confirm_id = uuid.uuid4().hex[:12]
        req = PendingConfirmation(
            confirm_id=confirm_id,
            action=action,
            app_id=app_id,
            app_key=app_key,
            display_name=display_name,
            target_processes=list(target_processes),
            expires_at=time.monotonic() + self.timeout_seconds,
        )
        loop = asyncio.get_running_loop()
        self._pending[confirm_id] = req
        self._futures[confirm_id] = loop.create_future()
        return req

    def active_request(self) -> PendingConfirmation | None:
        """Return the active unexpired confirmation request, expiring stale ones cleanly."""
        now = time.monotonic()
        for cid, req in list(self._pending.items()):
            if now >= req.expires_at:
                self.resolve(cid, "timeout", source="timer")
                continue
            return req
        return None

    def resolve(self, confirm_id: str, decision: str, source: str = "hud") -> bool:
        """Resolve a pending confirmation once; return False if expired, already resolved, or unknown."""
        req = self._pending.pop(confirm_id, None)
        fut = self._futures.get(confirm_id)
        if req is None or fut is None or fut.done():
            return False
        if time.monotonic() >= req.expires_at and source not in {"timer", "superseded"}:
            fut.set_result(ConfirmationResult(decision="timeout", source="timer"))
            return False
        fut.set_result(ConfirmationResult(decision=decision, source=source))
        return True

    async def wait_for_decision(self, confirm_id: str) -> ConfirmationResult:
        """Await a confirmation decision or return a timeout result upon expiration."""
        fut = self._futures.get(confirm_id)
        if fut is None:
            return ConfirmationResult(decision="cancel", source="missing")
        if fut.done():
            self._futures.pop(confirm_id, None)
            if fut.cancelled():
                return ConfirmationResult(decision="cancel", source="missing")
            return fut.result()

        req = self._pending.get(confirm_id)
        if req is None:
            self._futures.pop(confirm_id, None)
            return ConfirmationResult(decision="cancel", source="missing")

        remaining = max(0.01, req.expires_at - time.monotonic())
        try:
            return await asyncio.wait_for(fut, timeout=remaining)
        except asyncio.TimeoutError:
            return ConfirmationResult(decision="timeout", source="timer")
        finally:
            self._pending.pop(confirm_id, None)
            self._futures.pop(confirm_id, None)

    @staticmethod
    def parse_voice_confirmation(text: str) -> str | None:
        """Parse a Thai/English voice utterance into 'approve', 'cancel', or None safely."""
        normalized = text.replace("’", "'").replace("‘", "'")
        cleaned = re.sub(r"[\s,!.\"“”]+", " ", normalized.strip().lower()).strip()
        if not cleaned or _INTERROGATIVE_RE.search(cleaned):
            return None
        if any(tok in cleaned for tok in _TH_CANCEL_PREFIXES):
            return "cancel"
        if _TH_EN_CANCEL_RE.search(cleaned):
            return "cancel"
        if _EN_CANCEL_RE.search(cleaned):
            return "cancel"
        if re.fullmatch(r"ไม่(?:\s*(?:ครับ|ค่ะ|นะ|เลย))?", cleaned):
            return "cancel"
        if _EN_APPROVE_RE.search(cleaned):
            return "approve"
        stripped_polite = re.sub(r"(?:\s*(?:ครับ|ค่ะ|นะ|เลย))+$", "", cleaned).strip()
        if any(cleaned.startswith(tok) or stripped_polite == tok for tok in _TH_APPROVE_EXACT):
            return "approve"
        return None
