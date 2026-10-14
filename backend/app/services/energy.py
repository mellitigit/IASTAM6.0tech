"""Task-energy estimate: receive + compute + transmit, each E = P x T.

Physics fallback for the README's Random Forest task-energy predictor, whose trained model is not in
this repo. `estimate` is the only entry point, so the learned model can replace it without touching
callers. Power and cycle figures are typical CubeSat values, not measurements.
"""

from app.schemas.mission import EnergyRequest

MODEL = "physics fallback (E = P x T per phase)"
WARNING = "Physics estimate, not the trained task-energy model; figures are typical CubeSat values."

# Per task type: CPU cycles per MB processed (G), compute power at 1 GHz (W), output/input volume.
TASKS = {
    "image_capture": (0.02, 1.5, 1.0),
    "image_compression": (0.35, 2.2, 0.25),
    "image_classification": (1.6, 3.4, 0.02),
    "object_detection": (3.2, 4.1, 0.03),
    "hyperspectral_processing": (6.5, 4.8, 0.15),
    "data_filtering": (0.12, 1.8, 0.4),
}

RECEIVE_POWER_W = 0.9  # payload/bus interface while moving data into memory
RECEIVE_RATE_MBPS = 400.0
RADIO_OVERHEAD_W = 3.5  # transceiver electronics on top of RF output power
HANDSHAKE_S = 2.0


def estimate(req: EnergyRequest) -> dict:
    cycles_per_mb, power_1ghz, out_ratio = TASKS[req.task_type]
    cycles_g = req.cpu_cycles_G if req.cpu_cycles_G is not None else cycles_per_mb * req.data_volume_MB

    receive_s = req.data_volume_MB * 8 / RECEIVE_RATE_MBPS
    # Contention from other processes stretches compute time; memory pressure adds swap/refresh cost.
    compute_s = cycles_g / req.cpu_freq_GHz / (1 - req.cpu_load)
    compute_w = power_1ghz * req.cpu_freq_GHz**1.5 * (1 + 0.3 * req.memory_load) + 0.002 * req.memory_MB
    output_mb = req.data_volume_MB * out_ratio
    transmit_s = (HANDSHAKE_S + output_mb * 8 / req.data_rate_Mbps) if req.transmit_output else 0.0

    breakdown = {
        "receive": RECEIVE_POWER_W * receive_s,
        "compute": compute_w * compute_s,
        "transmit": (req.tx_power_W + RADIO_OVERHEAD_W) * transmit_s,
    }
    return {
        "energy_J": round(sum(breakdown.values()), 3),
        "duration_s": round(receive_s + compute_s + transmit_s, 3),
        "breakdown_J": {k: round(v, 3) for k, v in breakdown.items()},
        "output_volume_MB": round(output_mb, 4),
        "model": MODEL,
        "warning": WARNING,
    }
