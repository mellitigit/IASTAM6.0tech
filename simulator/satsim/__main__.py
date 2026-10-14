"""Run the simulated spacecraft against a running Satellite Brain API.

    python -m satsim --api http://localhost:8000 --scenario nominal_day --speed 60
"""

import argparse
import logging
import signal
import time

from satsim.client import GroundLink
from satsim.orbit import utc
from satsim.scenarios import SCENARIOS, build
from satsim.spacecraft import Spacecraft


def main() -> None:
    parser = argparse.ArgumentParser(prog="satsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:8000", help="base URL of the FastAPI backend")
    parser.add_argument("--scenario", default="nominal_day", choices=sorted(SCENARIOS))
    parser.add_argument("--speed", type=float, default=60.0, help="simulated seconds per wall-clock second (0 = as fast as possible)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sat-id", default="ORBITAL-01")
    parser.add_argument("--start", help="simulation start, ISO 8601 UTC (default: now)")
    parser.add_argument("--duration", type=float, help="stop after this many simulated hours (default: run until interrupted)")
    parser.add_argument("--dt", type=float, default=10.0, help="simulation time step in seconds")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("satsim")

    cfg = build(args.scenario, seed=args.seed, sat_id=args.sat_id, dt_s=args.dt)
    sc = Spacecraft(cfg, GroundLink(args.api), utc(args.start), args.speed)
    nxt = sc._next_pass()
    log.info("%s up: scenario %s, %.0f km SSO, speed x%s, ground %s", cfg.sat_id, cfg.scenario, cfg.altitude_km, args.speed or "max", args.api)
    if nxt:
        log.info("first pass: %s at %s (in %.0f min sim)", nxt.station.name, nxt.aos.strftime("%H:%M:%S"), (nxt.aos - sc.t).total_seconds() / 60)

    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    end = sc.t.timestamp() + args.duration * 3600 if args.duration else None
    wall_dt = cfg.dt_s / args.speed if args.speed > 0 else 0.0
    deadline = time.monotonic()
    while not stop and (end is None or sc.t.timestamp() < end):
        sc.step()
        if wall_dt:
            deadline += wall_dt
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -5:
                deadline = time.monotonic()  # fell far behind (slow ground); don't try to catch up
    log.info("stopped at %s; counters %s", sc.t.isoformat(), sc.counters)


if __name__ == "__main__":
    main()
