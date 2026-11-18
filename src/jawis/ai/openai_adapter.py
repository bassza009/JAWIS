"""Lazy-imported OpenAI adapter omitting empty tools and wrapping SDK exceptions."""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any, Callable

from jawis.ai.base import (
    MAX_HISTORY_MESSAGES,
    MAX_TOOL_ITERATIONS,
    SYSTEM_PROMPT,
    AIProviderError,
    AssistantTurnResult,
    ChatMessage,
    KeyedClientCache,
    ToolCallback,
)
from jawis.storage.secrets import SecretVault


class OpenAIAdapter:
    """OpenAI Chat Completions + Whisper/STT provider adapter with lazy SDK imports."""

    provider_name = "openai"

    def __init__(
        self,
        vault: SecretVault,
        *,
        model: str = "gpt-4o-mini",
        stt_model: str = "gpt-4o-mini-transcribe",
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self.stt_model = stt_model
        self._client_cache = KeyedClientCache(
            vault,
            self.provider_name,
            client_factory or self._default_client_factory,
        )

    @staticmethod
    def _default_client_factory(api_key: str) -> Any:
        try:
            from openai import OpenAI

            return OpenAI(api_key=api_key, timeout=30.0)
        except ImportError as exc:
            raise AIProviderError("openai package is not installed") from exc

    def _get_client(self) -> Any:
        return self._client_cache.get()

    @staticmethod
    def _to_openai_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for t in tools:
            params = t.get("parameters") or {"type": "object", "properties": {}, "required": []}
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": params,
                    },
                }
            )
        return out

    @staticmethod
    def _parse_tool_args(raw_args: str | None) -> dict[str, object]:
        try:
            parsed = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def transcribe_audio(self, audio_wav: bytes) -> str:
        if not audio_wav:
            return ""
        client = self._get_client()
        audio_file = io.BytesIO(audio_wav)
        audio_file.name = "command.wav"
        try:
            tx = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model=self.stt_model,
                file=audio_file,
            )
            return (getattr(tx, "text", "") or "").strip()
        except Exception as exc:
            raise AIProviderError(f"OpenAI transcription failed: {exc}") from exc

    async def process_turn(
        self,
        *,
        audio_wav: bytes | None,
        text_input: str | None,
        history: list[ChatMessage],
        tools: list[dict[str, object]],
        on_tool_call: ToolCallback,
    ) -> AssistantTurnResult:
        user_text = (text_input or "").strip()
        if audio_wav:
            user_text = await self.transcribe_audio(audio_wav)
            if not user_text:
                return AssistantTurnResult(
                    reply_text="ไม่ได้ยินคำสั่งชัดเจนครับ ลองพูดใหม่อีกครั้งนะครับ",
                    user_transcript="(Unclear audio)",
                    provider=self.provider_name,
                )
        if not user_text:
            raise AIProviderError("Empty voice and text input")

        client = self._get_client()
        messages: list[dict[str, object]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-MAX_HISTORY_MESSAGES:]:
            role = "assistant" if m.role == "model" else m.role
            messages.append({"role": role, "content": m.content})
        messages.append({"role": "user", "content": user_text})

        openai_tools = self._to_openai_tools(tools)
        executed: list[str] = []

        for _ in range(MAX_TOOL_ITERATIONS):
            kwargs: dict[str, object] = {"model": self.model, "messages": messages}
            if openai_tools:
                kwargs["tools"] = openai_tools
            try:
                resp = await asyncio.to_thread(client.chat.completions.create, **kwargs)
            except Exception as exc:
                raise AIProviderError(f"OpenAI API error: {exc}") from exc

            if not getattr(resp, "choices", None):
                raise AIProviderError("OpenAI returned empty choices")
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            if not tool_calls:
                reply = (getattr(msg, "content", "") or "").strip()
                return AssistantTurnResult(
                    reply_text=reply or "ดำเนินการเรียบร้อยครับ",
                    user_transcript=user_text,
                    provider=self.provider_name,
                    tool_calls_executed=executed,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": getattr(msg, "content", "") or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                tool_name = str(tc.function.name)
                args = self._parse_tool_args(tc.function.arguments)
                executed.append(tool_name)
                tool_res = await on_tool_call(tool_name, args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_res, ensure_ascii=False, default=str),
                    }
                )

        raise AIProviderError("OpenAI exceeded maximum tool-call iterations")
