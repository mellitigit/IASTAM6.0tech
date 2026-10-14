from typing import Literal

from pydantic import BaseModel

FeatureValue = float | int | str | None


class LinkModelInfo(BaseModel):
    model: str
    target: str
    features: list[str]
    categorical_features: list[str]
    threshold: float
    threshold_policy: str
    calibration: str
    sklearn_version: str
    dataset: str
    warning: str


class LinkSample(BaseModel):
    id: str
    station_id: str
    pass_id: str
    recorded_success: int
    simulated_success_probability: float | None
    failure_reason: str
    features: dict[str, FeatureValue]


class PredictRequest(BaseModel):
    features: dict[str, FeatureValue]


class PredictResponse(BaseModel):
    probability: float
    threshold: float
    decision: Literal["ATTEMPT", "DEFER"]
    model: str
    warning: str
