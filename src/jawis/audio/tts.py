"""Lazy edge-tts + miniaudio playback via dedicated OutputStream (never interrupts InputStream)."""

from __future__ import annotations

import asyncio
import re
import threading
import numpy as np
import sounddevice as sd

_THAI_RE = re.compile(r"[\u0E00-\u0E7F]")


class TTSManager:
    """Manages non-blocking speech synthesis and dedicated OutputStream playback."""

    def __init__(self) -> None:
        self.is_speaking = False
        self.muted = False
        self._gen_id = 0
        self._stop_event = threading.Event()
        self._async_lock: asyncio.Lock | None = None
        self._async_lock_loop: asyncio.AbstractEventLoop | None = None
        self._stream_lock = threading.Lock()
        self._out_stream: sd.OutputStream | None = None
        self._sapi_speaker = None

    def _get_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._async_lock is None or self._async_lock_loop is not loop or self._async_lock_loop.is_closed():
            self._async_lock = asyncio.Lock()
            self._async_lock_loop = loop
        return self._async_lock

    @staticmethod
    def select_voice(text: str) -> str:
        return "th-TH-NiwatNeural" if _THAI_RE.search(text) else "en-GB-RyanNeural"

    @staticmethod
    def _format_rate(rate_pct: int) -> str:
        return f"{max(-20, min(40, rate_pct - 100)):+d}%"

    @staticmethod
    def _format_pitch(pitch_pct: int) -> str:
        return f"{max(-20, min(20, pitch_pct - 100)):+d}Hz"

    @staticmethod
    def resample_pcm16(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        if src_rate == dst_rate or samples.size == 0:
            return samples
        out_len = max(1, int(round(samples.shape[0] * dst_rate / src_rate)))
        x_old = np.linspace(0.0, 1.0, samples.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, out_len, endpoint=False)
        return np.interp(x_new, x_old, samples.astype(np.float32)).astype(np.int16)

    def stop(self) -> None:
        self._gen_id += 1
        self._stop_event.set()
        with self._stream_lock:
            if self._out_stream is not None:
                try:
                    self._out_stream.abort()
                    self._out_stream.close()
                except Exception:
                    pass
                self._out_stream = None
        if self._sapi_speaker is not None:
            try:
                self._sapi_speaker.Speak("", 2)  # SVSFPurgeBeforeSpeak
            except Exception:
                pass
        self.is_speaking = False

    async def speak(self, text: str, *, pitch_pct: int = 100, rate_pct: int = 105) -> None:
        clean = text.strip()
        if not clean or self.muted:
            return
        async with self._get_async_lock():
            self._gen_id += 1
            my_gen = self._gen_id
            self._stop_event.clear()
            self.is_speaking = True
            try:
                import edge_tts

                voice = self.select_voice(clean)
                communicate = edge_tts.Communicate(
                    clean,
                    voice=voice,
                    rate=self._format_rate(rate_pct),
                    pitch=self._format_pitch(pitch_pct),
                )
                mp3_chunks = bytearray()
                async for chunk in communicate.stream():
                    if self._stop_event.is_set() or my_gen != self._gen_id:
                        return
                    if chunk.get("type") == "audio":
                        mp3_chunks.extend(chunk["data"])
                if mp3_chunks and not self._stop_event.is_set() and my_gen == self._gen_id:
                    await asyncio.to_thread(self._play_mp3_bytes, bytes(mp3_chunks), my_gen)
            except Exception:
                if not self._stop_event.is_set() and my_gen == self._gen_id:
                    await asyncio.to_thread(self._fallback_sapi5, clean, my_gen)
            finally:
                if my_gen == self._gen_id:
                    self.is_speaking = False

    def _play_mp3_bytes(self, mp3_bytes: bytes, my_gen: int) -> None:
        if not mp3_bytes:
            return
        import miniaudio

        decoded = miniaudio.decode(mp3_bytes, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1)
        samples = np.frombuffer(decoded.samples, dtype=np.int16)
        if samples.size == 0:
            return
        rate = decoded.sample_rate
        stream: sd.OutputStream | None = None
        for try_rate in (rate, 16_000):
            if self._stop_event.is_set() or my_gen != self._gen_id:
                break
            try:
                pcm = self.resample_pcm16(samples, rate, try_rate)
                stream = sd.OutputStream(samplerate=try_rate, channels=1, dtype="int16")
                stream.start()
                with self._stream_lock:
                    if self._stop_event.is_set() or my_gen != self._gen_id:
                        break
                    self._out_stream = stream
                block = 2048
                for i in range(0, len(pcm), block):
                    if self._stop_event.is_set() or my_gen != self._gen_id:
                        break
                    stream.write(pcm[i : i + block].reshape(-1, 1))
                break
            except Exception:
                with self._stream_lock:
                    if self._out_stream is stream:
                        self._out_stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                    stream = None
        if stream is not None:
            with self._stream_lock:
                if self._out_stream is stream:
                    self._out_stream = None
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass

    def _fallback_sapi5(self, text: str, my_gen: int) -> None:
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            try:
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                self._sapi_speaker = speaker
                speaker.Speak(text, 1)  # SVSFlagsAsync
                while not speaker.WaitUntilDone(100):
                    if self._stop_event.is_set() or my_gen != self._gen_id:
                        speaker.Speak("", 2)
                        break
            finally:
                self._sapi_speaker = None
                pythoncom.CoUninitialize()
        except Exception:
            return
