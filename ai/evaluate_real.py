#!/usr/bin/env python3
"""Evaluasi model pada REKAMAN NYATA dari HP (sensor_log.csv).

Log diputar ulang melalui AIEngine yang sama dengan server (kalibrasi, window,
validasi, inference), memakai waktu kedatangan di log. Label berasal dari
kolom ``label`` (diatur dari dashboard / POST /api/label). Konvensi nama label:
  normal_*      -> seharusnya TIDAK alarm   (normal_idle, normal_walking, normal_vibration, ...)
  suspicious_*  -> seharusnya alarm         (suspicious_open_move, suspicious_open_rummage, ...)

Untuk perbandingan lengkap aturan vs AI (zona batas, sesi, tuning ambang) pakai ai/compare_real.py.

Pemakaian (dari folder utama proyek):
  python ai/evaluate_real.py [--log sensor_log.csv] [--role B] [--out ai/models/real_eval.md]

Setiap kali HP berhenti kirim >3 detik lalu mulai lagi dianggap sesi penjagaan
baru (kalibrasi ulang: HP harus diam beberapa detik). Tandai label SETELAH kalibrasi selesai.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402

config.LOG_CSV = False  # jangan menulis ai_log.csv saat replay
from server import decision_engine as de  # noqa: E402

THRESHOLDS = [0.40, 0.50, 0.60, 0.65, 0.70, 0.80]


def replay(df: pd.DataFrame, model_threshold: float, consecutive: int):
    """Putar ulang log; kembalikan DataFrame evaluasi (waktu, sesi, label, skor)."""
    engine = de.AIEngine("B")
    rows, session, last_t = [], 0, None
    for r in df.itertuples():
        t = float(r.waktu_laptop)
        if last_t is not None and t - last_t > de.GAP_RESET_S:
            session += 1
        last_t = t
        before = engine.evaluations
        msg = {"light": {"source": r.sumber_cahaya, "value": r.nilai_cahaya}, "accel_mag": r.accel_mag}
        engine.process(msg, t)
        if engine.evaluations > before:
            label = r.label if isinstance(r.label, str) and r.label else "(tanpa label)"
            rows.append({"t": t, "session": session, "label": label, "score": engine.score})
    return pd.DataFrame(rows)


def segment_alarms(ev: pd.DataFrame, thr: float, need: int) -> pd.DataFrame:
    """Per segmen (sesi, label berurutan): apakah ada >= need skor berurutan >= thr."""
    out = []
    ev = ev.sort_values("t").reset_index(drop=True)
    seg_id = ((ev.label != ev.label.shift()) | (ev.session != ev.session.shift())).cumsum()
    for _, g in ev.groupby(seg_id):
        run = best = 0
        for s in g.score:
            run = run + 1 if s >= thr else 0
            best = max(best, run)
        out.append({"label": g.label.iloc[0], "evals": len(g), "alarm": best >= need,
                    "mean_score": g.score.mean(), "max_score": g.score.max()})
    return pd.DataFrame(out)


def expected(label: str):
    if label.startswith("suspicious"):
        return True
    if label.startswith("normal"):
        return False
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", type=Path, default=ROOT / "sensor_log.csv")
    ap.add_argument("--role", default="B")  # HP penjaga (di dalam tas)
    ap.add_argument("--out", type=Path, default=ROOT / "ai" / "models" / "real_eval.md")
    args = ap.parse_args()

    model = de.get_model()
    if not model.available:
        sys.exit(f"Model tidak tersedia: {model.error}")
    df = pd.read_csv(args.log)
    if "role" in df.columns:
        df = df[df["role"] == args.role]
    n_all = len(df)
    df = df[df["sumber_cahaya"] == "camera"]
    skipped = n_all - len(df)
    df = df.sort_values("waktu_laptop").reset_index(drop=True)
    ev = replay(df, model.threshold, model.consecutive)
    if ev.empty:
        sys.exit("Tidak ada evaluasi (data terlalu sedikit; butuh >= 5 detik kalibrasi + 3 detik window).")

    L = ["# Evaluasi pada rekaman nyata\n",
         f"Sumber: `{args.log.name}`, role {args.role}, {len(df)} sampel kamera "
         f"({skipped} sampel non-kamera diabaikan), {len(ev)} evaluasi window.\n",
         "Rekaman kecil dan dibuat oleh tim: hasil ini indikatif, bukan akurasi deteksi pencurian nyata.\n",
         f"Model: ambang {model.threshold}, {model.consecutive} window berurutan.\n",
         "## Skor per label (ambang model)\n",
         "| Label | Evaluasi | Skor rata-rata | Skor maks | % window >= ambang | Segmen | Segmen alarm |",
         "|---|---|---|---|---|---|---|"]
    segs = segment_alarms(ev, model.threshold, model.consecutive)
    for label, g in ev.groupby("label"):
        s = segs[segs.label == label]
        L.append(f"| {label} | {len(g)} | {g.score.mean():.3f} | {g.score.max():.3f} | "
                 f"{100 * (g.score >= model.threshold).mean():.0f} | {len(s)} | {int(s.alarm.sum())} |")

    L += ["\n## Sweep ambang pada data nyata\n",
          "Segmen NORMAL yang alarm = salah alarm; segmen SUSPICIOUS yang alarm = terdeteksi.\n",
          "| Ambang | Salah alarm (segmen normal) | Terdeteksi (segmen suspicious) |", "|---|---|---|"]
    for thr in THRESHOLDS:
        s = segment_alarms(ev, thr, model.consecutive)
        s["exp"] = s.label.map(expected)
        n_norm, n_susp = (s.exp == False).sum(), (s.exp == True).sum()  # noqa: E712
        fa = f"{int(s[s.exp == False].alarm.sum())}/{n_norm}" if n_norm else "-"  # noqa: E712
        tp = f"{int(s[s.exp == True].alarm.sum())}/{n_susp}" if n_susp else "-"  # noqa: E712
        L.append(f"| {thr:.2f} | {fa} | {tp} |")
    text = "\n".join(L) + "\n"
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(text)


if __name__ == "__main__":
    main()
