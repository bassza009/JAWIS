"""Lock-free queue mic capture with worker-thread DSP, 20Hz spectrum, and VAD end-of-speech."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import io
import queue
import threading
import time
from typing import Callable
import wave
import numpy as np
import sounddevice as sd

from jawis.config import AppConfig


@dataclass(frozen=True)
class SpectrumMetrics:
    db_level: float
    peak_freq_hz: float
    bands: list[float]
    audio_level: float


def pcm16_to_wav_bytes(pcm: bytes, sample_rate_hz: int = 16_000) -> bytes:
    """Wrap raw mono 16-bit PCM bytes in a standard RIFF WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate_hz)
        wf.writeframes(pcm)
    return buf.getvalue()


def compute_spectrum_metrics(samples_int16: np.ndarray, sample_rate_hz: int = 16_000) -> SpectrumMetrics:
    """Compute dB level, peak frequency, 15 log-spaced bands, and normalized audio level."""
    if samples_int16.size == 0:
        return SpectrumMetrics(-60.0, 0.0, [0.0] * 15, 0.0)
    norm = samples_int16.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(norm * norm)) + 1e-9)
    db = max(-60.0, min(0.0, 20.0 * float(np.log10(rms))))
    audio_level = min(1.0, max(0.0, (db + 55.0) / 50.0))

    windowed = norm * np.hanning(norm.size)
    fft_mag = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(norm.size, d=1.0 / sample_rate_hz)
    peak_idx = int(np.argmax(fft_mag)) if fft_mag.size else 0
    peak_freq = float(freqs[peak_idx]) if freqs.size else 0.0

    edges = np.geomspace(80.0, min(8000.0, sample_rate_hz / 2.0), 16)
    bands: list[float] = []
    for i in range(15):
        mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
        val = float(np.mean(fft_mag[mask])) if np.any(mask) else 0.0
        bands.append(min(1.0, val / 15.0))

    return SpectrumMetrics(round(db, 1), round(peak_freq, 1), bands, round(audio_level, 3))


class AudioCaptureManager:
    """Offloads DSP/wakeword away from PortAudio's C callback onto a single worker thread."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._frame_samples = int(config.sample_rate_hz * config.frame_duration_ms / 1000)
        max_ring_frames = max(1, int((config.max_audio_ring_seconds * 1000) / config.frame_duration_ms))
        self._ring_buffer: deque[bytes] = deque(maxlen=max_ring_frames)
        self._command_frames: list[bytes] = []
        self._max_cmd_frames = int((config.max_utterance_seconds * 1000) / config.frame_duration_ms)
        self._capturing_command = False
        self._lock = threading.Lock()
        self._queue: queue.SimpleQueue[np.ndarray | None] = queue.SimpleQueue()
        self._worker: threading.Thread | None = None
        self._stream: sd.InputStream | None = None
        self.is_mic_available = False
        self._on_spectrum: Callable[[SpectrumMetrics], None] | None = None
        self._on_pcm_frame: Callable[[np.ndarray], None] | None = None
        self._last_spectrum_ts = 0.0
        self._last_speech_ts = 0.0
        self._speech_detected = False
        self.noise_gate_enabled = True

    def set_callbacks(
        self,
        *,
        on_spectrum: Callable[[SpectrumMetrics], None] | None = None,
        on_pcm_frame: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self._on_spectrum = on_spectrum
        self._on_pcm_frame = on_pcm_frame

    def start(self) -> bool:
        if self._stream is not None:
            return True
        try:
            self._worker = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
            self._stream = sd.InputStream(
                samplerate=self.config.sample_rate_hz,
                channels=1,
                dtype="int16",
                blocksize=self._frame_samples,
                callback=self._sd_callback,
            )
            self._stream.start()
            self.is_mic_available = True
            return True
        except Exception:
            self.stop()
            return False

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        self.is_mic_available = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if self._worker is not None:
            self._queue.put(None)
            self._worker.join(timeout=1.0)
            self._worker = None
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._ring_buffer.clear()
            self._command_frames.clear()
            self._capturing_command = False

    def set_capturing_command(self, active: bool, *, include_preroll: bool = False) -> None:
        with self._lock:
            self._capturing_command = active
            self._speech_detected = False
            self._last_speech_ts = time.monotonic()
            if active:
                self._command_frames = list(self._ring_buffer) if include_preroll else []

    def pop_command_wav(self) -> bytes:
        with self._lock:
            self._capturing_command = False
            pcm = b"".join(self._command_frames)
            self._command_frames.clear()
        return pcm16_to_wav_bytes(pcm, self.config.sample_rate_hz) if pcm else b""

    async def wait_for_utterance_end(
        self,
        *,
        max_seconds: float,
        silence_seconds: float = 1.2,
        no_speech_timeout: float = 5.0,
        cancel_event: asyncio.Event | None = None,
    ) -> None:
        start = time.monotonic()
        while True:
            if cancel_event and cancel_event.is_set():
                return
            now = time.monotonic()
            if now - start >= max_seconds:
                return
            with self._lock:
                spoke = self._speech_detected
                since_speech = now - self._last_speech_ts
            if spoke and since_speech >= silence_seconds:
                return
            if not spoke and (now - start) >= no_speech_timeout:
                return
            await asyncio.sleep(0.08)

    def _sd_callback(self, indata: np.ndarray, _frames: int, _time_info, _status) -> None:
        self._queue.put(indata[:, 0].copy())

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            self._process_pcm_chunk(item)

    def _process_pcm_chunk(self, mono_int16: np.ndarray) -> None:
        norm = mono_int16.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(norm * norm)) + 1e-9)
        vad_threshold = 0.015 if self.noise_gate_enabled else 0.005
        is_speech = rms >= vad_threshold
        raw = mono_int16.tobytes()

        with self._lock:
            self._ring_buffer.append(raw)
            if self._capturing_command:
                if is_speech:
                    self._speech_detected = True
                    self._last_speech_ts = time.monotonic()
                if len(self._command_frames) < self._max_cmd_frames:
                    if self._speech_detected or not self.noise_gate_enabled or len(self._command_frames) < 10:
                        self._command_frames.append(raw)

        if self._on_pcm_frame is not None:
            self._on_pcm_frame(mono_int16)

        now = time.monotonic()
        if self._on_spectrum is not None and (now - self._last_spectrum_ts) >= 0.05:
            self._last_spectrum_ts = now
            self._on_spectrum(compute_spectrum_metrics(mono_int16, self.config.sample_rate_hz))
