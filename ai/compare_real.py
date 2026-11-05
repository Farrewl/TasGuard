#!/usr/bin/env python3
"""Bandingkan aturan dan AI pada REKAMAN NYATA berlabel dari HP penjaga (HP B).

Model TIDAK dilatih ulang di sini: yang diuji adalah model sintetis yang sama
dengan yang dipakai server. Rekaman dibuat lewat pemilih label di dashboard
(kolom ``label`` di ai_log.csv dan sensor_log.csv).

Metode yang dibandingkan pada window dan segmen yang SAMA:
  1. Aturan PPT minggu lalu  : ambang absolut (luma >= LUMA_THRESHOLD bertahan
                               LIGHT_HOLD_MS DAN getar) dengan replay detector.py.
  2. Aturan A (cahaya saja)  : cahaya naik di atas baseline >= LIGHT_EVIDENCE_S.
  3. Aturan B (cahaya+getar) : Aturan A DAN motion_duration >= 0,6 detik.
  4. AI saja                 : skor Random Forest >= ambang (tanpa syarat tas dibuka).
  5. AI + syarat tas dibuka  : yang dipakai server.

Aturan evaluasi:
  * window 3 detik pertama setelah label berganti dibuang (zona batas);
  * satu SEGMEN = deretan label sama dalam satu sesi; segmen "mencurigakan" bila
    labelnya ``suspicious_*``; alarm segmen = >= ``consecutive`` window berurutan;
  * --tune-first: ambang AI dipilih dari SESI PERTAMA saja, lalu diuji di sesi
    lain (ambang tidak boleh dipilih dan dinilai pada data yang sama).

Replay: ``ai_log.csv`` berisi fitur yang dihitung SAAT merekam (margin dan model saat itu). Dengan
``--replay`` fitur dihitung ulang dari ``sensor_log.csv`` memakai kode ``feature_extraction.py``
yang sekarang, sehingga perubahan margin atau model bisa diuji pada rekaman yang sama tanpa
merekam ulang. Berkas log asli tidak diubah.

Pemakaian (dari folder utama proyek):
  python ai/compare_real.py [--replay] [--tune-first] [--kondisi "HH:MM=nama" ...] [--abaikan NAMA ...]
  python ai/compare_real.py --selftest      # uji sanity pada window sintetis (split test)
Keluaran bawaan: ai/models/real_comparison.md
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from feature_extraction import LIGHT_EVIDENCE_S, MODEL_FEATURES  # noqa: E402

SESSION_GAP_S = 3.0      # jeda data mentah yang memicu kalibrasi ulang (sama dengan GAP_RESET_S server)
EVAL_SESSION_GAP_S = 8.0  # jeda antar evaluasi di ai_log.csv = sesi penjagaan baru (mulai ulang + kalibrasi)
BORDER_S = 3.0           # zona batas setelah label berganti (panjang window)
MOTION_MIN_S = 0.6       # syarat getar Aturan B
VERDICT_MARGIN = 0.05    # selisih F1 yang dianggap berarti
METHODS = [
    ("ppt", "Aturan PPT minggu lalu (ambang absolut)"),
    ("rule_a", "Aturan A: cahaya naik saja"),
    ("rule_b", "Aturan B: cahaya naik DAN getar"),
    ("ai_only", "AI saja (tanpa syarat tas dibuka)"),
    ("ai", "AI + syarat tas dibuka (dipakai server)"),
]


# Persiapan data

def prepare_real(ai_log: Path) -> pd.DataFrame:
    """Baca ai_log.csv (fitur + label per detik) dan tandai sesi, segmen, serta zona batas."""
    df = pd.read_csv(ai_log)
    if "role" in df.columns:
        df = df[df["role"] == "B"]
    df = df.sort_values("waktu_laptop").reset_index(drop=True)
    df["label"] = df["label"].map(lambda v: "" if pd.isna(v) else str(v))
    df["sesi"] = (df.waktu_laptop.diff() > EVAL_SESSION_GAP_S).cumsum()
    df["segmen"] = ((df.label != df.label.shift()) | (df.sesi != df.sesi.shift())).cumsum()
    df["detik_di_segmen"] = df.groupby("segmen").waktu_laptop.transform(lambda s: s - s.iloc[0])
    df = df[(df.label != "") & (df.detik_di_segmen >= BORDER_S)].copy()
    df["label_nama"] = df["label"]
    df["y"] = df.label_nama.str.startswith("suspicious").astype(int)
    return df.reset_index(drop=True)


def prepare_selftest() -> pd.DataFrame:
    """Window sintetis (split test) diperlakukan seolah rekaman: satu sesi sintetis = satu segmen."""
    w = pd.read_csv(HERE / "dataset" / "tasguard_window_features.csv")
    w = w[w.split == "test"].sort_values(["session_id", "window_id"]).reset_index(drop=True)
    w["waktu_laptop"] = w.window_id.astype(float)
    codes = {sid: i for i, sid in enumerate(w.session_id.unique())}
    w["sesi"] = 0                                  # satu "sesi" besar; ambang tidak dituning
    w["segmen"] = w.session_id.map(codes)
    w["label_nama"] = w.scenario
    w["y"] = (w.label == "SUSPICIOUS").astype(int)  # label window sintetis (event_fraction)
    return w


def assign_conditions(df: pd.DataFrame, specs: list) -> pd.DataFrame:
    """Beri nama kondisi menurut jam mulai ("HH:MM=nama"). Satu kondisi bisa terdiri dari beberapa
    potongan sesi (data sempat putus > 8 detik). Baris sebelum jam pertama bernama "(sebelum kondisi pertama)"."""
    if not specs:
        return df
    bounds = []
    for sp in specs:
        jam, _, nama = sp.partition("=")
        h, m = jam.strip().split(":")
        bounds.append((int(h) * 60 + int(m), nama.strip() or jam.strip()))
    bounds.sort()

    def nama_untuk(ts: float) -> str:
        t = datetime.fromtimestamp(float(ts))
        mnt, cur = t.hour * 60 + t.minute, "(sebelum kondisi pertama)"
        for b, n in bounds:
            if mnt >= b:
                cur = n
        return cur

    df = df.copy()
    df["kondisi"] = [nama_untuk(t) for t in df.waktu_laptop]
    return df


# Replay fitur dari sensor_log.csv

class _ModelBisu:
    """Pengganti model saat replay: fitur tidak bergantung pada model; skor dihitung ulang dari fitur."""
    available = True
    threshold = 2.0
    consecutive = 99

    @staticmethod
    def predict(_features):
        return {"probability_suspicious": 0.0}


def replay_ai_log(sensor_log: Path, out: Path) -> int:
    """Putar ulang sensor_log.csv (HP B, kamera) lewat AIEngine yang sama dengan server dan tulis ai_log ke `out`.

    Mengembalikan jumlah evaluasi window. Label dibaca dari kolom ``label`` tiap baris."""
    from server import config
    from server import decision_engine as de

    s = pd.read_csv(sensor_log)
    if "role" in s.columns:
        s = s[s["role"] == "B"]
    s = s[s["sumber_cahaya"] == "camera"].sort_values("waktu_laptop").reset_index(drop=True)
    s["label"] = s["label"].map(lambda v: "" if pd.isna(v) else str(v)) if "label" in s.columns else ""
    if out.exists():
        out.unlink()

    label = {"v": ""}
    saved = (config.LOG_CSV, de.AI_LOG_PATH, de.current_label)
    config.LOG_CSV, de.AI_LOG_PATH, de.current_label = True, str(out), (lambda: label["v"])
    try:
        engine = de.AIEngine("B")
        engine.model = _ModelBisu()
        for r in s.itertuples(index=False):
            label["v"] = r.label
            engine.process({"light": {"source": "camera", "value": r.nilai_cahaya}, "accel_mag": r.accel_mag},
                           float(r.waktu_laptop))
        return engine.evaluations
    finally:
        config.LOG_CSV, de.AI_LOG_PATH, de.current_label = saved


# Aturan lama PPT (replay mentah)

def ppt_rule_alarm_times(sensor_log: Path) -> Optional[np.ndarray]:
    """Waktu alarm aturan PPT bila diputar ulang pada sensor_log.csv (tanpa cooldown, kondisi murni)."""
    if not sensor_log.exists():
        return None
    from server import config
    from server import detector as det

    config.LOG_CSV = False
    config.ALARM_COOLDOWN_S = 0.0                   # ukur kondisi aturannya, bukan jeda alarm
    s = pd.read_csv(sensor_log)
    if "role" in s.columns:
        s = s[s["role"] == "B"]
    s = s[s["sumber_cahaya"] == "camera"].sort_values("waktu_laptop")
    clock = {"t": 0.0}
    real_time = det.time
    det.time = types.SimpleNamespace(time=lambda: clock["t"])   # jam tiruan = waktu rekaman
    times, engine, last = [], None, None
    try:
        for r in s.itertuples(index=False):
            t = float(r.waktu_laptop)
            if engine is None or t - last > SESSION_GAP_S:
                engine = det.DetectionEngine("B")
            last, clock["t"] = t, t
            msg = {"light": {"source": "camera", "value": r.nilai_cahaya}, "accel_mag": r.accel_mag, "ts": t}
            if engine.evaluate(msg):
                times.append(t)
    finally:
        det.time = real_time
    return np.array(times)


def window_has_alarm(eval_times: np.ndarray, alarm_times: np.ndarray, span: float = 3.0) -> np.ndarray:
    """Apakah ada alarm aturan dalam `span` detik terakhir sebelum tiap evaluasi."""
    lo = np.searchsorted(alarm_times, eval_times - span, side="right")
    hi = np.searchsorted(alarm_times, eval_times, side="right")
    return hi > lo


# Metrik

def window_metrics(y: np.ndarray, flag: np.ndarray) -> dict:
    tp = int(((y == 1) & flag).sum()); fp = int(((y == 0) & flag).sum())
    fn = int(((y == 1) & ~flag).sum()); tn = int(((y == 0) & ~flag).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f1, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def segment_alarms(df: pd.DataFrame, flag: np.ndarray, consecutive: int) -> pd.DataFrame:
    """Per segmen: apakah ada >= consecutive window berurutan yang ditandai."""
    d = df[["segmen", "label_nama"]].copy()
    d["f"] = flag
    rows = []
    for seg, g in d.groupby("segmen", sort=False):
        run = best = 0
        for v in g.f.astype(int):
            run = run + 1 if v else 0
            best = max(best, run)
        rows.append({"segmen": seg, "label_nama": g.label_nama.iloc[0], "alarm": best >= consecutive})
    t = pd.DataFrame(rows)
    t["positif"] = t.label_nama.str.startswith("suspicious")
    return t


def summarize(df: pd.DataFrame, flags: Dict[str, np.ndarray], consecutive: int) -> dict:
    out = {}
    for key, _ in METHODS:
        if key not in flags:
            continue
        seg = segment_alarms(df, flags[key], consecutive)
        pos, neg = seg[seg.positif], seg[~seg.positif]
        out[key] = {
            "window": window_metrics(df.y.to_numpy(), flags[key]),
            "detect": (int(pos.alarm.sum()), len(pos)),
            "false_alarm": (int(neg.alarm.sum()), len(neg)),
            "per_label": seg.groupby("label_nama").alarm.agg(["sum", "size"]).astype(int),
        }
    return out


def verdict(res: dict) -> str:
    """Kesimpulan otomatis: AI vs aturan terbaik pada F1 window."""
    if res["ai"]["detect"][1] == 0:
        return ("Belum ada segmen tas dibuka pada data uji, jadi AI dan aturan belum bisa dibandingkan. "
                "Rekam sesi dengan label suspicious_* di luar sesi penyetelan ambang.")
    best_key = max(("rule_a", "rule_b", "ppt"), key=lambda k: res[k]["window"]["f1"] if k in res else -1)
    f_ai, f_rule = res["ai"]["window"]["f1"], res[best_key]["window"]["f1"]
    nama = dict(METHODS)[best_key]
    if f_ai >= f_rule + VERDICT_MARGIN:
        return (f"AI UNGGUL: F1 window {f_ai:.2f} vs {f_rule:.2f} untuk aturan terbaik ({nama}), "
                f"selisih >= {VERDICT_MARGIN}.")
    if f_ai >= f_rule - VERDICT_MARGIN:
        return (f"AI SETARA aturan terbaik: F1 window {f_ai:.2f} vs {f_rule:.2f} ({nama}); selisih < {VERDICT_MARGIN}. "
                "Pada data ini AI tidak terbukti lebih baik dari aturan; nilai sistem ada pada baseline adaptif dan skor yang bisa disetel.")
    return (f"AI DI BAWAH aturan: F1 window {f_ai:.2f} vs {f_rule:.2f} ({nama}). "
            "Laporkan apa adanya dan periksa skala sensor atau ambang.")


# Laporan

def pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f} %)" if b else "-"


def table_methods(res: dict) -> list:
    lines = ["| Metode | F1 window | Presisi | Recall | Salah alarm segmen normal | Terdeteksi segmen mencurigakan |",
             "|---|---|---|---|---|---|"]
    for key, nama in METHODS:
        if key not in res:
            continue
        w = res[key]["window"]
        lines.append(f"| {nama} | {w['f1']:.2f} | {w['precision']:.2f} | {w['recall']:.2f} | "
                     f"{pct(*res[key]['false_alarm'])} | {pct(*res[key]['detect'])} |")
    return lines


def table_labels(res: dict) -> list:
    keys = [k for k, _ in METHODS if k in res]
    labels = sorted(set().union(*[set(res[k]["per_label"].index) for k in keys]))
    lines = ["| Label | " + " | ".join(dict(METHODS)[k].split(" (")[0].split(":")[0] for k in keys) + " |",
             "|---|" + "---|" * len(keys)]
    for lab in labels:
        cells = []
        for k in keys:
            pl = res[k]["per_label"]
            cells.append(pct(int(pl.loc[lab, "sum"]), int(pl.loc[lab, "size"])) if lab in pl.index else "-")
        lines.append(f"| {lab} | " + " | ".join(cells) + " |")
    return lines


def table_sessions(df: pd.DataFrame, flags: Dict[str, np.ndarray], consecutive: int) -> list:
    """Per kondisi (atau per sesi bila kondisi tidak diberikan): salah alarm dan terdeteksi untuk metode utama."""
    unit = "kondisi" if "kondisi" in df.columns else "sesi"
    keys = [k for k in ("ppt", "rule_a", "ai") if k in flags]
    judul = {"ppt": "Aturan PPT", "rule_a": "Aturan A (cahaya saja)", "ai": "AI + syarat tas dibuka"}
    lines = [f"| {'Kondisi (mulai)' if unit == 'kondisi' else 'Sesi (mulai)'} | Segmen normal | Segmen dibuka | " + " | ".join(f"{judul[k]}: salah alarm / terdeteksi" for k in keys) + " |",
             "|---|---|---|" + "---|" * len(keys)]
    urut = df.groupby(unit).waktu_laptop.min().sort_values().index
    for sesi in urut:
        g = df[df[unit] == sesi]
        rows = g.index.to_numpy()
        cells, n_neg, n_pos = [], 0, 0
        for k in keys:
            seg = segment_alarms(g, np.asarray(flags[k])[rows], consecutive)
            pos, neg = seg[seg.positif], seg[~seg.positif]
            n_neg, n_pos = len(neg), len(pos)
            cells.append(f"{int(neg.alarm.sum())}/{n_neg} / {int(pos.alarm.sum())}/{n_pos}")
        mulai = datetime.fromtimestamp(float(g.waktu_laptop.iloc[0])).strftime("%H:%M")
        lines.append(f"| {sesi} ({mulai}) | {n_neg} | {n_pos} | " + " | ".join(cells) + " |")
    return lines


def write_report(path: Path, df: pd.DataFrame, res: dict, info: dict,
                 flags: Optional[Dict[str, np.ndarray]] = None) -> None:
    seg = df.groupby("segmen").agg(label=("label_nama", "first"), n=("y", "size"))
    count = seg.groupby("label").agg(segmen=("n", "size"), window=("n", "sum"))
    L = ["# Perbandingan aturan dan AI pada rekaman nyata\n",
         "Rekaman dibuat tim dengan memeragakan sendiri kondisi di depan HP (bukan pencuri sungguhan), jumlahnya kecil, "
         "sehingga hasil ini **indikatif**, bukan akurasi deteksi pencurian di dunia nyata. Model dilatih pada data "
         "sintetis dan **tidak** dilatih ulang pada rekaman ini.\n",
         f"- Sesi penjagaan dalam rekaman: {info['n_sesi']}; window dipakai: {len(df)}; segmen: {seg.shape[0]}.",
         f"- Window {BORDER_S:g} detik pertama setelah label berganti dibuang (zona batas).",
         f"- Ambang AI {info['threshold']:.2f}; alarm segmen = {info['consecutive']} window berurutan.",
         f"- {info['tuning']}\n",
         "## Jumlah data per label\n", "| Label | Segmen | Window |", "|---|---|---|"]
    L += [f"| {lab} | {int(r.segmen)} | {int(r.window)} |" for lab, r in count.iterrows()]
    L += ["\n## Hasil keseluruhan\n"] + table_methods(res)
    L += ["\nKolom segmen: jumlah segmen yang membunyikan alarm dibanding jumlah segmen pada kelasnya.\n",
          "## Per label (segmen yang membunyikan alarm)\n"] + table_labels(res)
    unit = "kondisi" if "kondisi" in df.columns else "sesi"
    if flags is not None and df[unit].nunique() > 1:
        L += [f"\n## Per {unit} (untuk membandingkan kondisi rekaman)\n"] + table_sessions(df, flags, info["consecutive"])
    L += ["\n## Kesimpulan otomatis\n", verdict(res) + "\n",
          "Catatan: perbandingan sederhana pada sampel kecil; selisih beberapa segmen bisa kebetulan. "
          "Pernyataan di PPT harus mengikuti angka di atas, termasuk bila AI tidak lebih baik.\n"]
    path.write_text("\n".join(L), encoding="utf-8", newline="\n")


# Alur utama

def build_flags(df: pd.DataFrame, score: np.ndarray, thr: float,
                ppt_times: Optional[np.ndarray]) -> Dict[str, np.ndarray]:
    open_ = (df.light_elevated_duration >= LIGHT_EVIDENCE_S).to_numpy()
    flags = {
        "rule_a": open_,
        "rule_b": open_ & (df.motion_duration >= MOTION_MIN_S).to_numpy(),
        "ai_only": score >= thr,
        "ai": (score >= thr) & open_,
    }
    if ppt_times is not None:
        flags["ppt"] = window_has_alarm(df.waktu_laptop.to_numpy(), ppt_times)
    return flags


def tune_threshold(df: pd.DataFrame, score: np.ndarray, consecutive: int) -> float:
    """Ambang terbaik pada subset (selisih terdeteksi - salah alarm segmen; F1 window sebagai pemutus seri)."""
    best, best_key = 0.5, (-9.0, -9.0)
    open_ = (df.light_elevated_duration >= LIGHT_EVIDENCE_S).to_numpy()
    for t in np.round(np.arange(0.3, 0.91, 0.05), 2):
        flag = (score >= t) & open_
        seg = segment_alarms(df, flag, consecutive)
        pos, neg = seg[seg.positif], seg[~seg.positif]
        gap = (pos.alarm.mean() if len(pos) else 0.0) - (neg.alarm.mean() if len(neg) else 0.0)
        key = (gap, window_metrics(df.y.to_numpy(), flag)["f1"])
        if key > best_key:
            best, best_key = float(t), key
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai-log", type=Path, default=ROOT / "ai_log.csv")
    ap.add_argument("--sensor-log", type=Path, default=ROOT / "sensor_log.csv")
    ap.add_argument("--model", type=Path, default=HERE / "models" / "tasguard_rf.joblib")
    ap.add_argument("--out", type=Path, default=HERE / "models" / "real_comparison.md")
    ap.add_argument("--threshold", type=float, default=None, help="timpa ambang AI (default: ambang dari model)")
    ap.add_argument("--tune-first", action="store_true",
                    help="pilih ambang dari sesi pertama yang memuat tas dibuka saja, uji di sesi lain")
    ap.add_argument("--kondisi", action="append", default=[], metavar="HH:MM=nama",
                    help="nama kondisi menurut jam mulai (boleh diulang), mis. --kondisi \"16:44=atas, buka sedikit\"")
    ap.add_argument("--abaikan", action="append", default=[], metavar="NAMA",
                    help="keluarkan kondisi (awalan nama) dari angka ujian, mis. kondisi yang dipakai menyetel "
                         "simulator lewat ai/fit_simulator.py; boleh diulang")
    ap.add_argument("--replay", action="store_true",
                    help="hitung ulang fitur dari sensor_log.csv dengan kode fitur yang sekarang (abaikan --ai-log)")
    ap.add_argument("--selftest", action="store_true", help="uji sanity pada window sintetis (split test)")
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    consecutive = int(bundle.get("consecutive", 2))
    thr = args.threshold if args.threshold is not None else float(bundle["threshold"])

    if args.selftest:
        df, ppt_times, tuning = prepare_selftest(), None, "Uji sanity: window sintetis (split test), tanpa penyetelan ambang."
        out = Path(tempfile.gettempdir()) / "real_comparison_selftest.md"   # tidak meninggalkan berkas di repo
    else:
        if args.replay:
            if not args.sensor_log.exists():
                sys.exit(f"Tidak menemukan {args.sensor_log}. Jalankan server dan rekam dulu.")
            args.ai_log = Path(tempfile.gettempdir()) / "tasguard_ai_log_replay.csv"    # tidak menimpa ai_log.csv
            n_eval = replay_ai_log(args.sensor_log, args.ai_log)
            print(f"Replay: {n_eval} evaluasi window dari {args.sensor_log.name} -> {args.ai_log}")
        if not args.ai_log.exists():
            sys.exit(f"Tidak menemukan {args.ai_log}. Jalankan server dan rekam dulu.")
        df = prepare_real(args.ai_log)
        if df.empty:
            sys.exit("Belum ada rekaman berlabel (kolom label kosong semua). Pilih label di dashboard saat merekam.")
        df = assign_conditions(df, args.kondisi)
        ppt_times, tuning, out = ppt_rule_alarm_times(args.sensor_log), "", args.out

    score = bundle["model"].predict_proba(df[MODEL_FEATURES].to_numpy())[:, 1]

    if args.tune_first and not args.selftest:
        unit = "kondisi" if "kondisi" in df.columns else "sesi"
        urut = list(df.groupby(unit).waktu_laptop.min().sort_values().index)
        if len(urut) < 2:
            sys.exit("--tune-first butuh minimal 2 sesi/kondisi berlabel.")
        agg = df.groupby(unit).y.agg(["min", "max"]).loc[urut]
        kandidat = agg[(agg["max"] == 1) & (agg["min"] == 0)].index          # memuat tas dibuka DAN tas tertutup
        if len(kandidat) == 0:
            kandidat = agg[agg["max"] == 1].index
        if len(kandidat) == 0:
            sys.exit("--tune-first butuh sesi yang memuat rekaman tas dibuka (label suspicious_*).")
        tune_id = kandidat[0]
        first = df[unit] == tune_id
        thr = tune_threshold(df[first], score[first.to_numpy()], consecutive)
        tuning = (f"Ambang AI **{thr:.2f}** dipilih dari satu {unit} saja ({tune_id}: yang pertama memuat tas dibuka, "
                  f"{int(first.sum())} window); hasil di bawah memakai {len(urut) - 1} {unit} lain.")
        mask = ~first
        df, score = df[mask].reset_index(drop=True), score[mask.to_numpy()]
    elif not args.selftest:
        tuning = "Ambang AI dari model (tidak disetel pada rekaman ini)."

    if args.abaikan and not args.selftest:
        if "kondisi" not in df.columns:
            sys.exit("--abaikan butuh --kondisi untuk menamai kondisi.")
        buang = df["kondisi"].map(lambda k: any(k.startswith(a) for a in args.abaikan))
        dibuang = sorted(df.loc[buang, "kondisi"].unique())
        df, score = df[~buang].reset_index(drop=True), score[(~buang).to_numpy()]
        tuning += (" Dikeluarkan dari angka ujian (dipakai menyetel simulator): "
                   + (", ".join(f"\"{k}\"" for k in dibuang) if dibuang else "tidak ada yang cocok") + ".")

    flags = build_flags(df, score, thr, ppt_times)
    res = summarize(df, flags, consecutive)
    info = {"n_sesi": int(df.sesi.nunique()), "threshold": thr, "consecutive": consecutive, "tuning": tuning}
    write_report(out, df, res, info, flags)
    print(out.read_text(encoding="utf-8"))
    if args.selftest:
        print("Pembanding: angka di atas harus sama dengan bagian window (test) di models/metrics.json "
              "bila dataset dan model tidak berubah.")


if __name__ == "__main__":
    main()
