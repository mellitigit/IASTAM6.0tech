"""Mission operations: the ground segment the simulated spacecraft talks to.

Spacecraft -> ground: telemetry, captures (DVM + watchdog), decision requests, downlink plans/outcomes,
command acknowledgements. Ground -> spacecraft: telecommands (from FDIR or an operator).
Dashboard: a snapshot (/state) and a live Server-Sent Events stream (/stream).
"""

import asyncio
import json

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.routes.diagnostics import diagnose, read_patch
from app.db.mission_store import get_store
from app.ml import link_delivery
from app.schemas.mission import (
    Ack,
    CaptureResponse,
    Command,
    CommandAck,
    CommandRequest,
    CompressionPlan,
    CompressionRequest,
    Decision,
    DecisionRequest,
    DownlinkOutcome,
    DownlinkPlan,
    DownlinkPlanRequest,
    EnergyEstimate,
    EnergyRequest,
    MissionState,
    Telemetry,
)
from app.services import compression, decision_policy, energy, mission_ops

router = APIRouter(prefix="/mission", tags=["mission"])

DEFAULT_SAT = "ORBITAL-01"
STREAM_POLL_S = 0.5
STREAM_HEARTBEAT_S = 15.0


@router.post("/telemetry", response_model=Ack)
def receive_telemetry(frame: Telemetry) -> dict:
    store = get_store()
    event = store.add_event(frame.sat_id, "telemetry", frame.model_dump())
    return {"event_id": event["id"], "pending_commands": len(store.commands(frame.sat_id, "PENDING"))}


@router.post("/captures", response_model=CaptureResponse)
def receive_capture(
    patch: UploadFile = File(..., description=".npy float32 (6, H, W) quicklook of the product"),
    sat_id: str = Form(...),
    item_id: str = Form(...),
    battery_pct: float = Form(..., ge=0, le=100),
) -> dict:
    result = diagnose(read_patch(patch), battery_pct).model_dump()
    store = get_store()
    store.add_event(sat_id, "diagnostic", {"item_id": item_id, **result})
    if result["health"]["severity"] == "ALERT":
        store.add_event(sat_id, "alert", {"item_id": item_id, "message": result["health"]["diagnostic_message"]})
    commands = mission_ops.fdir_commands(store, sat_id, item_id, result["health"])
    return {"item_id": item_id, "diagnostics": result, "commands": commands}


@router.post("/energy/predict", response_model=EnergyEstimate)
def predict_energy(req: EnergyRequest) -> dict:
    return energy.estimate(req)


@router.post("/compression/plan", response_model=CompressionPlan)
def plan_compression(req: CompressionRequest) -> dict:
    return compression.plan(req.data_class, req.size_MB, req.window_capacity_MB)


@router.post("/decisions", response_model=Decision)
def decide(req: DecisionRequest) -> dict:
    decision = decision_policy.decide(req)
    get_store().add_event(req.sat_id, "decision", {**decision, "data_class": req.item.data_class, "size_MB": req.item.size_MB})
    return decision


@router.post("/downlinks", response_model=DownlinkPlan)
def plan_downlink(req: DownlinkPlanRequest) -> dict:
    try:
        meta = link_delivery.metadata()
        prediction = link_delivery.predict(req.features)
    except link_delivery.LinkModelUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except KeyError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"missing features: {', '.join(exc.args[0])}")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid feature value: {exc}")

    size = float(req.features["file_size_MB"])
    capacity = float(req.features["estimated_window_capacity_MB"])
    plan = {
        **prediction,
        "compression": compression.plan(req.data_class, size, capacity),
        "model": meta["model"],
        "warning": meta["warning"],
        "station_id": req.station_id,
        "size_MB": size,
        "inputs": {k: req.features.get(k) for k in ("elevation_angle_deg", "sinr_dB", "link_margin_dB", "modulation_coding", "weather_state", "remaining_window_s", "capacity_ratio")},
    }
    store = get_store()
    attempt = store.add_downlink(req.sat_id, req.item_id, plan)
    store.add_event(req.sat_id, "downlink", {"phase": "planned", "attempt_id": attempt["id"], "item_id": req.item_id, **plan})
    return {"attempt_id": attempt["id"], "item_id": req.item_id, **plan}


