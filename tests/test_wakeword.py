from __future__ import annotations

from pathlib import Path
import sys
import threading
import types

import numpy as np
import pytest

from jawis.audio.wakeword import OPENWAKEWORD_CHUNK_SAMPLES, OnnxWakeWordEngine
from jawis.config import load_config


class FakeModel:
    def __init__(self, score: float, label: str = "hey_jarvis") -> None:
        self.score = score
        self.label = label
        self.chunk_lengths: list[int] = []
        self.chunk_dtypes: list[np.dtype] = []

    def predict(self, audio_frame: np.ndarray) -> dict[str, float]:
        self.chunk_lengths.append(int(audio_frame.size))
        self.chunk_dtypes.append(audio_frame.dtype)
        return {self.label: self.score}


def test_wakeword_accumulates_1280_samples_and_unloads_safely(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    fake = FakeModel(score=0.9)
    engine = OnnxWakeWordEngine(cfg, model_factory=lambda _models_dir: fake)

    assert engine.is_loaded is False
    assert engine.load() is True
    assert engine.is_loaded is True

    # Feed 480-sample (30ms) chunks continuously; 3 chunks = 1440 >= 1280 samples -> 1 inference call of 1280
    loud = np.full(480, 4000, dtype=np.int16)
    assert engine.process_frame(loud) is None
    assert engine.process_frame(loud) is None
    assert engine.process_frame(loud) == "hey_jarvis"
    assert fake.chunk_lengths == [OPENWAKEWORD_CHUNK_SAMPLES]
    assert fake.chunk_dtypes == [np.dtype(np.int16)]
    # Cooldown suppresses immediate re-trigger across subsequent blocks
    assert engine.process_frame(loud) is None
    assert engine.process_frame(loud) is None
    assert engine.process_frame(loud) is None

    engine.unload()
    assert engine.is_loaded is False
    # Calling process_frame when unloaded is a safe no-op
    assert engine.process_frame(loud) is None


def test_wakeword_handles_missing_dependency_gracefully(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    engine = OnnxWakeWordEngine(
        cfg,
        model_factory=lambda _d: (_ for _ in ()).throw(ImportError("missing onnx")),
    )
    assert engine.load() is False
    status = engine.status()
    assert status["available"] is False
    assert status["loaded"] is False
    assert "missing onnx" in str(status["detail"])


def test_wakeword_lazy_import_and_graceful_missing_optional_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "openwakeword" not in sys.modules
    assert "onnxruntime" not in sys.modules

    monkeypatch.setitem(sys.modules, "openwakeword", None)
    monkeypatch.setitem(sys.modules, "openwakeword.model", None)
    monkeypatch.setitem(sys.modules, "openwakeword.utils", None)

    cfg = load_config(tmp_path)
    engine = OnnxWakeWordEngine(cfg)

    assert engine.load() is False
    assert engine.is_loaded is False
    assert engine.status()["available"] is False

    dl = engine.download_english_models()
    assert dl["ok"] is False
    assert "error" in dl


def test_wakeword_handles_arbitrary_shapes_and_multi_chunk_frames(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    fake = FakeModel(score=0.8)
    engine = OnnxWakeWordEngine(cfg, model_factory=lambda _d: fake, cooldown_seconds=0.0)
    assert engine.load() is True

    # Empty frame returns None without calling model
    assert engine.process_frame(np.empty(0, dtype=np.int16)) is None
    assert fake.chunk_lengths == []

    # 2D (480, 1) and (1, 480) and (2, 800) inputs are flattened cleanly
    assert engine.process_frame(np.ones((480, 1), dtype=np.int16)) is None
    assert engine.process_frame(np.ones((1, 480), dtype=np.int16)) is None
    assert engine.process_frame(np.ones((2, 800), dtype=np.int16)) == "hey_jarvis"
    # Total = 480 + 480 + 1600 = 2560 samples => 2 calls of 1280
    assert fake.chunk_lengths == [1280, 1280]
    assert fake.chunk_dtypes == [np.dtype(np.int16), np.dtype(np.int16)]


def test_wakeword_threshold_and_cooldown_with_small_monotonic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(tmp_path)
    fake = FakeModel(score=0.50)
    now = [0.2]
    monkeypatch.setattr("jawis.audio.wakeword.time.monotonic", lambda: now[0])

    engine = OnnxWakeWordEngine(
        cfg,
        threshold=0.55,
        cooldown_seconds=2.0,
        model_factory=lambda _d: fake,
    )
    assert engine.load() is True

    frame = np.ones(1280, dtype=np.int16)
    # Below threshold does not trigger
    assert engine.process_frame(frame) is None

    # Above threshold triggers even when monotonic() < cooldown_seconds
    fake.score = 0.75
    assert engine.process_frame(frame) == "hey_jarvis"

    # Within cooldown window -> suppressed
    now[0] = 1.5
    assert engine.process_frame(frame) is None

    # After cooldown expires -> triggers again
    now[0] = 2.3
    assert engine.process_frame(frame) == "hey_jarvis"

    # Unload clears cooldown state so reload triggers immediately
    engine.unload()
    assert engine.load() is True
    assert engine.process_frame(frame) == "hey_jarvis"


def test_wakeword_status_and_default_factory_model_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(tmp_path)
    captured_calls: list[dict[str, object]] = []

    class FakeOwwModel:
        def __init__(self, *, wakeword_models: list[str], inference_framework: str) -> None:
            captured_calls.append(
                {"wakeword_models": wakeword_models, "inference_framework": inference_framework}
            )

        def predict(self, audio_frame: np.ndarray) -> dict[str, float]:
            return {"hey_jarvis": 0.1}

    fake_oww_pkg = types.ModuleType("openwakeword")
    fake_oww_model = types.ModuleType("openwakeword.model")
    fake_oww_model.Model = FakeOwwModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openwakeword", fake_oww_pkg)
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake_oww_model)

    engine = OnnxWakeWordEngine(cfg)
    # Missing hey_jarvis_v0.1.onnx on disk
    assert engine.load() is False
    st = engine.status()
    assert st["english_model_present"] is False
    assert st["thai_custom_model_present"] is False
    assert st["available"] is False

    # Create English + optional Thai custom ONNX files
    en_path = cfg.models_dir / "hey_jarvis_v0.1.onnx"
    th_path = cfg.models_dir / "nee_jarvis.onnx"
    en_path.write_bytes(b"onnx")
    th_path.write_bytes(b"onnx")

    st_ready = engine.status()
    assert st_ready["english_model_present"] is True
    assert st_ready["thai_custom_model_present"] is True
    assert st_ready["available"] is True

    assert engine.load() is True
    assert captured_calls == [
        {
            "wakeword_models": [str(en_path), str(th_path)],
            "inference_framework": "onnx",
        }
    ]
    assert engine.status()["detail"] == "Active"


def test_wakeword_thread_safety_under_concurrent_load_process_unload(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    fake = FakeModel(score=0.2)
    engine = OnnxWakeWordEngine(cfg, model_factory=lambda _d: fake)
    errors: list[Exception] = []
    frame = np.ones(480, dtype=np.int16)

    def feeder() -> None:
        try:
            for _ in range(100):
                engine.process_frame(frame)
                engine.status()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def toggler() -> None:
        try:
            for _ in range(25):
                engine.load()
                engine.unload()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [
        threading.Thread(target=feeder),
        threading.Thread(target=feeder),
        threading.Thread(target=toggler),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert errors == []
