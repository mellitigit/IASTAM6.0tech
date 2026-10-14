from typing import Literal

from pydantic import BaseModel


class AiOutputs(BaseModel):
    # None marks a non-finite model output (NaN/Inf cannot be sent as JSON).
    urgency_probs: list[float | None]
    usability_pred: float | None
    entropy: float | None
    recon_error: float | None


class HealthReport(BaseModel):
    health_status: str
    severity: Literal["NOMINAL", "WARNING", "CRITICAL", "ALERT"]
    root_cause: str
    recommended_action: str
    ai_prediction_trusted: bool
    battery_safe: bool
    diagnostic_message: str
    timestamp: str


class DiagnosticsResponse(BaseModel):
    # None when inference was skipped because the patch contained NaN/Inf pixels.
    ai: AiOutputs | None
    health: HealthReport
