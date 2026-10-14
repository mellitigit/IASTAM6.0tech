"""Circular-orbit propagation, sun/eclipse geometry and ground-station visibility.

Spherical Earth, two-body motion (no J2 or drag), a low-precision sun ephemeris and a cylindrical
shadow. Good to a few tens of km and seconds, which is plenty for pass planning and power modelling.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np

EARTH_RADIUS_KM = 6371.0
MU_KM3_S2 = 398600.4418
EARTH_ROTATION_RAD_S = 7.2921159e-5


def julian_date(t: datetime) -> float:
    return t.timestamp() / 86400.0 + 2440587.5


def gmst_rad(t: datetime) -> float:
    return math.radians((280.46061837 + 360.98564736629 * (julian_date(t) - 2451545.0)) % 360)


def sun_unit_eci(t: datetime) -> np.ndarray:
    n = julian_date(t) - 2451545.0
    mean_lon = math.radians((280.460 + 0.9856474 * n) % 360)
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360)
    ecl_lon = mean_lon + math.radians(1.915) * math.sin(anomaly) + math.radians(0.020) * math.sin(2 * anomaly)
    obliquity = math.radians(23.439)
    return np.array([math.cos(ecl_lon), math.cos(obliquity) * math.sin(ecl_lon), math.sin(obliquity) * math.sin(ecl_lon)])


@dataclass
class Orbit:
    altitude_km: float
    inclination_deg: float
    raan_deg: float
    epoch: datetime
    arg_lat0_deg: float = 0.0

    @classmethod
    def sun_synchronous(cls, altitude_km: float, ltan_h: float, epoch: datetime, inclination_deg: float = 97.5) -> "Orbit":
        """RAAN placed so the ascending node crosses at the given local solar time."""
        s = sun_unit_eci(epoch)
        sun_ra = math.degrees(math.atan2(s[1], s[0]))
        return cls(altitude_km, inclination_deg, (sun_ra + (ltan_h - 12.0) * 15.0) % 360, epoch)

    @property
    def radius_km(self) -> float:
        return EARTH_RADIUS_KM + self.altitude_km

    @property
    def period_s(self) -> float:
        return 2 * math.pi * math.sqrt(self.radius_km**3 / MU_KM3_S2)

    def eci(self, t: datetime) -> np.ndarray:
        dt = (t - self.epoch).total_seconds()
        u = math.radians(self.arg_lat0_deg) + 2 * math.pi * dt / self.period_s
        i, raan = math.radians(self.inclination_deg), math.radians(self.raan_deg)
        x, y = math.cos(u), math.sin(u)
        return self.radius_km * np.array([
            math.cos(raan) * x - math.sin(raan) * math.cos(i) * y,
            math.sin(raan) * x + math.cos(raan) * math.cos(i) * y,
            math.sin(i) * y,
        ])

    def ecef(self, t: datetime) -> np.ndarray:
        return _rotate_z(self.eci(t), -gmst_rad(t))

    def subpoint(self, t: datetime) -> tuple[float, float]:
        x, y, z = self.ecef(t)
        return math.degrees(math.asin(z / math.sqrt(x * x + y * y + z * z))), math.degrees(math.atan2(y, x))

    def in_eclipse(self, t: datetime) -> bool:
        r, s = self.eci(t), sun_unit_eci(t)
        along = float(r @ s)
        return along < 0 and float(np.linalg.norm(r - along * s)) < EARTH_RADIUS_KM

    def next_eclipse_change(self, t: datetime, step_s: float = 20.0) -> float:
        """Seconds until the spacecraft next enters or leaves the Earth's shadow."""
        state = self.in_eclipse(t)
        for k in range(1, int(self.period_s / step_s) + 2):
            if self.in_eclipse(t + timedelta(seconds=k * step_s)) != state:
                return k * step_s
        return self.period_s


def _rotate_z(v: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1], v[2]])


@dataclass
class Station:
    """Illustrative ground station built on one of the radio-link dataset's station archetypes."""

    station_id: str
    name: str
    lat_deg: float
    lon_deg: float
    climate_zone: str
    dish_m: float
    elevation_mask_deg: float

    def ecef(self) -> np.ndarray:
        lat, lon = math.radians(self.lat_deg), math.radians(self.lon_deg)
        return EARTH_RADIUS_KM * np.array([math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)])

    def look(self, sat_ecef: np.ndarray) -> tuple[float, float]:
        """(elevation_deg, slant_range_km) of the spacecraft."""
        ground = self.ecef()
        d = sat_ecef - ground
        rng = float(np.linalg.norm(d))
        return math.degrees(math.asin(float(d @ ground) / (rng * EARTH_RADIUS_KM))), rng


@dataclass
class Pass:
    station: Station
    aos: datetime
    los: datetime
    max_elevation_deg: float

    @property
    def duration_s(self) -> float:
        return (self.los - self.aos).total_seconds()

    def summary(self) -> dict:
        return {
            "station_id": self.station.station_id,
            "station_name": self.station.name,
            "aos": self.aos.isoformat(),
            "los": self.los.isoformat(),
            "max_elevation_deg": round(self.max_elevation_deg, 1),
            "duration_s": round(self.duration_s),
        }


def predict_passes(orbit: Orbit, stations: list[Station], start: datetime, hours: float, step_s: float = 10.0) -> list[Pass]:
    times = [start + timedelta(seconds=k * step_s) for k in range(int(hours * 3600 / step_s))]
    sats = [orbit.ecef(t) for t in times]
    passes = []
    for st in stations:
        elev = np.array([st.look(p)[0] for p in sats])
        visible = elev >= st.elevation_mask_deg
        k = 0
        while k < len(times):
            if visible[k]:
                j = k
                while j + 1 < len(times) and visible[j + 1]:
                    j += 1
                if j > k and k > 0:  # skip a pass already in progress at the start of the window
                    passes.append(Pass(st, times[k], times[j], float(elev[k : j + 1].max())))
                k = j + 1
            else:
                k += 1
    return sorted(passes, key=lambda p: p.aos)


def utc(s: str | None = None) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc) if s else datetime.now(timezone.utc).replace(microsecond=0)
