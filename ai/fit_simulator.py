#!/usr/bin/env python3
"""Ukur karakter sinyal REKAMAN NYATA dan simpan sebagai bahan kalibrasi simulator dataset.

Dataset training tetap SINTETIS. Skrip ini hanya mengukur beberapa angka ringkas dari
sensor_log.csv (HP penjaga di dalam tas) supaya rentang nilai simulator mengikuti
perilaku sensor asli, bukan tebakan:

  * cahaya : baseline tas gelap, besar kenaikan cahaya saat tas dibuka, lama tas terbuka;
  * gerak  : persentil ke-95 simpangan akselerasi per label, dan nilai akselerasi maksimum.

Agar ujian pada rekaman nyata tetap adil, hanya sebagian rekaman yang dipakai mengukur:
  * tas dibuka (suspicious_*) : segmen yang mulai antara FIT_BUKA_DARI dan FIT_BUKA_SAMPAI;
  * normal_*                  : segmen yang mulai sebelum FIT_NORMAL_SEBELUM.
Sisanya (kondisi lain dan normal yang lebih akhir) TIDAK disentuh dan dipakai sebagai ujian
oleh ai/compare_real.py. Hasil ukur ditulis ke ai/kalibrasi_nyata.json (masukan sumber, ikut
disimpan di repo karena sensor_log.csv tidak) dan dibaca oleh generate_dataset.py. Berkas log
asli tidak diubah.

Pemakaian (dari folder utama proyek):
  python ai/fit_simulator.py [--log sensor_log.csv] [--out ai/kalibrasi_nyata.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402

config.LOG_CSV = False  # replay tidak boleh menulis log
from server import decision_engine as de  # noqa: E402

# Pembagian rekaman: bagian "penyetelan" dipakai mengukur, sisanya untuk ujian.
FIT_BUKA_DARI = "16:05"
FIT_BUKA_SAMPAI = "16:54"        # kondisi 1 (atas, buka lebar) dan 2 (atas, buka sedikit)
FIT_NORMAL_SEBELUM = "15:50"     # normal yang direkam lebih akhir dipakai sebagai ujian
LIGHT_OPEN_LUMA = 10.0           # sama dengan margin cahaya 0,04 (kira-kira 10 luma)
BORDER_S = 3.0                   # buang 3 detik pertama setiap segmen (label baru ditekan)
MIN_SAMPLES = 10
MERGE_GAP_SAMPLES = 3            # celah <= 0,6 detik di antara dua lonjakan dianggap satu bukaan
SESSION_GAP_S = 3.0


class _ModelBisu:
    """Pengganti model: yang diperlukan di sini hanya baseline, bukan skor."""
    available = True
    threshold = 2.0
    consecutive = 99

    @staticmethod
    def predict(_features):
        return {"probability_suspicious": 0.0}


def _menit(jam: str) -> int:
    h, m = jam.split(":")
    return int(h) * 60 + int(m)


def _menit_dari_ts(ts: float) -> int:
    t = datetime.fromtimestamp(float(ts))
    return t.hour * 60 + t.minute


def replay_baseline(log: Path) -> pd.DataFrame:
    """Putar ulang log lewat AIEngine yang sama dengan server; simpan baseline tiap sampel."""
    df = pd.read_csv(log)
    df = df[(df.role == "B") & (df.sumber_cahaya == "camera")].sort_values("waktu_laptop").reset_index(drop=True)
    df["label"] = df["label"].map(lambda v: "" if pd.isna(v) else str(v))
    df = df[df.waktu_laptop >= df[df.label != ""].waktu_laptop.min() - 120].reset_index(drop=True)
    eng = de.AIEngine("B")
    eng.model = _ModelBisu()
    base_l, base_a = [], []
    for r in df.itertuples():
        eng.process({"light": {"source": r.sumber_cahaya, "value": r.nilai_cahaya}, "accel_mag": r.accel_mag},
                    float(r.waktu_laptop))
        siap = eng.phase == "ready" and bool(eng._cal_light)
        base_l.append(float(np.median(eng._cal_light)) * 255 if siap else np.nan)
        base_a.append(float(np.median(eng._cal_accel)) if siap else np.nan)
    out = df[["waktu_laptop", "label", "nilai_cahaya", "accel_mag"]].copy()
    out["base_luma"], out["base_accel"] = base_l, base_a
    return out


def segmen_berlabel(s: pd.DataFrame) -> list:
    """Pecah rekaman menjadi segmen (label sama, jeda < 3 detik) tanpa 3 detik pertama."""
    lab = s[(s.label != "") & s.base_luma.notna()].copy()
    lab["seg"] = ((lab.label != lab.label.shift()) | (lab.waktu_laptop.diff() > SESSION_GAP_S)).cumsum()
    hasil = []
    for _, g in lab.groupby("seg"):
        mulai = float(g.waktu_laptop.iloc[0])
        g = g[g.waktu_laptop >= mulai + BORDER_S]
        if len(g) >= MIN_SAMPLES:
            hasil.append((g.label.iloc[0], _menit_dari_ts(mulai), g))
    return hasil


def lognormal_dari_kuantil(nilai: np.ndarray) -> dict:
    """Median dan sigma lognormal dari persentil 10/50/90 (tahan terhadap pencilan)."""
    x = np.log(np.maximum(np.asarray(nilai, dtype=float), 1e-6))
    p10, p50, p90 = np.percentile(x, [10, 50, 90])
    return {"median": float(np.exp(p50)), "sigma": float(max(0.05, (p90 - p10) / (2 * 1.2816))), "n": int(len(x))}


def lama_bukaan(rise: np.ndarray) -> list:
    """Panjang (detik) tiap deretan sampel cahaya di atas ambang; celah pendek digabung."""
    atas = rise > LIGHT_OPEN_LUMA
    idx = np.flatnonzero(atas)
    if len(idx) == 0:
        return []
    awal = [idx[0]]
    akhir = []
    for a, b in zip(idx[:-1], idx[1:]):
        if b - a > MERGE_GAP_SAMPLES + 1:
            akhir.append(a)
            awal.append(b)
    akhir.append(idx[-1])
    return [(e - s + 1) / 5.0 for s, e in zip(awal, akhir)]


def ukur(s: pd.DataFrame) -> dict:
    buka_dari, buka_sampai, normal_sebelum = _menit(FIT_BUKA_DARI), _menit(FIT_BUKA_SAMPAI), _menit(FIT_NORMAL_SEBELUM)
    amp, durasi, base_gelap, p95 = [], [], [], {}
    akselerasi_maks, n_seg = 0.0, {"buka": 0, "normal": 0}
    for label, mnt, g in segmen_berlabel(s):
        buka = label.startswith("suspicious")
        if buka and not (buka_dari <= mnt <= buka_sampai):
            continue
        if not buka and mnt >= normal_sebelum:
            continue
        n_seg["buka" if buka else "normal"] += 1
        rise = (g.nilai_cahaya - g.base_luma).to_numpy()
        dev = (g.accel_mag - g.base_accel).to_numpy()
        base_gelap.append(float(g.base_luma.median()))
        p95.setdefault(label, []).append(float(np.percentile(np.abs(dev), 95)))
        akselerasi_maks = max(akselerasi_maks, float(g.accel_mag.max()))
        if buka:
            amp.append(max(1.0, float(np.max(rise))) / 255.0)
            durasi.extend(lama_bukaan(rise))
    if not amp or not durasi or not p95:
        sys.exit("Rekaman terlalu sedikit untuk diukur (butuh segmen tas dibuka dan normal pada rentang penyetelan).")
    return {
        "cahaya": {
            "baseline_tas_gelap_p90": float(np.percentile(base_gelap, 90)) / 255.0,
            "bukaan_amplitudo": lognormal_dari_kuantil(np.array(amp)),
            "bukaan_durasi_s": lognormal_dari_kuantil(np.array(durasi)),
        },
        "akselerasi": {
            "p95_nyata": {k: float(np.median(v)) for k, v in sorted(p95.items())},
            "n_segmen_per_label": {k: len(v) for k, v in sorted(p95.items())},
            "maks_teramati": akselerasi_maks,
        },
        "segmen_dipakai": n_seg,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Ukur sinyal rekaman nyata untuk kalibrasi simulator")
    ap.add_argument("--log", type=Path, default=ROOT / "sensor_log.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "ai" / "kalibrasi_nyata.json")
    args = ap.parse_args()
    if not args.log.exists():
        sys.exit(f"Tidak menemukan {args.log}")
    s = replay_baseline(args.log)
    hasil = ukur(s)
    hasil["pembagian_rekaman"] = {
        "tas_dibuka_dipakai_mengukur": f"segmen mulai {FIT_BUKA_DARI} sampai {FIT_BUKA_SAMPAI}",
        "normal_dipakai_mengukur": f"segmen mulai sebelum {FIT_NORMAL_SEBELUM}",
        "catatan": "Sisanya tidak dipakai mengukur dan menjadi bahan ujian (ai/compare_real.py).",
    }
    hasil["sumber"] = {"berkas": args.log.name, "tanggal_rekaman": datetime.fromtimestamp(float(s.waktu_laptop.iloc[0])).strftime("%Y-%m-%d")}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(hasil, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(hasil, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
