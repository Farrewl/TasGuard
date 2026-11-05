"""Mesin keputusan hybrid: Random Forest + validasi berbasis aturan.

Satu AIEngine dipakai untuk HP penjaga (HP B, di dalam tas), satu-satunya
sumber sensor. HP pemantau (HP A) tidak punya AIEngine. Tanggung jawab:
  * kalibrasi baseline (AI_CALIBRATION_S detik pertama setelah penjagaan mulai)
  * buffer 15 sampel (window 3 detik @ 5 Hz), evaluasi tiap ~1 detik
  * validasi window (jumlah sampel, rentang waktu, celah data)
  * skor model -> alarm bila skor >= ambang pada N evaluasi berurutan + cooldown
  * model tidak tersedia / cahaya tidak didukung -> status saja, tanpa alarm

Waktu memakai jam laptop (waktu kedatangan), seperti detector.py.
Hasil yang diprediksi bukan bukti pencurian; hanya pola sensor yang mirip pola
SUSPICIOUS pada data pelatihan sintetis.
"""

import csv
import os
import time
from collections import deque
from statistics import median, pstdev
from typing import Optional

from server import config
from server.detector import current_label

try:
    from ai.feature_extraction import (LIGHT_EVIDENCE_S, MODEL_FEATURES, WINDOW_SAMPLES, compute_features,
                                        normalize_light)
    from ai.inference import TasGuardModel
except Exception as exc:  # numpy/ai tidak tersedia -> AI nonaktif tapi server tetap jalan
    compute_features = None  # type: ignore
    LIGHT_EVIDENCE_S = 0.4
    TasGuardModel = None  # type: ignore
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    _IMPORT_ERROR = ""

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AI_LOG_PATH = os.path.join(ROOT_DIR, "ai_log.csv")

EVAL_INTERVAL_S = 1.0       # stride window
GAP_RESET_S = 3.0           # jeda data lebih lama dari ini = pemantauan baru -> kalibrasi ulang
WINDOW_SPAN_MIN_S = 2.2     # 15 sampel @200 ms = 2.8 s; toleransi jitter jaringan
WINDOW_SPAN_MAX_S = 3.8
MAX_SAMPLE_GAP_S = 1.0

# Kalibrasi baseline: tunggu sampai HP stabil (diam) agar baseline tidak terambil
# saat HP masih dipegang atau sedang dimasukkan ke tas.
CAL_LIGHT_STD = 0.02        # simpangan baku cahaya (skala 0..1, sekitar 5 luma) yang dianggap stabil
CAL_ACCEL_STD = 0.3         # simpangan baku akselerasi (m/s^2) yang dianggap diam
CAL_MIN_SAMPLES = 10
CAL_MAX_WAIT_S = 45.0       # bila HP tidak pernah stabil selama ini, pakai sampel terbaru apa adanya
REBASE_DROP = 0.08          # baseline cahaya boleh TURUN bila HP stabil sebesar ini lebih gelap

_model = None


def get_model():
    """Muat model satu kali. Mengembalikan objek dengan atribut ``available``."""
    global _model
    if _model is not None:
        return _model
    if TasGuardModel is None:
        class _Unavailable:
            available = False
            error = _IMPORT_ERROR
            threshold = 0.65
            consecutive = 2
        _model = _Unavailable()
    else:
        path = config.AI_MODEL_PATH or None
        _model = TasGuardModel(path)
        if config.AI_THRESHOLD:
            _model.threshold = float(config.AI_THRESHOLD)
        if config.AI_CONSECUTIVE:
            _model.consecutive = int(config.AI_CONSECUTIVE)
    if not _model.available:
        print(f"[AI] Model tidak tersedia: {_model.error}")
    else:
        print(f"[AI] Model dimuat (ambang {_model.threshold}, "
              f"{_model.consecutive} window berurutan)")
    return _model


def model_info() -> dict:
    m = get_model()
    return {
        "enabled": config.AI_ENABLED,
        "available": bool(m.available),
        "error": "" if m.available else m.error,
        "threshold": m.threshold,
        "consecutive": m.consecutive,
    }


