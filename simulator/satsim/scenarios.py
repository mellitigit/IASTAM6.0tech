"""Simulation config and the named scenarios that drive different paths through the ground segment."""

from dataclasses import dataclass, field, replace

from satsim.link import SatRadio
from satsim.orbit import Station

# Illustrative stations built on the radio-link dataset's archetypes (GS01..GS10); not real facilities.
STATIONS = [
    Station("GS08", "Polar North", 78.2, 15.4, "subarctic", 3.7, 5.0),
    Station("GS03", "Atlantic West", 53.3, -9.0, "temperate_oceanic", 3.7, 5.0),
    Station("GS04", "Central Plains", 48.1, 11.6, "continental", 2.4, 7.0),
    Station("GS01", "Desert South", 23.0, 5.5, "arid_desert", 2.4, 5.0),
    Station("GS05", "Equatorial East", 1.3, 103.8, "tropical_humid", 5.4, 5.0),
    Station("GS10", "Highland South", -23.0, -67.8, "semi_arid_highland", 1.2, 10.0),
]


@dataclass
class Fault:
    """Something that happens to the spacecraft at a time offset (simulated seconds from start)."""

    at_s: float
    kind: str  # dead_band | bit_flips | saturation | event_scenes | extra_load
    params: dict = field(default_factory=dict)
    until_s: float | None = None


@dataclass
class SimConfig:
    sat_id: str = "ORBITAL-01"
    scenario: str = "nominal_day"
    seed: int = 7
    dt_s: float = 10.0
    altitude_km: float = 520.0
    ltan_h: float = 10.5
    stations: list[Station] = field(default_factory=lambda: list(STATIONS))
    radio: SatRadio = field(default_factory=SatRadio)

    # Power
    battery_capacity_Wh: float = 80.0
    battery_start_pct: float = 85.0
    solar_W: float = 30.0
    base_load_W: float = 6.5
    payload_W: float = 7.0
    radio_overhead_W: float = 3.5
    eclipse_heater_W: float = 2.0
    charge_efficiency: float = 0.92
    auto_safe_below_pct: float = 20.0
    auto_safe_exit_pct: float = 45.0

    # Data
    storage_capacity_MB: float = 16000.0
    housekeeping_MB: float = 150.0
    capture_interval_s: float = 300.0
    image_MB_median: float = 160.0
    science_interval_s: float = 3 * 3600.0
    science_MB_median: float = 900.0
    telemetry_log_interval_s: float = 1800.0
    patch_px: int = 128
    # The DVM is most confident on ordinary land at about 0.3x the nominal reflectances in
    # payload.SPECTRA (texture is then added); emitted SWIR from active fire is not sunlight, so
    # event scenes are neither scaled nor textured.
    radiometric_gain: float = 0.3
    restow_review_s: float = 600.0

    # Weather forced at every station (bad_weather_pass), else drawn per climate.
    forced_weather: list[str] | None = None
    faults: list[Fault] = field(default_factory=list)


def nominal_day(c: SimConfig) -> SimConfig:
    return c


def eclipse_low_battery(c: SimConfig) -> SimConfig:
    """Degraded panels plus a stuck heater: the battery crosses the reserve, then the safe-mode floor."""
    return replace(c, battery_start_pct=36.0, solar_W=16.0, base_load_W=8.0, faults=[Fault(0, "extra_load", {"W": 4.0, "why": "stuck payload heater"}, until_s=5 * 3600)])


def swir_detector_failure(c: SimConfig) -> SimConfig:
    return replace(c, faults=[Fault(900, "dead_band", {"band": 5, "level": 0.3})])


def radiation_bit_flips(c: SimConfig) -> SimConfig:
    return replace(c, faults=[
        Fault(600, "bit_flips", {"probability": 0.35}, until_s=4 * 3600),
        Fault(600, "saturation", {"probability": 0.12, "fraction": 0.6}, until_s=4 * 3600),
    ])


def novel_event(c: SimConfig) -> SimConfig:
    return replace(c, faults=[Fault(600, "event_scenes", {"scene": "wildfire", "probability": 0.5}, until_s=6 * 3600)])


def bad_weather_pass(c: SimConfig) -> SimConfig:
    return replace(c, forced_weather=["heavy_rain", "rain", "heavy_rain", "overcast"])


def storage_pressure(c: SimConfig) -> SimConfig:
    return replace(c, storage_capacity_MB=3500.0, capture_interval_s=120.0, image_MB_median=240.0, science_interval_s=3600.0)


SCENARIOS = {f.__name__: f for f in (nominal_day, eclipse_low_battery, swir_detector_failure, radiation_bit_flips, novel_event, bad_weather_pass, storage_pressure)}


def build(name: str, **overrides) -> SimConfig:
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}")
    return SCENARIOS[name](replace(SimConfig(scenario=name), **overrides))
