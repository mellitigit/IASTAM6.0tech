from fastapi import APIRouter, HTTPException, status

from app.db.mission_store import get_store
from app.schemas.queue import QueuePlan, QueuePlanRequest
from app.services import downlink_queue

router = APIRouter(prefix="/queue", tags=["queue"])


@router.post("/plan", response_model=QueuePlan)
def plan_queue(req: QueuePlanRequest) -> dict:
    """Rank onboard products by importance and fill the next pass in that order."""
    try:
        queue_plan = downlink_queue.plan(req)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    get_store().add_event(req.sat_id, "queue_plan", {
        "capacity_MB": queue_plan["capacity_MB"],
        "booked_MB": queue_plan["booked_MB"],
        "this_pass": [s["item_id"] for s in queue_plan["this_pass"]],
        "waiting": [s["item_id"] for s in queue_plan["waiting"]],
        "dropped": [s["item_id"] for s in queue_plan["dropped"]],
    })
    return queue_plan
