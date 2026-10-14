from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.diagnostics import DiagnosticsResponse

DataClass = Literal["image_batch", "science_bulk", "telemetry_log"]
Mode = Literal["NOMINAL", "SAFE", "VNIR_4BAND", "AI_RESTART"]
Action = Literal["PROCESS_NOW", "STORE", "TRANSMIT_RAW", "TRANSMIT_COMPRESSED", "DROP"]
CommandType = Literal[
    # raised automatically from the FDIR watchdog's recommended_action
    "SWITCH_TO_4BAND_VNIR",
    "RELOAD_WEIGHTS_WARM_RESTART",
    "SAVE_RAW_THUMBNAIL_FOR_GROUND",
    "TRIGGER_EMERGENCY_ANOMALY_ALERT",
    # operator commands
    "ENTER_SAFE_MODE",
    "EXIT_SAFE_MODE",
    "RESTORE_6BAND",
    "CAPTURE_NOW",
    "PRIORITIZE_ITEM",
    "DELETE_ITEM",
]
CommandStatus = Literal["PENDING", "SENT", "EXECUTED", "REJECTED"]


# ---- telemetry --------------------------------------------------------------------------------


class Position(BaseModel):
    lat_deg: float
    lon_deg: float
    alt_km: float


class PassInfo(BaseModel):
    station_id: str
    station_name: str
    aos: str
    los: str
    max_elevation_deg: float
    duration_s: float


class QueueItem(BaseModel):
    id: str
    data_class: DataClass
    size_MB: float
    state: str
    created_at: str
    value: float | None = None
    action: Action | None = None
    reason: str | None = None
    protected: bool = False
    compression_ratio: float | None = None


class Telemetry(BaseModel):
    sat_id: str
    sim_time: str
    speed: float = Field(gt=0)
    scenario: str
    mode: Mode
    position: Position
    in_eclipse: bool
    eclipse_change_in_s: float | None = None
    battery_pct: float = Field(ge=0, le=100)
    battery_capacity_Wh: float = Field(gt=0)
    power_in_W: float
    power_out_W: float
    temperature_C: float
    storage_used_MB: float = Field(ge=0)
    storage_capacity_MB: float = Field(gt=0)
    bands_active: int
    ai_available: bool
    in_contact: str | None = None
    next_passes: list[PassInfo] = []
    queue: list[QueueItem] = []
    counters: dict[str, int] = {}


class Ack(BaseModel):
    ok: bool = True
    event_id: int
    pending_commands: int


# ---- energy & compression ---------------------------------------------------------------------

TaskType = Literal[
    "image_capture",
    "image_compression",
    "image_classification",
    "object_detection",
    "hyperspectral_processing",
    "data_filtering",
]


class EnergyRequest(BaseModel):
    task_type: TaskType
    data_volume_MB: float = Field(ge=0)
    cpu_cycles_G: float | None = Field(None, ge=0, description="defaults to a per-task cycles/MB figure")
    cpu_freq_GHz: float = Field(1.2, gt=0)
    memory_MB: float = Field(256, ge=0)
    cpu_load: float = Field(0.2, ge=0, lt=1)
    memory_load: float = Field(0.3, ge=0, lt=1)
    tx_power_W: float = Field(2.0, ge=0)
    data_rate_Mbps: float = Field(50.0, gt=0)
    transmit_output: bool = False


class EnergyEstimate(BaseModel):
    energy_J: float
    duration_s: float
    breakdown_J: dict[str, float]
    output_volume_MB: float
    model: str
    warning: str


class CompressionRequest(BaseModel):
    data_class: DataClass
    size_MB: float = Field(gt=0)
    window_capacity_MB: float = Field(ge=0)


class CompressionPlan(BaseModel):
    can_send_all: bool
    recommended_compression_ratio: float
    codec: Literal["none", "JPEG", "LZ4", "Zstandard"]
    max_ratio: float
    feasible: bool
    compressed_size_MB: float
    model: str
    warning: str


# ---- decisions --------------------------------------------------------------------------------


class ItemAssessment(BaseModel):
    id: str
    data_class: DataClass
    size_MB: float = Field(gt=0)
    usability: float | None = Field(None, ge=0, le=1)
    urgency_probs: list[float] | None = None
    severity: Literal["NOMINAL", "WARNING", "CRITICAL", "ALERT"] | None = None
    health_status: str | None = None
    ai_trusted: bool = False
    protected: bool = False


class SpacecraftState(BaseModel):
    battery_pct: float = Field(ge=0, le=100)
    battery_capacity_Wh: float = Field(gt=0)
    in_eclipse: bool
    eclipse_change_in_s: float | None = None
    storage_used_MB: float = Field(ge=0)
    storage_capacity_MB: float = Field(gt=0)
    queued_for_downlink_MB: float = Field(0, ge=0)
    mode: Mode = "NOMINAL"


class NextPass(BaseModel):
    starts_in_s: float
    duration_s: float = Field(gt=0)
    capacity_MB: float = Field(ge=0, description="forecast deliverable volume over the pass")


class DecisionRequest(BaseModel):
    sat_id: str
    item: ItemAssessment
    state: SpacecraftState
    next_pass: NextPass | None = None


class Decision(BaseModel):
    item_id: str
    action: Action
    rule: str
    reason: str
    value: float
    priority: int
    energy: EnergyEstimate | None = None
    compression: CompressionPlan | None = None
    inputs: dict[str, Any]


# ---- downlink attempts ------------------------------------------------------------------------


class DownlinkPlanRequest(BaseModel):
    sat_id: str
    item_id: str
    data_class: DataClass
    station_id: str
    features: dict[str, float | int | str | None]


class DownlinkPlan(BaseModel):
    attempt_id: str
    item_id: str
    probability: float
    threshold: float
    decision: Literal["ATTEMPT", "DEFER"]
    compression: CompressionPlan
    model: str
    warning: str


class DownlinkOutcome(BaseModel):
    delivered: bool
    delivered_MB: float = Field(ge=0)
    duration_s: float = Field(ge=0)
    failure_reason: str = "success"
    actual_mean_sinr_dB: float | None = None
    attempted: bool = True


# ---- telecommands -----------------------------------------------------------------------------


class CommandRequest(BaseModel):
    sat_id: str
    type: CommandType
    params: dict[str, Any] = {}
    reason: str = "Operator request"


class Command(BaseModel):
    id: str
    sat_id: str
    type: CommandType
    params: dict[str, Any]
    source: Literal["operator", "fdir", "policy"]
    reason: str
    status: CommandStatus
    created_at: str
    acked_at: str | None
    result: str | None


class CommandAck(BaseModel):
    status: Literal["EXECUTED", "REJECTED"]
    result: str = ""


# ---- captures (DVM + watchdog for a patch the spacecraft just took) ---------------------------


class CaptureResponse(BaseModel):
    item_id: str
    diagnostics: DiagnosticsResponse
    commands: list[Command]


# ---- dashboard snapshot -----------------------------------------------------------------------


class MissionState(BaseModel):
    sat_id: str
    online: bool
    telemetry_age_s: float | None
    battery_reserve_pct: float
    telemetry: Telemetry | None
    battery_history: list[dict[str, Any]]
    events: list[dict[str, Any]]
    commands: list[Command]
    downlinks: list[dict[str, Any]]
    last_event_id: int
