"""Aturan threshold lama (terang bertahan + getar bersamaan) dan log sensor.

Aturan ini kini hanya pembanding (*shadow*) bagi AI: hasilnya dihitung dan
ditampilkan di dashboard, tetapi tidak membunyikan alarm kecuali AI_ENABLED=false.
Modul ini juga menulis setiap sampel sensor HP penjaga ke sensor_log.csv.

Murni Python, tanpa FastAPI/WebSocket. Semua jendela waktu memakai jam laptop
(time.time()), bukan jam HP.
"""

import csv
import os
import time
from typing import List, Optional, Tuple

from server import config

CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "sensor_log.csv")
CSV_HEADER = ["waktu_laptop", "waktu_hp", "sumber_cahaya", "nilai_cahaya", "accel_mag", "deviasi", "baterai",
              "role", "label"]

# Label kondisi untuk merekam data nyata. Diatur dari HP A (pesan WebSocket "label") atau dari
# dashboard laptop (POST /api/label). Daftar ini satu-satunya sumber nama label.
LABELS = [
    ("normal_idle", "Tas tertutup, diam"),
    ("normal_walking", "Tas tertutup, dibawa berjalan"),
    ("normal_handling", "Tas tertutup, diangkat/diletakkan"),
    ("normal_vibration", "Tas tertutup, digoyang kuat"),
    ("suspicious_open_move", "Tas dibuka, tangan bergerak"),
    ("suspicious_open_rummage", "Tas dibuka, isi diaduk"),
    ("suspicious_open_pull", "Tas dibuka, barang ditarik"),
]
LABEL_VALUES = {value for value, _ in LABELS}

_label = {"name": ""}


def set_label(name: str) -> None:
    _label["name"] = name.strip()[:40]


def current_label() -> str:
    return _label["name"]


def labels_payload() -> list:
    """Daftar label untuk klien: [{value, text}, ...]."""
    return [{"value": value, "text": text} for value, text in LABELS]


class DetectionEngine:
    """Detektor threshold lama untuk satu HP penjaga."""

    def __init__(self, role: str = "?") -> None:
        self.role = role
        self._accel_samples: List[Tuple[float, float]] = []  # (waktu laptop, deviasi)
        self._bright_since: Optional[float] = None
        self._last_source: Optional[str] = None
        self._last_shake_time: float = 0.0
        self._last_alarm_time: float = 0.0

    # Siklus penjagaan

    def reset(self) -> None:
        """Kosongkan keadaan (dipanggil saat penjagaan dimulai atau dihentikan)."""
        self._accel_samples.clear()
        self._bright_since = None
        if config.LOG_CSV:
            self._init_csv()

    # CSV

    def _init_csv(self) -> None:
        if os.path.exists(CSV_PATH):
            try:
                with open(CSV_PATH, "r", encoding="utf-8") as handle:
                    header = handle.readline().strip().split(",")
                if header == CSV_HEADER:
                    return
                os.replace(CSV_PATH, CSV_PATH + ".old")  # format lama: simpan, mulai file baru
            except OSError:
                return
        try:
            with open(CSV_PATH, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(CSV_HEADER)
        except OSError as exc:
            print(f"[CSV] Gagal membuat file log: {exc}")

    def _append_csv(
        self,
        now: float,
        sensor_ts: float,
        source: str,
        value: float,
        mag: float,
        delta: float,
        battery,
    ) -> None:
        if not config.LOG_CSV:
            return
        try:
            with open(CSV_PATH, "a", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerow(
                    [f"{now:.3f}", f"{sensor_ts:.3f}", source, f"{value:.1f}", f"{mag:.3f}", f"{delta:.3f}", battery,
                     self.role, _label["name"]]
                )
        except OSError as exc:
            print(f"[CSV] Gagal menulis log: {exc}")

    # Evaluasi data sensor

    def evaluate(self, message: dict) -> Optional[dict]:
        """Catat sampel dan jalankan aturan lama. Mengembalikan dict alarm atau None."""
        now = time.time()

        light = message.get("light") or {}
        source = str(light.get("source", "unknown"))
        try:
            value = float(light.get("value", 0) or 0)
        except (TypeError, ValueError):
            value = 0.0

        try:
            mag = float(message.get("accel_mag", 9.81) or 9.81)
        except (TypeError, ValueError):
            mag = 9.81
        delta = abs(mag - 9.81)

        try:
            sensor_ts = float(message.get("ts", now) or now)
        except (TypeError, ValueError):
            sensor_ts = now

        battery = message.get("battery")
        self._append_csv(now, sensor_ts, source, value, mag, delta, battery)

        threshold = config.LUX_THRESHOLD if source == "ambient_sensor" else config.LUMA_THRESHOLD
        if source != self._last_source:
            self._last_source = source
            self._bright_since = None

        # 1. Terang yang bertahan (LIGHT_HOLD_MS)
        if value >= threshold:
            if self._bright_since is None:
                self._bright_since = now
        else:
            self._bright_since = None

        light_hold = (now - self._bright_since) if self._bright_since else 0.0

        # 2. Getar dalam sliding window 1 detik
        self._accel_samples.append((now, delta))
        cutoff = now - 1.0
        self._accel_samples = [item for item in self._accel_samples if item[0] >= cutoff]

        shake_count = sum(1 for _, d in self._accel_samples if d > config.ACCEL_THRESHOLD)
        is_shaking = shake_count >= config.SHAKE_MIN_SAMPLES
        if is_shaking:
            self._last_shake_time = now
        shake_score = max((d for _, d in self._accel_samples), default=0.0)

        # 3. Coincidence: terang bertahan + getar dalam jendela yang sama
        if not self._bright_since:
            return None
        if light_hold * 1000 < config.LIGHT_HOLD_MS:
            return None
        if (now - self._last_shake_time) > config.COINCIDENCE_WINDOW_S:
            return None
        if (now - self._last_alarm_time) < config.ALARM_COOLDOWN_S:
            return None

        self._last_alarm_time = now
        self._bright_since = None  # alarm sudah dikirim; tunggu bukaan berikutnya

        return {
            "type": "alarm",
            "level": "HIGH",
            "light": {"source": source, "value": value},
            "shake_score": round(shake_score, 2),
            "trigger_sensor_ts": sensor_ts,
            "ts": time.time(),
        }
