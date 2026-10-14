import json
from datetime import timedelta
from pathlib import Path

import httpx
import numpy as np
import pytest

from satsim import link, payload
from satsim.client import GroundLink
from satsim.orbit import Orbit, predict_passes, utc
from satsim.scenarios import SCENARIOS, build
from satsim.spacecraft import Spacecraft

START = utc("2026-10-14T12:00:00+00:00")
META = json.loads((Path(__file__).resolve().parents[2] / "backend/app/ml/models/link_delivery_meta.json").read_text())
CATEGORIES = {
    "station_climate_zone": set(link.CLIMATE_WEATHER),
    "weather_state": set(link.WEATHER_STATES),
    "mission_data_class": {"image_batch", "science_bulk", "telemetry_log"},
    "modcod_policy": {"adaptive", "fixed_preplanned"},
    "modulation_coding": set(link.MODCODS),
}


class DeadGround(httpx.BaseTransport):
    def handle_request(self, request):
        raise httpx.ConnectError("ground down", request=request)


def spacecraft(scenario="nominal_day", transport=None) -> Spacecraft:
    ground = GroundLink("http://ground", client=httpx.Client(transport=transport or DeadGround()))
    return Spacecraft(build(scenario), ground, START, speed=0)


def test_orbit_period_and_eclipse():
    o = Orbit.sun_synchronous(520, 10.5, START)
    assert o.period_s / 60 == pytest.approx(94.9, abs=0.2)
    eclipse = [o.in_eclipse(START + timedelta(seconds=30 * k)) for k in range(int(o.period_s / 30))]
    assert 0.3 < np.mean(eclipse) < 0.42


def test_passes_respect_elevation_mask():
    cfg = build("nominal_day")
    o = Orbit.sun_synchronous(520, 10.5, START)
    passes = predict_passes(o, cfg.stations, START, 24)
    assert len(passes) > 20
    p = passes[0]
    mid = p.aos + (p.los - p.aos) / 2
    assert p.station.look(o.ecef(mid))[0] >= p.station.elevation_mask_deg
    assert all(60 < q.duration_s < 800 for q in passes)


def test_link_features_match_the_model_contract():
    cfg = build("nominal_day")
    o = Orbit.sun_synchronous(520, 10.5, START)
    p = predict_passes(o, cfg.stations, START, 24)[0]
    rng = np.random.default_rng(0)
    contact = link.Contact(o, p, cfg.radio, link.station_radio(rng, p.station), link.draw_weather(rng, p.station.climate_zone))
    f = contact.features(p.aos + timedelta(seconds=30), 200.0, "image_batch")
    assert set(f) == set(META["features"])
    for name, allowed in CATEGORIES.items():
        assert f[name] in allowed
    assert f["free_space_path_loss_dB"] == pytest.approx(20 * np.log10(f["distance_km"]) + 20 * np.log10(f["frequency_GHz"] * 1000) + 32.45)
    assert f["capacity_ratio"] == pytest.approx(f["estimated_window_capacity_MB"] / f["file_size_MB"])


def test_rain_costs_link_margin():
    cfg = build("nominal_day")
    st = cfg.stations[0]
    gs = link.station_radio(np.random.default_rng(1), st)
    clear = link.Weather("clear", 0, 0.004, 0.5, 50, 10, 2.7)
    storm = link.Weather("heavy_rain", 40, 0.8, 3.0, 95, 10, 7.5)
    assert link.link_state(cfg.radio, st, gs, storm, 1500, 15).sinr_dB < link.link_state(cfg.radio, st, gs, clear, 1500, 15).sinr_dB - 3


def test_payload_faults():
    rng = np.random.default_rng(0)
    img = payload.add_texture(payload.make_scene(rng, "cropland") * 0.3, rng)
    assert img.shape == (6, 128, 128) and img.dtype == np.float32 and 0 <= img.min() and img.max() <= 1
    assert all(img[b].var() > 1e-4 for b in range(6))  # above the watchdog's dead-detector floor
    assert payload.dead_band(img.copy(), 5)[5].var() == 0
    assert np.isnan(payload.bit_flip(img.copy(), rng)).sum() == 1
    assert payload.thumbnail(img).shape == (6, 32, 32)


def test_ground_outage_buffers_telemetry_and_replays_in_order():
    sent, up = [], {"ok": False}

    def handler(request):
        if not up["ok"]:
            raise httpx.ConnectError("down", request=request)
        sent.append(json.loads(request.content)["n"])
        return httpx.Response(200, json={"ok": True, "event_id": len(sent), "pending_commands": 0})

    g = GroundLink("http://ground", client=httpx.Client(transport=httpx.MockTransport(handler)))
    g.send_telemetry({"n": 1})
    assert not g.online and len(g.telemetry_buffer) == 1
    g.retry_at = 0  # skip the backoff wait
    g.send_telemetry({"n": 2})
    up["ok"], g.retry_at = True, 0
    assert g.send_telemetry({"n": 3})["event_id"] == 3
    assert sent == [1, 2, 3] and not g.telemetry_buffer and g.online


def test_spacecraft_flies_on_onboard_rules_without_ground():
    sc = spacecraft()
    for _ in range(int(3 * 3600 / sc.cfg.dt_s)):
        sc.step()
    assert sc.counters["captured"] > 0
    assert sc.counters["offline_decisions"] > 0
    assert 0 <= sc.battery_pct <= 100
    assert not any(i.state == "dropped" for i in sc.items)  # storage was never short


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_scenario_runs(name):
    sc = spacecraft(name)
    for _ in range(int(2 * 3600 / sc.cfg.dt_s)):
        sc.step()
    frame = sc.telemetry()
    assert frame["scenario"] == name and 0 <= frame["battery_pct"] <= 100


def test_low_battery_scenario_enters_safe_mode():
    sc = spacecraft("eclipse_low_battery")
    modes = set()
    for _ in range(int(6 * 3600 / sc.cfg.dt_s)):
        sc.step()
        modes.add(sc.mode)
    assert "SAFE" in modes


def command(type_, **params):
    return {"id": "CMD-T", "type": type_, "params": params}


def test_executive_commands():
    sc = spacecraft()
    assert sc.execute(command("SWITCH_TO_4BAND_VNIR"))[0] == "EXECUTED"
    assert sc.mode == "VNIR_4BAND" and not sc.ai_available
    assert sc.execute(command("RESTORE_6BAND"))[0] == "EXECUTED"
    assert sc.mode == "NOMINAL" and sc.ai_available

    assert sc.execute(command("ENTER_SAFE_MODE"))[0] == "EXECUTED"
    assert sc.execute(command("CAPTURE_NOW"))[0] == "REJECTED"
    assert sc.execute(command("EXIT_SAFE_MODE"))[0] == "EXECUTED"

    status, result = sc.execute(command("SAVE_RAW_THUMBNAIL_FOR_GROUND", item_id="IMG-00001"))
    thumb = sc.items[-1]
    assert status == "EXECUTED" and thumb.protected and thumb.state == "queued" and thumb.id in result

    assert sc.execute(command("DELETE_ITEM", item_id="NOPE"))[0] == "REJECTED"
    assert sc.execute(command("DELETE_ITEM", item_id=thumb.id))[0] == "EXECUTED"
    assert thumb.state == "dropped"
    assert sc.execute(command("SELF_DESTRUCT"))[0] == "REJECTED"
