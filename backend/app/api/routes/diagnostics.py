import io
import math

import numpy as np
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import settings
from app.ml import dvm_inference
from app.schemas.diagnostics import DiagnosticsResponse
from app.services.satellite_health_watchdog import evaluate_satellite_health_endpoint

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])

BANDS = 6
MIN_SIDE = 16
MAX_SIDE = 1024

# Handed to the watchdog when inference is skipped; its level-1 pixel check fails first,
# so these values are never used to reach a verdict.
_SKIPPED_AI = {"urgency_probs": [0.25] * 4, "usability_pred": 0.0, "entropy": 0.0, "recon_error": 0.0}


def _json_safe(value: float) -> float | None:
    return value if math.isfinite(value) else None


def read_patch(patch: UploadFile) -> np.ndarray:
    limit = settings.diagnostics_max_upload_bytes
    data = patch.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"patch exceeds {limit} bytes")
    try:
        image = np.load(io.BytesIO(data), allow_pickle=False)
    except Exception:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "patch must be a valid .npy file")
    if not isinstance(image, np.ndarray) or image.dtype.kind not in "fiu":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "patch must be a numeric .npy array")
    if image.ndim != 3 or image.shape[0] != BANDS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"expected shape ({BANDS}, H, W), got {image.shape}")
    if not (MIN_SIDE <= image.shape[1] <= MAX_SIDE and MIN_SIDE <= image.shape[2] <= MAX_SIDE):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"H and W must be between {MIN_SIDE} and {MAX_SIDE}, got {image.shape[1:]}",
        )
    return image.astype(np.float32, copy=False)


def diagnose(image: np.ndarray, battery_pct: float) -> DiagnosticsResponse:
    """DVM inference followed by the FDIR watchdog. Shared with the mission capture endpoint."""
    # NaN/Inf would poison the model, so skip inference and let the watchdog flag the pixels.
    ai = None
    if np.isfinite(image).all():
        try:
            ai = dvm_inference.predict(image)
        except dvm_inference.ModelUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    health = evaluate_satellite_health_endpoint(image, ai or _SKIPPED_AI, battery_pct)
    if ai is not None:
        ai = {
            "urgency_probs": [_json_safe(p) for p in ai["urgency_probs"]],
            "usability_pred": _json_safe(ai["usability_pred"]),
            "entropy": _json_safe(ai["entropy"]),
            "recon_error": _json_safe(ai["recon_error"]),
        }
    return DiagnosticsResponse.model_validate({"ai": ai, "health": health})


@router.post("/health", response_model=DiagnosticsResponse)
def diagnose_health(
    patch: UploadFile = File(..., description=".npy float32 array (6, H, W), reflectance in [0, 1], bands B04,B03,B02,B08,B11,B12"),
    battery_pct: float = Form(85.0, ge=0, le=100),
) -> DiagnosticsResponse:
    return diagnose(read_patch(patch), battery_pct)
