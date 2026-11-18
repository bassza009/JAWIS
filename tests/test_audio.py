import asyncio
import io
import sys
import wave

import numpy as np
import pytest

from jawis.audio.capture import (
    AudioCaptureManager,
    compute_spectrum_metrics,
    pcm16_to_wav_bytes,
)
from jawis.audio.tts import TTSManager
from jawis.config import load_config


def test_spectrum_metrics_and_wav_encoding() -> None:
    sr = 16_000
    t = np.linspace(0, 0.03, 480, endpoint=False)
    tone = (np.sin(2 * np.pi * 440.0 * t) * 16_000).astype(np.int16)

    metrics = compute_spectrum_metrics(tone, sr)
    assert len(metrics.bands) == 15
    assert all(0.0 <= b <= 1.0 for b in metrics.bands)
    assert 350.0 <= metrics.peak_freq_hz <= 550.0
    assert -60.0 < metrics.db_level <= 0.0

    wav_bytes = pcm16_to_wav_bytes(tone.tobytes(), sr)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == sr
        assert wf.getnframes() == 480


def test_ring_buffer_bounded_and_missing_mic_graceful(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = load_config(tmp_path)
    cap = AudioCaptureManager(cfg)

    def broken_stream(*args, **kwargs):
        raise RuntimeError("No audio device")

    monkeypatch.setattr("jawis.audio.capture.sd.InputStream", broken_stream)
    assert cap.start() is False
    assert cap.is_mic_available is False

    frame = np.zeros(480, dtype=np.int16)
    for _ in range(140):
        cap._process_pcm_chunk(frame)
    max_frames = int((cfg.max_audio_ring_seconds * 1000) / cfg.frame_duration_ms)
    assert len(cap._ring_buffer) <= max_frames


@pytest.mark.asyncio
async def test_command_capture_preroll_and_utterance_end(tmp_path) -> None:
    cfg = load_config(tmp_path)
    cap = AudioCaptureManager(cfg)
    silence = np.zeros(480, dtype=np.int16)
    speech = (np.ones(480, dtype=np.int16) * 4_000).astype(np.int16)

    cap._process_pcm_chunk(silence)
    cap.set_capturing_command(True, include_preroll=True)
    cap._process_pcm_chunk(speech)

    cancel_event = asyncio.Event()
    await cap.wait_for_utterance_end(
        max_seconds=0.3,
        silence_seconds=0.05,
        no_speech_timeout=0.3,
        cancel_event=cancel_event,
    )

    wav_bytes = cap.pop_command_wav()
    assert wav_bytes
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == cfg.sample_rate_hz
        assert wf.getnframes() >= 960
    assert cap.pop_command_wav() == b""
    cap.stop()


@pytest.mark.asyncio
async def test_tts_selects_voice_and_resamples() -> None:
    tts = TTSManager()
    assert tts.select_voice("เปิด Chrome ให้แล้วครับ") == "th-TH-NiwatNeural"
    assert tts.select_voice("All systems operational, sir.") == "en-GB-RyanNeural"
    samples = np.arange(240, dtype=np.int16)
    down = TTSManager.resample_pcm16(samples, 24_000, 16_000)
    assert len(down) == 160
    tts.stop()
    assert tts.is_speaking is False


def test_tts_lazy_imports_not_at_module_top_level() -> None:
    import jawis.audio.tts as tts_mod

    for forbidden in ("edge_tts", "miniaudio", "win32com"):
        assert forbidden not in vars(tts_mod)


@pytest.mark.asyncio
async def test_tts_empty_and_muted_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    tts = TTSManager()
    await tts.speak("   ")
    assert tts.is_speaking is False

    tts.muted = True
    await tts.speak("Hello sir")
    assert tts.is_speaking is False


@pytest.mark.asyncio
async def test_tts_dedicated_output_stream_and_hfp_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    tts = TTSManager()

    # Guard: global sd.stop() must NEVER be called by TTSManager
    def fail_global_stop():
        raise AssertionError("Global sd.stop() must not be called")

    monkeypatch.setattr("jawis.audio.tts.sd.stop", fail_global_stop)

    class FakeCommunicate:
        def __init__(self, text: str, voice: str, rate: str, pitch: str) -> None:
            self.text = text
            self.voice = voice

        async def stream(self):
            yield {"type": "audio", "data": b"fake-mp3-bytes"}

    fake_edge_tts = types.SimpleNamespace(Communicate=FakeCommunicate)

    class FakeDecoded:
        sample_rate = 24_000
        samples = np.ones(2400, dtype=np.int16).tobytes()

    fake_miniaudio = types.SimpleNamespace(
        SampleFormat=types.SimpleNamespace(SIGNED16="S16"),
        decode=lambda data, output_format, nchannels: FakeDecoded(),
    )

    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge_tts)
    monkeypatch.setitem(sys.modules, "miniaudio", fake_miniaudio)

    opened_rates: list[int] = []
    written_frames: list[int] = []

    class FakeOutputStream:
        def __init__(self, samplerate: int, channels: int, dtype: str) -> None:
            if samplerate == 24_000:
                raise RuntimeError("Bluetooth HFP rejects 24kHz")
            opened_rates.append(samplerate)
            self.closed = False

        def start(self) -> None:
            pass

        def write(self, data: np.ndarray) -> None:
            written_frames.append(data.shape[0])

        def abort(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("jawis.audio.tts.sd.OutputStream", FakeOutputStream)

    await tts.speak("Systems online, sir.")
    assert opened_rates == [16_000]
    assert sum(written_frames) == 1600
    assert tts.is_speaking is False
    tts.stop()
