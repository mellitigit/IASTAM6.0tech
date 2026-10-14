"""Multispectral payload: synthetic 6-band quicklooks plus injected sensor faults.

Bands follow the backend contract: B04 red, B03 green, B02 blue, B08 NIR, B11 SWIR1, B12 SWIR2,
reflectance in [0, 1]. Scenes are smooth mixtures of material spectra, not noise.
"""

import numpy as np

SPECTRA = {
    "vegetation": [0.05, 0.08, 0.04, 0.35, 0.20, 0.10],
    "soil": [0.18, 0.15, 0.12, 0.28, 0.32, 0.25],
    "water": [0.03, 0.05, 0.07, 0.02, 0.01, 0.005],
    "urban": [0.15, 0.14, 0.13, 0.20, 0.22, 0.20],
    "cloud": [0.72, 0.73, 0.76, 0.70, 0.45, 0.30],
    "snow": [0.85, 0.87, 0.90, 0.75, 0.10, 0.08],
    "burn_scar": [0.06, 0.05, 0.04, 0.08, 0.25, 0.30],
    "active_fire": [0.18, 0.10, 0.08, 0.25, 0.80, 0.97],
    "flood": [0.12, 0.13, 0.11, 0.06, 0.03, 0.02],
}

# Ordinary scenes and the materials they mix.
SCENES = {
    "cropland": ["vegetation", "soil"],
    "forest": ["vegetation", "vegetation", "soil"],
    "coast": ["water", "soil", "vegetation"],
    "city": ["urban", "vegetation", "soil"],
    "desert": ["soil", "soil", "urban"],
    "cloudy": ["cloud", "vegetation", "soil"],
    "arctic": ["snow", "water", "soil"],
}
# Earth events the payload should flag as novel.
EVENTS = {
    "wildfire": ["active_fire", "burn_scar", "vegetation"],
    "flood": ["flood", "flood", "urban"],
}

BAND_NAMES = ["B04", "B03", "B02", "B08", "B11", "B12"]


def _field(rng: np.random.Generator, size: int, waves: int = 3) -> np.ndarray:
    yy, xx = np.mgrid[0:size, 0:size] / size
    f = np.zeros((size, size))
    for _ in range(waves):
        kx, ky = rng.uniform(-2.5, 2.5, 2)
        f += rng.uniform(0.5, 1.0) * np.sin(2 * np.pi * (kx * xx + ky * yy) + rng.uniform(0, 2 * np.pi))
    return f


def make_scene(rng: np.random.Generator, kind: str, size: int = 128) -> np.ndarray:
    materials = (SCENES | EVENTS)[kind]
    logits = np.stack([_field(rng, size) * rng.uniform(1.5, 3.0) for _ in materials])
    weights = np.exp(logits - logits.max(axis=0))
    weights /= weights.sum(axis=0)
    spectra = np.array([SPECTRA[m] for m in materials])  # (k, 6)
    image = np.einsum("khw,kb->bhw", weights, spectra)
    image *= rng.uniform(0.9, 1.1)  # illumination / sun angle
    image += rng.normal(0, 0.004, image.shape)  # detector noise
    return np.clip(image, 0, 1).astype(np.float32)


# Sub-pixel heterogeneity, stronger in NIR/SWIR. Smooth mixtures alone are too flat once dimmed:
# their visible bands fall under the watchdog's dead-detector variance floor (1e-4).
TEXTURE_BAND_WEIGHT = np.array([1.1, 1.1, 1.1, 2.0, 1.7, 1.5])[:, None, None]


def add_texture(image: np.ndarray, rng: np.random.Generator, amplitude: float = 0.02) -> np.ndarray:
    c, h, w = image.shape
    t = np.zeros((h, w))
    for scale in (2, 4, 8):
        t += np.kron(rng.normal(0, 1, (-(-h // scale), -(-w // scale))), np.ones((scale, scale)))[:h, :w] / 3
    return np.clip(image + amplitude * t[None] * TEXTURE_BAND_WEIGHT, 0, 1).astype(np.float32)


# ---- faults -----------------------------------------------------------------------------------


def dead_band(image: np.ndarray, band: int, level: float = 0.3) -> np.ndarray:
    image[band] = level
    return image


def saturate_band(image: np.ndarray, band: int, fraction: float, rng: np.random.Generator) -> np.ndarray:
    mask = rng.random(image.shape[1:]) < fraction
    image[band][mask] = 1.0
    return image


def bit_flip(image: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    b, y, x = rng.integers(0, image.shape[0]), rng.integers(0, image.shape[1]), rng.integers(0, image.shape[2])
    image[b, y, x] = np.nan
    return image


def thumbnail(image: np.ndarray, factor: int = 4) -> np.ndarray:
    c, h, w = image.shape
    return image[:, : h - h % factor, : w - w % factor].reshape(c, h // factor, factor, w // factor, factor).mean(axis=(2, 4))
