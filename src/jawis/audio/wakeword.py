"""Thread-safe lazy ONNX wake word detector aligned to openWakeWord's 1280-sample (80ms) frame."""

from __future__ import annotations

import gc
from pathlib import Path
import threading
import time
from typing import Any, Callable, Protocol
import numpy as np

from jawis.config import AppConfig

OPENWAKEWORD_CHUNK_SAMPLES = 1280  # 80 ms @ 16 kHz required by melspectrogram.onnx


class WakeWordModel(Protocol):
    """Minimal protocol satisfied by openWakeWord's ONNX Model and test doubles."""

    def predict(self, audio_frame: np.ndarray) -> dict[str, float]: ...


class OnnxWakeWordEngine:
    """Zero-PyTorch wake word detector loaded only while WAKE WORD mode is active."""

    def __init__(
        self,
        config: AppConfig,
        *,
        threshold: float = 0.55,
        cooldown_seconds: float = 2.0,
        model_factory: Callable[[Path], WakeWordModel] | None = None,
    ) -> None:
        self.config = config
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        self._model_factory = model_factory or self._default_model_factory
        self._lock = threading.Lock()
        self._model: Any = None
        self._sample_accum = np.empty(0, dtype=np.int16)
        self._last_trigger_ts: float | None = None
        self._last_error = ""

    @property
    def is_loaded(self) -> bool:
        """Return whether an ONNX wake word model is currently resident in memory."""
        with self._lock:
            return self._model is not None

    @staticmethod
    def _default_model_factory(models_dir: Path) -> WakeWordModel:
        from openwakeword.model import Model

        en_model = models_dir / "hey_jarvis_v0.1.onnx"
        if not en_model.exists():
            raise FileNotFoundError(
                f"Missing {en_model.name}; download models in Security Grid first"
            )
        paths = [str(en_model)]
        custom_thai = models_dir / "nee_jarvis.onnx"
        if custom_thai.exists():
            paths.append(str(custom_thai))
        return Model(wakeword_models=paths, inference_framework="onnx")

    def download_english_models(self) -> dict[str, object]:
        """Lazily download openWakeWord English ONNX weights into models_dir."""
        try:
            import openwakeword.utils

            openwakeword.utils.download_models(
                model_names=["hey_jarvis"],
                target_directory=str(self.config.models_dir),
            )
            with self._lock:
                self._last_error = ""
            return {"ok": True, "models_dir": str(self.config.models_dir)}
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}

    def load(self) -> bool:
        """Load ONNX wake word models into memory; returns False cleanly on failure."""
        with self._lock:
            if self._model is not None:
                return True
            try:
                self._model = self._model_factory(self.config.models_dir)
                self._sample_accum = np.empty(0, dtype=np.int16)
                self._last_error = ""
                return True
            except Exception as exc:
                self._model = None
                self._last_error = str(exc)
                return False

    def unload(self) -> None:
        """Release the ONNX model and sample accumulator and trigger garbage collection."""
        with self._lock:
            if self._model is not None:
                self._model = None
                self._sample_accum = np.empty(0, dtype=np.int16)
                self._last_trigger_ts = None
                gc.collect()

    def status(self) -> dict[str, object]:
        """Return current availability, model presence on disk, and active state."""
        en_present = (self.config.models_dir / "hey_jarvis_v0.1.onnx").exists()
        thai_present = (self.config.models_dir / "nee_jarvis.onnx").exists()
        with self._lock:
            loaded = self._model is not None
            last_error = self._last_error
        detail = last_error or (
            "Active"
            if loaded
            else (
                "Ready"
                if en_present
                else "Download English model or drop nee_jarvis.onnx"
            )
        )
        return {
            "loaded": loaded,
            "available": loaded or en_present,
            "english_model_present": en_present,
            "thai_custom_model_present": thai_present,
            "models_dir": str(self.config.models_dir),
            "detail": detail,
        }

    def process_frame(self, mono_int16: np.ndarray) -> str | None:
        """Accumulate incoming PCM int16 frames and run 1280-sample ONNX inference."""
        if mono_int16 is None:
            return None
        with self._lock:
            if self._model is None:
                return None
            flat = np.asarray(mono_int16, dtype=np.int16).reshape(-1)
            if flat.size == 0:
                return None
            self._sample_accum = np.concatenate((self._sample_accum, flat))
            triggered: str | None = None
            while self._sample_accum.size >= OPENWAKEWORD_CHUNK_SAMPLES:
                chunk = self._sample_accum[:OPENWAKEWORD_CHUNK_SAMPLES]
                self._sample_accum = self._sample_accum[OPENWAKEWORD_CHUNK_SAMPLES:]
                scores = self._model.predict(chunk)
                now = time.monotonic()
                if (
                    self._last_trigger_ts is not None
                    and (now - self._last_trigger_ts) < self.cooldown_seconds
                ):
                    continue
                for label, score in scores.items():
                    if float(score) >= self.threshold:
                        self._last_trigger_ts = now
                        triggered = str(label)
                        break
            return triggered
