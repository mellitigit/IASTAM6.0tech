import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.mission_store import MissionStore, reset_store
from app.main import app
from app.ml import dvm_inference
from app.schemas.mission import DecisionRequest
from app.services import compression, decision_policy

client = TestClient(app)
SAT = "TEST-SAT"
CONFIDENT_AI = {"urgency_probs": [0.7, 0.2, 0.07, 0.03], "usability_pred": 0.8, "entropy": 0.3, "recon_error": 0.05}


@pytest.fixture(autouse=True)
def store():
    s = MissionStore(":memory:")
    reset_store(s)
    yield s
    reset_store(None)


@pytest.fixture
def confident_model(monkeypatch):
    monkeypatch.setattr(dvm_inference, "predict", lambda image: dict(CONFIDENT_AI))


def telemetry(**overrides) -> dict:
    frame = {
        "sat_id": SAT, "sim_time": "2026-10-14T12:00:00+00:00", "speed": 60, "scenario": "test", "mode": "NOMINAL",
        "position": {"lat_deg": 10, "lon_deg": 20, "alt_km": 520}, "in_eclipse": False, "battery_pct": 80,
        "battery_capacity_Wh": 80, "power_in_W": 20, "power_out_W": 12, "temperature_C": 18,
        "storage_used_MB": 1000, "storage_capacity_MB": 8000, "bands_active": 6, "ai_available": True,
    }
    return frame | overrides


def smooth_patch(size: int = 64) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size] / size
    field = np.sin(2 * np.pi * (1.3 * xx + 0.7 * yy)) + np.sin(2 * np.pi * (0.4 * xx - 1.1 * yy) + 1.0)
    field = (field - field.min()) / (field.max() - field.min())
    veg = np.array([0.05, 0.08, 0.04, 0.35, 0.20, 0.10])[:, None, None]
    soil = np.array([0.18, 0.15, 0.12, 0.28, 0.32, 0.25])[:, None, None]
    return (veg * field + soil * (1 - field)).astype(np.float32)


def capture(patch: np.ndarray, item_id: str = "IMG-1"):
    buf = io.BytesIO()
    np.save(buf, patch)
    buf.seek(0)
    return client.post("/api/mission/captures", files={"patch": ("p.npy", buf)}, data={"sat_id": SAT, "item_id": item_id, "battery_pct": 80})


def decision(item: dict | None = None, state: dict | None = None, next_pass: dict | None = None) -> dict:
    body = {
        "sat_id": SAT,
        "item": {"id": "IMG-1", "data_class": "image_batch", "size_MB": 300, "usability": 0.8,
                 "urgency_probs": [0.7, 0.2, 0.07, 0.03], "severity": "NOMINAL", "ai_trusted": True} | (item or {}),
        "state": {"battery_pct": 80, "battery_capacity_Wh": 80, "in_eclipse": False, "storage_used_MB": 1000,
                  "storage_capacity_MB": 8000} | (state or {}),
        "next_pass": {"starts_in_s": 600, "duration_s": 480, "capacity_MB": 2000} if next_pass is None else next_pass,
    }
    return decision_policy.decide(DecisionRequest.model_validate(body))


# ---- telemetry, state and stream --------------------------------------------------------------


def test_telemetry_is_stored_and_shown_in_state():
    r = client.post("/api/mission/telemetry", json=telemetry())
    assert r.status_code == 200
    state = client.get("/api/mission/state", params={"sat_id": SAT}).json()
    assert state["online"] is True
    assert state["telemetry"]["battery_pct"] == 80
    assert state["battery_reserve_pct"] == settings.battery_reserve_pct
    assert len(state["battery_history"]) == 1


def test_invalid_telemetry_is_rejected():
    assert client.post("/api/mission/telemetry", json=telemetry(battery_pct=140)).status_code == 422


def test_state_for_unknown_satellite_is_offline():
    state = client.get("/api/mission/state", params={"sat_id": "NOBODY"}).json()
    assert state["online"] is False and state["telemetry"] is None


