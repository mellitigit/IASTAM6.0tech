"""Space-ground link to the Satellite Brain API, with a spacecraft's tolerance for a missing ground.

Every call returns None instead of raising when the ground segment is unreachable or answers with
an error; the spacecraft then falls back to onboard rules. Telemetry is buffered and replayed in
order once the link is back. After a failure, calls are skipped until an exponential backoff expires.
"""

import io
import logging
import time
from collections import deque
from typing import Any

import httpx
import numpy as np

log = logging.getLogger("satsim.link")


class GroundLink:
    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout_s: float = 10.0, buffer: int = 2000):
        self.base = base_url.rstrip("/") + "/api/mission"
        self.http = client or httpx.Client(timeout=timeout_s)
        self.telemetry_buffer: deque[dict] = deque(maxlen=buffer)
        self.ack_buffer: deque[tuple[str, dict]] = deque()
        self.backoff_s = 0.0
        self.retry_at = 0.0
        self.online = True
        self.failures = 0

    # ---- plumbing -----------------------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs) -> Any:
        if time.monotonic() < self.retry_at:
            return None
        try:
            r = self.http.request(method, self.base + path, **kwargs)
        except httpx.HTTPError as exc:
            self._down(f"{method} {path}: {exc.__class__.__name__}")
            return None
        if r.status_code >= 500:
            self._down(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
            return None
        if r.status_code >= 400:
            # A rejected request is our bug or bad data, not an outage: report it and carry on.
            log.warning("%s %s rejected: HTTP %s %s", method, path, r.status_code, r.text[:300])
            return None
        self._up()
        return r.json()

    def _down(self, why: str) -> None:
        self.failures += 1
        self.backoff_s = min(30.0, max(1.0, self.backoff_s * 2))
        self.retry_at = time.monotonic() + self.backoff_s
        if self.online:
            log.warning("ground link lost (%s); retrying with backoff", why)
        self.online = False

    def _up(self) -> None:
        if not self.online:
            log.info("ground link restored")
        self.online = True
        self.backoff_s = 0.0

    # ---- spacecraft -> ground -----------------------------------------------------------------

    def send_telemetry(self, frame: dict) -> dict | None:
        """Sends buffered frames oldest first; returns the ground's ack for the newest one sent."""
        self.telemetry_buffer.append(frame)
        ack = None
        while self.telemetry_buffer:
            ack = self._call("POST", "/telemetry", json=self.telemetry_buffer[0])
            if ack is None:
                return None
            self.telemetry_buffer.popleft()
        return ack

    def capture(self, sat_id: str, item_id: str, patch: np.ndarray, battery_pct: float) -> dict | None:
        buf = io.BytesIO()
        np.save(buf, patch.astype(np.float32))
        return self._call(
            "POST", "/captures",
            files={"patch": (f"{item_id}.npy", buf.getvalue(), "application/octet-stream")},
            data={"sat_id": sat_id, "item_id": item_id, "battery_pct": f"{battery_pct:.2f}"},
        )

    def decide(self, body: dict) -> dict | None:
        return self._call("POST", "/decisions", json=body)

    def energy(self, body: dict) -> dict | None:
        return self._call("POST", "/energy/predict", json=body)

    def plan_downlink(self, body: dict) -> dict | None:
        return self._call("POST", "/downlinks", json=body)

    def report_downlink(self, attempt_id: str, outcome: dict) -> dict | None:
        return self._call("POST", f"/downlinks/{attempt_id}/outcome", json=outcome)

    def log(self, sat_id: str, message: str, level: str = "info") -> None:
        self._call("POST", "/log", data={"sat_id": sat_id, "level": level, "message": message})

    # ---- ground -> spacecraft -----------------------------------------------------------------

    def pending_commands(self, sat_id: str) -> list[dict]:
        return self._call("GET", "/commands/pending", params={"sat_id": sat_id}) or []

    def ack(self, command_id: str, status: str, result: str) -> None:
        self.ack_buffer.append((command_id, {"status": status, "result": result}))
        self.flush_acks()

    def flush_acks(self) -> None:
        while self.ack_buffer:
            command_id, body = self.ack_buffer[0]
            if self._call("POST", f"/commands/{command_id}/ack", json=body) is None and not self.online:
                return
            self.ack_buffer.popleft()
