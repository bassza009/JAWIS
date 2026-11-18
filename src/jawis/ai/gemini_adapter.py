"""Lazy-imported Google Gemini adapter handling empty schemas and grouped parallel tool responses."""

from __future__ import annotations

import asyncio
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
    split_transcript_line,
)
from jawis.storage.secrets import SecretVault


class GeminiAdapter:
    """Google Gemini provider adapter with lazy SDK imports and grouped function responses."""

    provider_name = "gemini"

    def __init__(
        self,
        vault: SecretVault,
        *,
        model: str = "gemini-2.5-flash",
        client_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self.model = model
        self._client_cache = KeyedClientCache(
            vault,
            self.provider_name,
            client_factory or self._default_client_factory,
        )

    @staticmethod
    def _default_client_factory(api_key: str) -> Any:
        try:
            from google import genai
            from google.genai import types

            return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))
        except ImportError as exc:
            raise AIProviderError("google-genai package is not installed") from exc

    def _get_client(self) -> Any:
        return self._client_cache.get()

    @staticmethod
    def _build_gemini_tools(tools: list[dict[str, object]]) -> list[Any] | None:
        if not tools:
            return None
        try:
            from google.genai import types

            declarations = []
            for t in tools:
                params = t.get("parameters")
                has_props = isinstance(params, dict) and bool(params.get("properties"))
                kwargs: dict[str, Any] = {
                    "name": str(t["name"]),
                    "description": str(t["description"]),
                }
                if has_props:
                    kwargs["parameters_json_schema"] = params
                declarations.append(types.FunctionDeclaration(**kwargs))
            return [types.Tool(function_declarations=declarations)]
        except ImportError:
            return tools

    @staticmethod
    def _build_turn_payload(
        history: list[ChatMessage],
        audio_wav: bytes | None,
        text_input: str | None,
        gemini_tools: list[Any] | None,
    ) -> tuple[list[Any], Any]:
        recent = history[-MAX_HISTORY_MESSAGES:]
        try:
            from google.genai import types

            contents: list[Any] = [
                types.Content(
                    role="user" if m.role == "user" else "model",
                    parts=[types.Part.from_text(text=m.content)],
                )
                for m in recent
            ]
            user_parts: list[Any] = []
            if audio_wav:
                user_parts.append(types.Part.from_bytes(data=audio_wav, mime_type="audio/wav"))
                user_parts.append(
                    types.Part.from_text(
                        text=(
                            "Listen to the user's audio. In your final text reply, put "
                            "'TRANSCRIPT: <verbatim speech>' on the first line, then a newline and your answer."
                        )
                    )
                )
            else:
                user_parts.append(types.Part.from_text(text=str(text_input).strip()))
            contents.append(types.Content(role="user", parts=user_parts))

            cfg_kwargs: dict[str, Any] = {
                "system_instruction": SYSTEM_PROMPT,
                "temperature": 0.2,
                "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
            }
            if gemini_tools:
                cfg_kwargs["tools"] = gemini_tools
            return contents, types.GenerateContentConfig(**cfg_kwargs)
        except ImportError:
            contents = [{"role": m.role, "content": m.content} for m in recent]
            contents.append({"role": "user", "text": text_input or "(audio)"})
            cfg = {"system": SYSTEM_PROMPT, **({"tools": gemini_tools} if gemini_tools else {})}
            return contents, cfg

    @staticmethod
    def _make_tool_turn(items: list[tuple[str, dict[str, object], str | None]]) -> Any:
        try:
            from google.genai import types

            # Group parallel function responses into a single 'user' Content turn per Gemini SDK rules.
            parts = [
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        response=res if isinstance(res, dict) else {"result": res},
                        id=call_id,
                    )
                )
                for name, res, call_id in items
            ]
            return types.Content(role="user", parts=parts)
        except ImportError:
            return {
                "role": "user",
                "parts": [
                    {"name": name, "response": res if isinstance(res, dict) else {"result": res}, "id": call_id}
                    for name, res, call_id in items
                ],
            }

    async def transcribe_audio(self, audio_wav: bytes) -> str:
        if not audio_wav:
            return ""
        client = self._get_client()
        try:
            from google.genai import types

            contents: Any = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=audio_wav, mime_type="audio/wav"),
                        types.Part.from_text(text="Transcribe verbatim in Thai/English. Output ONLY the spoken words."),
                    ],
                )
            ]
            cfg: Any = types.GenerateContentConfig(temperature=0.0)
        except ImportError:
            contents = [{"role": "user", "text": "transcribe"}]
            cfg = {}

        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=contents,
                config=cfg,
            )
            return (getattr(response, "text", "") or "").strip()
        except Exception as exc:
            raise AIProviderError(f"Gemini transcription failed: {exc}") from exc

    async def process_turn(
        self,
        *,
        audio_wav: bytes | None,
        text_input: str | None,
        history: list[ChatMessage],
        tools: list[dict[str, object]],
        on_tool_call: ToolCallback,
    ) -> AssistantTurnResult:
        if not audio_wav and not (text_input and text_input.strip()):
            raise AIProviderError("Empty voice and text input")

        client = self._get_client()
        gemini_tools = self._build_gemini_tools(tools)
        contents, cfg = self._build_turn_payload(history, audio_wav, text_input, gemini_tools)

        executed: list[str] = []
        fallback_tx = (text_input or "").strip() or "(Voice command)"

        for _ in range(MAX_TOOL_ITERATIONS):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=cfg,
                )
            except Exception as exc:
                raise AIProviderError(f"Gemini API error: {exc}") from exc

            fcs = getattr(response, "function_calls", None) or []
            if not fcs:
                raw_text = getattr(response, "text", "") or ""
                transcript, reply = (
                    split_transcript_line(raw_text, fallback_tx)
                    if audio_wav
                    else (fallback_tx, raw_text.strip())
                )
                return AssistantTurnResult(
                    reply_text=reply or "ดำเนินการเรียบร้อยครับ",
                    user_transcript=transcript,
                    provider=self.provider_name,
                    tool_calls_executed=executed,
                )

            candidates = getattr(response, "candidates", None) or []
            if candidates and getattr(candidates[0], "content", None):
                contents.append(candidates[0].content)

            grouped_responses: list[tuple[str, dict[str, object], str | None]] = []
            for fc in fcs:
                tool_name = str(fc.name)
                tool_args = dict(getattr(fc, "args", None) or {})
                call_id = getattr(fc, "id", None)
                executed.append(tool_name)
                tool_res = await on_tool_call(tool_name, tool_args)
                grouped_responses.append((tool_name, tool_res, call_id))
            contents.append(self._make_tool_turn(grouped_responses))

        raise AIProviderError("Gemini exceeded maximum tool-call iterations")
