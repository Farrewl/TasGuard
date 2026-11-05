#!/usr/bin/env python3
"""Membuat dataset_report.md dari CSV dataset.

Semua angka di laporan dihitung di sini dari file di disk; tidak ada yang
diketik tangan. Model pembanding opsional dilatih di split train dan dinilai
HANYA di split validation. Split test tidak disentuh.

Pemakaian:  python ai/audit_dataset.py [--data-dir ai/dataset]
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
import generate_dataset as gen  # noqa: E402
from validate_dataset import rank_auc  # noqa: E402

F = gen.FEATURE_COLUMNS


def md_table(df, floatfmt="{:.3f}"):
    cols = list(df.columns)
    out = ["| " + " | ".join(str(c) for c in cols) + " |",
           "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        cells = []
        for v in row:
            if isinstance(v, (float, np.floating)):
                cells.append(floatfmt.format(v))
            elif isinstance(v, (int, np.integer)):
                cells.append(f"{int(v):,}")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def overlap_coefficient(a, b, bins=50):
    """Luas irisan dua histogram ternormalisasi (1 = identik, 0 = terpisah total)."""
    lo, hi = min(a.min(), b.min()), max(a.max(), b.max())
    if hi <= lo:
        return 1.0
    ha, _ = np.histogram(a, bins=bins, range=(lo, hi))
    hb, _ = np.histogram(b, bins=bins, range=(lo, hi))
    return float(np.minimum(ha / ha.sum(), hb / hb.sum()).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=HERE / "dataset")
    args = ap.parse_args()
    D = args.data_dir
    cfg = json.loads((D / gen.CONFIG_FILE).read_text(encoding="utf-8"))
    raw = pd.read_csv(D / gen.RAW_FILE)
    win = pd.read_csv(D / gen.WINDOW_FILE)
    log = pd.read_csv(D / gen.ARTIFACT_FILE)
    fam = raw.groupby("session_id")["scenario_label"].first()
    log["family"] = log["session_id"].map(fam)
    win["family"] = win["scenario"].map(gen.SCENARIOS)
    L = []
    add = L.append

    add("# Laporan audit dataset TasGuard\n")
    add(f"Konfigurasi dibaca dari `{gen.CONFIG_FILE}`: {cfg['sessions_per_scenario']} sesi per skenario, "
        f"{cfg['sampling_freq']:g} Hz, {cfg['duration_sec']:g} detik per sesi, window {cfg['window_sec']:g} detik, "
        f"stride {cfg['stride_sec']:g} detik, ambang label {cfg['label_threshold']}, seed {cfg['seed']}. "
        "Semua angka di bawah dihitung oleh `audit_dataset.py` dari file CSV.\n")

    # 1. ukuran
    add("## 1. Ukuran\n")
    add(md_table(pd.DataFrame([
        ["Sesi", raw["session_id"].nunique()], ["Baris sampel mentah", len(raw)],
        ["Baris window", len(win)], ["Sampel per sesi", int(raw.groupby("session_id").size().iloc[0])],
        ["Window per sesi", int(win.groupby("session_id").size().iloc[0])],
        ["Baris log artefak", len(log)]], columns=["Item", "Nilai"])) + "\n")

    # 2. distribusi
    add("## 2. Distribusi kelas, skenario, dan split\n")
    vc = win["label"].value_counts()
    add(md_table(pd.DataFrame({"Label window": vc.index, "Window": vc.values,
                               "Porsi": (vc.values / len(win) * 100).round(2).astype(str) + " %"})) + "\n")
    per_sc = win.pivot_table(index="scenario", columns="label", values="window_id", aggfunc="count", fill_value=0)
    per_sc["share_suspicious_%"] = (per_sc["SUSPICIOUS"] / per_sc.sum(axis=1) * 100).round(1)
    add("Window per skenario dan label (tiap skenario punya jumlah sesi yang sama):\n")
    add(md_table(per_sc.reset_index()) + "\n")
    sp = win.pivot_table(index="split", columns="label", values="window_id", aggfunc="count", fill_value=0)
    sp["Sesi"] = raw.groupby("split")["session_id"].nunique()
    sp["Baris mentah"] = raw.groupby("split").size()
    add("Distribusi split (sesi dibagi di dalam tiap skenario):\n")
    add(md_table(sp.reset_index()) + "\n")
    st = (raw[["scenario", "session_id", "split"]].drop_duplicates()
          .groupby(["scenario", "split"]).size().unstack(fill_value=0))
    add(f"Sesi per skenario dan split: {', '.join(f'{c}={int(st[c].iloc[0])}' for c in ['train', 'validation', 'test'])} "
        f"(sama untuk semua {len(st)} skenario: {'ya' if bool((st == st.iloc[0]).all().all()) else 'tidak'}).\n")

    # 3. event_fraction
    add("## 3. Distribusi event_fraction\n")
    efc = win["event_fraction"].round(4).value_counts().sort_index()
    add(md_table(pd.DataFrame({"event_fraction": efc.index, "Window": efc.values,
                               "Label": np.where(efc.index + 1e-9 >= cfg["label_threshold"], "SUSPICIOUS", "NORMAL")}),
                 floatfmt="{:.4f}") + "\n")
    susp_sessions = (win[win["family"] == "SUSPICIOUS"].groupby("session_id")["label"]
                     .apply(lambda s: (s == "SUSPICIOUS").sum()))
    zero = int((susp_sessions == 0).sum())
    add(f"{zero} dari {len(susp_sessions)} sesi skenario mencurigakan ({zero / len(susp_sessions):.1%}) tidak punya "
        "window SUSPICIOUS, karena kejadiannya singkat atau berada di tepi sesi sehingga tidak pernah mencapai "
        f"ambang {cfg['label_threshold']} dalam satu window. Porsi window SUSPICIOUS menurut posisi window:\n")
    pos = win.groupby("window_id")["label"].apply(lambda s: (s == "SUSPICIOUS").mean() * 100).round(1)
    add(md_table(pd.DataFrame({"window_id": pos.index.astype(int), "suspicious_%": pos.values}), floatfmt="{:.1f}") + "\n")
    add("Window 0 jarang SUSPICIOUS karena setiap kejadian dimulai setelah periode kalibrasi 1 detik. "
        "`window_id` bukan fitur model dan tidak boleh diberikan ke model.\n")

    # 4. rentang nilai sensor
    add("## 4. Rentang nilai sensor\n")
    rows = []
    for s in ["light_value", "accel_mag"]:
        for fam_name in ["NORMAL", "SUSPICIOUS"]:
            g = raw.loc[raw["scenario_label"] == fam_name, s]
            rows.append([s, fam_name, g.min(), g.quantile(.01), g.median(), g.quantile(.99), g.max()])
    add(md_table(pd.DataFrame(rows, columns=["Sensor", "Keluarga skenario", "Min", "1%", "Median", "99%", "Maks"])) + "\n")
    add(f"`light_value` adalah nilai simulasi ternormalisasi yang dipotong ke {cfg['light_bounds']}, bukan lux. "
        f"`accel_mag` dalam m/s2 dan dipotong ke {cfg['accel_bounds']}, dengan asumsi skala penuh 4 g.\n")
    sc = win.groupby("scenario")["accel_std"].median().round(3)
    add("Median `accel_std` window per skenario (m/s2), untuk memeriksa urutan tingkat aktivitas masuk akal:\n")
    add(md_table(pd.DataFrame({"scenario": sc.index, "median_accel_std": sc.values})) + "\n")
    cal = raw[raw["timestamp_sec"] < cfg["calibration_sec"] - 1e-9].groupby("session_id").agg(
        light=("light_value", "median"), accel=("accel_mag", "median"))
    add("Variasi antar sesi, memakai median periode kalibrasi tiap sesi:\n")
    add(md_table(pd.DataFrame({"Ukuran": ["Baseline cahaya", "Baseline akselerasi (m/s2)"],
                               "Min": [cal.light.min(), cal.accel.min()],
                               "10%": [cal.light.quantile(.1), cal.accel.quantile(.1)],
                               "Median": [cal.light.median(), cal.accel.median()],
                               "90%": [cal.light.quantile(.9), cal.accel.quantile(.9)],
                               "Maks": [cal.light.max(), cal.accel.max()]})) + "\n")

    # 5. statistik fitur
    add("## 5. Statistik fitur\n")
    rows = []
    for f in F:
        n, s = win.loc[win.label == "NORMAL", f], win.loc[win.label == "SUSPICIOUS", f]
        rows.append([f, win[f].min(), win[f].max(), n.mean(), n.median(), s.mean(), s.median()])
    add(md_table(pd.DataFrame(rows, columns=["Fitur", "Min", "Maks", "Rata-rata NORMAL", "Median NORMAL",
                                             "Rata-rata SUSPICIOUS", "Median SUSPICIOUS"]), floatfmt="{:.4g}") + "\n")

    # 6. nilai hilang / tak hingga
    add("## 6. Nilai hilang dan tak hingga\n")
    add(md_table(pd.DataFrame(
        [["Sampel mentah", int(raw.isna().sum().sum()), int(np.isinf(raw.select_dtypes('number')).sum().sum())],
         ["Fitur window", int(win.isna().sum().sum()), int(np.isinf(win.select_dtypes('number')).sum().sum())]],
        columns=["Tabel", "Sel kosong", "Nilai tak hingga"])) + "\n")
    add("Pembacaan yang hilang pada simulasi tidak diekspor sebagai NaN, melainkan diisi dengan pembacaan "
        "sebelumnya sehingga kolom sensor tampak seperti nilai yang tertahan. Log artefak mencatatnya sebagai "
        "`dropout_locf`, terpisah dari `held_value` (pembacaan macet), meskipun keduanya tampak sama di CSV.\n")

    # 7. artefak dan pencilan
    add("## 7. Artefak sensor dan pencilan\n")
    piv = log.pivot_table(index=["sensor", "artifact_type"], columns="family", values="n_samples",
                          aggfunc="sum", fill_value=0)
    piv["Kejadian"] = log.groupby(["sensor", "artifact_type"]).size()
    piv["Sesi terdampak"] = log.groupby(["sensor", "artifact_type"])["session_id"].nunique()
    piv["% baris mentah"] = (piv[["NORMAL", "SUSPICIOUS"]].sum(axis=1) / len(raw) * 100).round(3)
    add(f"Jumlah sampel terdampak dari `{gen.ARTIFACT_FILE}`. Kedua keluarga mendapat peluang artefak yang sama:\n")
    add(md_table(piv.reset_index()) + "\n")
    n_sess_fam = fam.value_counts()
    has_art = log.groupby("family")["session_id"].nunique()
    add("Porsi sesi dengan minimal satu artefak tercatat: " +
        ", ".join(f"{k} {has_art.get(k, 0) / n_sess_fam[k]:.1%}" for k in ["NORMAL", "SUSPICIOUS"]) + ".\n")
    flagged = pd.Series(False, index=win.index)
    for f in F:
        q1, q3 = win[f].quantile([.25, .75])
        iqr = q3 - q1
        if iqr > 0:
            flagged |= (win[f] < q1 - 1.5 * iqr) | (win[f] > q3 + 1.5 * iqr)
    byc = win.assign(flag=flagged).groupby("label")["flag"].agg(["sum", "mean", "size"])
    add("Window dengan minimal satu fitur di luar pagar Tukey (1,5 x IQR atas seluruh window). "
        "Ini pencilan statistik, belum tentu gangguan, dan tidak ada yang dibuang:\n")
    add(md_table(pd.DataFrame({"label": byc.index, "window": byc["sum"].astype(int).values,
                               "porsi_kelas_%": (byc["mean"] * 100).round(2).values})) + "\n")
    # porsi window yang memuat minimal satu lonjakan hasil suntikan, menurut label window
    stride = int(round(cfg["stride_sec"] * cfg["sampling_freq"]))
    n_win = int(round(cfg["window_sec"] * cfg["sampling_freq"]))
    spikes = log[log["artifact_type"] == "spike"]
    hit = set()
    for r in spikes.itertuples(index=False):
        for w in range(int(win["window_id"].max()) + 1):
            if w * stride <= r.start_sample < w * stride + n_win:
                hit.add((r.session_id, w))
    win["has_spike"] = [(a, b) in hit for a, b in zip(win["session_id"], win["window_id"])]
    sp_by = win.groupby("label")["has_spike"].mean() * 100
    add("Porsi window yang memuat minimal satu sampel lonjakan hasil suntikan: " +
        ", ".join(f"{k} {v:.1f} %" for k, v in sp_by.items()) +
        ". Porsi yang ditandai Tukey lebih tinggi pada window SUSPICIOUS karena window itu memuat aktivitas "
        "simulasi nyata (sentakan, paparan cahaya), bukan karena artefak terkonsentrasi di sana. Lonjakan "
        "tersebar merata di kedua kelas sehingga tidak bisa menjadi jalan pintas.\n")
    add("Istilah yang dipakai di proyek ini: pembacaan valid tetapi tidak biasa adalah kejadian simulasi nyata "
        "seperti sentakan atau paparan cahaya kuat. Gangguan sensor adalah lonjakan, nilai macet, atau clipping "
        "yang tercatat di log artefak. Nilai tidak valid atau hilang adalah dropout, yang pada dataset ini "
        "diwakili pengisian dengan nilai sebelumnya.\n")

    # 8. kebocoran
    add("## 8. Kebocoran sesi dan kebocoran target\n")
    cross = int((raw.groupby("session_id")["split"].nunique() > 1).sum())
    wcross = int((win.groupby("session_id")["split"].nunique() > 1).sum())
    mism = int((raw.groupby("session_id")["split"].first().sort_index()
                != win.groupby("session_id")["split"].first().sort_index()).sum())
    add(md_table(pd.DataFrame([["Sesi mentah di lebih dari satu split", cross],
                               ["Sesi window di lebih dari satu split", wcross],
                               ["Ketidakcocokan split mentah/window", mism]], columns=["Pemeriksaan", "Jumlah"])) + "\n")
    leak_ids = bool(raw["session_id"].str.contains("|".join(gen.SCENARIOS)).any())
    add(f"ID sesi anonim dan diacak (nama skenario ditemukan di ID: {'ya' if leak_ids else 'tidak'}). "
        f"Masukan model hanya {len(F)} kolom fitur. `event_fraction`, `label`, `scenario`, `session_id`, "
        "`window_id`, timestamp, dan `split` tidak boleh menjadi masukan model.\n")

    # 9. tumpang tindih kelas dan jalan pintas
    add("## 9. Tumpang tindih kelas dan tinjauan jalan pintas\n")
    tr = win[win.split == "train"]
    y = (tr.label == "SUSPICIOUS").to_numpy()
    rows = []
    for f in F:
        a = rank_auc(tr[f].to_numpy(dtype=float), y)
        ovl = overlap_coefficient(win.loc[win.label == "NORMAL", f].to_numpy(),
                                  win.loc[win.label == "SUSPICIOUS", f].to_numpy())
        rows.append([f, a, max(a, 1 - a), ovl])
    auc_df = pd.DataFrame(rows, columns=["Fitur", "AUC (train)", "Kekuatan max(AUC,1-AUC)", "Irisan histogram"])
    add("Keterpisahan satu fitur pada split train (AUC 0,5 = tanpa informasi, 1,0 = sempurna). "
        "Irisan adalah luas bersama dua histogram ternormalisasi 50 bin:\n")
    add(md_table(auc_df) + "\n")
    best = auc_df.sort_values("Kekuatan max(AUC,1-AUC)", ascending=False).iloc[0]
    strongest = float(best['Kekuatan max(AUC,1-AUC)'])
    if strongest >= 0.9:
        add(f"Fitur tunggal terkuat adalah `{best['Fitur']}` dengan AUC {best['AUC (train)']:.3f}. "
            "Satu fitur hampir cukup untuk memisahkan kelas. Ini konsekuensi konsep dataset (mencurigakan = tas "
            "dibuka = cahaya naik), sehingga metrik model pada data sintetis ini akan sangat tinggi dan tidak "
            "boleh dibaca sebagai akurasi pada pencurian nyata.\n")
    else:
        add(f"Fitur tunggal terkuat adalah `{best['Fitur']}` dengan AUC {best['AUC (train)']:.3f}. "
            f"Tidak ada fitur yang memisahkan kelas sendirian (irisan terkecil {auc_df['Irisan histogram'].min():.2f}).\n")
    nm, sp_ = win[win.label == "NORMAL"], win[win.label == "SUSPICIOUS"]
    p_hi_light = float(np.mean(nm["light_max"] >= sp_["light_max"].median()))
    p_hi_acc = float(np.mean(nm["accel_max"] >= sp_["accel_max"].median()))
    ql, qa = nm["light_delta"].abs().quantile(.75), nm["accel_std"].quantile(.75)
    quiet = float(np.mean((sp_["light_delta"].abs() <= ql) & (sp_["accel_std"] <= qa)))
    add(md_table(pd.DataFrame([
        ["Window NORMAL dengan light_max sama atau di atas median SUSPICIOUS", f"{p_hi_light:.1%}"],
        ["Window NORMAL dengan accel_max sama atau di atas median SUSPICIOUS", f"{p_hi_acc:.1%}"],
        ["Window SUSPICIOUS yang tidak lebih aktif dari persentil ke-75 window NORMAL (nilai mutlak light_delta dan accel_std)",
         f"{quiet:.1%}"],
        ["Window skenario normal dengan accel_max di atas 15 m/s2",
         f"{(win[win.family == 'NORMAL']['accel_max'] > 15).mean():.1%}"],
        ["Window NORMAL dari skenario mencurigakan (kejadian di luar window atau di bawah ambang)",
         f"{int(((win.family == 'SUSPICIOUS') & (win.label == 'NORMAL')).sum()):,}"]],
        columns=["Contoh tumpang tindih", "Nilai"])) + "\n")

    # model pembanding
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score
        va = win[win.split == "validation"]
        rf = RandomForestClassifier(n_estimators=300, random_state=0, n_jobs=-1, class_weight="balanced")
        rf.fit(tr[F], y)
        pv = rf.predict_proba(va[F])[:, 1]
        yv = (va.label == "SUSPICIOUS").to_numpy()
        add("Uji kewajaran dengan Random Forest tanpa penyetelan (300 pohon, class_weight balanced), dilatih di "
            "train dan dinilai di validation. Split test tidak dipakai. Ini pemeriksaan tingkat kesulitan, "
            "bukan hasil model:\n")
        add(md_table(pd.DataFrame([["Accuracy", accuracy_score(yv, pv > .5)],
                                   ["Balanced accuracy", balanced_accuracy_score(yv, pv > .5)],
                                   ["F1 (SUSPICIOUS)", f1_score(yv, pv > .5)], ["ROC AUC", roc_auc_score(yv, pv)]],
                                  columns=["Metrik", "Validation"])) + "\n")
        imp = pd.Series(rf.feature_importances_, index=F).sort_values(ascending=False).head(5)
        add("Lima fitur terpenting: " + ", ".join(f"{k} {v:.3f}" for k, v in imp.items()) + ".\n")
    except ImportError:
        add("scikit-learn tidak terpasang, sehingga uji model pembanding dilewati.\n")

    add("Tinjauan jalan pintas, berdasarkan angka di atas:\n")
    if strongest >= 0.9:
        add(f"1. Satu fitur hampir menentukan sendirian (`{best['Fitur']}`, AUC {best['AUC (train)']:.3f}) karena "
            "kejadian mencurigakan didefinisikan sebagai tas dibuka. Ini disengaja, tetapi berarti model hanya "
            "sedikit lebih baik dari aturan cahaya sederhana pada data ini.")
    else:
        add(f"1. Tidak ada fitur tunggal yang menentukan (AUC terbaik {best['AUC (train)']:.3f}). "
            "Fitur cahaya dan akselerometer sama-sama membawa sinyal.")
    add("2. Keluarga skenario terhubung ke label secara konstruksi: skenario NORMAL tidak pernah memuat kejadian. "
        "Kolom `scenario` tidak boleh menjadi masukan model. Karena berjalan biasa muncul di sebagian sesi "
        "mencurigakan, aktivitas akselerometer saja tidak cukup untuk menentukan kelas.")
    add(f"3. Posisi kejadian berpengaruh: hanya {pos.iloc[0]:.1f} % window 0 yang SUSPICIOUS dibanding "
        f"{pos.max():.1f} % pada posisi tersibuk. Model yang melihat indeks window akan memanfaatkannya, "
        "sehingga `window_id` tidak dipakai sebagai masukan.")
    add("4. Lonjakan dan nilai macet tercatat dengan laju sama di kedua keluarga, sehingga bukan jalan pintas kelas.")
    add("5. Label bergantung pada waktu kejadian, bukan kekuatan sensor. Sebagian window SUSPICIOUS memuat sinyal "
        "lemah dan sebagian window NORMAL memuat sinyal kuat. Sebagian galat model apa pun pada data ini "
        "karenanya tidak dapat dihindari.\n")

    add("## 10. Batas audit ini\n")
    add("Audit ini memeriksa konsistensi internal dan tingkat kesulitan sebuah simulasi. Audit tidak dapat "
        "menunjukkan bahwa perilaku sensor simulasi cocok dengan HP, tas, atau pencurian yang sebenarnya. "
        "Untuk itu diperlukan rekaman nyata.\n")
    (D / "dataset_report.md").write_text("\n".join(L), encoding="utf-8", newline="\n")
    print("dataset_report.md ditulis")


if __name__ == "__main__":
    main()
