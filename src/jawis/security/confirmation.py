"""Single-use 10s confirmation gate with PID create_time binding and TH/EN negation-safe parser."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
import re
import threading
import time
import uuid

_INTERROGATIVE_RE = re.compile(
    r"(\?|ไหม|มั้ย|มั๊ย|หรือเปล่า|รึเปล่า|หรือไม่|หรือยัง|รึยัง|เปล่า|เหรอ|หรอ|ป่ะ|ปะ)"
)
_EN_CANCEL_RE = re.compile(
    r"\b(?:"
    r"no|nope|nah|not|never(?:\s*mind)?|negative|"
    r"cancel(?:l?(?:ed|ing))?|abort(?:ed|ing)?|stop(?:ped|ping)?|"
    r"refus(?:e|ed|ing)|declin(?:e|ed|ing)|den(?:y|ied|ying)|reject(?:ed|ing)?|"
    r"cannot|can\s+not|can'?t|won'?t|will\s+not|"
    r"(?:do|does|did|is|are|was|were|should|would|could|must|need|have|has|had)(?:n'?t|\s+not)"
    r")\b"
)
_EN_APPROVE_RE = re.compile(r"\b(?:yes|confirm|confirmed|approve|approved|proceed)\b")
_TH_CANCEL_RE = re.compile(r"ไม่|อย่า(?!ง)|หยุด|ยกเลิก")
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
    target_processes: tuple[TargetProcess, ...] = ()
    expires_at: float = 0.0


@dataclass(frozen=True)
class ConfirmationResult:
    """Resolved outcome and origin for a confirmation request."""

    decision: str  # "approve" | "cancel" | "timeout"
    source: str    # "hud" | "voice" | "timer" | "hud_text"


class ConfirmationManager:
    """Thread-safe manager for one active pending confirmation with deterministic 10s expiry."""

    def __init__(self, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._lock = threading.RLock()
        self._pending: dict[str, PendingConfirmation] = {}
        self._futures: dict[str, asyncio.Future[ConfirmationResult]] = {}
        self._results: dict[str, ConfirmationResult] = {}

    @staticmethod
    def _safe_set_result(fut: asyncio.Future[ConfirmationResult], result: ConfirmationResult) -> None:
        if not fut.done():
            fut.set_result(result)

    @staticmethod
    def _resolve_future(fut: asyncio.Future[ConfirmationResult], result: ConfirmationResult) -> None:
        loop = fut.get_loop()
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None
        if running_loop is loop:
            ConfirmationManager._safe_set_result(fut, result)
            return
        if loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(ConfirmationManager._safe_set_result, fut, result)
        except RuntimeError:
            return

    def create_request(
        self,
        *,
        action: str,
        app_id: int,
        app_key: str,
        display_name: str,
        target_processes: Sequence[TargetProcess],
    ) -> PendingConfirmation:
        """Create a new single-use confirmation request, superseding any existing pending one."""
        loop = asyncio.get_running_loop()
        with self._lock:
            for old_id in list(self._pending.keys()):
                self.resolve(old_id, "cancel", source="superseded")

            while len(self._results) > 16:
                self._results.pop(next(iter(self._results)))

            confirm_id = uuid.uuid4().hex[:12]
            req = PendingConfirmation(
                confirm_id=confirm_id,
                action=action,
                app_id=app_id,
                app_key=app_key,
                display_name=display_name,
                target_processes=tuple(target_processes),
                expires_at=time.monotonic() + self.timeout_seconds,
            )
            self._pending[confirm_id] = req
            self._futures[confirm_id] = loop.create_future()
            return req

    def active_request(self) -> PendingConfirmation | None:
        """Return the active unexpired confirmation request, expiring stale ones cleanly."""
        with self._lock:
            now = time.monotonic()
            for cid, req in list(self._pending.items()):
                if now >= req.expires_at:
                    self.resolve(cid, "timeout", source="timer")
                    continue
                return req
            return None

    def resolve(self, confirm_id: str, decision: str, source: str = "hud") -> bool:
        """Resolve a pending confirmation once; return False if expired, already resolved, or unknown."""
        with self._lock:
            req = self._pending.pop(confirm_id, None)
            fut = self._futures.pop(confirm_id, None)
            if req is None or fut is None or fut.done():
                return False
            expired = time.monotonic() >= req.expires_at and source not in {"timer", "superseded"}
            result = (
                ConfirmationResult(decision="timeout", source="timer")
                if expired
                else ConfirmationResult(decision=decision, source=source)
            )
            self._results[confirm_id] = result
            self._resolve_future(fut, result)
            return not expired

    async def wait_for_decision(self, confirm_id: str) -> ConfirmationResult:
        """Await a confirmation decision or return a timeout result upon expiration."""
        with self._lock:
            req = self._pending.get(confirm_id)
            fut = self._futures.get(confirm_id)
            if fut is None or req is None:
                self._futures.pop(confirm_id, None)
                resolved = self._results.pop(confirm_id, None)
                return resolved if resolved is not None else ConfirmationResult(decision="cancel", source="missing")

            remaining = req.expires_at - time.monotonic()
            if remaining <= 0:
                self._pending.pop(confirm_id, None)
                self._futures.pop(confirm_id, None)
                self._results.pop(confirm_id, None)
                timeout_result = ConfirmationResult(decision="timeout", source="timer")
                self._resolve_future(fut, timeout_result)
                return timeout_result

        try:
            return await asyncio.wait_for(fut, timeout=max(0.01, remaining))
        except asyncio.TimeoutError:
            return ConfirmationResult(decision="timeout", source="timer")
        finally:
            with self._lock:
                self._pending.pop(confirm_id, None)
                self._futures.pop(confirm_id, None)
                self._results.pop(confirm_id, None)

    @staticmethod
    def parse_voice_confirmation(text: str) -> str | None:
        """Parse a Thai/English voice utterance into 'approve', 'cancel', or None safely."""
        normalized = text.replace("’", "'").replace("‘", "'")
        cleaned = re.sub(r"[\s,!.\"“”]+", " ", normalized.strip().lower()).strip()
        if not cleaned or _INTERROGATIVE_RE.search(cleaned):
            return None
        if _TH_CANCEL_RE.search(cleaned):
            return "cancel"
        if _EN_CANCEL_RE.search(cleaned):
            return "cancel"
        if _EN_APPROVE_RE.search(cleaned):
            return "approve"
        stripped_polite = re.sub(r"(?:\s*(?:ครับ|ค่ะ|นะ|จ้า|จ้ะ))+$", "", cleaned).strip()
        if stripped_polite in _TH_APPROVE_EXACT:
            return "approve"
        stripped_emphatic = re.sub(r"\s*เลย$", "", stripped_polite).strip()
        if stripped_emphatic in _TH_APPROVE_EXACT:
            return "approve"
        return None
