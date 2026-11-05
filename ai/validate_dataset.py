#!/usr/bin/env python3
"""Validasi CSV dataset TasGuard terhadap kontrak pembangkitannya.

Ukuran yang diharapkan, batas sensor, dan ambang dibaca dari dataset_config.json
(ditulis oleh generator). Opsi baris perintah menimpa nilai dari config.

Tingkat hasil: PASS, WARN (perlu dilihat, tidak menggagalkan), FAIL.
Kode keluar 1 bila ada satu pemeriksaan yang FAIL.

Pemakaian:  python ai/validate_dataset.py [--data-dir ai/dataset]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import generate_dataset as gen  # noqa: E402  (satu folder)

RAW_COLUMNS = gen.RAW_COLUMNS
FEATURE_COLUMNS = gen.FEATURE_COLUMNS
WINDOW_COLUMNS = gen.WINDOW_COLUMNS
SCENARIOS = gen.SCENARIOS
METADATA_COLUMNS = ["session_id", "scenario", "window_id", "window_start_sec", "window_end_sec",
                    "event_fraction", "label", "split"]
FORBIDDEN_AS_FEATURES = {"event_active", "ordinary_activity_active", "scenario", "session_id",
                         "scenario_label", "event_fraction", "label", "split", "window_id",
                         "window_start_sec", "window_end_sec"}


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, name, detail=""):
        self.rows.append((status, name, detail))

    def check(self, condition, name, detail=""):
        self.add("PASS" if bool(condition) else "FAIL", name, detail)

    def warn_if(self, bad, name, detail=""):
        self.add("WARN" if bool(bad) else "PASS", name, detail)

    @property
    def n_fail(self):
        return sum(1 for r in self.rows if r[0] == "FAIL")

    @property
    def n_warn(self):
        return sum(1 for r in self.rows if r[0] == "WARN")

    def print(self):
        for status, name, detail in self.rows:
            print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))


def rank_auc(x, y):
    """AUC Mann-Whitney tanpa dependensi tambahan. y bertipe boolean."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x))
    xs = x[order]
    i = 0
    while i < len(xs):
        j = i
        while j + 1 < len(xs) and xs[j + 1] == xs[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    n1 = int(y.sum())
    n0 = len(y) - n1
    return (ranks[y].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def load_config(data_dir, args):
    path = data_dir / gen.CONFIG_FILE
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    d = gen.Config()

    def pick(key, cli, default):
        return cli if cli is not None else cfg.get(key, default)

    return {
        "sessions_per_scenario": pick("sessions_per_scenario", args.sessions_per_scenario, d.sessions_per_scenario),
        "fs": pick("sampling_freq", args.sampling_freq, d.sampling_freq),
        "duration_sec": pick("duration_sec", args.duration_sec, d.duration_sec),
        "window_sec": pick("window_sec", args.window_sec, d.window_sec),
        "stride_sec": pick("stride_sec", args.stride_sec, d.stride_sec),
        "label_threshold": pick("label_threshold", args.label_threshold, d.label_threshold),
        "light_bounds": cfg.get("light_bounds", [gen.LIGHT_MIN, gen.LIGHT_MAX]),
        "accel_bounds": cfg.get("accel_bounds", [gen.ACCEL_MIN, gen.ACCEL_MAX]),
        "calibration_sec": cfg.get("calibration_sec", 1.0),
        "config_found": path.exists(),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=HERE / "dataset")
    ap.add_argument("--sessions-per-scenario", type=int, default=None)
    ap.add_argument("--sampling-freq", type=float, default=None)
    ap.add_argument("--duration-sec", type=float, default=None)
    ap.add_argument("--window-sec", type=float, default=None)
    ap.add_argument("--stride-sec", type=float, default=None)
    ap.add_argument("--label-threshold", type=float, default=None)
    args = ap.parse_args(argv)
    R = Report()
    c = load_config(args.data_dir, args)

    raw_p, win_p = args.data_dir / gen.RAW_FILE, args.data_dir / gen.WINDOW_FILE
    log_p = args.data_dir / gen.ARTIFACT_FILE
    if not raw_p.exists() or not win_p.exists():
        print("FAIL: file CSV dataset tidak ditemukan (jalankan ai/generate_dataset.py)")
        return 2
    raw, win = pd.read_csv(raw_p), pd.read_csv(win_p)
    log = pd.read_csv(log_p) if log_p.exists() else None

    fs = c["fs"]
    n_per = int(round(c["duration_sec"] * fs))
    n_win = int(round(c["window_sec"] * fs))
    stride = int(round(c["stride_sec"] * fs))
    n_cal = int(round(c["calibration_sec"] * fs))
    w_per = (n_per - n_win) // stride + 1
    n_sess = c["sessions_per_scenario"] * len(SCENARIOS)
    R.check(c["config_found"], f"{gen.CONFIG_FILE} ditemukan", "ukuran yang diharapkan berasal dari config")

    # 1. skema dan ukuran
    R.check(raw.columns.tolist() == RAW_COLUMNS, "kolom dan urutan data mentah")
    R.check(win.columns.tolist() == WINDOW_COLUMNS, "kolom dan urutan data window")
    R.check(raw["session_id"].nunique() == n_sess, "jumlah sesi", f"{raw['session_id'].nunique()} dari {n_sess}")
    R.check(len(raw) == n_sess * n_per, "jumlah baris mentah", f"{len(raw)} dari {n_sess * n_per}")
    R.check(len(win) == n_sess * w_per, "jumlah baris window", f"{len(win)} dari {n_sess * w_per}")
    R.check((raw.groupby("session_id").size() == n_per).all(), "jumlah sampel per sesi")
    R.check((win.groupby("session_id").size() == w_per).all(), "jumlah window per sesi")
    R.check(set(raw["scenario"]) == set(SCENARIOS) and set(win["scenario"]) == set(SCENARIOS), "himpunan skenario")
    R.check((raw.groupby("scenario")["session_id"].nunique() == c["sessions_per_scenario"]).all(),
            "jumlah sesi per skenario")
    R.check(set(raw["session_id"]) == set(win["session_id"]), "ID sesi data mentah dan window sama")

    # 2. label dan nama split
    R.check(set(win["label"]) <= {"NORMAL", "SUSPICIOUS"}, "label window valid")
    exp_fam = raw["scenario"].map(gen.SCENARIOS)
    R.check((raw["scenario_label"] == exp_fam).all(), "scenario_label sesuai keluarga skenario")
    valid_splits = {"train", "validation", "test"}
    R.check(set(raw["split"]) <= valid_splits and set(win["split"]) <= valid_splits, "nama split valid")

    # 3. integritas split per sesi
    R.check((raw.groupby("session_id")["split"].nunique() == 1).all(), "tidak ada sesi mentah lintas split")
    R.check((win.groupby("session_id")["split"].nunique() == 1).all(), "tidak ada sesi window lintas split")
    s_raw = raw.groupby("session_id")["split"].first().sort_index()
    s_win = win.groupby("session_id")["split"].first().sort_index()
    R.check((s_raw == s_win).all(), "split identik di file mentah dan file window")
    n_train, n_val, n_test = gen._allocate_split_counts(c["sessions_per_scenario"])
    table = (raw[["scenario", "session_id", "split"]].drop_duplicates()
             .groupby(["scenario", "split"]).size().unstack(fill_value=0))
    R.check(((table["train"] == n_train) & (table["validation"] == n_val) & (table["test"] == n_test)).all(),
            "70/15/15 di setiap skenario", f"{n_train}/{n_val}/{n_test}")

    # 4. timestamp dan batas window
    raw = raw.sort_values(["session_id", "timestamp_sec"]).reset_index(drop=True)
    dts = raw.groupby("session_id")["timestamp_sec"].diff().dropna()
    R.check(np.abs(dts - 1.0 / fs).max() < 1e-6, "jarak timestamp", f"{1 / fs:g} detik")
    R.check(np.abs(win["window_end_sec"] - win["window_start_sec"] - c["window_sec"]).max() < 1e-6,
            "window_end_sec = awal + panjang window (batas akhir eksklusif)",
            "timestamp sampel terakhir satu interval sampling lebih awal")

    # 5. ground truth, dihitung ulang dari data mentah
    R.check(set(raw["event_active"].unique()) <= {0, 1} and set(raw["ordinary_activity_active"].unique()) <= {0, 1},
            "event_active dan ordinary_activity_active bernilai biner")
    fam_normal = raw["scenario_label"] == "NORMAL"
    R.check(raw.loc[fam_normal, "event_active"].sum() == 0, "tidak ada event_active pada skenario NORMAL")
    calib = raw["timestamp_sec"] < c["calibration_sec"] - 1e-9
    R.check(raw.loc[calib, "event_active"].sum() == 0 and raw.loc[calib, "ordinary_activity_active"].sum() == 0,
            "periode kalibrasi bebas kejadian dan aktivitas biasa")
    R.check(raw.loc[raw["scenario"] == "normal_idle", "ordinary_activity_active"].sum() == 0,
            "normal_idle tidak punya aktivitas biasa terjadwal")
    ev = raw["event_active"].to_numpy().reshape(-1, n_per)
    sids = raw["session_id"].to_numpy()[::n_per]
    frac = np.stack([ev[:, w * stride:w * stride + n_win].mean(axis=1) for w in range(w_per)], axis=1)
    fr = pd.DataFrame(frac, index=sids).stack().rename("ef_raw").reset_index()
    fr.columns = ["session_id", "window_id", "ef_raw"]
    m = win.merge(fr, on=["session_id", "window_id"], how="left")
    R.check(m["ef_raw"].notna().all() and np.abs(m["ef_raw"] - m["event_fraction"]).max() < 1e-6,
            "event_fraction dihitung ulang dari data mentah",
            f"galat maks {np.abs(m['ef_raw'] - m['event_fraction']).max():.1e}")
    exp_label = np.where(m["ef_raw"] + 1e-9 >= c["label_threshold"], "SUSPICIOUS", "NORMAL")
    R.check((exp_label == m["label"].to_numpy()).all(), "label window mengikuti ambang event_fraction",
            f"ambang {c['label_threshold']}")
    R.check(win.loc[win["scenario"].map(gen.SCENARIOS) == "NORMAL", "label"].eq("NORMAL").all(),
            "semua window skenario NORMAL berlabel NORMAL")

    # 6. kesehatan numerik dan batas nilai
    R.check(np.isfinite(raw.select_dtypes("number").to_numpy()).all(), "data mentah tanpa NaN/inf")
    R.check(np.isfinite(win.select_dtypes("number").to_numpy()).all(), "data window tanpa NaN/inf")
    lo, hi = c["light_bounds"]
    R.check(raw["light_value"].between(lo, hi).all(), "light_value di dalam batas", f"[{lo}, {hi}]")
    lo, hi = c["accel_bounds"]
    R.check(raw["accel_mag"].between(lo, hi).all(), "accel_mag di dalam batas", f"[{lo}, {hi}]")
    R.check(win["event_fraction"].between(0, 1).all(), "event_fraction di [0, 1]")
    for col in ("bright_ratio", "light_motion_coincidence"):
        R.check(win[col].between(0, 1).all(), f"{col} di [0, 1]")
    for col in ("motion_duration", "light_elevated_duration"):
        R.check(win[col].between(0, c["window_sec"] + 1e-8).all(), f"{col} tidak melebihi panjang window")
    R.check((win["light_min"] <= win["light_mean"] + 1e-9).all() and (win["light_mean"] <= win["light_max"] + 1e-9).all(),
            "light_min <= light_mean <= light_max")
    R.check((win["motion_event_count"] == win["motion_event_count"].round()).all(), "motion_event_count bilangan bulat")

    # 7. fitur dihitung ulang dari data mentah dengan rumus generator sendiri
    groups = {sid: g for sid, g in raw.groupby("session_id", sort=False)}
    sample_ids = sorted(groups)[:: max(1, len(groups) // 400)]       # sekitar 400 sesi
    max_diff = 0.0
    for sid in sample_ids:
        g = groups[sid].reset_index(drop=True)
        cal = g.iloc[:n_cal]
        for w in range(w_per):
            part = g.iloc[w * stride:w * stride + n_win]
            ref = gen.extract_window_features(part, cal, fs)
            got = win[(win["session_id"] == sid) & (win["window_id"] == w)].iloc[0]
            max_diff = max(max_diff, max(abs(float(got[k]) - ref[k]) for k in FEATURE_COLUMNS))
    R.check(max_diff < 1e-5, "fitur hasil hitung ulang dari data mentah sama dengan CSV",
            f"{len(sample_ids)} sesi, selisih maks {max_diff:.1e}")

    # 8. penjaga kebocoran (berbasis data, bukan sekadar membandingkan konstanta)
    feat_cols_in_file = [col for col in win.columns if col not in METADATA_COLUMNS]
    R.check(feat_cols_in_file == FEATURE_COLUMNS and len(feat_cols_in_file) == 14, "tepat 14 kolom fitur")
    R.check(not (FORBIDDEN_AS_FEATURES & set(feat_cols_in_file)), "tidak ada kolom target/metadata di antara fitur")
    corr = win[FEATURE_COLUMNS].corrwith(win["event_fraction"]).abs().max()
    R.check(corr < 0.999, "tidak ada fitur yang menyalin event_fraction", f"|korelasi| maks {corr:.3f}")
    R.warn_if(corr >= 0.95, "korelasi fitur dengan event_fraction sangat tinggi",
              f"{corr:.2f}: wajar bila kejadian mencurigakan = tas dibuka = cahaya naik, tetapi berarti satu fitur hampir cukup")
    R.check(not raw["session_id"].str.contains("|".join(SCENARIOS)).any(), "session_id tidak memuat nama skenario")

    # 9. distribusi dan peringatan jalan pintas
    labels = win["label"].value_counts(normalize=True)
    R.warn_if(labels.min() < 0.10, "keseimbangan kelas", ", ".join(f"{k} {v:.1%}" for k, v in labels.items()))
    for sp, g in win.groupby("split"):
        R.check(g["label"].nunique() == 2, f"kedua kelas ada di split {sp}")
    tr = win[win["split"] == "train"]
    y = (tr["label"] == "SUSPICIOUS").to_numpy()
    aucs = {f: rank_auc(tr[f].to_numpy(dtype=float), y) for f in FEATURE_COLUMNS}
    best = max(aucs, key=lambda k: max(aucs[k], 1 - aucs[k]))
    best_auc = max(aucs[best], 1 - aucs[best])
    R.warn_if(best_auc > 0.85, "tidak ada satu fitur yang memisahkan kelas sendirian",
              f"terbaik {best} AUC {best_auc:.3f} (train)")
    med = win.groupby("scenario")["accel_std"].median()
    R.warn_if(med["normal_walking"] < 3 * med["normal_idle"], "berjalan jelas lebih aktif daripada diam",
              f"median accel_std berjalan {med['normal_walking']:.2f}, diam {med['normal_idle']:.2f}")
    R.warn_if(raw["accel_mag"].max() < 15, "akselerometer mencapai magnitudo sentakan",
              f"maks {raw['accel_mag'].max():.1f} m/s2")

    # 10. log artefak
    if log is None:
        R.warn_if(True, "log artefak ada", f"{gen.ARTIFACT_FILE} tidak ditemukan")
    else:
        R.check(log.columns.tolist() == gen.ARTIFACT_COLUMNS, "kolom log artefak")
        R.check(set(log["session_id"]) <= set(raw["session_id"]), "ID sesi di log artefak ada di data mentah")
        fam = log["session_id"].map(raw.groupby("session_id")["scenario_label"].first())
        for kind in ("spike", "held_value", "dropout_locf", "clip"):
            sub = fam[log["artifact_type"] == kind].value_counts()
            R.warn_if(sub.get("NORMAL", 0) == 0 or sub.get("SUSPICIOUS", 0) == 0,
                      f"artefak '{kind}' muncul di kedua kelas",
                      f"NORMAL {sub.get('NORMAL', 0)}, SUSPICIOUS {sub.get('SUSPICIOUS', 0)}")

    R.print()
    print(f"\nSesi {raw['session_id'].nunique():,} | baris mentah {len(raw):,} | window {len(win):,}")
    print("Label window:", win["label"].value_counts().to_dict())
    print("Window per split:", win["split"].value_counts().to_dict())
    print(f"\n{sum(1 for r in R.rows if r[0] == 'PASS')} lolos, {R.n_warn} peringatan, {R.n_fail} gagal")
    return 1 if R.n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
