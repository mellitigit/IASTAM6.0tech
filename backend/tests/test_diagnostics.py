import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.ml import dvm_inference
from app.services.satellite_health_watchdog import SatelliteSystemHealthWatchdog

client = TestClient(app)

URL = "/api/diagnostics/health"
CONFIDENT_AI = {"urgency_probs": [0.05, 0.9, 0.03, 0.02], "usability_pred": 0.92, "entropy": 0.3, "recon_error": 0.05}
model_missing = not settings.dvm_model_path.is_file()


def npy(array: np.ndarray) -> io.BytesIO:
    buf = io.BytesIO()
    np.save(buf, array)
    buf.seek(0)
    return buf


def post(array: np.ndarray, battery_pct: float = 85.0):
    return client.post(URL, files={"patch": ("patch.npy", npy(array))}, data={"battery_pct": battery_pct})


def smooth_patch(size: int = 64) -> np.ndarray:
    """Spatially correlated 6-band reflectance (vegetation/soil mix), not random noise."""
    yy, xx = np.mgrid[0:size, 0:size] / size
    field = np.sin(2 * np.pi * (1.3 * xx + 0.7 * yy)) + np.sin(2 * np.pi * (0.4 * xx - 1.1 * yy) + 1.0)
    field = (field - field.min()) / (field.max() - field.min())
    veg = np.array([0.05, 0.08, 0.04, 0.35, 0.20, 0.10])[:, None, None]
    soil = np.array([0.18, 0.15, 0.12, 0.28, 0.32, 0.25])[:, None, None]
    return (veg * field + soil * (1 - field)).astype(np.float32)


@pytest.fixture
def confident_model(monkeypatch):
    """Stub the network so watchdog paths are tested deterministically."""
    calls = []

    def fake_predict(image):
        calls.append(image.shape)
        return dict(CONFIDENT_AI)

    monkeypatch.setattr(dvm_inference, "predict", fake_predict)
    return calls


def test_nominal_patch_is_healthy(confident_model):
    r = post(smooth_patch())
    assert r.status_code == 200
    body = r.json()
    assert body["ai"]["usability_pred"] == pytest.approx(0.92)
    assert body["health"]["health_status"] == "NOMINAL_HEALTHY"
    assert body["health"]["severity"] == "NOMINAL"
    assert body["health"]["recommended_action"] == "PROCEED_NORMAL"
    assert body["health"]["ai_prediction_trusted"] is True
    assert body["health"]["battery_safe"] is True


def test_timestamp_is_current_utc(confident_model):
    stamp = datetime.fromisoformat(post(smooth_patch()).json()["health"]["timestamp"])
    assert stamp.tzinfo is not None
    assert abs(datetime.now(timezone.utc) - stamp) < timedelta(minutes=1)


def test_dead_band(confident_model):
    patch = smooth_patch()
    patch[5] = 0.3  # SWIR2 stuck at a constant value
    health = post(patch).json()["health"]
    assert health["health_status"] == "SENSOR_DEAD_CHANNEL"
    assert health["severity"] == "WARNING"
    assert health["recommended_action"] == "SWITCH_TO_4BAND_VNIR"
    assert "B12_SWIR2" in health["diagnostic_message"]


def test_saturated_band_is_not_reported_as_dead(confident_model):
    patch = smooth_patch()
    patch[0] = 1.0  # fully clipped red band: zero variance, but the cause is saturation
    health = post(patch).json()["health"]
    assert health["health_status"] == "SENSOR_NOISE_SATURATION"
    assert health["root_cause"] == "HARDWARE_OPTICS_GLARE"
    assert "B04_RED" in health["diagnostic_message"]


def test_nan_pixel_skips_model(confident_model):
    patch = smooth_patch()
    patch[2, 0, 0] = np.nan
    r = post(patch)
    assert r.status_code == 200
    body = r.json()
    assert confident_model == []  # model never called
    assert body["ai"] is None
    assert body["health"]["health_status"] == "RADIATION_BIT_FLIP"
    assert body["health"]["severity"] == "CRITICAL"
    assert body["health"]["ai_prediction_trusted"] is False


def test_low_battery_is_flagged(confident_model):
    health = post(smooth_patch(), battery_pct=22).json()["health"]
    assert health["battery_safe"] is False
    assert post(smooth_patch(), battery_pct=30).json()["health"]["battery_safe"] is True


@pytest.mark.parametrize("shape", [(3, 64, 64), (6, 64), (6, 8, 8), (6, 2000, 16)])
def test_bad_shape_is_rejected(confident_model, shape):
    assert post(np.zeros(shape, dtype=np.float32)).status_code == 422
    assert confident_model == []


def test_invalid_file_is_rejected(confident_model):
    r = client.post(URL, files={"patch": ("patch.npy", io.BytesIO(b"not a numpy file"))}, data={"battery_pct": 80})
    assert r.status_code == 400


def test_battery_out_of_range_is_rejected(confident_model):
    assert post(smooth_patch(), battery_pct=150).status_code == 422


def test_upload_size_is_capped(confident_model, monkeypatch):
    monkeypatch.setattr(settings, "diagnostics_max_upload_bytes", 1024)
    assert post(smooth_patch()).status_code == 413


def test_missing_model_returns_503(monkeypatch, tmp_path):
    monkeypatch.setattr(dvm_inference, "_model", None)
    monkeypatch.setattr(settings, "dvm_model_path", tmp_path / "missing.pt")
    r = post(smooth_patch())
    assert r.status_code == 503


def test_watchdog_checks_saturation_before_variance():
    patch = smooth_patch()
    patch[3] = 1.0
    report = SatelliteSystemHealthWatchdog().diagnose_system(patch, CONFIDENT_AI, 85)
    assert report["health_status"] == "SENSOR_NOISE_SATURATION"


@pytest.mark.skipif(model_missing, reason="DVM model file not present")
def test_real_model_outputs_are_well_formed():
    r = post(smooth_patch(128))
    assert r.status_code == 200
    ai = r.json()["ai"]
    assert len(ai["urgency_probs"]) == 4
    assert sum(ai["urgency_probs"]) == pytest.approx(1.0, abs=1e-3)
    assert 0.0 <= ai["entropy"] <= 1.0
    assert 0.0 <= ai["usability_pred"] <= 1.0
    assert ai["recon_error"] >= 0.0


@pytest.mark.skipif(model_missing, reason="DVM model file not present")
def test_real_model_dead_band_end_to_end():
    patch = smooth_patch(128)
    patch[5] = 0.3
    health = post(patch).json()["health"]
    assert health["health_status"] == "SENSOR_DEAD_CHANNEL"
