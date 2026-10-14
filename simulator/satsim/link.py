"""X-band downlink: weather, link budget and the 59 features the backend's delivery model expects.

Formulas follow Data/satellite_radio_link_data_dictionary.csv (FSPL, kTB noise, power-law rain,
Rayleigh cloud, equivalent-height gas, P.618-style scintillation, DVB-S2-like MODCOD table) and
were checked against rows of the training dataset. `Channel` then plays out what "really" happens
during a transfer, with fading the forecast does not see, so predictions can be scored.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from satsim.orbit import Orbit, Pass, Station

C_M_S = 299_792_458.0
HEADER_BYTES = 32

# MODCOD -> (spectral efficiency bit/s/Hz, Es/N0 threshold dB). Derived from the training dataset:
# rate = bandwidth x efficiency, margin = SINR - implementation loss - threshold.
MODCODS = {
    "QPSK_1/4": (0.4125, -3.142),
    "QPSK_1/2": (0.825, 0.208),
    "QPSK_2/3": (1.100, 2.308),
    "QPSK_3/4": (1.2375, 3.238),
    "QPSK_5/6": (1.375, 4.388),
    "8PSK_2/3": (1.650, 5.828),
    "8PSK_3/4": (1.85625, 7.118),
    "8PSK_5/6": (2.0625, 8.558),
    "16APSK_3/4": (2.475, 9.418),
    "16APSK_5/6": (2.750, 10.818),
    "32APSK_4/5": (3.300, 12.848),
    "32APSK_8/9": (3.6667, 14.898),
}
ADAPTIVE_BACKOFF_DB = 1.0

WEATHER_STATES = ["clear", "partly_cloudy", "overcast", "rain", "heavy_rain"]
# Weather odds per climate archetype (clear, partly cloudy, overcast, rain, heavy rain).
CLIMATE_WEATHER = {
    "arid_desert": [0.70, 0.18, 0.08, 0.03, 0.01],
    "semi_arid_highland": [0.55, 0.25, 0.12, 0.06, 0.02],
    "mediterranean": [0.45, 0.25, 0.15, 0.11, 0.04],
    "continental": [0.30, 0.25, 0.22, 0.17, 0.06],
    "temperate_oceanic": [0.18, 0.24, 0.30, 0.22, 0.06],
    "urban_temperate_high_rfi": [0.25, 0.25, 0.25, 0.19, 0.06],
    "subarctic": [0.22, 0.24, 0.32, 0.18, 0.04],
    "subtropical_humid": [0.25, 0.25, 0.18, 0.20, 0.12],
    "coastal_tropical": [0.22, 0.25, 0.18, 0.21, 0.14],
    "tropical_humid": [0.15, 0.22, 0.20, 0.25, 0.18],
}
CLIMATE_TEMP_C = {
    "arid_desert": 30, "semi_arid_highland": 12, "mediterranean": 20, "continental": 10, "temperate_oceanic": 11,
    "urban_temperate_high_rfi": 14, "subarctic": -5, "subtropical_humid": 22, "coastal_tropical": 27, "tropical_humid": 27,
}
RAIN_HEIGHT_KM = {"subarctic": 2.0, "temperate_oceanic": 3.0, "continental": 3.2, "urban_temperate_high_rfi": 3.2, "mediterranean": 3.5}


@dataclass
class Weather:
    state: str
    rain_rate_mm_h: float
    cloud_lwc_g_m3: float
    cloud_thickness_km: float
    relative_humidity_pct: float
    temperature_C: float
    turbulence: float

    @property
    def water_vapor_density(self) -> float:
        es = 6.112 * math.exp(17.62 * self.temperature_C / (243.12 + self.temperature_C))  # hPa
        return 216.7 * es * self.relative_humidity_pct / 100 / (273.15 + self.temperature_C)


def draw_weather(rng: np.random.Generator, climate: str, forced: str | None = None) -> Weather:
    state = forced or WEATHER_STATES[rng.choice(5, p=CLIMATE_WEATHER[climate])]
    level = WEATHER_STATES.index(state)
    rain = {3: rng.lognormal(math.log(4.5), 0.5), 4: rng.lognormal(math.log(19), 0.45)}.get(level, 0.0)
    return Weather(
        state=state,
        rain_rate_mm_h=float(rain),
        cloud_lwc_g_m3=float([0.004, 0.05, 0.2, 0.4, 0.8][level] * rng.uniform(0.5, 1.5)),
        cloud_thickness_km=float(rng.uniform(0.3, 1.5) + 0.6 * level),
        relative_humidity_pct=float(min(100, rng.uniform(30, 55) + 11 * level)),
        temperature_C=float(CLIMATE_TEMP_C[climate] + rng.normal(0, 4)),
        turbulence=float(np.clip(rng.normal([2.7, 3.9, 4.9, 6.2, 7.5][level], 1.0), 0, 10)),
    )


@dataclass
class StationRadio:
    """Fixed ground-station hardware, drawn once per station."""

    noise_figure_dB: float
    other_losses_dB: float
    implementation_loss_dB: float
    pointing_error_deg: float
    interference_offset_dB: float


@dataclass
class SatRadio:
    frequency_GHz: float = 8.2
    bandwidth_MHz: float = 20.0
    tx_power_W: float = 5.0
    antenna_gain_dBi: float = 12.0
    attitude_error_deg: float = 0.6
    packet_size_bytes: int = 2048
    max_retransmissions: int = 5
    modcod_policy: str = "adaptive"
    fixed_modcod: str = "QPSK_3/4"


def station_radio(rng: np.random.Generator, station: Station) -> StationRadio:
    return StationRadio(
        noise_figure_dB=float(rng.uniform(0.7, 1.8)),
        other_losses_dB=float(rng.uniform(0.9, 2.3)),
        implementation_loss_dB=float(rng.uniform(1.0, 2.0)),
        pointing_error_deg=float(rng.uniform(0.02, 0.2)),
        interference_offset_dB=float(rng.normal(-8, 2) + (6 if "high_rfi" in station.climate_zone else 0)),
    )


@dataclass
class LinkState:
    """Nominal conditions at one instant (no fading)."""

    distance_km: float
    elevation_deg: float
    pointing_loss_dB: float
    fspl_dB: float
    rain_dB: float
    cloud_dB: float
    gas_dB: float
    rx_dBm: float
    noise_dBm: float
    tsys_K: float
    interference_dBm: float
    scint_std_dB: float
    snr_dB: float
    sinr_dB: float
    ground_gain_dBi: float


def _db_sum(*dbm: float) -> float:
    return 10 * math.log10(sum(10 ** (x / 10) for x in dbm))


def link_state(sat: SatRadio, st: Station, gs: StationRadio, wx: Weather, distance_km: float, elevation_deg: float) -> LinkState:
    f_ghz = sat.frequency_GHz
    lam = C_M_S / (f_ghz * 1e9)
    sin_el = math.sin(math.radians(max(elevation_deg, 1.0)))
    ground_gain = 10 * math.log10(0.632 * (math.pi * st.dish_m / lam) ** 2)
    beam_ground = 70 * lam / st.dish_m
    beam_sat = math.sqrt(32400 / 10 ** (sat.antenna_gain_dBi / 10))
    pointing = 12 * 2 * ((gs.pointing_error_deg / beam_ground) ** 2 + (sat.attitude_error_deg / beam_sat) ** 2)
    fspl = 20 * math.log10(distance_km) + 20 * math.log10(f_ghz * 1000) + 32.45

    gamma_rain = 0.00454 * wx.rain_rate_mm_h**1.327  # dB/km at ~8 GHz
    rain_path = RAIN_HEIGHT_KM.get(st.climate_zone, 4.5) / sin_el
    rain = gamma_rain * rain_path / (1 + rain_path / 35)
    cloud = 0.0636 * wx.cloud_lwc_g_m3 * wx.cloud_thickness_km / sin_el
    gas = (0.043 + 0.0015 * wx.water_vapor_density) / sin_el
    atm = rain + cloud + gas

    tsys = 290 * (10 ** (gs.noise_figure_dB / 10) - 1) + 15 + 275 * (1 - 10 ** (-atm / 10))
    noise = -198.6 + 10 * math.log10(tsys) + 10 * math.log10(sat.bandwidth_MHz * 1e6)
    rx = 10 * math.log10(sat.tx_power_W * 1000) + sat.antenna_gain_dBi + ground_gain - fspl - atm - pointing - gs.other_losses_dB
    interference = noise + gs.interference_offset_dB
    scint = 0.0064 * (1 + 0.4 * wx.turbulence) * f_ghz ** (7 / 12) / sin_el**1.2
    return LinkState(distance_km, elevation_deg, pointing, fspl, rain, cloud, gas, rx, noise, tsys, interference, scint,
                     rx - noise, rx - _db_sum(noise, interference), ground_gain)


def pick_modcod(sat: SatRadio, sinr_dB: float, impl_loss_dB: float) -> str:
    if sat.modcod_policy != "adaptive":
        return sat.fixed_modcod
    best = "QPSK_1/4"
    for name, (_, threshold) in MODCODS.items():
        if sinr_dB - impl_loss_dB - threshold >= ADAPTIVE_BACKOFF_DB:
            best = name
    return best


def packet_error_prob(margin_dB: float) -> float:
    return 1 / (1 + math.exp(min(60.0, 6 * (margin_dB + 0.2))))


def goodput_Mbps(sat: SatRadio, modcod: str, sinr_dB: float, impl_loss_dB: float) -> tuple[float, float, float]:
    """(effective rate Mbit/s, packet error probability, margin dB) for one instant."""
    eff, threshold = MODCODS[modcod]
    margin = sinr_dB - impl_loss_dB - threshold
    per = packet_error_prob(margin)
    rate = sat.bandwidth_MHz * eff
    return rate * (1 - per) * (1 - HEADER_BYTES / sat.packet_size_bytes), per, margin


@dataclass
class Contact:
    """One pass with frozen weather: produces model features and plays out real transfers."""

    orbit: Orbit
    pass_: Pass
    sat: SatRadio
    gs: StationRadio
    weather: Weather
    fade_dB: float = 0.0  # slow, unforecast fade (rain cell drift); drawn per contact
    forecast_step_s: float = 10.0
    _cache: dict = field(default_factory=dict)

    def state_at(self, t: datetime) -> LinkState:
        elevation, distance = self.pass_.station.look(self.orbit.ecef(t))
        return link_state(self.sat, self.pass_.station, self.gs, self.weather, distance, elevation)

    def features(self, t: datetime, file_size_MB: float, data_class: str) -> dict:
        p, now = self.pass_, self.state_at(t)
        impl = self.gs.implementation_loss_dB
        modcod = pick_modcod(self.sat, now.sinr_dB, impl)
        eff_rate, per, margin = goodput_Mbps(self.sat, modcod, now.sinr_dB, impl)
        remaining = max(1.0, (p.los - t).total_seconds())

        window_sinr, window_rate = [], []
        steps = max(1, int(remaining // self.forecast_step_s))
        for k in range(steps):
            s = self.state_at(t + timedelta(seconds=k * self.forecast_step_s))
            window_sinr.append(s.sinr_dB)
            window_rate.append(goodput_Mbps(self.sat, modcod, s.sinr_dB, impl)[0])
        mean_rate = float(np.mean(window_rate))
        capacity_MB = mean_rate * remaining / 8
        transfer_s = min(1e6, file_size_MB * 8 / mean_rate) if mean_rate > 0 else 1e6
        wx = self.weather
        return {
            "snr_dB": now.snr_dB,
            "sinr_dB": now.sinr_dB,
            "received_power_dBm": now.rx_dBm,
            "noise_power_dBm": now.noise_dBm,
            "system_noise_temp_K": now.tsys_K,
            "link_margin_dB": margin,
            "predicted_mean_sinr_window_dB": float(np.mean(window_sinr)),
            "predicted_min_sinr_window_dB": float(np.min(window_sinr)),
            "interference_power_dBm": now.interference_dBm,
            "estimated_packet_error_prob": per,
            "scintillation_std_dB": now.scint_std_dB,
            "capacity_ratio": capacity_MB / file_size_MB,
            "time_ratio": transfer_s / remaining,
            "estimated_transfer_time_s": transfer_s,
            "estimated_window_capacity_MB": capacity_MB,
            "window_mean_effective_rate_Mbps": mean_rate,
            "effective_rate_Mbps": eff_rate,
            "estimated_data_rate_Mbps": self.sat.bandwidth_MHz * MODCODS[modcod][0],
            "file_size_MB": file_size_MB,
            "file_packets": math.ceil(file_size_MB * 1e6 / (self.sat.packet_size_bytes - HEADER_BYTES)),
            "packet_size_bytes": self.sat.packet_size_bytes,
            "max_retransmissions": self.sat.max_retransmissions,
            "mission_data_class": data_class,
            "modcod_policy": self.sat.modcod_policy,
            "modulation_coding": modcod,
            "remaining_window_s": remaining,
            "time_since_aos_s": max(0.0, (t - p.aos).total_seconds()),
            "pass_duration_s": p.duration_s,
            "pass_max_elevation_deg": p.max_elevation_deg,
            "elevation_mask_deg": p.station.elevation_mask_deg,
            "distance_km": now.distance_km,
            "elevation_angle_deg": now.elevation_deg,
            "satellite_altitude_km": self.orbit.altitude_km,
            "rain_rate_mm_h": wx.rain_rate_mm_h,
            "cloud_liquid_water_g_m3": wx.cloud_lwc_g_m3,
            "cloud_thickness_km": wx.cloud_thickness_km,
            "relative_humidity_pct": wx.relative_humidity_pct,
            "water_vapor_density_g_m3": wx.water_vapor_density,
            "atmospheric_turbulence_index": wx.turbulence,
            "temperature_C": wx.temperature_C,
            "weather_state": wx.state,
            "rain_attenuation_dB": now.rain_dB,
            "cloud_attenuation_dB": now.cloud_dB,
            "gaseous_attenuation_dB": now.gas_dB,
            "total_atmospheric_loss_dB": now.rain_dB + now.cloud_dB + now.gas_dB,
            "station_climate_zone": p.station.climate_zone,
            "frequency_GHz": self.sat.frequency_GHz,
            "bandwidth_MHz": self.sat.bandwidth_MHz,
            "tx_power_W": self.sat.tx_power_W,
            "satellite_antenna_gain_dBi": self.sat.antenna_gain_dBi,
            "ground_antenna_diameter_m": p.station.dish_m,
            "ground_station_antenna_gain_dBi": now.ground_gain_dBi,
            "antenna_pointing_error_deg": self.gs.pointing_error_deg,
            "satellite_attitude_error_deg": self.sat.attitude_error_deg,
            "receiver_noise_figure_dB": self.gs.noise_figure_dB,
            "other_losses_dB": self.gs.other_losses_dB,
            "implementation_loss_dB": impl,
            "pointing_loss_dB": now.pointing_loss_dB,
            "free_space_path_loss_dB": now.fspl_dB,
        }


@dataclass
class Transfer:
    """A file being downlinked; advanced tick by tick under the real (faded) channel."""

    item_id: str
    attempt_id: str | None
    size_MB: float
    modcod: str
    started: datetime
    delivered_MB: float = 0.0
    retransmissions: float = 0.0
    outage_s: float = 0.0
    active_s: float = 0.0
    sinr_sum: float = 0.0
    file_packets: int = 1
    max_retransmissions: int = 5

    def advance(self, contact: Contact, t: datetime, dt: float, rng: np.random.Generator) -> float:
        """Transmit for dt seconds; returns MB delivered this step."""
        s = contact.state_at(t)
        sinr = s.sinr_dB - contact.fade_dB + rng.normal(0, s.scint_std_dB) + rng.normal(0, 0.6)
        rate, per, _ = goodput_Mbps(contact.sat, self.modcod, sinr, contact.gs.implementation_loss_dB)
        packets = contact.sat.bandwidth_MHz * MODCODS[self.modcod][0] * 1e6 * dt / 8 / contact.sat.packet_size_bytes
        # Selective-repeat ARQ: lost packets are resent later under fresh channel conditions,
        # so the cost of a bad step is retransmissions (and time), not an immediate file loss.
        self.retransmissions += packets * per
        self.active_s += dt
        self.sinr_sum += sinr * dt
        if per > 0.9:
            self.outage_s += dt
        step = min(rate * dt / 8, self.size_MB - self.delivered_MB)
        self.delivered_MB += step
        return step

    @property
    def retry_budget_exhausted(self) -> bool:
        """On average every packet has used half its allowed retransmissions: the file is abandoned."""
        return self.retransmissions > 0.5 * self.max_retransmissions * self.file_packets

    @property
    def done(self) -> bool:
        return self.delivered_MB >= self.size_MB - 1e-9

    @property
    def mean_sinr(self) -> float | None:
        return self.sinr_sum / self.active_s if self.active_s else None

    def failure_reason(self, window_closed: bool) -> str:
        if self.done:
            return "success"
        if self.active_s and self.outage_s / self.active_s > 0.5:
            return "link_outage"
        if self.retry_budget_exhausted:
            return "excessive_packet_errors"
        if window_closed:
            return "combined_time_and_link" if self.outage_s > 0 else "insufficient_contact_time"
        return "aborted"
