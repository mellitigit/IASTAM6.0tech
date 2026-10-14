"""Priority downlink queue: the most important products go down first, the rest wait in order.

Importance comes from the onboard decision policy (decision_policy.decide), so the queue and the
per-product decisions never disagree: protected alerts (priority 100) first, then products by their
value. Ties go to the higher value, then to the smaller product (more products per pass).

The next pass is then filled in that order. Each product is re-decided with the volume already booked
by more important products, so it only sees the capacity they left; whatever does not fit (even
compressed) waits onboard, ranked, for later passes.
"""

from app.db.mission_store import now_iso
from app.schemas.mission import DecisionRequest, ItemAssessment
from app.schemas.queue import QueuePlanRequest
from app.services import decision_policy

TRANSMIT = {"TRANSMIT_RAW", "TRANSMIT_COMPRESSED"}


def _decide(req: QueuePlanRequest, item: ItemAssessment, booked_MB: float) -> dict:
    # Queued products are already onboard: storage_used_MB counts them, and decide() adds the
    # product's size again, so take it out to evaluate storage as it really is.
    state = req.state.model_copy(update={
        "storage_used_MB": max(0.0, req.state.storage_used_MB - item.size_MB),
        "queued_for_downlink_MB": booked_MB,
    })
    return decision_policy.decide(DecisionRequest(sat_id=req.sat_id, item=item, state=state, next_pass=req.next_pass))


def _slot(rank: int, item: ItemAssessment, decision: dict) -> dict:
    return {
        "rank": rank,
        "item_id": item.id,
        "data_class": item.data_class,
        "size_MB": item.size_MB,
        "action": decision["action"],
        "rule": decision["rule"],
        "reason": decision["reason"],
        "value": decision["value"],
        "priority": decision["priority"],
    }


def plan(req: QueuePlanRequest) -> dict:
    ids = [item.id for item in req.items]
    if len(ids) != len(set(ids)):
        raise ValueError("item ids must be unique")

    base = req.state.queued_for_downlink_MB
    first_pass = {item.id: _decide(req, item, base) for item in req.items}
    ranked = sorted(req.items, key=lambda it: (-first_pass[it.id]["priority"], -first_pass[it.id]["value"], it.size_MB))

    booked = base
    this_pass, waiting, dropped = [], [], []
    for rank, item in enumerate(ranked, start=1):
        decision = _decide(req, item, booked)
        slot = _slot(rank, item, decision)
        if decision["action"] in TRANSMIT:
            compression = decision["compression"]
            send = compression["compressed_size_MB"]
            this_pass.append({**slot, "send_MB": send, "codec": compression["codec"], "pass_start_MB": round(booked, 4)})
            booked += send
        elif decision["action"] == "DROP":
            dropped.append(slot)
        else:
            waiting.append(slot)

    return {
        "sat_id": req.sat_id,
        "created_at": now_iso(),
        "capacity_MB": req.next_pass.capacity_MB if req.next_pass else None,
        "booked_MB": round(booked, 4),
        "this_pass": this_pass,
        "waiting": waiting,
        "dropped": dropped,
    }
