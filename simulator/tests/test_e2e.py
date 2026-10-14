"""End-to-end: the simulator flies against the real FastAPI app (DVM, watchdog, link model) in-process."""

import pytest

from satsim.client import GroundLink
from satsim.orbit import utc
from satsim.scenarios import build
from satsim.spacecraft import Spacecraft

fastapi_testclient = pytest.importorskip("fastapi.testclient")
from app.core.config import settings  # noqa: E402
from app.db.mission_store import MissionStore, reset_store  # noqa: E402
from app.main import app  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (settings.dvm_model_path.is_file() and settings.link_model_path.is_file()), reason="model files not present"
)


@pytest.fixture
def store():
    s = MissionStore(":memory:")
    reset_store(s)
    yield s
    reset_store(None)


def fly(scenario: str, hours: float, before_step=None):
    client = fastapi_testclient.TestClient(app)
    sc = Spacecraft(build(scenario), GroundLink("http://testserver", client=client), utc("2026-10-14T12:00:00+00:00"), speed=0)
    for k in range(int(hours * 3600 / sc.cfg.dt_s)):
        if before_step:
            before_step(k, sc, client)
        sc.step()
    return sc, client


def test_full_loop(store):
    def operator(k, sc, client):
        if k == 200:
            r = client.post("/api/mission/commands", json={"sat_id": sc.cfg.sat_id, "type": "CAPTURE_NOW"})
            assert r.status_code == 201

    sc, client = fly("nominal_day", 6, operator)
    state = client.get("/api/mission/state", params={"sat_id": sc.cfg.sat_id}).json()
    kinds = {e["kind"] for e in store.events_after(0, sc.cfg.sat_id, limit=100000)}

    assert state["telemetry"]["sim_time"] > "2026-10-14T17:00"
    assert {"telemetry", "diagnostic", "decision", "downlink"} <= kinds
    assert sc.counters["captured"] > 20 and sc.counters["analysed"] == sc.counters["captured"]
    assert sc.counters["offline_decisions"] == 0

    completed = [d for d in store.downlinks(sc.cfg.sat_id, limit=500) if d["outcome"] and d["outcome"]["attempted"]]
    assert completed, "no downlink attempt was completed"
    assert any(d["outcome"]["delivered"] for d in completed)

    operator_cmd = [c for c in store.commands(sc.cfg.sat_id, limit=500) if c["source"] == "operator"]
    assert operator_cmd and operator_cmd[0]["status"] in ("EXECUTED", "REJECTED")  # acknowledged either way


def test_swir_failure_is_isolated_and_recovered(store):
    sc, client = fly("swir_detector_failure", 1.5)
    commands = store.commands(sc.cfg.sat_id, limit=500)
    switch = [c for c in commands if c["type"] == "SWITCH_TO_4BAND_VNIR"]
    assert len(switch) == 1 and switch[0]["source"] == "fdir" and switch[0]["status"] == "EXECUTED"
    assert sc.mode == "VNIR_4BAND" and sc.telemetry()["bands_active"] == 4

    client.post("/api/mission/commands", json={"sat_id": sc.cfg.sat_id, "type": "RESTORE_6BAND"})
    for _ in range(30):
        sc.step()
    restore = [c for c in store.commands(sc.cfg.sat_id, limit=500) if c["type"] == "RESTORE_6BAND"]
    assert restore[0]["status"] == "EXECUTED" and sc.mode == "NOMINAL"
