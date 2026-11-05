#!/usr/bin/env python3
"""Pembangkit dataset sintetis TasGuard (reproducible, seed tetap).

Ini simulator tingkat-magnitudo yang sederhana, BUKAN model fisik sensor dan
BUKAN sumber label pencurian nyata. Pola di bawah adalah ASUMSI tim, tetapi besaran
sinyalnya dikalibrasi dari rekaman HP nyata (``kalibrasi_nyata.json``, dibuat oleh
``fit_simulator.py``): baseline tas gelap, besar dan lama kenaikan cahaya saat tas
dibuka, serta besar gerakan normal dan gerakan saat tas dibuka. Datasetnya tetap
sintetis; rekaman nyata tidak disalin ke dalamnya.

Konsep: barang di dalam tas hanya bisa diambil bila TAS DIBUKA. HP penjaga ada di
dalam tas yang gelap, jadi tas dibuka = cahaya naik jelas. Getaran saja (tas
berjalan, terguncang di kendaraan, terbentur) tidak dianggap mencurigakan.

  NORMAL      : tas diam; dibawa berjalan; dipegang/diangkat (gerakan pendek);
                bergetar atau terguncang kuat TANPA dibuka (kendaraan, tas
                dijatuhkan, dibenturkan).
  SUSPICIOUS  : tas DIBUKA (cahaya naik jelas, 1,4-4,5 detik) disertai: gerakan
                tangan, mengaduk isi berulang, menarik barang dengan kuat, atau
                kombinasi beberapa komponen.

Cara kerja:
  * satu sesi = 7 detik @ 5 Hz (35 sampel); detik pertama bebas aktivitas
    dan dipakai sebagai kalibrasi baseline;
  * window 3 detik, stride 1 detik (5 window per sesi);
  * label window = SUSPICIOUS bila >= 40 % window beririsan dengan kejadian
    mencurigakan (``event_fraction``); selain itu NORMAL;
  * artefak sensor (lonjakan, nilai macet, dropout, clipping) disuntikkan
    pada kedua kelas dengan peluang sama dan dicatat di log artefak;
  * split train/validation/test dilakukan per SESI (stratified per skenario),
    bukan per baris, agar window satu sesi tidak bocor antar split.

Rumus fitur ada di ``feature_extraction.py`` (dipakai bersama oleh generator,
training, dan server).

Pemakaian:  python ai/generate_dataset.py [--sessions-per-scenario 500] [--kalibrasi ai/kalibrasi_nyata.json]
Keluaran :  ai/dataset/ (tiga CSV + dataset_config.json)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import feature_extraction as fe  # noqa: E402

SEED = 20261105
LIGHT_MIN, LIGHT_MAX = 0.0, 1.0          # satuan simulasi ternormalisasi, BUKAN lux
ACCEL_MIN, ACCEL_MAX = 0.0, 78.48        # skala penuh 8 g; rekaman HP nyata jenuh di 78,44 m/s^2
CALIBRATION_FILE = HERE / "kalibrasi_nyata.json"
P_DARK_BAG = 0.5                         # peluang sesi berbaseline tas gelap (keputusan desain, bukan hasil ukur)
PILOT_SESSIONS = 150                     # sesi percobaan per skenario untuk menyamakan besar gerakan
CALIBRATION_ROUNDS = 6                   # putaran penyesuaian skala gerakan
ACCEL_SCALE_MAX = 25.0                   # batas atas skala; normal_handling mentok di sini (letupan simulasi lebih singkat dari nyata)
RAW_FILE = "tasguard_raw_samples.csv"
WINDOW_FILE = "tasguard_window_features.csv"
ARTIFACT_FILE = "tasguard_artifact_log.csv"
CONFIG_FILE = "dataset_config.json"
ARTIFACT_COLUMNS = ["session_id", "sensor", "artifact_type", "start_sample", "n_samples", "magnitude"]
P_BACKGROUND_WALK = 0.15   # peluang berjalan biasa terjadi di dalam sesi mencurigakan
SCENARIOS = {
    "normal_idle": "NORMAL",
    "normal_walking": "NORMAL",
    "normal_handling": "NORMAL",
    "normal_vibration": "NORMAL",
    "suspicious_open_move": "SUSPICIOUS",
    "suspicious_open_rummage": "SUSPICIOUS",
    "suspicious_open_pull": "SUSPICIOUS",
    "suspicious_combined": "SUSPICIOUS",
}
RAW_COLUMNS = [
    "session_id", "scenario", "scenario_label", "timestamp_sec",
    "light_value", "accel_mag", "event_active",
    "ordinary_activity_active", "split",
]
FEATURE_COLUMNS = list(fe.ALL_FEATURES)
WINDOW_COLUMNS = [
    "session_id", "scenario", "window_id", "window_start_sec",
    "window_end_sec", *FEATURE_COLUMNS, "event_fraction", "label", "split",
]


@dataclass
class Config:
    sessions_per_scenario: int = 500
    sampling_freq: float = 5.0
    duration_sec: float = 7.0
    window_sec: float = 3.0
    stride_sec: float = 1.0
    label_threshold: float = 0.40
    seed: int = SEED
    calibration_path: Path = CALIBRATION_FILE


@dataclass
class Calibration:
    """Besaran sinyal yang diukur dari rekaman HP nyata (lihat fit_simulator.py).

    ``accel_scale`` bukan hasil ukur langsung: ia dihitung ulang tiap pembuatan dataset dengan
    mensimulasikan sesi percobaan, lalu membagi persentil ke-95 simpangan akselerasi NYATA
    dengan yang SIMULASI per skenario."""
    dark_baseline_max: float                     # baseline tas gelap (luma/255), persentil ke-90
    open_amp_median: float                       # kenaikan cahaya maksimum saat tas dibuka (lognormal)
    open_amp_sigma: float
    open_dur_median: float                       # lama satu bukaan, detik (lognormal)
    open_dur_sigma: float
    accel_p95_real: Dict[str, float]             # per label nyata, m/s^2
    source: dict = field(default_factory=dict)
    accel_scale: Dict[str, float] = field(default_factory=dict)


_CAL: Optional[Calibration] = None   # diisi generate_dataset() sebelum sesi disimulasikan


def load_calibration(path: Path) -> Calibration:
    if not Path(path).exists():
        raise FileNotFoundError(
            f"{path} tidak ada. Jalankan `python ai/fit_simulator.py` pada rekaman nyata (sensor_log.csv) "
            f"atau salin berkas kalibrasi dari repositori.")
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Calibration(
        dark_baseline_max=float(d["cahaya"]["baseline_tas_gelap_p90"]),
        open_amp_median=float(d["cahaya"]["bukaan_amplitudo"]["median"]),
        open_amp_sigma=float(d["cahaya"]["bukaan_amplitudo"]["sigma"]),
        open_dur_median=float(d["cahaya"]["bukaan_durasi_s"]["median"]),
        open_dur_sigma=float(d["cahaya"]["bukaan_durasi_s"]["sigma"]),
        accel_p95_real={k: float(v) for k, v in d["akselerasi"]["p95_nyata"].items()},
        source={"berkas": Path(path).name, "pembagian_rekaman": d.get("pembagian_rekaman", {}),
                "segmen_dipakai": d.get("segmen_dipakai", {})},
    )


def _samples_for(seconds: float, fs: float, name: str) -> int:
    value = seconds * fs
    rounded = int(round(value))
    if rounded < 1 or not np.isclose(value, rounded, atol=1e-8):
        raise ValueError(f"{name} * sampling_freq harus bilangan bulat positif; didapat {value}")
    return rounded


def _allocate_split_counts(n: int) -> Tuple[int, int, int]:
    """Pembagian 70/15/15 metode sisa terbesar, jumlahnya tepat n."""
    raw = np.array([0.70, 0.15, 0.15]) * n
    base = np.floor(raw).astype(int)
    remainder = n - int(base.sum())
    order = np.argsort(-(raw - base))
    for i in order[:remainder]:
        base[i] += 1
    return tuple(int(x) for x in base)


def make_session_ids(config: Config) -> Dict[Tuple[str, int], str]:
    """ID sesi anonim dan diacak, sehingga ID tidak memuat info skenario."""
    rng = np.random.default_rng(config.seed + 31)
    pairs = [(s, i) for s in SCENARIOS for i in range(config.sessions_per_scenario)]
    order = rng.permutation(len(pairs))
    return {pairs[int(j)]: f"S{rank:05d}" for rank, j in enumerate(order)}


def make_session_splits(config: Config, id_map: Dict[Tuple[str, int], str]) -> Dict[str, str]:
    """Stratifikasi per skenario; satu sesi utuh hanya masuk satu split."""
    rng = np.random.default_rng(config.seed + 17)
    mapping: Dict[str, str] = {}
    n_train, n_val, n_test = _allocate_split_counts(config.sessions_per_scenario)
    labels = ["train"] * n_train + ["validation"] * n_val + ["test"] * n_test
    for scenario in SCENARIOS:
        ids = [id_map[(scenario, i)] for i in range(config.sessions_per_scenario)]
        order = rng.permutation(len(ids))
        for idx, split in zip(order, labels):
            mapping[ids[int(idx)]] = split
    return mapping


# Komponen sinyal

def _raised_cosine_gate(t: np.ndarray, start: float, duration: float,
                        ramp_sec: float) -> np.ndarray:
    """Selubung halus naik/tahan/turun bernilai 0..1."""
    if duration <= 0:
        return np.zeros_like(t)
    ramp = max(0.04, min(ramp_sec, duration / 2.0))
    attack_u = np.clip((t - start) / ramp, 0.0, 1.0)
    release_u = np.clip((start + duration - t) / ramp, 0.0, 1.0)
    attack = 0.5 - 0.5 * np.cos(np.pi * attack_u)
    release = 0.5 - 0.5 * np.cos(np.pi * release_u)
    inside = (t >= start) & (t <= start + duration)
    return np.where(inside, np.minimum(attack, release), 0.0)


def _add_light_exposure(light_delta: np.ndarray, t: np.ndarray,
                        rng: np.random.Generator, start: float,
                        duration: float, amplitude: float,
                        ramp: float | None = None) -> None:
    ramp_sec = ramp if ramp is not None else float(rng.uniform(0.12, 0.55))
    light_delta += amplitude * _raised_cosine_gate(t, start, duration, ramp_sec)


def _add_motion_bout(accel_delta: np.ndarray, t: np.ndarray,
                     rng: np.random.Generator, start: float, duration: float,
                     amplitude: float, frequency: float | None = None) -> None:
    """Penyimpangan magnitudo berosilasi (bertanda), bukan offset positif tetap.

    Magnitudo hanya melihat bagian gerak yang terproyeksi ke sumbu gravitasi,
    sehingga tiap gerakan diskalakan kopling acak di [0,25 - 1]."""
    freq = frequency if frequency is not None else float(rng.uniform(1.0, 3.2))
    phase = float(rng.uniform(0, 2 * np.pi))
    coupling = float(rng.uniform(0.25, 1.0))
    gate = _raised_cosine_gate(t, start, duration, min(0.24, duration / 3.0))
    local_t = np.maximum(0.0, t - start)
    wave = np.sin(2 * np.pi * freq * local_t + phase)
    wave += 0.28 * np.sin(2 * np.pi * (freq * 1.85) * local_t + phase * 0.63)
    accel_delta += coupling * amplitude * gate * wave


def _add_jolt(accel_delta: np.ndarray, t: np.ndarray, rng: np.random.Generator,
              center: float, amplitude: float) -> None:
    """Sentakan singkat bertanda, diikuti pantulan balik yang lebih kecil."""
    sign = float(rng.choice([-1.0, 1.0]))
    coupling = float(rng.uniform(0.3, 1.0))
    first = np.exp(-0.5 * ((t - center) / 0.075) ** 2)
    rebound = np.exp(-0.5 * ((t - (center + 0.17)) / 0.11) ** 2)
    accel_delta += sign * coupling * amplitude * (first - 0.52 * rebound)


def _log_uniform(rng: np.random.Generator, lo: float, hi: float) -> float:
    return float(np.exp(rng.uniform(np.log(lo), np.log(hi))))


def _add_walk(accel_delta: np.ndarray, t: np.ndarray, rng: np.random.Generator,
              start: float, end: float, amplitude: float) -> None:
    cadence = float(rng.uniform(1.45, 2.25))
    phase = float(rng.uniform(0, 2 * np.pi))
    local = np.maximum(0.0, t - start)
    phase_track = 2 * np.pi * (cadence * local + 0.035 * np.sin(2 * np.pi * 0.22 * local)) + phase
    gait = np.sin(phase_track) + 0.22 * np.sin(2 * phase_track + 0.6)
    gait *= 1.0 + 0.25 * rng.normal(0.0, 0.5, len(t))     # ketidakteraturan antar langkah
    gate = _raised_cosine_gate(t, start, end - start, 0.28)
    accel_delta += amplitude * gate * gait


def _interval_mask(t: np.ndarray, intervals: List[Tuple[float, float]]) -> np.ndarray:
    mask = np.zeros(t.shape, dtype=bool)
    for start, end in intervals:
        mask |= (t >= start) & (t < end)
    return mask


# Jadwal dan aktivitas per skenario

def make_schedule(scenario: str, rng: np.random.Generator, duration: float
                  ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
    """Kembalikan (interval kejadian mencurigakan, interval aktivitas biasa)."""
    events: List[Tuple[float, float]] = []
    ordinary: List[Tuple[float, float]] = []

    def place(dmin: float, dmax: float, smin: float) -> Tuple[float, float]:
        dur = float(rng.uniform(dmin, dmax))
        start = float(rng.uniform(smin, duration - dur - 0.1))
        return start, start + dur

    # Detik pertama dicadangkan untuk kalibrasi yang bersih dari aktivitas.
    if scenario == "normal_idle":
        pass
    elif scenario == "normal_walking":
        ordinary = [(1.0, duration)]
    elif scenario == "normal_handling":
        for _ in range(int(rng.integers(1, 4))):
            start = float(rng.uniform(1.15, duration - 0.75))
            ordinary.append((start, min(duration, start + float(rng.uniform(0.2, 0.6)))))
    elif scenario == "normal_vibration":
        ordinary = [place(1.5, 4.5, 1.0)]
    elif scenario == "suspicious_open_move":
        events = [place(1.4, 4.0, 1.1)]
    elif scenario == "suspicious_open_rummage":
        events = [place(1.6, 4.4, 1.1)]
    elif scenario == "suspicious_open_pull":
        events = [place(1.4, 4.0, 1.1)]
    elif scenario == "suspicious_combined":
        events = [place(1.8, 4.5, 1.1)]
    else:
        raise ValueError(f"Skenario tidak dikenal: {scenario}")
    if events and rng.random() < P_BACKGROUND_WALK:
        # Berjalan biasa bisa terjadi bersamaan dengan kejadian (ambigu secara sengaja).
        ordinary = [(float(rng.uniform(1.0, 2.2)), duration)]
    return events, ordinary


def _add_bag_open(light_delta: np.ndarray, t: np.ndarray, rng: np.random.Generator,
                  start: float, end: float) -> None:
    """Tas dibuka di dalam kejadian: besar dan lama kenaikan cahaya mengikuti rekaman nyata.

    Rekaman nyata menunjukkan tas terbuka hanya sebentar (median kurang dari 1 detik per bukaan)
    dengan kenaikan cahaya bervariasi lebar, jadi bukaan tidak lagi memenuhi hampir seluruh kejadian."""
    cal = _CAL
    event_len = end - start
    amplitude = float(np.clip(cal.open_amp_median * np.exp(rng.normal(0.0, cal.open_amp_sigma)), 0.02, 0.9))
    dur = float(np.clip(cal.open_dur_median * np.exp(rng.normal(0.0, cal.open_dur_sigma)),
                        0.3, max(0.3, event_len)))
    s = start + float(rng.uniform(0.0, max(0.0, event_len - dur))) + float(rng.uniform(-0.15, 0.15))
    s = float(np.clip(s, 1.0, t[-1] - 0.5))
    _add_light_exposure(light_delta, t, rng, s, dur, amplitude, float(rng.uniform(0.1, 0.4)))


def simulate_activity(scenario: str, t: np.ndarray, rng: np.random.Generator,
                      light_delta: np.ndarray, accel_delta: np.ndarray,
                      event_intervals: List[Tuple[float, float]],
                      ordinary_intervals: List[Tuple[float, float]],
                      ambient: float,
                      walk_delta: Optional[np.ndarray] = None) -> None:
    """Tambahkan komponen sinyal yang koheren secara waktu sesuai skenario.

    Amplitudo akselerasi adalah penyimpangan magnitudo (m/s^2) sebelum skala skenario dan gain
    gerak per sesi. Amplitudo cahaya dalam satuan ternormalisasi sebelum gain cahaya per sesi.
    Berjalan di latar belakang sesi mencurigakan ditulis ke ``walk_delta`` (bila diberikan)
    agar diskalakan seperti berjalan biasa, bukan seperti gerakan tangan."""
    U = rng.uniform

    if event_intervals and ordinary_intervals:
        s, e = ordinary_intervals[0]
        _add_walk(accel_delta if walk_delta is None else walk_delta, t, rng, s, e, float(U(0.5, 2.0)))

    if scenario == "normal_idle":
        if rng.random() < 0.35:
            for _ in range(int(rng.integers(1, 3))):
                _add_motion_bout(accel_delta, t, rng, float(U(1.2, 5.6)),
                                 float(U(0.18, 0.7)), float(U(0.05, 0.9)))
        if rng.random() < 0.10:
            _add_jolt(accel_delta, t, rng, float(U(1.2, 6.5)), float(U(0.4, 2.0)))
        return

    if scenario == "normal_walking":
        start, end = ordinary_intervals[0]
        amp = float(U(3.0, 5.0)) if rng.random() < 0.05 else float(U(0.8, 3.0))
        _add_walk(accel_delta, t, rng, start, end, amp * float(U(0.6, 1.0)))
        if rng.random() < 0.10:      # tersandung, tas terbentur sesuatu
            _add_jolt(accel_delta, t, rng, float(U(start + 0.3, max(start + 0.4, end - 0.3))),
                      float(U(2.0, 5.0)))
        if rng.random() < 0.20:      # cahaya merembes lewat kain atau perubahan bayangan (di bawah margin 0,04)
            _add_light_exposure(light_delta, t, rng,
                                float(U(start + 0.3, max(start + 0.31, end - 0.2))),
                                float(U(0.25, 0.8)), float(U(0.01, 0.03)))
        return

    if scenario == "normal_handling":
        for start, end in ordinary_intervals:
            dur = max(0.18, end - start)
            _add_motion_bout(accel_delta, t, rng, start, dur, float(U(0.4, 2.5)))
            if rng.random() < 0.15:
                _add_jolt(accel_delta, t, rng, float(U(start, start + dur)), float(U(0.8, 3.0)))
            if rng.random() < 0.30:   # kedipan cahaya singkat dan kecil, di bawah tanda tas dibuka
                l_start = float(np.clip(start + U(-0.25, 0.45), 1.0, max(1.0, t[-1] - 0.1)))
                _add_light_exposure(light_delta, t, rng, l_start, float(U(0.18, 0.5)),
                                    float(U(0.012, 0.035)))
        return

    if scenario == "normal_vibration":
        # Getaran atau guncangan kuat TANPA tas dibuka: kendaraan, tas dijatuhkan atau dibenturkan.
        for start, end in ordinary_intervals:
            _add_motion_bout(accel_delta, t, rng, start, end - start, _log_uniform(rng, 0.8, 5.0),
                             frequency=float(U(0.8, 3.2)))
            if rng.random() < 0.5:   # getaran kedua yang tumpang tindih
                s2 = float(U(start, max(start + 0.05, end - 0.5)))
                _add_motion_bout(accel_delta, t, rng, s2, float(U(0.5, 1.5)), _log_uniform(rng, 0.8, 5.0),
                                 frequency=float(U(1.0, 3.5)))
        if rng.random() < 0.40:
            _add_jolt(accel_delta, t, rng, float(U(1.2, 6.5)), float(U(2.0, 9.0)))
        if rng.random() < 0.10:      # sedikit cahaya merembes, tetap di bawah tanda tas dibuka
            _add_light_exposure(light_delta, t, rng, float(U(1.2, 5.5)), float(U(0.3, 1.0)),
                                float(U(0.01, 0.03)))
        return

    if scenario == "suspicious_open_move":
        start, end = event_intervals[0]
        dur = end - start
        _add_bag_open(light_delta, t, rng, start, end)
        if rng.random() < 0.85:      # sisanya cukup dengan cahaya: tangan bergerak pelan atau tidak sama sekali
            a_start = float(np.clip(start + U(-0.5, 0.6), 1.0, t[-1] - 0.1))
            _add_motion_bout(accel_delta, t, rng, a_start, dur * float(U(0.5, 0.9)),
                             float(U(0.3, 4.0)))
        if rng.random() < 0.30:
            _add_jolt(accel_delta, t, rng, float(U(start, end)), float(U(2.0, 6.0)))
        return

    if scenario == "suspicious_open_rummage":
        # Tas dibuka dan isinya diaduk: beberapa gerakan berulang.
        start, end = event_intervals[0]
        _add_bag_open(light_delta, t, rng, start, end)
        for _ in range(int(rng.integers(2, 5))):
            s = float(U(start, max(start + 0.05, end - 0.3)))
            d = float(U(0.3, min(0.95, max(0.35, end - s))))
            _add_motion_bout(accel_delta, t, rng, s, d, _log_uniform(rng, 1.0, 5.0),
                             frequency=float(U(0.7, 3.1)))
        if rng.random() < 0.40:
            _add_jolt(accel_delta, t, rng, float(U(start, end)), float(U(2.0, 8.0)))
        return

    if scenario == "suspicious_open_pull":
        # Tas dibuka dan barang ditarik kuat.
        start, end = event_intervals[0]
        dur = end - start
        _add_bag_open(light_delta, t, rng, start, end)
        _add_motion_bout(accel_delta, t, rng, start, dur * float(U(0.6, 0.95)),
                         _log_uniform(rng, 1.5, 6.0), frequency=float(U(0.8, 2.8)))
        if rng.random() < 0.6:       # tarikan kedua yang tidak beraturan
            s2 = float(U(start, max(start + 0.05, end - 0.5)))
            _add_motion_bout(accel_delta, t, rng, s2, float(U(0.4, 0.9)),
                             _log_uniform(rng, 1.5, 6.0), frequency=float(U(1.0, 3.0)))
        if rng.random() < 0.50:
            _add_jolt(accel_delta, t, rng, float(U(start, end)), float(U(2.5, 8.0)))
        return

    if scenario == "suspicious_combined":
        start, end = event_intervals[0]
        _add_bag_open(light_delta, t, rng, start, end)
        for _ in range(int(rng.integers(1, 4))):
            c_start = float(np.clip(start + U(-0.5, 1.0), 1.0, t[-1] - 0.12))
            c_dur = float(U(0.4, min(1.7, max(0.5, end - start))))
            if rng.random() < 0.4:
                _add_light_exposure(light_delta, t, rng, c_start, c_dur, float(U(0.05, 0.4)))
            else:
                _add_motion_bout(accel_delta, t, rng, c_start, c_dur, float(U(1.0, 4.5)))
        if rng.random() < 0.40:
            _add_jolt(accel_delta, t, rng, float(U(start, end)), float(U(2.5, 9.0)))
        return

    raise ValueError(f"Skenario tidak dikenal: {scenario}")


# Artefak sensor

def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def inject_sensor_artifacts(light: np.ndarray, accel: np.ndarray,
                            rng: np.random.Generator
                            ) -> Tuple[np.ndarray, np.ndarray, dict, list]:
    """Suntikkan lonjakan, segmen macet/dropout, dan clipping pada kedua kelas.

    Pembacaan hilang diwakili pengulangan pembacaan sebelumnya, sehingga baris
    CSV tetap berhingga. Log artefak mencatat jenis gangguannya.
    Mengembalikan (light, accel, ringkasan per sesi, daftar kejadian artefak)."""
    light = light.copy()
    accel = accel.copy()
    n = len(light)
    events: list = []
    summary = {
        "light_spikes": 0, "accel_spikes": 0,
        "light_hold_samples": 0, "accel_hold_samples": 0,
        "light_clipped_samples": 0, "accel_clipped_samples": 0,
    }
    if rng.random() < 0.30:
        for idx in rng.choice(np.arange(1, n), size=min(int(rng.integers(1, 3)), n - 1), replace=False):
            delta = float(rng.choice([-1, 1]) * rng.uniform(0.20, 0.58))
            light[idx] += delta
            summary["light_spikes"] += 1
            events.append(("light", "spike", int(idx), 1, delta))
    if rng.random() < 0.31:
        for idx in rng.choice(np.arange(1, n), size=min(int(rng.integers(1, 3)), n - 1), replace=False):
            delta = float(rng.uniform(3.0, 12.0) if rng.random() < 0.80 else -rng.uniform(2.0, 5.0))
            accel[idx] += delta
            summary["accel_spikes"] += 1
            events.append(("accel", "spike", int(idx), 1, delta))

    for name, channel, key in (("light", light, "light_hold_samples"),
                               ("accel", accel, "accel_hold_samples")):
        if rng.random() < 0.17 and n > 5:
            start = int(rng.integers(2, n - 2))
            length = int(rng.integers(1, 4))
            stop = min(n, start + length)
            kind = "held_value" if rng.random() < 0.55 else "dropout_locf"
            channel[start:stop] = channel[start - 1]
            summary[key] += stop - start
            events.append((name, kind, start, stop - start, float(channel[start - 1])))

    light_clip = np.clip(light, LIGHT_MIN, LIGHT_MAX)
    accel_clip = np.clip(accel, ACCEL_MIN, ACCEL_MAX)
    summary["light_clipped_samples"] = int(np.count_nonzero(light != light_clip))
    summary["accel_clipped_samples"] = int(np.count_nonzero(accel != accel_clip))
    for name, arr in (("light", light_clip), ("accel", accel_clip)):
        lo, hi = (LIGHT_MIN, LIGHT_MAX) if name == "light" else (ACCEL_MIN, ACCEL_MAX)
        for s, length in _runs((arr <= lo) | (arr >= hi)):
            events.append((name, "clip", s, length, float(arr[s])))
    return light_clip, accel_clip, summary, events


# Simulasi sesi dan ekstraksi window

def simulate_session(session_id: str, scenario: str, split: str,
                     config: Config, rng: np.random.Generator
                     ) -> Tuple[pd.DataFrame, dict]:
    fs = config.sampling_freq
    n_samples = _samples_for(config.duration_sec, fs, "duration_sec")
    t = np.arange(n_samples, dtype=float) / fs
    event_intervals, ordinary_intervals = make_schedule(scenario, rng, config.duration_sec)

    # Level cahaya sekitar, gain, offset sensor, dan drift lambat khas tiap sesi.
    # Separuh sesi berbaseline tas gelap (HP di dalam tas tertutup: rekaman nyata 0-6 luma, tanpa noise).
    cal = _CAL
    dark_bag = bool(rng.random() < P_DARK_BAG)
    ambient = float(rng.uniform(0.0, cal.dark_baseline_max)) if dark_bag else float(rng.uniform(0.035, 0.31))
    light_gain = float(rng.uniform(0.72, 1.38))
    light_base = ambient * light_gain
    light_drift = float(rng.uniform(-0.018, 0.018)) * (t / max(config.duration_sec, 1e-9))
    light_slow = float(rng.uniform(0.002, 0.012)) * np.sin(
        2 * np.pi * float(rng.uniform(0.08, 0.24)) * t + float(rng.uniform(0, 2 * np.pi)))

    accel_base = float(rng.normal(9.81, 0.16))
    accel_base += float(rng.uniform(-0.10, 0.10))
    accel_drift = float(rng.uniform(-0.07, 0.07)) * (t / max(config.duration_sec, 1e-9))

    light_delta = np.zeros(n_samples, dtype=float)
    accel_delta = np.zeros(n_samples, dtype=float)
    walk_delta = np.zeros(n_samples, dtype=float)
    simulate_activity(scenario, t, rng, light_delta, accel_delta,
                      event_intervals, ordinary_intervals, ambient, walk_delta)
    # Besar gerakan disamakan dengan rekaman nyata per skenario; berjalan di latar belakang
    # memakai skala berjalan biasa.
    accel_delta = (accel_delta * cal.accel_scale.get(scenario, 1.0)
                   + walk_delta * cal.accel_scale.get("normal_walking", 1.0))
    # Cara membawa tas / kekakuan tas per sesi menskalakan semua komponen dinamis.
    accel_delta *= float(np.exp(rng.normal(0.0, 0.3)))
    # Penyimpangan dinamis tidak boleh menarik magnitudo mendekati nol saat pemakaian biasa.
    accel_delta = np.maximum(accel_delta, -0.6 * accel_base)

    # Noise sensor berkorelasi; tingkat noise bervariasi antar sesi.
    light_sigma = float(rng.uniform(0.0, 0.004)) if dark_bag else float(rng.uniform(0.003, 0.018))
    accel_sigma = float(rng.uniform(0.025, 0.105))
    light_white = rng.normal(0, light_sigma, n_samples)
    accel_white = rng.normal(0, accel_sigma, n_samples)
    light_noise = np.zeros(n_samples)
    accel_noise = np.zeros(n_samples)
    for i in range(n_samples):
        light_noise[i] = (0.48 * light_noise[i - 1] if i else 0.0) + light_white[i]
        accel_noise[i] = (0.34 * accel_noise[i - 1] if i else 0.0) + accel_white[i]

    light = light_base + light_drift + light_slow + light_delta * light_gain + light_noise
    accel = accel_base + accel_drift + accel_delta + accel_noise
    light, accel, artifact_summary, artifact_events = inject_sensor_artifacts(light, accel, rng)

    event_active = _interval_mask(t, event_intervals)
    ordinary_active = _interval_mask(t, ordinary_intervals)
    scenario_label = SCENARIOS[scenario]
    raw = pd.DataFrame({
        "session_id": session_id,
        "scenario": scenario,
        "scenario_label": scenario_label,
        "timestamp_sec": t,
        "light_value": light,
        "accel_mag": accel,
        "event_active": event_active.astype(np.int8),
        "ordinary_activity_active": ordinary_active.astype(np.int8),
        "split": split,
    })
    artifact_summary.update({"session_id": session_id, "scenario": scenario,
                             "scenario_label": scenario_label,
                             "events": artifact_events})
    return raw, artifact_summary


def _p95_deviation(raw: pd.DataFrame) -> float:
    """Persentil ke-95 simpangan akselerasi dari baseline (1 detik pertama), sama dengan ukuran di fit_simulator.py."""
    a = raw["accel_mag"].to_numpy(dtype=float)
    return float(np.percentile(np.abs(a[5:] - np.median(a[:5])), 95))


def calibrate_accel_scales(config: Config, cal: Calibration) -> Dict[str, float]:
    """Skala besar gerakan per skenario sehingga p95 simpangan SIMULASI sama dengan p95 NYATA.

    Hubungan skala dan p95 tidak linear (magnitudo tidak bisa turun di bawah nol, gelombang negatif
    terpotong), jadi skala dihitung iteratif dari sesi percobaan. normal_idle dibiarkan (dominan noise
    sensor); suspicious_combined memakai rata-rata tiga skenario tas dibuka. Berjalan sebelum dataset
    dibuat dan memakai aliran acak tersendiri (hasilnya deterministik)."""
    global _CAL
    _CAL = cal
    cal.accel_scale = {name: 1.0 for name in SCENARIOS}
    # Urutan SCENARIOS menempatkan normal_walking sebelum skenario tas dibuka: skala berjalan latar
    # belakang sudah mendekati final saat skenario tas dibuka disimulasikan.
    for putaran in range(CALIBRATION_ROUNDS):
        rng = np.random.default_rng(config.seed + 101)          # aliran sama tiap putaran: perubahan murni karena skala
        for scenario in SCENARIOS:
            if scenario in ("normal_idle", "suspicious_combined") or scenario not in cal.accel_p95_real:
                continue
            sim = float(np.median([_p95_deviation(simulate_session("pilot", scenario, "train", config, rng)[0])
                                   for _ in range(PILOT_SESSIONS)]))
            nyata = cal.accel_p95_real[scenario]
            cal.accel_scale[scenario] = float(np.clip(cal.accel_scale[scenario] * nyata / max(sim, 1e-6),
                                                      0.2, ACCEL_SCALE_MAX))
        tiga = [cal.accel_scale[k] for k in ("suspicious_open_move", "suspicious_open_rummage", "suspicious_open_pull")]
        cal.accel_scale["suspicious_combined"] = float(np.mean(tiga))
    return cal.accel_scale


def extract_window_features(window: pd.DataFrame, calibration: pd.DataFrame,
                            sampling_freq: float) -> dict:
    """Hitung 14 fitur window; kalibrasi = 1,0 detik pertama sesi.

    Rumusnya ada di feature_extraction.py (dipakai juga oleh training/server)."""
    return fe.compute_features(window["light_value"].to_numpy(dtype=float),
                               window["accel_mag"].to_numpy(dtype=float),
                               calibration["light_value"].to_numpy(dtype=float),
                               calibration["accel_mag"].to_numpy(dtype=float),
                               sampling_freq)


def extract_all_windows(raw_df: pd.DataFrame, config: Config) -> pd.DataFrame:
    fs = config.sampling_freq
    win_n = _samples_for(config.window_sec, fs, "window_sec")
    stride_n = _samples_for(config.stride_sec, fs, "stride_sec")
    calibration_n = _samples_for(1.0, fs, "calibration_sec")
    rows: List[dict] = []
    for session_id, session in raw_df.groupby("session_id", sort=False):
        session = session.sort_values("timestamp_sec").reset_index(drop=True)
        if len(session) < calibration_n:
            raise ValueError(f"Sesi {session_id} lebih pendek dari periode kalibrasi")
        calibration = session.iloc[:calibration_n]
        for window_id, start_idx in enumerate(range(0, len(session) - win_n + 1, stride_n)):
            window = session.iloc[start_idx:start_idx + win_n]
            feats = extract_window_features(window, calibration, fs)
            event_fraction = float(window["event_active"].mean())
            label = "SUSPICIOUS" if event_fraction >= config.label_threshold else "NORMAL"
            start_sec = float(window.iloc[0]["timestamp_sec"])
            # Batas akhir eksklusif; sampel terakhir satu interval sampling sebelum batas.
            end_sec = start_sec + config.window_sec
            rows.append({
                "session_id": session_id,
                "scenario": str(session.iloc[0]["scenario"]),
                "window_id": int(window_id),
                "window_start_sec": start_sec,
                "window_end_sec": end_sec,
                **feats,
                "event_fraction": event_fraction,
                "label": label,
                "split": str(session.iloc[0]["split"]),
            })
    return pd.DataFrame(rows, columns=WINDOW_COLUMNS)


def generate_dataset(config: Config, output_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame, List[dict]]:
    if config.sessions_per_scenario < 1:
        raise ValueError("sessions_per_scenario minimal 1")
    if not (0.0 <= config.label_threshold <= 1.0):
        raise ValueError("label_threshold harus di antara 0 dan 1")
    if config.duration_sec <= config.window_sec:
        raise ValueError("duration_sec harus lebih besar dari window_sec")
    _samples_for(config.duration_sec, config.sampling_freq, "duration_sec")
    _samples_for(config.window_sec, config.sampling_freq, "window_sec")
    _samples_for(config.stride_sec, config.sampling_freq, "stride_sec")
    _samples_for(1.0, config.sampling_freq, "sampling_freq (kalibrasi 1 detik)")

    cal = load_calibration(config.calibration_path)
    calibrate_accel_scales(config, cal)
    id_map = make_session_ids(config)
    split_map = make_session_splits(config, id_map)
    # Satu aliran acak ber-seed membuat seluruh proses deterministik untuk konfigurasi yang sama.
    rng = np.random.default_rng(config.seed)
    raw_parts: List[pd.DataFrame] = []
    artifact_rows: List[dict] = []
    for scenario in SCENARIOS:
        for i in range(config.sessions_per_scenario):
            session_id = id_map[(scenario, i)]
            raw, art = simulate_session(session_id, scenario, split_map[session_id], config, rng)
            raw_parts.append(raw)
            artifact_rows.append(art)
    raw_df = pd.concat(raw_parts, ignore_index=True)
    windows_df = extract_all_windows(raw_df, config)

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_df = raw_df.sort_values(["session_id", "timestamp_sec"], kind="stable").reset_index(drop=True)
    windows_df = windows_df.sort_values(["session_id", "window_id"], kind="stable").reset_index(drop=True)
    raw_df.to_csv(output_dir / RAW_FILE, index=False, float_format="%.8f")
    windows_df.to_csv(output_dir / WINDOW_FILE, index=False, float_format="%.8f")
    log_rows = [(row["session_id"],) + ev for row in artifact_rows for ev in row["events"]]
    log_df = pd.DataFrame(log_rows, columns=ARTIFACT_COLUMNS)
    log_df = log_df.sort_values(["session_id", "sensor", "start_sample", "artifact_type"], kind="stable")
    log_df.to_csv(output_dir / ARTIFACT_FILE, index=False, float_format="%.6f")
    meta = {
        "seed": config.seed, "sessions_per_scenario": config.sessions_per_scenario,
        "sampling_freq": config.sampling_freq, "duration_sec": config.duration_sec,
        "window_sec": config.window_sec, "stride_sec": config.stride_sec,
        "label_threshold": config.label_threshold,
        "light_bounds": [LIGHT_MIN, LIGHT_MAX], "accel_bounds": [ACCEL_MIN, ACCEL_MAX],
        "calibration_sec": 1.0, "numpy_version": np.__version__,
        "scenarios": SCENARIOS, "feature_columns": FEATURE_COLUMNS,
        "light_margin": fe.LIGHT_MARGIN,
        "kalibrasi_nyata": {
            **cal.source,
            "p_tas_gelap": P_DARK_BAG,
            "cahaya": {"baseline_tas_gelap_maks": round(cal.dark_baseline_max, 5),
                       "bukaan_amplitudo": [round(cal.open_amp_median, 4), round(cal.open_amp_sigma, 4)],
                       "bukaan_durasi_s": [round(cal.open_dur_median, 4), round(cal.open_dur_sigma, 4)]},
            "akselerasi_p95_nyata": {k: round(v, 3) for k, v in cal.accel_p95_real.items()},
            "akselerasi_skala_simulasi": {k: round(v, 3) for k, v in cal.accel_scale.items()},
        },
    }
    (output_dir / CONFIG_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    return raw_df, windows_df, artifact_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Bangkitkan dataset sintetis TasGuard")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "dataset")
    parser.add_argument("--sessions-per-scenario", type=int, default=500)
    parser.add_argument("--sampling-freq", type=float, default=5.0)
    parser.add_argument("--duration-sec", type=float, default=7.0)
    parser.add_argument("--window-sec", type=float, default=3.0)
    parser.add_argument("--stride-sec", type=float, default=1.0)
    parser.add_argument("--label-threshold", type=float, default=0.40)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--kalibrasi", type=Path, default=CALIBRATION_FILE,
                        help="berkas hasil ai/fit_simulator.py (ukuran sinyal rekaman nyata)")
    args = parser.parse_args()
    config = Config(
        sessions_per_scenario=args.sessions_per_scenario,
        sampling_freq=args.sampling_freq,
        duration_sec=args.duration_sec,
        window_sec=args.window_sec,
        stride_sec=args.stride_sec,
        label_threshold=args.label_threshold,
        seed=args.seed,
        calibration_path=args.kalibrasi,
    )
    raw_df, windows_df, _ = generate_dataset(config, args.output_dir)
    print(f"Dihasilkan {raw_df['session_id'].nunique():,} sesi, "
          f"{len(raw_df):,} sampel mentah, {len(windows_df):,} window -> {args.output_dir}")
    print("Label window:")
    print(windows_df["label"].value_counts().to_string())
    print("Split window:")
    print(windows_df["split"].value_counts().to_string())


if __name__ == "__main__":
    main()
