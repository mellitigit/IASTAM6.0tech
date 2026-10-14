"""Downlink delivery prediction: calibrated P(file delivered before the contact window closes).

The artifact is exported by the last cell of IASTAM_Satellite_Radio_Link_Prediction.ipynb as plain
scikit-learn objects: {"pipeline": Pipeline, "calibration": {"method", "estimator"}}.
Trained on synthetic simulator data, so predictions are illustrative, not validated for a real link.
"""

import json
import threading
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.core.config import settings

_bundle: dict[str, Any] | None = None
_lock = threading.Lock()


class LinkModelUnavailableError(RuntimeError):
    """The model or its metadata is missing or could not be loaded."""


def _load() -> dict[str, Any]:
    global _bundle
    if _bundle is None:
        with _lock:
            if _bundle is None:
                try:
                    artifact = joblib.load(settings.link_model_path)
                    meta = json.loads(settings.link_meta_path.read_text())
                except FileNotFoundError as exc:
                    raise LinkModelUnavailableError(f"link delivery model file not found: {exc.filename}") from exc
                except Exception as exc:  # corrupt pickle, incompatible scikit-learn, ...
                    raise LinkModelUnavailableError(f"could not load link delivery model: {exc}") from exc
                _bundle = {"artifact": artifact, "meta": meta}
    return _bundle


def metadata() -> dict[str, Any]:
    return _load()["meta"]


def samples() -> list[dict[str, Any]]:
    try:
        return json.loads(settings.link_samples_path.read_text())["samples"]
    except FileNotFoundError:
        return []


def _calibrate(p_raw: np.ndarray, method: str, estimator: Any, clip: float) -> np.ndarray:
    if method == "raw":
        return p_raw
    if method == "isotonic":
        return estimator.transform(p_raw)
    if method == "platt":
        p = np.clip(p_raw, clip, 1 - clip)
        return estimator.predict_proba(np.log(p / (1 - p)).reshape(-1, 1))[:, 1]
    raise LinkModelUnavailableError(f"unknown calibration method {method!r}")


def predict(features: dict[str, Any]) -> dict[str, Any]:
    """features: one value per model input (None is allowed and imputed). Raises KeyError if any are missing."""
    bundle = _load()
    meta, artifact = bundle["meta"], bundle["artifact"]
    missing = [f for f in meta["features"] if f not in features]
    if missing:
        raise KeyError(missing)
    X = pd.DataFrame([{f: features[f] for f in meta["features"]}])
    numeric = [f for f in meta["features"] if f not in meta["categorical_features"]]
    X[numeric] = X[numeric].apply(pd.to_numeric, errors="raise")
    p_raw = artifact["pipeline"].predict_proba(X)[:, 1]
    calibration = artifact["calibration"]
    probability = float(_calibrate(p_raw, calibration["method"], calibration["estimator"], meta["logit_clip"])[0])
    return {
        "probability": probability,
        "threshold": meta["threshold"],
        "decision": "ATTEMPT" if probability >= meta["threshold"] else "DEFER",
    }
