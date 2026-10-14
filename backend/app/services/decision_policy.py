"""Integrated onboard decision policy (README Phase 3, rule-based first).

For one data product it combines the DVM's value/urgency, the FDIR watchdog verdict, the predicted
processing energy, battery and eclipse timing, storage headroom and the next pass's forecast capacity
into one action: PROCESS_NOW, STORE, TRANSMIT_RAW, TRANSMIT_COMPRESSED or DROP.

Rules are evaluated in order and the first match wins; R1-R3 are hard safety constraints.
The DVM's four urgency classes are treated as ordinal (class 1 = routine ... class 4 = most urgent).
The model spreads probability over the upper classes even on ordinary scenes, so only a confident
class-4 call (probability >= URGENT_PROB from a trusted prediction) is protected as urgent.
"""

from app.core.config import settings
from app.schemas.mission import DecisionRequest, EnergyRequest
from app.services import compression, energy

URGENT_PROB = 0.6
LOW_VALUE = 0.25
STORAGE_PRESSURE = 0.9
PRESSURE_VALUE = 0.5
UNTRUSTED_PRIORITY = 10
# Untrusted products normally wait onboard for ground review; once storage is past this fill level
# they go down raw at low priority, if the pass has this much spare room, rather than being dropped later.
UNTRUSTED_DRAIN_STORAGE = 0.5
SPARE_CAPACITY_FACTOR = 2.0
# Onboard processing must leave this much charge above the reserve, more in eclipse (no recharge).
ENERGY_MARGIN_PCT = 5.0
ECLIPSE_MARGIN_PCT = 10.0
# Onboard analysis turns a raw product into a much smaller derived product.
PROCESSING_TASK = {"image_batch": "image_classification", "science_bulk": "hyperspectral_processing", "telemetry_log": "data_filtering"}


def urgency_score(probs: list[float] | None) -> float:
    if not probs:
        return 0.0
    return sum(p * i for i, p in enumerate(probs)) / (len(probs) - 1)


def value_score(usability: float | None, urgency: float) -> float:
    """0-100 science value shown on the dashboard: mostly usability, boosted by urgency."""
    if usability is None:
        return round(40 * urgency, 1)
    return round(100 * min(1.0, 0.65 * usability + 0.35 * urgency), 1)


