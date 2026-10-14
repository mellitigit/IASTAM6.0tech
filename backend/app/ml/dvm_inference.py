"""Inference for the traced DVM (data value model) CNN.

The model is loaded lazily, once per process, on CPU. Input is a float32 patch of
shape (6, H, W) with reflectance in [0, 1] and band order B04, B03, B02, B08, B11, B12.
"""

import math
import threading

import numpy as np
import torch

from app.core.config import settings

URGENCY_CLASSES = 4

_model: torch.jit.ScriptModule | None = None
_lock = threading.Lock()


class ModelUnavailableError(RuntimeError):
    """The model file is missing or could not be loaded."""


def get_model() -> torch.jit.ScriptModule:
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                path = settings.dvm_model_path
                if not path.is_file():
                    raise ModelUnavailableError(f"DVM model not found at {path}")
                try:
                    _model = torch.jit.load(str(path), map_location="cpu").eval()
                except Exception as exc:  # corrupt file, incompatible torch version, ...
                    raise ModelUnavailableError(f"Could not load DVM model: {exc}") from exc
    return _model


def predict(image: np.ndarray) -> dict:
    """Run the model on one patch and return the outputs the watchdog expects."""
    if image.ndim != 3 or image.shape[0] != 6:
        raise ValueError(f"expected (6, H, W), got {image.shape}")
    x = torch.from_numpy(np.ascontiguousarray(image, dtype=np.float32)).unsqueeze(0)
    with torch.inference_mode():
        usability, urgency, recon = get_model()(x)
    probs = [float(p) for p in urgency[0].tolist()]
    # Shannon entropy normalised to [0, 1] (the watchdog's threshold is 0.82).
    entropy = -sum(p * math.log(p) for p in probs if p > 0) / math.log(URGENCY_CLASSES)
    return {
        "urgency_probs": probs,
        "usability_pred": float(usability[0]),
        "entropy": float(entropy),
        "recon_error": float(recon[0]),
    }
