from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Satellite Brain API"
    api_prefix: str = "/api"
    cors_origins: list[str] = ["http://localhost:3000"]

    # Satellite health diagnostics
    dvm_model_path: Path = APP_DIR / "ml" / "models" / "best_satellite_dvm_traced.pt"
    diagnostics_max_upload_bytes: int = 16 * 1024 * 1024

    # Downlink delivery prediction (exported from IASTAM_Satellite_Radio_Link_Prediction.ipynb)
    link_model_path: Path = APP_DIR / "ml" / "models" / "link_delivery_model.joblib"
    link_meta_path: Path = APP_DIR / "ml" / "models" / "link_delivery_meta.json"
    link_samples_path: Path = APP_DIR / "ml" / "models" / "link_delivery_samples.json"

    # Mission operations (simulated spacecraft <-> ground segment)
    mission_db_path: Path = APP_DIR.parent / "data" / "mission.db"
    # Single source of truth for the battery reserve used by the decision policy and the dashboard.
    battery_reserve_pct: float = 30.0
    # Telemetry older than this marks the spacecraft as out of contact on the dashboard.
    telemetry_stale_s: float = 15.0
    # Telemetry frames kept per spacecraft; older ones are pruned.
    telemetry_history: int = 5000


settings = Settings()
