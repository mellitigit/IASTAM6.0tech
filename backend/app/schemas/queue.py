from pydantic import BaseModel, Field

from app.schemas.mission import Action, DataClass, ItemAssessment, NextPass, SpacecraftState


class QueuePlanRequest(BaseModel):
    sat_id: str
    # Products already stored onboard (storage_used_MB includes them) waiting for a downlink slot.
    items: list[ItemAssessment] = Field(min_length=1, max_length=500)
    state: SpacecraftState
    next_pass: NextPass | None = None


class QueueSlot(BaseModel):
    rank: int  # 1 = most important overall
    item_id: str
    data_class: DataClass
    size_MB: float
    action: Action
    rule: str
    reason: str
    value: float
    priority: int
    # Only for products sent in the next pass:
    send_MB: float | None = None  # volume on the link (after compression)
    codec: str | None = None
    pass_start_MB: float | None = None  # where the product starts within the pass


class QueuePlan(BaseModel):
    sat_id: str
    created_at: str
    capacity_MB: float | None
    booked_MB: float
    this_pass: list[QueueSlot]  # in transmission order
    waiting: list[QueueSlot]  # kept onboard, in the order they will be considered for later passes
    dropped: list[QueueSlot]
