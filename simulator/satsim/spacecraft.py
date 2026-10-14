"""The simulated CubeSat: subsystems, onboard executive and the tick loop that talks to the ground.

Each tick (dt simulated seconds): update power and thermal state, run the payload, finish onboard
processing, re-review stored products, work the current ground contact, then send telemetry and
execute any telecommands the ground has queued. All analysis and decisions come from the ground
segment's API; when it is unreachable the spacecraft keeps flying on simple onboard rules.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from satsim import link, payload
from satsim.client import GroundLink
from satsim.orbit import Orbit, Pass, predict_passes
from satsim.scenarios import SimConfig

log = logging.getLogger("satsim")

LIVE_STATES = {"captured", "stored", "queued", "processing", "transmitting"}
PROCESSING_TASK = {"image_batch": "image_classification", "science_bulk": "hyperspectral_processing", "telemetry_log": "data_filtering"}
# Mission-planning value for products the DVM does not assess.
PLANNED_USABILITY = {"science_bulk": 0.62, "telemetry_log": 0.8}
THUMBNAIL_MB = 1.5


@dataclass
class Item:
    id: str
    data_class: str
    size_MB: float
    created: datetime
    state: str = "captured"
    value: float | None = None
    action: str | None = None
    reason: str | None = None
    rule: str | None = None
    protected: bool = False
    priority: int = 0
    compression_ratio: float | None = None
    planned_ratio: float | None = None
    usability: float | None = None
    urgency_probs: list[float] | None = None
    severity: str | None = None
    health_status: str | None = None
    ai_trusted: bool = False
    patch: np.ndarray | None = None
    processing_until: datetime | None = None
    processing_W: float = 0.0
    processed_MB: float | None = None
    deferred_passes: set = field(default_factory=set)
    last_review: datetime | None = None
    finished: datetime | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "data_class": self.data_class,
            "size_MB": round(self.size_MB, 2),
            "state": self.state,
            "created_at": self.created.isoformat(),
            "value": self.value,
            "action": self.action,
            "reason": self.reason,
            "protected": self.protected,
            "compression_ratio": self.compression_ratio,
        }


class Spacecraft:
    def __init__(self, cfg: SimConfig, ground: GroundLink, start: datetime, speed: float):
        self.cfg = cfg
        self.ground = ground
        self.t = start
        self.start = start
        self.speed = speed
        self.rng = np.random.default_rng(cfg.seed)
        self.orbit = Orbit.sun_synchronous(cfg.altitude_km, cfg.ltan_h, start)
        self.orbit.arg_lat0_deg = float(self.rng.uniform(0, 360))
        self.station_radio = {s.station_id: link.station_radio(self.rng, s) for s in cfg.stations}

        self.battery_Wh = cfg.battery_capacity_Wh * cfg.battery_start_pct / 100
        self.temperature_C = 18.0
        self.power_in_W = 0.0
        self.power_out_W = 0.0
        self.mode = "NOMINAL"
        self.auto_safe = False
        self.swir_enabled = True
        self.ai_restart_until: datetime | None = None
        self.dead_band: dict | None = None

        self.items: list[Item] = []
        self.serial = 0
        self.next_capture = start + timedelta(seconds=60)
        self.capture_now = False
        self.next_science = start + timedelta(seconds=cfg.science_interval_s / 2)
        self.next_tlm_log = start + timedelta(seconds=cfg.telemetry_log_interval_s)
        self.next_review = start + timedelta(seconds=cfg.restow_review_s)
        self.next_telemetry = start
        self.telemetry_period_s = max(cfg.dt_s, speed) if speed > 0 else 60.0

        self.passes: list[Pass] = []
        self._refresh_passes()
        self.contact: link.Contact | None = None
        self.transfer: link.Transfer | None = None
        self.contacts_seen = 0
        self.eclipse_change_s = self.orbit.next_eclipse_change(start)
        self.counters = {k: 0 for k in ("captured", "analysed", "alerts", "fdir_events", "downlinked", "downlinked_MB", "dropped", "failed_downlinks", "commands_executed", "offline_decisions")}

    # ---- helpers ------------------------------------------------------------------------------

    @property
    def battery_pct(self) -> float:
        return 100 * self.battery_Wh / self.cfg.battery_capacity_Wh

    @property
    def in_eclipse(self) -> bool:
        return self.orbit.in_eclipse(self.t)

    @property
    def ai_available(self) -> bool:
        return self.swir_enabled and (self.ai_restart_until is None or self.t >= self.ai_restart_until)

    @property
    def storage_used_MB(self) -> float:
        return self.cfg.housekeeping_MB + sum(i.size_MB for i in self.items if i.state in LIVE_STATES)

    def _new_id(self, prefix: str) -> str:
        self.serial += 1
        return f"{prefix}-{self.serial:05d}"

    def _fault(self, kind: str) -> dict | None:
        elapsed = (self.t - self.start).total_seconds()
        for f in self.cfg.faults:
            if f.kind == kind and f.at_s <= elapsed and (f.until_s is None or elapsed < f.until_s):
                return f.params
        return None

    def _item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def _refresh_passes(self) -> None:
        horizon = self.passes[-1].aos if self.passes else self.t
        if (horizon - self.t).total_seconds() < 6 * 3600:
            new = predict_passes(self.orbit, self.cfg.stations, max(self.t, horizon), 24)
            known = {(p.station.station_id, p.aos) for p in self.passes}
            self.passes += [p for p in new if (p.station.station_id, p.aos) not in known]
        self.passes = [p for p in self.passes if p.los > self.t]

    def _next_pass(self) -> Pass | None:
        return next((p for p in self.passes if p.aos > self.t), None)

    def _current_pass(self) -> Pass | None:
        return next((p for p in self.passes if p.aos <= self.t <= p.los), None)

    def _say(self, message: str, level: str = "info") -> None:
        getattr(log, "warning" if level == "warning" else "info")("[%s] %s", self.t.strftime("%H:%M:%S"), message)
        self.ground.log(self.cfg.sat_id, message, level)

    # ---- tick ---------------------------------------------------------------------------------

    def step(self) -> None:
        dt = self.cfg.dt_s
        self._update_power(dt)
        self._update_modes()
        self._run_payload()
        self._generate_other_products()
        self._finish_processing()
        self._review_stored()
        self._work_contact(dt)
        if self.t >= self.next_telemetry:
            self.next_telemetry = self.t + timedelta(seconds=self.telemetry_period_s)
            self._telemetry_and_commands()
        self.t += timedelta(seconds=dt)
        self.eclipse_change_s -= dt
        if self.eclipse_change_s <= 0:
            self.eclipse_change_s = self.orbit.next_eclipse_change(self.t)
        self._refresh_passes()

    # ---- power and thermal --------------------------------------------------------------------

    def _update_power(self, dt: float) -> None:
        cfg = self.cfg
        eclipse = self.in_eclipse
        self.power_in_W = 0.0 if eclipse else cfg.solar_W * (0.92 + 0.08 * math.sin((self.t - self.start).total_seconds() / 900))
        load = cfg.base_load_W + (cfg.eclipse_heater_W if eclipse else 0.0)
        if extra := self._fault("extra_load"):
            load += extra["W"]
        load += sum(i.processing_W for i in self.items if i.state == "processing")
        if self.transfer is not None:
            load += cfg.radio.tx_power_W + cfg.radio_overhead_W
        self.power_out_W = load
        net = self.power_in_W * cfg.charge_efficiency - load
        self.battery_Wh = min(cfg.battery_capacity_Wh, max(0.0, self.battery_Wh + net * dt / 3600))
        target = (24.0 if not eclipse else -4.0) + 0.6 * load
        self.temperature_C += (target - self.temperature_C) * min(1.0, dt / 1500)

    def _update_modes(self) -> None:
        if self.mode != "SAFE" and self.battery_pct < self.cfg.auto_safe_below_pct:
            self.mode, self.auto_safe = "SAFE", True
            self._say(f"Battery {self.battery_pct:.0f}%: autonomous entry into SAFE mode", "warning")
        elif self.mode == "SAFE" and self.auto_safe and self.battery_pct > self.cfg.auto_safe_exit_pct:
            self.auto_safe = False
            self.mode = self._operating_mode()
            self._say(f"Battery recovered to {self.battery_pct:.0f}%: leaving SAFE mode")
        elif self.mode == "AI_RESTART" and self.ai_available:
            self.mode = self._operating_mode()
            self._say("Edge AI warm restart complete: weights reloaded and verified")

    def _operating_mode(self) -> str:
        if not self.swir_enabled:
            return "VNIR_4BAND"
        if self.ai_restart_until is not None and self.t < self.ai_restart_until:
            return "AI_RESTART"
        return "NOMINAL"

    # ---- payload ------------------------------------------------------------------------------

    def _run_payload(self) -> None:
        due = self.t >= self.next_capture or self.capture_now
        if not due or self.mode == "SAFE" or self.in_eclipse:
            if due and not self.capture_now:
                self.next_capture = self.t + timedelta(seconds=self.cfg.capture_interval_s)
            return
        self.capture_now = False
        self.next_capture = self.t + timedelta(seconds=self.cfg.capture_interval_s)
        self.battery_Wh -= self.cfg.payload_W * 20 / 3600  # 20 s acquisition

        item = Item(self._new_id("IMG"), "image_batch", float(self.rng.lognormal(math.log(self.cfg.image_MB_median), 0.35)), self.t)
        item.patch = self._acquire()
        self.items.append(item)
        self.counters["captured"] += 1
        self._analyse(item)

    def _scene_kind(self) -> str:
        if (event := self._fault("event_scenes")) and self.rng.random() < event["probability"]:
            return event["scene"]
        lat, _ = self.orbit.subpoint(self.t)
        if abs(lat) > 62:
            return str(self.rng.choice(["arctic", "cloudy", "coast"], p=[0.6, 0.25, 0.15]))
        kinds = ["cropland", "forest", "desert", "city", "coast", "cloudy"]
        return str(self.rng.choice(kinds, p=[0.24, 0.22, 0.16, 0.12, 0.12, 0.14]))

    def _acquire(self) -> np.ndarray:
        kind = self._scene_kind()
        image = payload.make_scene(self.rng, kind, self.cfg.patch_px)
        if kind not in payload.EVENTS:
            image = payload.add_texture(image * self.cfg.radiometric_gain, self.rng)
        if self.dead_band is None and (dead := self._fault("dead_band")):
            self.dead_band = dead
            log.info("[%s] fault injected: detector band %s stuck", self.t.strftime("%H:%M:%S"), payload.BAND_NAMES[dead["band"]])
        if self.dead_band is not None:
            payload.dead_band(image, self.dead_band["band"], self.dead_band["level"])
        if (sat := self._fault("saturation")) and self.rng.random() < sat["probability"]:
            payload.saturate_band(image, int(self.rng.integers(0, 4)), sat["fraction"], self.rng)
        if (flips := self._fault("bit_flips")) and self.rng.random() < flips["probability"]:
            payload.bit_flip(image, self.rng)
        return image

    def _analyse(self, item: Item) -> None:
        """Ground-side DVM + watchdog on the quicklook, then a decision for the product."""
        if self.ai_available and item.patch is not None:
            result = self.ground.capture(self.cfg.sat_id, item.id, item.patch, self.battery_pct)
            if result is None:
                item.state = "captured"  # analysed later, once the ground answers
                return
            ai, health = result["diagnostics"]["ai"], result["diagnostics"]["health"]
            item.severity, item.health_status = health["severity"], health["health_status"]
            item.ai_trusted = health["ai_prediction_trusted"]
            if ai is not None:
                item.usability = ai["usability_pred"]
                item.urgency_probs = ai["urgency_probs"] if None not in ai["urgency_probs"] else None
            self.counters["analysed"] += 1
            if health["severity"] == "ALERT":
                item.protected = True
                self.counters["alerts"] += 1
            if health["recommended_action"] != "PROCEED_NORMAL":
                self.counters["fdir_events"] += 1
        self._decide(item)

    def _generate_other_products(self) -> None:
        if self.t >= self.next_science:
            self.next_science = self.t + timedelta(seconds=self.cfg.science_interval_s)
            item = Item(self._new_id("SCI"), "science_bulk", float(self.rng.lognormal(math.log(self.cfg.science_MB_median), 0.4)), self.t)
            self._add_planned(item)
        if self.t >= self.next_tlm_log:
            self.next_tlm_log = self.t + timedelta(seconds=self.cfg.telemetry_log_interval_s)
            self._add_planned(Item(self._new_id("TLM"), "telemetry_log", float(self.rng.uniform(1.5, 6.0)), self.t))

    def _add_planned(self, item: Item) -> None:
        item.usability, item.ai_trusted = PLANNED_USABILITY[item.data_class], True
        self.items.append(item)
        self._decide(item)

    # ---- decisions ----------------------------------------------------------------------------

    def _decision_request(self, item: Item) -> dict:
        nxt = self._current_pass() or self._next_pass()
        next_pass = None
        if nxt is not None:
            next_pass = {
                "starts_in_s": max(0.0, (nxt.aos - self.t).total_seconds()),
                "duration_s": nxt.duration_s,
                "capacity_MB": self._forecast_capacity(nxt),
            }
        queued = sum(i.size_MB for i in self.items if i.state in ("queued", "transmitting") and i is not item)
        return {
            "sat_id": self.cfg.sat_id,
            "item": {
                "id": item.id, "data_class": item.data_class, "size_MB": item.size_MB, "usability": item.usability,
                "urgency_probs": item.urgency_probs, "severity": item.severity, "health_status": item.health_status,
                "ai_trusted": item.ai_trusted, "protected": item.protected,
            },
            "state": {
                "battery_pct": round(self.battery_pct, 2), "battery_capacity_Wh": self.cfg.battery_capacity_Wh,
                "in_eclipse": self.in_eclipse, "eclipse_change_in_s": self.eclipse_change_s,
                "storage_used_MB": self.storage_used_MB - (item.size_MB if item.state in LIVE_STATES else 0),
                "storage_capacity_MB": self.cfg.storage_capacity_MB, "queued_for_downlink_MB": queued, "mode": self.mode,
            },
            "next_pass": next_pass,
        }

    def _forecast_capacity(self, p: Pass) -> float:
        """Onboard pass-capacity forecast at clear sky, sampled at the pass's mid-point."""
        mid = p.aos + (p.los - p.aos) / 2
        elevation, distance = p.station.look(self.orbit.ecef(mid))
        wx = link.Weather("clear", 0, 0.004, 0.5, 50, 15, 2.7)
        gs = self.station_radio[p.station.station_id]
        s = link.link_state(self.cfg.radio, p.station, gs, wx, distance, elevation)
        modcod = link.pick_modcod(self.cfg.radio, s.sinr_dB, gs.implementation_loss_dB)
        rate = link.goodput_Mbps(self.cfg.radio, modcod, s.sinr_dB, gs.implementation_loss_dB)[0]
        return round(0.7 * rate * p.duration_s / 8, 1)  # mid-pass is the best part of the pass

    def _decide(self, item: Item) -> None:
        decision = self.ground.decide(self._decision_request(item))
        item.last_review = self.t
        if decision is None:
            self.counters["offline_decisions"] += 1
            room = self.storage_used_MB + item.size_MB <= self.cfg.storage_capacity_MB
            decision = {"action": "STORE" if room or item.protected else "DROP", "rule": "ONBOARD_FALLBACK",
                        "reason": "Ground segment unreachable: onboard fallback keeps the product if storage allows.",
                        "value": item.value, "priority": item.priority, "energy": None, "compression": None}
        self._apply(item, decision)

    def _apply(self, item: Item, d: dict) -> None:
        item.action, item.rule, item.reason = d["action"], d["rule"], d["reason"]
        item.value, item.priority = d["value"], d["priority"]
        item.patch = None  # only unanalysed products keep their quicklook
        if d["action"] in ("TRANSMIT_RAW", "TRANSMIT_COMPRESSED"):
            item.state = "queued"
            item.planned_ratio = d["compression"]["recommended_compression_ratio"] if d["action"] == "TRANSMIT_COMPRESSED" else None
        elif d["action"] == "PROCESS_NOW":
            e = d["energy"]
            item.state = "processing"
            item.processing_until = self.t + timedelta(seconds=max(self.cfg.dt_s, e["duration_s"]))
            item.processing_W = e["energy_J"] / max(self.cfg.dt_s, e["duration_s"])
            item.processed_MB = max(0.05, e["output_volume_MB"])
        elif d["action"] == "STORE":
            item.state = "stored"
        else:
            self._drop(item)

    def _drop(self, item: Item) -> None:
        item.state, item.finished, item.patch = "dropped", self.t, None
        self.counters["dropped"] += 1

    def _finish_processing(self) -> None:
        for item in self.items:
            if item.state == "processing" and self.t >= item.processing_until:
                item.size_MB, item.state, item.processing_W = item.processed_MB, "queued", 0.0
                item.reason = f"Processed onboard into a {item.size_MB:.1f} MB product; queued for downlink."

    def _review_stored(self) -> None:
        """Re-decide a few stored or unanalysed products as power, storage and passes change."""
        if self.t < self.next_review:
            return
        self.next_review = self.t + timedelta(seconds=self.cfg.restow_review_s)
        stale = self.t - timedelta(seconds=3 * self.cfg.restow_review_s)
        waiting = [i for i in self.items if i.state in ("stored", "captured") and (i.last_review is None or i.last_review <= stale)]
        waiting.sort(key=lambda i: (not i.protected, i.last_review or i.created))
        for item in waiting[:3]:
            if item.state == "captured":
                self._analyse(item)
            else:
                self._decide(item)
        # Keep the in-memory history bounded.
        done = [i for i in self.items if i.state not in LIVE_STATES]
        if len(done) > 60:
            for old in done[:-60]:
                self.items.remove(old)

    # ---- ground contact -----------------------------------------------------------------------

    def _work_contact(self, dt: float) -> None:
        # Stay on the current station until its LOS, even if another pass overlaps.
        p = self.contact.pass_ if self.contact is not None and self.t <= self.contact.pass_.los else self._current_pass()
        if p is None:
            if self.contact is not None:
                self._end_contact()
            return
        if self.contact is None or self.contact.pass_ is not p:
            self._begin_contact(p)
        if self.transfer is None:
            self._start_next_transfer()
        if self.transfer is not None:
            self._advance_transfer(dt)

    def _begin_contact(self, p: Pass) -> None:
        forced = self.cfg.forced_weather
        weather_state = forced[self.contacts_seen % len(forced)] if forced else None
        self.contacts_seen += 1
        wx = link.draw_weather(self.rng, p.station.climate_zone, weather_state)
        fade = float(self.rng.exponential(1.2 if wx.state in ("rain", "heavy_rain") else 0.4))
        self.contact = link.Contact(self.orbit, p, self.cfg.radio, self.station_radio[p.station.station_id], wx, fade)
        self._say(f"AOS {p.station.name} ({p.station.station_id}): max elevation {p.max_elevation_deg:.0f} deg, {p.duration_s:.0f} s, weather {wx.state}")

    def _end_contact(self) -> None:
        if self.transfer is not None:
            self._finish_transfer(window_closed=True)
        p = self.contact.pass_
        self._say(f"LOS {p.station.name} ({p.station.station_id})")
        self.contact = None

    def _downlink_queue(self) -> list[Item]:
        pass_key = self.contact.pass_.aos.isoformat()
        queue = [i for i in self.items if i.state == "queued" and pass_key not in i.deferred_passes]
        return sorted(queue, key=lambda i: (-i.priority, i.created))

    def _start_next_transfer(self) -> None:
        contact = self.contact
        pass_key = contact.pass_.aos.isoformat()
        for item in self._downlink_queue():
            if (contact.pass_.los - self.t).total_seconds() < 20:
                return
            if item.planned_ratio and item.compression_ratio is None:
                self._compress(item, item.planned_ratio)
            for _ in range(2):  # a deferred file that compression can rescue gets one more look
                features = contact.features(self.t, item.size_MB, item.data_class)
                plan = self.ground.plan_downlink({
                    "sat_id": self.cfg.sat_id, "item_id": item.id, "data_class": item.data_class,
                    "station_id": contact.pass_.station.station_id, "features": features,
                })
                if plan is None or plan["decision"] == "ATTEMPT":
                    break
                self.ground.report_downlink(plan["attempt_id"], {"attempted": False, "delivered": False, "delivered_MB": 0, "duration_s": 0, "failure_reason": "deferred_by_model"})
                comp = plan["compression"]
                if item.compression_ratio is not None or comp["can_send_all"] or not comp["feasible"]:
                    break
                self._compress(item, comp["recommended_compression_ratio"])

            if plan is None:
                # Ground unreachable: attempt only if the onboard forecast leaves clear headroom.
                if features["capacity_ratio"] < 1.5:
                    item.deferred_passes.add(pass_key)
                    continue
            elif plan["decision"] == "DEFER":
                item.deferred_passes.add(pass_key)
                continue
            item.state = "transmitting"
            self.transfer = link.Transfer(item.id, plan["attempt_id"] if plan else None, item.size_MB, features["modulation_coding"], self.t,
                                          file_packets=features["file_packets"], max_retransmissions=features["max_retransmissions"])
            return

    def _compress(self, item: Item, ratio: float) -> None:
        if ratio <= 1.0:
            return
        before = item.size_MB
        item.size_MB = before / ratio
        item.compression_ratio = round(ratio, 2)
        task = {"task_type": "image_compression", "data_volume_MB": before}
        estimate = self.ground.energy(task)
        energy_J = estimate["energy_J"] if estimate else 0.35 * before * 2.2 / 0.8  # same physics, onboard copy
        self.battery_Wh -= energy_J / 3600

    def _advance_transfer(self, dt: float) -> None:
        tr = self.transfer
        tr.advance(self.contact, self.t, dt, self.rng)
        if tr.done or tr.retry_budget_exhausted:
            self._finish_transfer(window_closed=False)

    def _finish_transfer(self, window_closed: bool) -> None:
        tr, item = self.transfer, self._item(self.transfer.item_id)
        self.transfer = None
        reason = tr.failure_reason(window_closed)
        delivered = reason == "success"
        if item is not None:
            if delivered:
                item.state, item.finished = "downlinked", self.t
                self.counters["downlinked"] += 1
                self.counters["downlinked_MB"] += round(item.size_MB)
            else:
                item.state = "queued"
                item.deferred_passes.add(self.contact.pass_.aos.isoformat())
                self.counters["failed_downlinks"] += 1
        if tr.attempt_id:
            self.ground.report_downlink(tr.attempt_id, {
                "attempted": True, "delivered": delivered, "delivered_MB": round(tr.delivered_MB, 3),
                "duration_s": round(tr.active_s, 1), "failure_reason": reason,
                "actual_mean_sinr_dB": round(tr.mean_sinr, 2) if tr.mean_sinr is not None else None,
            })

    # ---- telemetry and telecommands -----------------------------------------------------------

    def telemetry(self) -> dict:
        lat, lon = self.orbit.subpoint(self.t)
        live = sorted((i for i in self.items if i.state in LIVE_STATES), key=lambda i: (-i.priority, i.created))
        recent = sorted((i for i in self.items if i.state not in LIVE_STATES), key=lambda i: i.finished or i.created, reverse=True)
        current = self._current_pass()
        return {
            "sat_id": self.cfg.sat_id,
            "sim_time": self.t.isoformat(),
            "speed": self.speed if self.speed > 0 else 1e6,
            "scenario": self.cfg.scenario,
            "mode": self.mode,
            "position": {"lat_deg": round(lat, 3), "lon_deg": round(lon, 3), "alt_km": self.cfg.altitude_km},
            "in_eclipse": self.in_eclipse,
            "eclipse_change_in_s": round(self.eclipse_change_s),
            "battery_pct": round(min(100.0, self.battery_pct), 2),
            "battery_capacity_Wh": self.cfg.battery_capacity_Wh,
            "power_in_W": round(self.power_in_W, 2),
            "power_out_W": round(self.power_out_W, 2),
            "temperature_C": round(self.temperature_C, 1),
            "storage_used_MB": round(self.storage_used_MB, 1),
            "storage_capacity_MB": self.cfg.storage_capacity_MB,
            "bands_active": 6 if self.swir_enabled else 4,
            "ai_available": self.ai_available,
            "in_contact": current.station.station_id if current else None,
            "next_passes": [p.summary() for p in self.passes[:5]],
            "queue": [i.snapshot() for i in live[:30] + recent[:10]],
            "counters": dict(self.counters),
        }

    def _telemetry_and_commands(self) -> None:
        ack = self.ground.send_telemetry(self.telemetry())
        self.ground.flush_acks()
        if ack and ack["pending_commands"] > 0:
            for command in self.ground.pending_commands(self.cfg.sat_id):
                status, result = self.execute(command)
                self.counters["commands_executed"] += status == "EXECUTED"
                log.info("[%s] telecommand %s %s -> %s: %s", self.t.strftime("%H:%M:%S"), command["id"], command["type"], status, result)
                self.ground.ack(command["id"], status, result)

    def execute(self, command: dict) -> tuple[str, str]:
        kind, params = command["type"], command.get("params") or {}
        item = self._item(params["item_id"]) if "item_id" in params else None

        if kind == "SWITCH_TO_4BAND_VNIR":
            if not self.swir_enabled:
                return "EXECUTED", "Already imaging in 4-band VNIR."
            self.swir_enabled = False
            self.mode = "SAFE" if self.mode == "SAFE" else "VNIR_4BAND"
            return "EXECUTED", "SWIR detectors powered off; imaging continues in 4-band VNIR. Edge AI paused (it needs 6 bands)."
        if kind == "RESTORE_6BAND":
            if self.swir_enabled:
                return "REJECTED", "SWIR detectors are already on."
            self.swir_enabled = True
            cleared = self.dead_band is not None
            self.dead_band = None
            self.mode = "SAFE" if self.mode == "SAFE" else self._operating_mode()
            return "EXECUTED", "SWIR detectors power-cycled and re-enabled" + (" (latch-up cleared)." if cleared else ".")
        if kind == "RELOAD_WEIGHTS_WARM_RESTART":
            self.ai_restart_until = self.t + timedelta(seconds=120)
            if self.mode == "NOMINAL":
                self.mode = "AI_RESTART"
            return "EXECUTED", "Edge AI weights reloaded from protected memory; warm restart takes 120 s."
        if kind == "SAVE_RAW_THUMBNAIL_FOR_GROUND":
            source = item.id if item else params.get("item_id", "unknown")
            thumb = Item(self._new_id("THB"), "image_batch", THUMBNAIL_MB, self.t, state="queued", protected=True, priority=90,
                         action="TRANSMIT_RAW", rule="FDIR", reason=f"Raw thumbnail of {source} for ground analysis (FDIR).")
            self.items.append(thumb)
            return "EXECUTED", f"Thumbnail {thumb.id} ({THUMBNAIL_MB} MB) of {source} queued for the next pass."
        if kind == "TRIGGER_EMERGENCY_ANOMALY_ALERT":
            if item is None:
                return "REJECTED", f"Product {params.get('item_id')} is no longer on board."
            item.protected, item.priority = True, 100
            if item.state == "stored":
                item.state, item.action = "queued", "TRANSMIT_RAW"
            return "EXECUTED", f"{item.id} protected and moved to the front of the downlink queue."
        if kind == "ENTER_SAFE_MODE":
            self.mode, self.auto_safe = "SAFE", False
            return "EXECUTED", "SAFE mode: payload off, no discretionary processing or downlink."
        if kind == "EXIT_SAFE_MODE":
            if self.mode != "SAFE":
                return "REJECTED", "Spacecraft is not in SAFE mode."
            if self.battery_pct < self.cfg.auto_safe_below_pct:
                return "REJECTED", f"Battery {self.battery_pct:.0f}% is below the {self.cfg.auto_safe_below_pct:.0f}% safe-mode floor."
            self.auto_safe = False
            self.mode = self._operating_mode()
            return "EXECUTED", f"Left SAFE mode; now {self.mode}."
        if kind == "CAPTURE_NOW":
            if self.mode == "SAFE":
                return "REJECTED", "Payload is off in SAFE mode."
            if self.in_eclipse:
                return "REJECTED", "Spacecraft is in eclipse; the optical payload needs sunlight."
            self.capture_now = True
            return "EXECUTED", "Capture scheduled for the next tick."
        if kind == "PRIORITIZE_ITEM":
            if item is None or item.state not in LIVE_STATES:
                return "REJECTED", f"Product {params.get('item_id')} is not on board."
            item.protected, item.priority = True, 100
            if item.state == "stored":
                item.state = "queued"
            return "EXECUTED", f"{item.id} protected and queued first for downlink."
        if kind == "DELETE_ITEM":
            if item is None or item.state not in LIVE_STATES:
                return "REJECTED", f"Product {params.get('item_id')} is not on board."
            if item.state == "transmitting":
                return "REJECTED", f"{item.id} is being transmitted."
            self._drop(item)
            item.reason = "Deleted by operator command."
            return "EXECUTED", f"{item.id} deleted, {item.size_MB:.0f} MB freed."
        return "REJECTED", f"Unknown command {kind}."
