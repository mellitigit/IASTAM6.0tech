import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.ml import link_delivery

client = TestClient(app)
pytestmark = pytest.mark.skipif(not settings.link_model_path.is_file(), reason="link delivery model not present")


def first_sample() -> dict:
    return client.get("/api/downlink/samples").json()[0]


def test_model_info():
    r = client.get("/api/downlink/model")
    assert r.status_code == 200
    info = r.json()
    assert len(info["features"]) == 59
    assert 0 < info["threshold"] < 1
    assert "SYNTHETIC" in info["warning"]


def test_samples_are_complete():
    samples = client.get("/api/downlink/samples").json()
    features = client.get("/api/downlink/model").json()["features"]
    assert len(samples) == 10
    assert all(set(s["features"]) == set(features) for s in samples)
    assert {s["recorded_success"] for s in samples} == {0, 1}


def test_predict_recorded_attempt():
    r = client.post("/api/downlink/predict", json={"features": first_sample()["features"]})
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["probability"] <= 1.0
    assert body["decision"] == ("ATTEMPT" if body["probability"] >= body["threshold"] else "DEFER")


def test_model_separates_clear_cases():
    samples = client.get("/api/downlink/samples").json()
    worst = min(samples, key=lambda s: s["simulated_success_probability"])
    best = max(samples, key=lambda s: s["simulated_success_probability"])
    p = lambda s: client.post("/api/downlink/predict", json={"features": s["features"]}).json()["probability"]
    assert p(worst) < 0.1 < 0.9 < p(best)


def test_missing_feature_is_rejected():
    features = first_sample()["features"]
    features.pop("sinr_dB")
    r = client.post("/api/downlink/predict", json={"features": features})
    assert r.status_code == 422
    assert "sinr_dB" in r.json()["detail"]


def test_non_numeric_value_is_rejected():
    features = first_sample()["features"] | {"file_size_MB": "big"}
    assert client.post("/api/downlink/predict", json={"features": features}).status_code == 422


def test_missing_numeric_value_is_imputed():
    features = first_sample()["features"] | {"temperature_C": None}
    assert client.post("/api/downlink/predict", json={"features": features}).status_code == 200


def test_missing_model_returns_503(monkeypatch, tmp_path):
    monkeypatch.setattr(link_delivery, "_bundle", None)
    monkeypatch.setattr(settings, "link_model_path", tmp_path / "missing.joblib")
    assert client.get("/api/downlink/model").status_code == 503
