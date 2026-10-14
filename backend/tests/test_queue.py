import pytest
from fastapi.testclient import TestClient

from app.db.mission_store import MissionStore, reset_store
from app.main import app

client = TestClient(app)
URL = "/api/queue/plan"
ROUTINE = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture(autouse=True)
def memory_store():
    store = MissionStore(":memory:")
    reset_store(store)
    yield store
    reset_store(None)


def item(id_, size, usability=0.8, data_class="image_batch", severity="NOMINAL", trusted=True, protected=False, urgency=ROUTINE):
    return {"id": id_, "data_class": data_class, "size_MB": size, "usability": usability, "urgency_probs": urgency,
            "severity": severity, "ai_trusted": trusted, "protected": protected}


def plan(items, capacity=1000.0, battery=80.0, used=2000.0, next_pass=True):
    body = {
        "sat_id": "SAT-T",
        "items": items,
        "state": {"battery_pct": battery, "battery_capacity_Wh": 40, "in_eclipse": False,
                  "storage_used_MB": used, "storage_capacity_MB": 8000},
        "next_pass": {"starts_in_s": 300, "duration_s": 480, "capacity_MB": capacity} if next_pass else None,
    }
    r = client.post(URL, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def ids(slots):
    return [s["item_id"] for s in slots]


def test_most_important_goes_first():
    p = plan([item("low", 50, usability=0.5), item("high", 50, usability=0.95), item("alert", 50, severity="ALERT")])
    assert ids(p["this_pass"]) == ["alert", "high", "low"]
    assert [s["rank"] for s in p["this_pass"]] == [1, 2, 3]
    assert p["this_pass"][0]["priority"] == 100


def test_protected_beats_higher_value():
    p = plan([item("valuable", 50, usability=0.99), item("protected", 50, usability=0.3, protected=True)])
    assert ids(p["this_pass"])[0] == "protected"


def test_overflow_waits_in_order():
    items = [item(f"S{i}", 400, usability=0.9 - i * 0.1, data_class="science_bulk") for i in range(4)]
    p = plan(items, capacity=900)
    assert ids(p["this_pass"]) == ["S0", "S1"]
    assert ids(p["waiting"]) == ["S2", "S3"]  # still ranked: S2 before S3
    assert p["booked_MB"] <= 900


def test_pass_is_packed_back_to_back():
    p = plan([item("A", 100, usability=0.9), item("B", 100, usability=0.8)], capacity=1000)
    a, b = p["this_pass"]
    assert a["pass_start_MB"] == 0
    assert b["pass_start_MB"] == pytest.approx(a["send_MB"])


def test_later_items_get_compressed_to_fit():
    p = plan([item("first", 300, usability=0.95), item("second", 300, usability=0.9)], capacity=400)
    first, second = p["this_pass"]
    assert first["action"] == "TRANSMIT_RAW"
    assert second["action"] == "TRANSMIT_COMPRESSED" and second["codec"] == "JPEG"
    assert p["booked_MB"] <= 400


def test_no_pass_everything_waits_ranked():
    p = plan([item("low", 50, usability=0.5), item("alert", 50, severity="ALERT"), item("high", 50, usability=0.95)], next_pass=False)
    assert p["this_pass"] == []
    assert ids(p["waiting"]) == ["alert", "high", "low"]


def test_low_value_and_untrusted():
    p = plan([item("junk", 50, usability=0.1), item("suspect", 50, severity="WARNING", trusted=False)])
    assert ids(p["dropped"]) == ["junk"]
    assert ids(p["waiting"]) == ["suspect"]  # kept for ground review, not sent


def test_battery_below_reserve_holds_everything_but_alerts():
    p = plan([item("normal", 50, usability=0.9), item("alert", 50, severity="ALERT")], battery=20)
    assert ids(p["this_pass"]) == ["alert"]
    assert ids(p["waiting"]) == ["normal"]


def test_duplicate_ids_rejected():
    r = client.post(URL, json={"sat_id": "S", "items": [item("x", 1), item("x", 2)],
                               "state": {"battery_pct": 80, "battery_capacity_Wh": 40, "in_eclipse": False,
                                         "storage_used_MB": 10, "storage_capacity_MB": 100}})
    assert r.status_code == 422


def test_plan_is_logged(memory_store):
    plan([item("A", 10)])
    event = memory_store.latest_event("SAT-T", "queue_plan")
    assert event and event["payload"]["this_pass"] == ["A"]
