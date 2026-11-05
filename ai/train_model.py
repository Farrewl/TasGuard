#!/usr/bin/env python3
"""Latih Random Forest TasGuard, lalu simpan model dan metrik.

Alur:
  1. Baca fitur window (kolom ``split`` sudah berdasarkan sesi).
  2. Pakai hanya MODEL_FEATURES dari feature_extraction.py.
  3. Latih di split train; window ambigu (0 < event_fraction < ambang label)
     dikeluarkan dari training saja.
  4. Pilih ambang keputusan di split validation: tengah dataran ambang yang
     membuat sesi NORMAL yang alarm <= --max-false-alarm dan deteksinya hampir
     terbaik. Sebuah sesi dianggap alarm bila ada ``CONSECUTIVE`` window
     berurutan dengan skor >= ambang DAN tas terbuka (cahaya naik).
  5. Evaluasi split test SATU kali, dibandingkan dengan aturan threshold lama.
  6. Simpan tasguard_rf.joblib (model + metadata), metrics.json, model_report.md.

Pemakaian:  python ai/train_model.py [--data-dir ai/dataset]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (accuracy_score, confusion_matrix, precision_recall_fscore_support,
                             roc_auc_score)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from feature_extraction import LIGHT_EVIDENCE_S, MODEL_FEATURES  # noqa: E402

CONSECUTIVE = 2   # jumlah window berurutan di atas ambang agar alarm berbunyi
RF_PARAMS = dict(n_estimators=100, max_depth=8, min_samples_split=4, min_samples_leaf=2,
                 class_weight="balanced", random_state=42, n_jobs=-1)


def rule_old(df: pd.DataFrame) -> np.ndarray:
    """Aproksimasi aturan lama (terang bertahan + getar bersamaan) pada fitur window."""
    return ((df.light_elevated_duration >= 0.4) & (df.motion_duration >= 0.6)).to_numpy()


def has_light_evidence(df: pd.DataFrame) -> np.ndarray:
    """Bukti tas dibuka: cahaya naik di atas baseline cukup lama dalam window."""
    return (df.light_elevated_duration >= LIGHT_EVIDENCE_S).to_numpy()


def is_ambiguous(df: pd.DataFrame, label_threshold: float) -> pd.Series:
    """Window yang memuat sebagian kejadian tetapi belum cukup untuk berlabel SUSPICIOUS."""
    return (df.event_fraction > 0) & (df.event_fraction < label_threshold)


def window_metrics(y, pred, score=None) -> dict:
    p, r, f, _ = precision_recall_fscore_support(y, pred, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    out = {"accuracy": accuracy_score(y, pred), "precision": p, "recall": r, "f1": f,
           "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)}
    if score is not None:
        out["roc_auc"] = roc_auc_score(y, score)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}


def session_alarm_rates(df: pd.DataFrame, flag: np.ndarray, consecutive: int) -> dict:
    """Persentase sesi yang memicu alarm (>= consecutive window berurutan terflag)."""
    d = df[["session_id", "window_id", "scenario"]].copy()
    d["flag"] = flag
    d = d.sort_values(["session_id", "window_id"])
    res = {}
    for sid, g in d.groupby("session_id"):
        run = best = 0
        for v in g.flag.to_numpy().astype(int):
            run = run + 1 if v else 0
            best = max(best, run)
        res[sid] = (g.scenario.iloc[0], best >= consecutive)
    s = pd.DataFrame(res, index=["scenario", "alarm"]).T
    s["family"] = s.scenario.str.startswith("suspicious").map({True: "suspicious", False: "normal"})
    per_scn = s.groupby("scenario").alarm.mean().mul(100).round(1).to_dict()
    fam = s.groupby("family").alarm.mean().mul(100).round(1).to_dict()
    return {"normal_sessions_alarm_pct": fam.get("normal", 0.0),
            "suspicious_sessions_alarm_pct": fam.get("suspicious", 0.0),
            "per_scenario_alarm_pct": per_scn}


def load(data_dir: Path) -> pd.DataFrame:
    df = pd.read_csv(data_dir / "tasguard_window_features.csv")
    df["y"] = (df.label == "SUSPICIOUS").astype(int)
    return df


def run(data_dir: Path, max_false_alarm: float, label_threshold: float) -> tuple:
    df = load(data_dir)
    train, val, test = (df[df.split == s] for s in ("train", "validation", "test"))
    fit_df = train[~is_ambiguous(train, label_threshold)]
    model = RandomForestClassifier(**RF_PARAMS).fit(fit_df[MODEL_FEATURES].to_numpy(), fit_df.y.to_numpy())

    pv = model.predict_proba(val[MODEL_FEATURES].to_numpy())[:, 1]
    pt = model.predict_proba(test[MODEL_FEATURES].to_numpy())[:, 1]

    # Ambang dipilih di validation (bukan di test): TENGAH dataran ambang yang memenuhi batas salah
    # alarm dan deteksinya hampir terbaik. Tengah dataran lebih tahan terhadap pergeseran skor di data nyata
    # daripada ambang terendah atau tertinggi yang kebetulan lolos.
    rows = []
    for t in np.round(np.arange(0.30, 0.91, 0.05), 2):
        r = session_alarm_rates(val, (pv >= t) & has_light_evidence(val), CONSECUTIVE)
        rows.append({"threshold": float(t), **{k: r[k] for k in
                     ("normal_sessions_alarm_pct", "suspicious_sessions_alarm_pct")}})
    allowed = [r for r in rows if r["normal_sessions_alarm_pct"] <= max_false_alarm * 100]
    if allowed:
        best_detect = max(r["suspicious_sessions_alarm_pct"] for r in allowed)
        plateau = [r["threshold"] for r in allowed if r["suspicious_sessions_alarm_pct"] >= best_detect - 1.0]
        best_t = plateau[len(plateau) // 2]
    else:
        best_t = 0.9
    sweep = pd.DataFrame(rows)

    amb_test = is_ambiguous(test, label_threshold).to_numpy()
    open_test = has_light_evidence(test)
    final = (pt >= best_t) & open_test      # keputusan yang dipakai server: skor tinggi DAN tas dibuka
    res = {
        "n_train_fit": int(len(fit_df)), "n_val": int(len(val)), "n_test": int(len(test)),
        "threshold": best_t, "consecutive": CONSECUTIVE,
        "validation_sweep": rows,
        "test_window_rf_thr_0.5": window_metrics(test.y, pt >= 0.5, pt),
        "test_window_rf_model_only": window_metrics(test.y, pt >= best_t, pt),
        "test_window_rf_selected": window_metrics(test.y, final, pt),
        "test_window_rule_old": window_metrics(test.y, rule_old(test)),
        "test_window_rf_ambiguous_removed": window_metrics(test[~amb_test].y, final[~amb_test]),
        "test_session_rf": session_alarm_rates(test, final, CONSECUTIVE),
        "test_session_rf_model_only": session_alarm_rates(test, pt >= best_t, CONSECUTIVE),
        "test_session_rule_old": session_alarm_rates(test, rule_old(test), CONSECUTIVE),
        "feature_importance": dict(sorted(zip(MODEL_FEATURES, model.feature_importances_.round(4).tolist()),
                                          key=lambda kv: -kv[1])),
    }
    return model, res, sweep


def write_report(path: Path, m: dict, sweep: pd.DataFrame) -> None:
    def row(name, d):
        return (f"| {name} | {d['accuracy']:.3f} | {d['precision']:.3f} | {d['recall']:.3f} | "
                f"{d['f1']:.3f} | {d['fp']} | {d['fn']} |")

    lines = ["# Laporan model TasGuard\n",
             "Dilatih pada data SINTETIS. Angka ini mengukur kecocokan dengan definisi pola di generator, "
             "bukan akurasi deteksi pencurian nyata.\n",
             f"Ambang keputusan dipilih di validation: skor >= {m['threshold']} pada {m['consecutive']} "
             "window berurutan (stride 1 detik). Split test dievaluasi sekali.\n",
             f"Server hanya membunyikan alarm bila skor tinggi DAN tas terbuka (cahaya naik >= {LIGHT_EVIDENCE_S} detik "
             "dalam window). Getaran tanpa tas dibuka tidak memicu alarm.\n",
             "## Tingkat window (test)\n",
             "| Metode | Accuracy | Precision | Recall | F1 | FP | FN |", "|---|---|---|---|---|---|---|",
             row("Aturan lama (aproksimasi)", m["test_window_rule_old"]),
             *([row("Random Forest saja, ambang 0,5", m["test_window_rf_thr_0.5"])] if m["threshold"] != 0.5 else []),
             row(f"Random Forest saja, ambang {m['threshold']}", m["test_window_rf_model_only"]),
             row(f"Random Forest + syarat tas dibuka (dipakai server)", m["test_window_rf_selected"]),
             f"\nROC AUC Random Forest: {m['test_window_rf_selected']['roc_auc']}\n",
             "## Tingkat sesi (test): persentase sesi yang memicu alarm\n",
             "| Metode | Sesi NORMAL alarm (salah alarm) | Sesi SUSPICIOUS terdeteksi |", "|---|---|---|",
             f"| Aturan lama | {m['test_session_rule_old']['normal_sessions_alarm_pct']} % | "
             f"{m['test_session_rule_old']['suspicious_sessions_alarm_pct']} % |",
             f"| Random Forest saja | {m['test_session_rf_model_only']['normal_sessions_alarm_pct']} % | "
             f"{m['test_session_rf_model_only']['suspicious_sessions_alarm_pct']} % |",
             f"| Random Forest + syarat tas dibuka (dipakai server) | {m['test_session_rf']['normal_sessions_alarm_pct']} % | "
             f"{m['test_session_rf']['suspicious_sessions_alarm_pct']} % |\n",
             "Per skenario (Random Forest + syarat tas dibuka, % sesi alarm):\n",
             "| Skenario | % |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in m["test_session_rf"]["per_scenario_alarm_pct"].items()]
    lines += ["\n## Sweep ambang di validation\n",
              "| Ambang | Sesi NORMAL alarm % | Sesi SUSPICIOUS terdeteksi % |", "|---|---|---|"]
    lines += [f"| {r.threshold:.2f} | {r.normal_sessions_alarm_pct} | {r.suspicious_sessions_alarm_pct} |"
              for r in sweep.itertuples()]
    lines += ["\n## Kepentingan fitur\n", "| Fitur | Importance |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in m["feature_importance"].items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=HERE / "dataset")
    ap.add_argument("--max-false-alarm", type=float, default=0.10,
                    help="batas porsi sesi NORMAL yang boleh alarm (0..1) saat memilih ambang")
    ap.add_argument("--label-threshold", type=float, default=0.4)
    ap.add_argument("--out", type=Path, default=HERE / "models")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    model, metrics, sweep = run(args.data_dir, args.max_false_alarm, args.label_threshold)

    joblib.dump({
        "model": model,
        "feature_columns": MODEL_FEATURES,
        "threshold": metrics["threshold"],
        "consecutive": CONSECUTIVE,
        "light_evidence_s": LIGHT_EVIDENCE_S,
        "sklearn_version": sklearn.__version__,
        "note": "Dilatih pada data sintetis; bukan bukti deteksi pencurian nyata.",
    }, args.out / "tasguard_rf.joblib", compress=3)
    (args.out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8", newline="\n")
    write_report(args.out / "model_report.md", metrics, sweep)
    print(json.dumps({k: metrics[k] for k in ("threshold", "test_window_rf_selected", "test_window_rule_old",
                                               "test_session_rf", "test_session_rule_old")}, indent=2))


if __name__ == "__main__":
    main()
