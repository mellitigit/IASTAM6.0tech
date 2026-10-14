"""Ground-segment logic: turn watchdog verdicts into telecommands and build the dashboard snapshot."""

from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.db.mission_store import MissionStore

# FDIR actions that change spacecraft-wide state are raised once until acknowledged;
# per-product actions are raised for every product that triggers them.
_SPACECRAFT_WIDE = {"SWITCH_TO_4BAND_VNIR", "RELOAD_WEIGHTS_WARM_RESTART"}


def fdir_commands(store: MissionStore, sat_id: str, item_id: str, health: dict[str, Any]) -> list[dict[str, Any]]:
    action = health["recommended_action"]
    if action == "PROCEED_NORMAL":
        return []
    if action in _SPACECRAFT_WIDE:
        open_ = store.commands(sat_id, "PENDING") + store.commands(sat_id, "SENT")
        if any(c["type"] == action for c in open_):
            return []
    command = store.add_command(
        sat_id, action, {"item_id": item_id}, "fdir", f"{health['health_status']}: {health['diagnostic_message']}"
    )
    store.add_event(sat_id, "command", {"command": command, "change": "created"})
    return [command]


def _age_s(ts: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()


def snapshot(store: MissionStore, sat_id: str) -> dict[str, Any]:
    last = store.latest_event(sat_id, "telemetry")
    age = _age_s(last["ts"]) if last else None
    history = store.recent_events(sat_id, ("telemetry",), 240)
    return {
        "sat_id": sat_id,
        "online": age is not None and age <= settings.telemetry_stale_s,
        "telemetry_age_s": round(age, 1) if age is not None else None,
        "battery_reserve_pct": settings.battery_reserve_pct,
        "telemetry": last["payload"] if last else None,
        "battery_history": [
            {"sim_time": e["payload"]["sim_time"], "battery_pct": e["payload"]["battery_pct"], "in_eclipse": e["payload"]["in_eclipse"]}
            for e in history
        ],
        "events": store.recent_events(sat_id, ("diagnostic", "decision", "downlink", "command", "alert", "log"), 80),
        "commands": store.commands(sat_id, limit=30),
        "downlinks": store.downlinks(sat_id, limit=30),
        "last_event_id": store.last_event_id(),
    }