@router.post("/downlinks/{attempt_id}/outcome")
def report_downlink_outcome(attempt_id: str, outcome: DownlinkOutcome) -> dict:
    store = get_store()
    attempt = store.get_downlink(attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown downlink attempt {attempt_id}")
    attempt = store.set_downlink_outcome(attempt_id, outcome.model_dump())
    plan = attempt["plan"]
    predicted = plan["decision"] == "ATTEMPT"
    store.add_event(attempt["sat_id"], "downlink", {
        "phase": "completed",
        "attempt_id": attempt_id,
        "item_id": attempt["item_id"],
        "station_id": plan["station_id"],
        "probability": plan["probability"],
        "predicted_success": predicted,
        "prediction_correct": predicted == outcome.delivered if outcome.attempted else None,
        **outcome.model_dump(),
    })
    return attempt


@router.get("/commands", response_model=list[Command])
def list_commands(sat_id: str = DEFAULT_SAT, limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return get_store().commands(sat_id, limit=limit)


@router.post("/commands", response_model=Command, status_code=status.HTTP_201_CREATED)
def send_command(req: CommandRequest) -> dict:
    store = get_store()
    command = store.add_command(req.sat_id, req.type, req.params, "operator", req.reason)
    store.add_event(req.sat_id, "command", {"command": command, "change": "created"})
    return command


@router.get("/commands/pending", response_model=list[Command])
def pending_commands(sat_id: str) -> list[dict]:
    """Uplink: hands pending commands to the spacecraft and marks them SENT."""
    store = get_store()
    pending = store.commands(sat_id, "PENDING")
    store.mark_sent([c["id"] for c in pending])
    return [{**c, "status": "SENT"} for c in pending]


@router.post("/commands/{command_id}/ack", response_model=Command)
def ack_command(command_id: str, ack: CommandAck) -> dict:
    store = get_store()
    if store.get_command(command_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown command {command_id}")
    command = store.ack_command(command_id, ack.status, ack.result)
    store.add_event(command["sat_id"], "command", {"command": command, "change": "acknowledged"})
    return command


@router.post("/log")
def spacecraft_log(sat_id: str = Form(...), level: str = Form("info"), message: str = Form(...)) -> dict:
    """Free-text onboard log line (mode changes, autonomous fallbacks, ...)."""
    event = get_store().add_event(sat_id, "log", {"level": level, "message": message})
    return {"event_id": event["id"]}


@router.get("/satellites", response_model=list[str])
def satellites() -> list[str]:
    return get_store().satellites()


@router.get("/state", response_model=MissionState)
def state(sat_id: str = DEFAULT_SAT) -> dict:
    return mission_ops.snapshot(get_store(), sat_id)


@router.get("/stream")
async def stream(
    request: Request,
    sat_id: str | None = None,
    after: int | None = Query(None, ge=0, description="replay events after this id (default: only new ones)"),
    follow: bool = Query(True, description="false: send the backlog and close"),
    last_event_id: str | None = Header(None),
) -> StreamingResponse:
    store = get_store()
    if after is None:
        after = int(last_event_id) if last_event_id and last_event_id.isdigit() else store.last_event_id()

    async def events():
        cursor = after
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            batch = await asyncio.to_thread(store.events_after, cursor, sat_id)
            for event in batch:
                cursor = event["id"]
                yield f"id: {event['id']}\nevent: {event['kind']}\ndata: {json.dumps(event)}\n\n"
            if not follow and not batch:
                return
            if batch:
                idle = 0.0
                continue
            if await request.is_disconnected():
                return
            await asyncio.sleep(STREAM_POLL_S)
            idle += STREAM_POLL_S
            if idle >= STREAM_HEARTBEAT_S:
                idle = 0.0
                yield ": keep-alive\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
