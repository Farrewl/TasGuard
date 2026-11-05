#!/usr/bin/env python3
"""Uji terkontrol AIEngine dengan aliran sensor buatan (tanpa HP dan tanpa server).

Tujuannya memisahkan dua hal yang di lapangan sering tercampur:
  A. getaran TANPA tas dibuka, dinilai oleh MODEL SAJA (syarat tas dibuka sengaja dimatikan);
  A2. getaran yang sama dengan syarat tas dibuka AKTIF (seperti server);
  B. tas dibuka dengan berbagai lama dan kuat gerakan tangan, dengan syarat tas dibuka AKTIF
     (seperti server);
  C. tas dibuka sedikit (cahaya hanya naik ke luma 60) dengan tangan pelan.

Tiap sel diulang 20-25 kali dengan seed berbeda; angka = persen ulangan yang memicu alarm.
Aliran sensor: 5 detik kalibrasi tas gelap (luma ~2), 3 detik tenang, lalu kejadian, lalu 4 detik tenang.

Pemakaian (dari folder utama proyek):  python ai/uji_terkontrol.py
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from server import config  # noqa: E402

config.LOG_CSV = False  # tidak menulis ai_log.csv
from server import decision_engine as de  # noqa: E402

MULAI_KEJADIAN_S = 8     # 5 detik kalibrasi + 3 detik tenang


def aliran(durasi: float, amp: float, luma_buka: float | None, seed: int) -> bool:
    """Satu ulangan: True bila AIEngine membunyikan alarm."""
    rng = random.Random(seed)
    eng = de.AIEngine("B")
    alarm = False
    for i in range(int((MULAI_KEJADIAN_S + durasi + 4) * 5)):
        d = i / 5
        luma = 2 + rng.gauss(0, 0.5)
        acc = 9.8 + rng.gauss(0, 0.03)
        if MULAI_KEJADIAN_S <= d < MULAI_KEJADIAN_S + durasi:
            if luma_buka is not None:
                luma = luma_buka + rng.gauss(0, 8)
            acc += amp * math.sin(2 * math.pi * 1.8 * (d - MULAI_KEJADIAN_S) + rng.random()) + rng.gauss(0, amp * 0.3 + 0.01)
        if eng.process({"light": {"source": "camera", "value": max(0.0, min(255.0, luma))}, "accel_mag": acc}, 1000 + d):
            alarm = True
    return alarm


def persen(durasi: float, amp: float, luma_buka: float | None, ulangan: int) -> str:
    return f"{100 * np.mean([aliran(durasi, amp, luma_buka, s) for s in range(ulangan)]):.0f}%"


def main() -> None:
    print("A. MODEL SAJA (syarat tas dibuka dimatikan): getaran tanpa tas dibuka")
    print("baris = lama getaran; kolom = kuat getaran (m/s2)")
    config.AI_REQUIRE_LIGHT = False
    print(pd.DataFrame({f"{d} dtk": {f"{a}": persen(d, a, None, 20) for a in (0.5, 1.0, 2.0, 4.0)}
                        for d in (2, 4, 8)}).T.to_string())
    config.AI_REQUIRE_LIGHT = True

    print("\nA2. Getaran yang sama, syarat tas dibuka AKTIF (seperti server)")
    print(pd.DataFrame({f"{d} dtk": {f"{a}": persen(d, a, None, 20) for a in (0.5, 1.0, 2.0, 4.0)}
                        for d in (2, 4, 8)}).T.to_string())

    print("\nB. TAS DIBUKA di tempat gelap (cahaya naik ke luma 150), syarat tas dibuka AKTIF")
    print("baris = lama tas terbuka; kolom = kuat gerakan tangan (m/s2; 0.0 = tangan diam)")
    print(pd.DataFrame({f"{d} dtk": {f"{a}": persen(d, a, 150, 25) for a in (0.0, 0.3, 1.0, 2.0, 4.0)}
                        for d in (1, 1.5, 2, 3, 5)}).T.to_string())

    print("\nC. TAS DIBUKA sedikit (luma naik hanya ke 60), tangan pelan, syarat tas dibuka AKTIF")
    print("   " + "  ".join(f"{d} dtk: {persen(d, 0.3, 60, 25)}" for d in (1.5, 2, 3)))


if __name__ == "__main__":
    main()