def test_stream_replays_events_in_order():
    client.post("/api/mission/telemetry", json=telemetry(battery_pct=70))
    client.post("/api/mission/telemetry", json=telemetry(battery_pct=69))
    with client.stream("GET", "/api/mission/stream", params={"after": 0, "follow": False, "sat_id": SAT}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    assert [e["payload"]["battery_pct"] for e in events] == [70, 69]
    assert events[0]["id"] < events[1]["id"]


# ---- captures and FDIR commands ---------------------------------------------------------------


def test_nominal_capture_raises_no_command(confident_model):
    body = capture(smooth_patch()).json()
    assert body["diagnostics"]["health"]["severity"] == "NOMINAL"
    assert body["commands"] == []


def test_dead_band_raises_one_switch_command(confident_model):
    patch = smooth_patch()
    patch[5] = 0.3
    first = capture(patch, "IMG-1").json()
    second = capture(patch, "IMG-2").json()
    assert [c["type"] for c in first["commands"]] == ["SWITCH_TO_4BAND_VNIR"]
    assert first["commands"][0]["source"] == "fdir"
    assert second["commands"] == []  # not re-raised while the first is still open


def test_bit_flip_raises_thumbnail_command_per_item(confident_model):
    patch = smooth_patch()
    patch[2, 0, 0] = np.nan
    assert capture(patch, "IMG-1").json()["commands"][0]["type"] == "SAVE_RAW_THUMBNAIL_FOR_GROUND"
    assert capture(patch, "IMG-2").json()["commands"][0]["params"] == {"item_id": "IMG-2"}


# ---- command uplink and acknowledgement -------------------------------------------------------


def test_command_lifecycle():
    created = client.post("/api/mission/commands", json={"sat_id": SAT, "type": "ENTER_SAFE_MODE"})
    assert created.status_code == 201
    command_id = created.json()["id"]

    pending = client.get("/api/mission/commands/pending", params={"sat_id": SAT}).json()
    assert [c["id"] for c in pending] == [command_id] and pending[0]["status"] == "SENT"
    assert client.get("/api/mission/commands/pending", params={"sat_id": SAT}).json() == []

    acked = client.post(f"/api/mission/commands/{command_id}/ack", json={"status": "EXECUTED", "result": "safe mode on"})
    assert acked.json()["status"] == "EXECUTED"
    assert client.get("/api/mission/commands", params={"sat_id": SAT}).json()[0]["result"] == "safe mode on"


def test_unknown_command_type_is_rejected():
    assert client.post("/api/mission/commands", json={"sat_id": SAT, "type": "SELF_DESTRUCT"}).status_code == 422


def test_ack_unknown_command_is_404():
    assert client.post("/api/mission/commands/CMD-NOPE/ack", json={"status": "EXECUTED"}).status_code == 404


# ---- energy and compression fallbacks ---------------------------------------------------------


def test_energy_grows_with_volume_and_cycles():
    small = client.post("/api/mission/energy/predict", json={"task_type": "object_detection", "data_volume_MB": 10}).json()
    large = client.post("/api/mission/energy/predict", json={"task_type": "object_detection", "data_volume_MB": 100}).json()
    assert 0 < small["energy_J"] < large["energy_J"]
    assert small["energy_J"] == pytest.approx(sum(small["breakdown_J"].values()), abs=0.01)
    assert "fallback" in small["model"]


def test_compression_plan():
    fits = compression.plan("image_batch", 100, 500)
    assert fits["can_send_all"] and fits["codec"] == "none"
    squeeze = compression.plan("image_batch", 1000, 400)
    assert not squeeze["can_send_all"] and squeeze["feasible"] and squeeze["codec"] == "JPEG"
    assert squeeze["compressed_size_MB"] * 1.1 == pytest.approx(400, rel=1e-4)
    hopeless = compression.plan("telemetry_log", 100, 10)
    assert not hopeless["feasible"] and hopeless["codec"] == "LZ4"


# ---- decision policy --------------------------------------------------------------------------


def test_good_product_that_fits_goes_raw():
    d = decision()
    assert d["action"] == "TRANSMIT_RAW" and d["rule"] == "R7_DOWNLINK"


def test_large_product_is_compressed():
    d = decision(next_pass={"starts_in_s": 600, "duration_s": 480, "capacity_MB": 150})
    assert d["action"] == "TRANSMIT_COMPRESSED"
    assert d["compression"]["codec"] == "JPEG"


def test_too_large_even_compressed_is_processed_onboard():
    d = decision(item={"size_MB": 3000}, next_pass={"starts_in_s": 600, "duration_s": 480, "capacity_MB": 100})
    assert d["action"] == "PROCESS_NOW" and d["energy"]["energy_J"] > 0


def test_processing_is_deferred_when_it_would_breach_the_reserve():
    d = decision(
        item={"size_MB": 3000},
        state={"battery_pct": settings.battery_reserve_pct + 6, "in_eclipse": True},
        next_pass={"starts_in_s": 600, "duration_s": 480, "capacity_MB": 100},
    )
    assert d["action"] == "STORE" and d["rule"] == "R8_ENERGY_BUDGET"


def test_battery_below_reserve_stores():
    d = decision(state={"battery_pct": settings.battery_reserve_pct - 1})
    assert d["action"] == "STORE" and d["rule"] == "R3_BATTERY_RESERVE"


def test_alert_is_protected_even_below_reserve():
    d = decision(item={"severity": "ALERT"}, state={"battery_pct": settings.battery_reserve_pct - 5})
    assert d["action"] == "TRANSMIT_RAW" and d["rule"] == "R2_PROTECT_ALERT" and d["priority"] == 100


def test_urgent_detection_is_protected():
    assert decision(item={"urgency_probs": [0.0, 0.05, 0.15, 0.8]})["rule"] == "R2_PROTECT_ALERT"


UNTRUSTED = {"severity": "CRITICAL", "ai_trusted": False, "usability": None, "urgency_probs": None}


def test_untrusted_product_is_kept_for_ground_review():
    d = decision(item=UNTRUSTED)
    assert d["action"] == "STORE" and d["rule"] == "R4_UNTRUSTED"


def test_untrusted_product_waits_when_the_pass_is_busy():
    d = decision(item=UNTRUSTED, state={"storage_used_MB": 5000, "queued_for_downlink_MB": 1500})
    assert d["action"] == "STORE" and d["rule"] == "R4_UNTRUSTED"


def test_untrusted_product_drains_at_low_priority_when_storage_fills():
    d = decision(item=UNTRUSTED, state={"storage_used_MB": 5000})
    assert d["action"] == "TRANSMIT_RAW" and d["rule"] == "R4_UNTRUSTED"
    assert d["priority"] < decision()["priority"]


def test_spread_urgency_is_not_protected():
    assert decision(item={"urgency_probs": [0.1, 0.1, 0.35, 0.45]})["rule"] == "R7_DOWNLINK"


def test_low_value_is_dropped():
    assert decision(item={"usability": 0.1})["action"] == "DROP"


def test_storage_pressure_drops_mediocre_products():
    d = decision(item={"usability": 0.4}, state={"storage_used_MB": 7600})
    assert d["action"] == "DROP" and d["rule"] == "R6_STORAGE_PRESSURE"


def test_safe_mode_stores_everything():
    assert decision(item={"severity": "ALERT"}, state={"mode": "SAFE"})["action"] == "STORE"


def test_no_pass_predicted_holds_product():
    d = client.post("/api/mission/decisions", json={
        "sat_id": SAT,
        "item": {"id": "X", "data_class": "telemetry_log", "size_MB": 2, "usability": 0.9, "ai_trusted": True},
        "state": {"battery_pct": 80, "battery_capacity_Wh": 80, "in_eclipse": False, "storage_used_MB": 0, "storage_capacity_MB": 8000},
    }).json()
    assert d["action"] == "STORE" and "No ground pass" in d["reason"]


# ---- downlink plan and outcome ----------------------------------------------------------------


@pytest.mark.skipif(not settings.link_model_path.is_file(), reason="link delivery model not present")
def test_downlink_plan_and_outcome():
    sample = client.get("/api/downlink/samples").json()[0]
    plan = client.post("/api/mission/downlinks", json={
        "sat_id": SAT, "item_id": "IMG-1", "data_class": "image_batch", "station_id": sample["station_id"], "features": sample["features"],
    })
    assert plan.status_code == 200
    body = plan.json()
    assert 0 <= body["probability"] <= 1 and body["compression"]["codec"] in {"none", "JPEG"}

    outcome = client.post(f"/api/mission/downlinks/{body['attempt_id']}/outcome", json={"delivered": True, "delivered_MB": 10, "duration_s": 30})
    assert outcome.status_code == 200
    state = client.get("/api/mission/state", params={"sat_id": SAT}).json()
    assert state["downlinks"][0]["outcome"]["delivered"] is True
    completed = [e for e in state["events"] if e["kind"] == "downlink" and e["payload"]["phase"] == "completed"]
    assert completed[0]["payload"]["prediction_correct"] == (body["decision"] == "ATTEMPT")


def test_outcome_for_unknown_attempt_is_404():
    r = client.post("/api/mission/downlinks/DL-NOPE/outcome", json={"delivered": False, "delivered_MB": 0, "duration_s": 0})
    assert r.status_code == 404