def decide(req: DecisionRequest) -> dict:
    item, state, next_pass = req.item, req.state, req.next_pass
    reserve = settings.battery_reserve_pct
    urgency = urgency_score(item.urgency_probs)
    value = value_score(item.usability, urgency)
    storage_after = (state.storage_used_MB + item.size_MB) / state.storage_capacity_MB
    room_to_store = storage_after <= 1.0
    available_MB = max(0.0, next_pass.capacity_MB - state.queued_for_downlink_MB) if next_pass else 0.0

    proc = energy.estimate(EnergyRequest(task_type=PROCESSING_TASK[item.data_class], data_volume_MB=item.size_MB))
    proc_pct = proc["energy_J"] / 3600 / state.battery_capacity_Wh * 100
    margin = ECLIPSE_MARGIN_PCT if state.in_eclipse else ENERGY_MARGIN_PCT
    energy_ok = state.battery_pct - proc_pct >= reserve + margin
    plan = compression.plan(item.data_class, item.size_MB, available_MB) if next_pass else None

    inputs = {
        "battery_pct": round(state.battery_pct, 1),
        "battery_reserve_pct": reserve,
        "in_eclipse": state.in_eclipse,
        "mode": state.mode,
        "storage_after_pct": round(100 * storage_after, 1),
        "usability": item.usability,
        "urgency_score": round(urgency, 3),
        "severity": item.severity,
        "ai_trusted": item.ai_trusted,
        "processing_energy_pct": round(proc_pct, 3),
        "next_pass_available_MB": round(available_MB, 1) if next_pass else None,
        "next_pass_in_s": round(next_pass.starts_in_s) if next_pass else None,
    }

    def result(action: str, rule: str, reason: str, priority: int, with_energy: bool = False, with_plan: bool = False) -> dict:
        return {
            "item_id": item.id,
            "action": action,
            "rule": rule,
            "reason": reason,
            "value": value,
            "priority": priority,
            "energy": proc if with_energy else None,
            "compression": plan if with_plan else None,
            "inputs": inputs,
        }

    def downlink(rule: str, why: str, priority: int) -> dict:
        if plan is None:
            return result("STORE", rule, f"{why} No ground pass is predicted yet, so it is held for the next contact.", priority)
        if plan["can_send_all"]:
            return result("TRANSMIT_RAW", rule, f"{why} It fits the next pass ({available_MB:.0f} MB free), so it goes raw.", priority, with_plan=True)
        if plan["feasible"]:
            return result(
                "TRANSMIT_COMPRESSED", rule,
                f"{why} It needs {plan['recommended_compression_ratio']:.1f}x {plan['codec']} to fit the {available_MB:.0f} MB left in the next pass.",
                priority, with_plan=True,
            )
        return None

    # R1 - safe mode: only storage, nothing discretionary.
    if state.mode == "SAFE":
        if room_to_store or item.protected:
            return result("STORE", "R1_SAFE_MODE", "Spacecraft is in safe mode: no discretionary compute or downlink, product is stored.", 50)
        return result("DROP", "R1_SAFE_MODE", "Safe mode and storage is full: unprotected product is dropped.", 0)

    # R2 - protect urgent alerts: never discarded, sent first.
    urgent = item.ai_trusted and bool(item.urgency_probs) and item.urgency_probs[-1] >= URGENT_PROB
    if item.protected or item.severity == "ALERT" or urgent:
        why = "Protected alert (novel event or high-urgency detection)."
        decided = downlink("R2_PROTECT_ALERT", why, 100)
        if decided:
            return decided
        return result("STORE", "R2_PROTECT_ALERT", f"{why} Too large for the next pass even compressed, so it is kept for later passes.", 100)

    # R3 - battery reserve: no discretionary work below it.
    if state.battery_pct < reserve:
        if room_to_store:
            return result("STORE", "R3_BATTERY_RESERVE", f"Battery {state.battery_pct:.0f}% is below the {reserve:.0f}% reserve: product is stored, not processed or sent.", 40)
        return result("DROP", "R3_BATTERY_RESERVE", "Battery below reserve and storage is full: product is dropped.", 0)

    # R4 - untrusted data: the AI's value estimate cannot be relied on; the ground has to look at it.
    if item.severity == "CRITICAL" or not item.ai_trusted:
        status = item.health_status or "AI unavailable"
        if storage_after > UNTRUSTED_DRAIN_STORAGE and plan is not None and available_MB >= SPARE_CAPACITY_FACTOR * item.size_MB:
            return result("TRANSMIT_RAW", "R4_UNTRUSTED", f"Watchdog verdict {status}: onboard value is untrusted and storage is {100 * storage_after:.0f}% full; the next pass has spare room, so it goes down raw at low priority for ground review.", UNTRUSTED_PRIORITY, with_plan=True)
        if room_to_store and storage_after <= STORAGE_PRESSURE:
            return result("STORE", "R4_UNTRUSTED", f"Watchdog verdict {status}: onboard value is untrusted, raw data is kept for ground review.", 30)
        return result("DROP", "R4_UNTRUSTED", f"Watchdog verdict {status} and storage is under pressure: untrusted product is dropped.", 0)

    # R5 - low value.
    if item.usability is not None and item.usability < LOW_VALUE:
        return result("DROP", "R5_LOW_VALUE", f"Usability {item.usability:.2f} is below {LOW_VALUE}: not worth storage or downlink.", 0)

    # R6 - storage pressure: only good products survive.
    if storage_after > STORAGE_PRESSURE and (item.usability or 0) < PRESSURE_VALUE:
        return result("DROP", "R6_STORAGE_PRESSURE", f"Storage would reach {100 * storage_after:.0f}%: products under {PRESSURE_VALUE} usability are dropped.", 0)

    # R7 - downlink if the next pass can take it.
    priority = int(value)
    decided = downlink("R7_DOWNLINK", f"Value {value:.0f}/100.", priority)
    if decided:
        return decided

    # R8 - too big for the pass even compressed: shrink it onboard if the energy budget allows.
    if energy_ok:
        return result(
            "PROCESS_NOW", "R8_PROCESS_ONBOARD",
            f"Too large for the next pass even compressed; onboard {PROCESSING_TASK[item.data_class].replace('_', ' ')} costs {proc_pct:.2f}% battery and leaves the reserve intact.",
            priority, with_energy=True, with_plan=plan is not None,
        )
    return result(
        "STORE", "R8_ENERGY_BUDGET",
        f"Onboard processing would cost {proc_pct:.2f}% battery, cutting into the {reserve:.0f}% reserve{' during eclipse' if state.in_eclipse else ''}: stored until power allows.",
        priority, with_energy=True, with_plan=plan is not None,
    )