class AIEngine:
    def __init__(self, role: str) -> None:
        self.role = role
        self.model = get_model()
        self._last_t: Optional[float] = None
        self._last_alarm_time = 0.0
        self.evaluations = 0
        self._reset("idle")

    def reset(self) -> None:
        """Mulai dari awal (dipanggil saat penjagaan dimulai atau dihentikan)."""
        self._last_t = None
        self._reset("idle")

    def _reset(self, phase: str) -> None:
        self.phase = phase
        self._cal_start: Optional[float] = None
        self._cal: deque = deque()          # kandidat sampel kalibrasi (waktu, cahaya, akselerasi)
        self._recent: deque = deque()       # sampel terbaru saat siap, untuk menurunkan baseline
        self._cal_light: list = []          # baseline cahaya yang dipakai
        self._cal_accel: list = []          # baseline akselerasi yang dipakai
        self.cal_fallback = False           # baseline diambil tanpa HP pernah stabil
        self.rebaselines = 0                # berapa kali baseline cahaya diturunkan otomatis
        self._buffer: deque = deque(maxlen=WINDOW_SAMPLES)
        self._last_eval = 0.0
        self.consecutive = 0
        self.score: Optional[float] = None
        self.prediction: Optional[str] = None
        self.window_valid: Optional[bool] = None
        self.light_evidence: Optional[bool] = None   # window terakhir menunjukkan tas dibuka
        self.last_eval_at: Optional[float] = None
        self.cal_left: Optional[float] = None

    def status(self) -> dict:
        now = time.time()
        return {
            "enabled": config.AI_ENABLED,
            "model": "loaded" if self.model.available else "unavailable",
            "phase": self.phase,
            "calibration_left_s": None if self.cal_left is None else round(self.cal_left, 1),
            "baseline_luma": round(median(self._cal_light) * 255) if self._cal_light else None,
            "calibration_fallback": self.cal_fallback,
            "rebaselines": self.rebaselines,
            "score": None if self.score is None else round(self.score, 3),
            "prediction": self.prediction,
            "window_valid": self.window_valid,
            "light_evidence": self.light_evidence,
            "require_light": config.AI_REQUIRE_LIGHT,
            "consecutive": self.consecutive,
            "required": self.model.consecutive,
            "threshold": self.model.threshold,
            "last_eval_s": None if self.last_eval_at is None else round(now - self.last_eval_at, 1),
            "evaluations": self.evaluations,
        }

    def process(self, message: dict, now: Optional[float] = None) -> Optional[dict]:
        """Terima satu pesan sensor. Mengembalikan dict alarm (format detector) atau None."""
        now = time.time() if now is None else now

        if not self.model.available:
            self.phase = "unavailable"
            return None

        light = message.get("light") or {}
        source = str(light.get("source", "unknown"))
        try:
            raw_light = float(light.get("value", 0) or 0)
            mag = float(message.get("accel_mag"))
        except (TypeError, ValueError):
            return None
        light_n = normalize_light(raw_light, source)
        if light_n is None:
            if self.phase != "unsupported_light":
                self._reset("unsupported_light")
            self._last_t = now
            return None

        # Jeda panjang = pemantauan dimulai ulang -> kalibrasi baseline baru.
        if self._last_t is not None and now - self._last_t > GAP_RESET_S and self.phase != "idle":
            self._reset("idle")
        self._last_t = now

        if self.phase in ("idle", "unsupported_light", "unavailable"):
            self._reset("calibrating")
            self._cal_start = now

        if self.phase == "calibrating":
            self._calibrate(now, light_n, mag)
            return None

        # phase == "ready"
        self._buffer.append((now, light_n, mag, raw_light, source))
        self._recent.append((now, light_n, mag))
        self._trim(self._recent, now)
        if len(self._buffer) < WINDOW_SAMPLES or now - self._last_eval < EVAL_INTERVAL_S - 0.05:
            return None
        self._last_eval = now
        self._maybe_lower_baseline(now)
        return self._evaluate(now)

    # Kalibrasi baseline

    @staticmethod
    def _trim(points: deque, now: float) -> None:
        cutoff = now - (config.AI_CALIBRATION_S + 5.0)   # simpan sedikit lebih panjang dari yang dibutuhkan
        while points and points[0][0] < cutoff:
            points.popleft()

    @staticmethod
    def _stable_suffix(points: list, use_accel: bool = True) -> list:
        """Bagian TERBARU dari ``points`` yang stabil: cahaya (dan akselerasi bila use_accel) hampir tidak berubah."""
        start = len(points)
        for i in range(len(points) - 1, -1, -1):
            seg = points[i:]
            if len(seg) >= 3 and (pstdev(p[1] for p in seg) > CAL_LIGHT_STD
                                  or (use_accel and pstdev(p[2] for p in seg) > CAL_ACCEL_STD)):
                break
            start = i
        return points[start:]

    def _set_baseline(self, points: list, now: float) -> None:
        self._cal_light = [p[1] for p in points]
        self._cal_accel = [p[2] for p in points]
        self._buffer.clear()
        self._recent.clear()
        self._last_eval = now
        self.consecutive = 0
        self.phase = "ready"
        self.cal_left = None

    def _calibrate(self, now: float, light_n: float, mag: float) -> None:
        """Kumpulkan sampel; baseline = bagian terbaru yang stabil selama AI_CALIBRATION_S detik."""
        self._cal.append((now, light_n, mag))
        self._trim(self._cal, now)
        stable = self._stable_suffix(list(self._cal))
        span = stable[-1][0] - stable[0][0] if len(stable) > 1 else 0.0
        self.cal_left = max(0.0, config.AI_CALIBRATION_S - span)   # hitung mundur sisa waktu diam

        done = span >= config.AI_CALIBRATION_S - 0.3 and len(stable) >= CAL_MIN_SAMPLES
        waited = now - (self._cal_start or now)
        if not done and not (waited >= CAL_MAX_WAIT_S and len(self._cal) >= WINDOW_SAMPLES):
            return
        points = stable if done else [p for p in self._cal if p[0] >= now - config.AI_CALIBRATION_S]
        self.cal_fallback = not done
        self._set_baseline(points, now)

    def _maybe_lower_baseline(self, now: float) -> bool:
        """Turunkan baseline cahaya bila HP kini stabil di level yang jelas lebih gelap.

        Hanya ke arah GELAP: pencuri membuat cahaya naik, jadi baseline yang naik tidak
        pernah dipakai (akan menyembunyikan pencurian). Ini memperbaiki baseline yang
        terambil saat HP masih terkena cahaya sebelum masuk tas. Hanya baseline cahaya
        yang diubah (HP tidak perlu diam); baseline akselerasi dan window berjalan tetap.
        """
        recent = self._stable_suffix(list(self._recent), use_accel=False)
        if len(recent) < CAL_MIN_SAMPLES or recent[-1][0] - recent[0][0] < config.AI_CALIBRATION_S - 0.3:
            return False
        if median(p[1] for p in recent) > median(self._cal_light) - REBASE_DROP:
            return False
        self._cal_light = [p[1] for p in recent]
        self.consecutive = 0
        self.rebaselines += 1
        print(f"[AI] Baseline cahaya diturunkan (cahaya stabil lebih gelap), "
              f"baseline kini {round(median(self._cal_light) * 255)} luma")
        return False

    def _evaluate(self, now: float) -> Optional[dict]:
        buf = list(self._buffer)
        times = [b[0] for b in buf]
        span = times[-1] - times[0]
        max_gap = max(b - a for a, b in zip(times, times[1:]))
        self.last_eval_at = now
        if not (WINDOW_SPAN_MIN_S <= span <= WINDOW_SPAN_MAX_S) or max_gap > MAX_SAMPLE_GAP_S:
            self.window_valid = False  # data hilang / tidak teratur: jangan lakukan inference
            self.consecutive = 0
            return None
        self.window_valid = True

        feats = compute_features([b[1] for b in buf], [b[2] for b in buf], self._cal_light, self._cal_accel)
        result = self.model.predict(feats)
        score = float(result["probability_suspicious"])
        self.score = score
        # Syarat tas dibuka: tanpa kenaikan cahaya, getaran sekuat apa pun tidak dianggap mencurigakan.
        self.light_evidence = feats["light_elevated_duration"] >= LIGHT_EVIDENCE_S
        suspicious = score >= self.model.threshold and (self.light_evidence or not config.AI_REQUIRE_LIGHT)
        self.prediction = "SUSPICIOUS" if suspicious else "NORMAL"
        self.evaluations += 1
        self.consecutive = self.consecutive + 1 if self.prediction == "SUSPICIOUS" else 0
        self._log_eval(now, feats)

        if self.consecutive < self.model.consecutive:
            return None
        if now - self._last_alarm_time < config.AI_COOLDOWN_S:
            return None

        self._last_alarm_time = now
        self.consecutive = 0
        base_accel = sorted(self._cal_accel)[len(self._cal_accel) // 2]
        last = buf[-1]
        return {
            "type": "alarm",
            "level": "HIGH",
            "light": {"source": last[4], "value": last[3]},
            "shake_score": round(max(abs(b[2] - base_accel) for b in buf), 2),
            "trigger_sensor_ts": now,
            "ts": time.time(),
            "ai": {"score": round(score, 3), "threshold": self.model.threshold},
        }

    def _log_eval(self, now: float, feats: dict) -> None:
        if not config.LOG_CSV:
            return
        try:
            new = not os.path.exists(AI_LOG_PATH)
            with open(AI_LOG_PATH, "a", newline="", encoding="utf-8") as handle:
                w = csv.writer(handle)
                if new:
                    w.writerow(["waktu_laptop", "role", "label", "score", "prediction", "consecutive", *MODEL_FEATURES])
                w.writerow([f"{now:.3f}", self.role, current_label(), f"{self.score:.4f}", self.prediction,
                            self.consecutive, *[f"{feats[c]:.5f}" for c in MODEL_FEATURES]])
        except OSError as exc:
            print(f"[AI] Gagal menulis ai_log.csv: {exc}")
