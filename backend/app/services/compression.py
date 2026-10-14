"""Send/compress plan for one file against a contact window's forecast capacity.

Rule fallback for the README's XGBoost compression predictor (can_send_all + recommended ratio),
whose trained model is not in this repo. The codec follows the README's choice per data type.
"""

MODEL = "rule fallback (ratio = size / forecast window capacity)"
WARNING = "Rule-based plan, not the trained compression model."

# Codec per data class and the highest ratio it reaches without unacceptable loss.
CODECS = {
    "image_batch": ("JPEG", 10.0),
    "telemetry_log": ("LZ4", 2.1),
    "science_bulk": ("Zstandard", 3.2),
}
SAFETY_MARGIN = 1.1  # aim for 10 % headroom inside the window


def plan(data_class: str, size_MB: float, window_capacity_MB: float) -> dict:
    codec, max_ratio = CODECS[data_class]
    if window_capacity_MB >= size_MB * SAFETY_MARGIN:
        return {
            "can_send_all": True,
            "recommended_compression_ratio": 1.0,
            "codec": "none",
            "max_ratio": max_ratio,
            "feasible": True,
            "compressed_size_MB": round(size_MB, 4),
            "model": MODEL,
            "warning": WARNING,
        }
    needed = size_MB * SAFETY_MARGIN / window_capacity_MB if window_capacity_MB > 0 else float("inf")
    ratio = min(needed, max_ratio)
    return {
        "can_send_all": False,
        "recommended_compression_ratio": round(ratio, 3),
        "codec": codec,
        "max_ratio": max_ratio,
        "feasible": needed <= max_ratio,
        "compressed_size_MB": round(size_MB / ratio, 4),
        "model": MODEL,
        "warning": WARNING,
    }
