from fastapi import APIRouter, HTTPException, status

from app.ml import link_delivery
from app.schemas.downlink import LinkModelInfo, LinkSample, PredictRequest, PredictResponse

router = APIRouter(prefix="/downlink", tags=["downlink"])


def _meta() -> dict:
    try:
        return link_delivery.metadata()
    except link_delivery.LinkModelUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))


@router.get("/model", response_model=LinkModelInfo)
def model_info() -> dict:
    return _meta()


@router.get("/samples", response_model=list[LinkSample])
def recorded_samples() -> list[dict]:
    """Real attempts from the dataset's held-out test split (never used in training)."""
    return link_delivery.samples()


@router.post("/predict", response_model=PredictResponse)
def predict_delivery(body: PredictRequest) -> dict:
    meta = _meta()
    try:
        result = link_delivery.predict(body.features)
    except KeyError as exc:
        missing = exc.args[0]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"missing features: {', '.join(missing)}")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid feature value: {exc}")
    return {**result, "model": meta["model"], "warning": meta["warning"]}
