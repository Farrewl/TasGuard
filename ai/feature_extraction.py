"""Satu-satunya definisi fitur TasGuard (dipakai generator, training, dan server).

Hanya bergantung pada numpy supaya aman diimpor server FastAPI.

Skala masukan (harus sama di semua jalur):
  light  : tanpa satuan, kira-kira 0..1. Kamera HP = luma / 255 (lihat
           ``normalize_light``). Bukan lux terkalibrasi.
  accel  : magnitudo akselerasi (m/s^2) dari accelerationIncludingGravity.
  Fitur dihitung relatif terhadap baseline (median periode kalibrasi), sehingga
  hanya selisih terhadap baseline yang penting, bukan level absolut.
"""
from __future__ import annotations

from typing import Dict, Iterable, Optional

import numpy as np

# 14 fitur yang dihasilkan dataset (urutan kolom CSV).
ALL_FEATURES = [
    "light_mean", "light_max", "light_min", "light_delta",
    "light_change_rate", "light_elevated_duration", "bright_ratio",
    "accel_mean", "accel_max", "accel_std", "accel_change_rate",
    "motion_duration", "motion_event_count", "light_motion_coincidence",
]
# Fitur absolut cahaya bergantung pada skala/sensor, jadi tidak dipakai model.
EXCLUDED_FROM_MODEL = ("light_mean", "light_max", "light_min")
MODEL_FEATURES = [f for f in ALL_FEATURES if f not in EXCLUDED_FROM_MODEL]

SAMPLING_FREQ = 5.0      # Hz
LIGHT_EVIDENCE_S = 0.4   # detik cahaya naik dalam satu window agar dianggap TAS DIBUKA
# Kenaikan cahaya minimum di atas baseline (skala 0..1) yang dihitung "naik"; 0,04 = kira-kira 10 luma.
# Sebelumnya 0,08 (20 luma), tetapi rekaman nyata tas dibuka sedikit atau miring hanya naik ~25 luma.
# Tas tertutup di dalam tas gelap tidak pernah naik lebih dari 0 luma pada rekaman nyata.
LIGHT_MARGIN = 0.04
WINDOW_SAMPLES = 15      # 3 detik x 5 Hz
CAMERA_LUMA_MAX = 255.0


def normalize_light(value: float, source: str) -> Optional[float]:
    """Ubah nilai cahaya mentah HP menjadi skala model (~0..1).

    Hanya sumber ``camera`` (luma 0..255) yang didukung model. ``ambient_sensor``
    (lux) tidak punya padanan skala di dataset sehingga mengembalikan None.
    """
    if source == "camera":
        return float(min(max(value, 0.0), CAMERA_LUMA_MAX) / CAMERA_LUMA_MAX)
    return None


def count_true_runs(mask: np.ndarray) -> int:
    if len(mask) == 0:
        return 0
    return int(np.count_nonzero(mask & ~np.r_[False, mask[:-1]]))


def compute_features(light: Iterable[float], accel: Iterable[float],
                     cal_light: Iterable[float], cal_accel: Iterable[float],
                     sampling_freq: float = SAMPLING_FREQ) -> Dict[str, float]:
    """Hitung 14 fitur sebuah window dengan baseline dari sampel kalibrasi."""
    light = np.asarray(light, dtype=float)
    accel = np.asarray(accel, dtype=float)
    cal_light = np.asarray(cal_light, dtype=float)
    cal_accel = np.asarray(cal_accel, dtype=float)

    light_base = float(np.median(cal_light))
    accel_base = float(np.median(cal_accel))
    light_mad = float(np.median(np.abs(cal_light - light_base)) * 1.4826)
    accel_mad = float(np.median(np.abs(cal_accel - accel_base)) * 1.4826)
    light_elevated_threshold = light_base + max(LIGHT_MARGIN, 2.5 * light_mad)
    bright_threshold = light_base + max(0.20, 4.0 * light_mad)
    motion_threshold = max(0.25, 3.0 * accel_mad)

    light_elevated = light > light_elevated_threshold
    bright = light > bright_threshold
    motion = np.abs(accel - accel_base) > motion_threshold
    light_motion = light_elevated & motion

    light_diff_rate = (float(np.mean(np.abs(np.diff(light))) * sampling_freq)
                       if len(light) > 1 else 0.0)
    accel_diff_rate = (float(np.mean(np.abs(np.diff(accel))) * sampling_freq)
                       if len(accel) > 1 else 0.0)

    return {
        "light_mean": float(np.mean(light)),
        "light_max": float(np.max(light)),
        "light_min": float(np.min(light)),
        "light_delta": float(light[-1] - light[0]),
        "light_change_rate": light_diff_rate,
        "light_elevated_duration": float(np.count_nonzero(light_elevated) / sampling_freq),
        "bright_ratio": float(np.mean(bright)),
        "accel_mean": float(np.mean(accel)),
        "accel_max": float(np.max(accel)),
        "accel_std": float(np.std(accel, ddof=0)),
        "accel_change_rate": accel_diff_rate,
        "motion_duration": float(np.count_nonzero(motion) / sampling_freq),
        "motion_event_count": float(count_true_runs(motion)),
        "light_motion_coincidence": float(np.mean(light_motion)),
    }


def feature_vector(features: Dict[str, float],
                   columns: Iterable[str] = MODEL_FEATURES) -> list:
    """Urutkan dict fitur menjadi vektor sesuai urutan kolom model."""
    return [float(features[c]) for c in columns]
