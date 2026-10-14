import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# The simulator package, and the backend for the end-to-end test (which runs the API in-process).
sys.path[:0] = [str(ROOT / "simulator"), str(ROOT / "backend")]
