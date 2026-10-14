"""Pick real held-out attempts from the radio-link dataset for the dashboard's downlink view.

Rebuilds the notebook's test split (GroupShuffleSplit by pass_id, 15 %, seed 42) so the samples
were never seen in training, then picks rows spread across the simulator's success probability.

    .venv/bin/python scripts/make_link_samples.py ../Data/iastam_satellite_radio_link_dataset_100k.csv
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit

MODELS = Path(__file__).resolve().parents[1] / "app" / "ml" / "models"
TARGETS = [0.01, 0.08, 0.2, 0.35, 0.5, 0.62, 0.75, 0.88, 0.96, 0.995]

df = pd.read_csv(sys.argv[1])
features = json.loads((MODELS / "link_delivery_meta.json").read_text())["features"]
_, test_idx = next(GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=42).split(df, groups=df["pass_id"]))
test = df.iloc[test_idx]

picked = []
for t in TARGETS:
    pool = test.drop(index=[p.name for p in picked])
    picked.append(pool.loc[(pool["success_probability_simulated"] - t).abs().idxmin()])


def clean(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if np.isnan(value) else round(float(value), 6)
    return value


samples = [
    {
        "id": row["scenario_id"],
        "station_id": row["station_id"],
        "pass_id": row["pass_id"],
        "recorded_success": int(row["delivery_success"]),
        "simulated_success_probability": clean(row["success_probability_simulated"]),
        "failure_reason": row["delivery_failure_reason"],
        "features": {f: clean(row[f]) for f in features},
    }
    for row in picked
]
out = MODELS / "link_delivery_samples.json"
out.write_text(json.dumps({"source": "held-out test split of iastam_satellite_radio_link_dataset_100k.csv", "samples": samples}, indent=2))
print(f"wrote {len(samples)} samples to {out}")
