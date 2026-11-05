"""Manajemen dua peran HP, status live, dan pengiriman peringatan satu arah.

Konsep (satu arah, B -> laptop -> A):
  HP A = Pemantau : dipegang pengguna. Hanya menerima status dan peringatan.
  HP B = Penjaga  : ditaruh di dalam tas. Satu-satunya sumber sensor; hasil AI
                    di laptop dikirim ke HP A. HP B sendiri tetap senyap.

Peran dipilih HP saat autentikasi (bukan urutan sambung). Satu peran hanya
boleh dipegang satu HP.
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Tuple

from server import config
from server.decision_engine import AIEngine, model_info
from server.detector import DetectionEngine, current_label, labels_payload

MONITOR = "A"            # HP pemantau (dipegang pengguna)
GUARD = "B"              # HP penjaga (di dalam tas)
ROLES = (MONITOR, GUARD)
STALE_AFTER_S = 15.0     # pemegang peran yang diam selama ini boleh digantikan HP lain

REASON_HIGH = "Aktivitas mencurigakan terdeteksi di dalam tas"
REASON_SILENT = "HP penjaga tidak mengirim data"
REASON_LOST = "Koneksi HP penjaga terputus"


def other_role(role: str) -> str:
    return GUARD if role == MONITOR else MONITOR


class Device:
    def __init__(self, role: str, device_id: str, websocket) -> None:
        self.role = role
        self.device_id = device_id
        self.ws = websocket
        self.connected_at = time.time()
        self.last_message_at = time.time()
        self.lock = asyncio.Lock()

        # Status tas (bermakna untuk penjaga)
        self.status: str = "safe"                 # "safe" | "danger"
        self.alarm_sent: Optional[str] = None     # level alarm terakhir yang sudah dikirim ke pemantau
        self.alarms: int = 0
        self.last_alarm: Optional[dict] = None

        # Khusus penjaga (HP B)
        self.guarding: bool = False               # pengguna sudah menekan Mulai Penjagaan
        self.guard_since: Optional[float] = None
        self.timeout_notified: bool = False
        self.lost_at: Optional[float] = None
        self.last_sensor: Optional[dict] = None
        self.last_sensor_at: Optional[float] = None
        self.location: Optional[dict] = None
        self.rule_alarms: int = 0                 # berapa kali aturan lama AKAN membunyikan alarm
        self.rule_last_at: Optional[float] = None
        self.detector: Optional[DetectionEngine] = None
        self.ai: Optional[AIEngine] = None
        if role == GUARD:
            self.detector = DetectionEngine(role)  # aturan lama: pembanding + log sensor
            self.detector.reset()
            self.ai = AIEngine(role)               # pemutus alarm (Random Forest)

    async def send(self, payload: dict) -> None:
        async with self.lock:
            try:
                await self.ws.send_text(json.dumps(payload))
            except Exception:
                pass

    def inherit(self, prev: "Device") -> None:
        """Warisi status dari sambungan sebelumnya milik HP yang sama (sambung ulang)."""
        self.status = prev.status
        self.alarms = prev.alarms
        self.last_alarm = prev.last_alarm
        self.guarding = prev.guarding
        self.guard_since = prev.guard_since
        self.last_sensor = prev.last_sensor
        self.last_sensor_at = prev.last_sensor_at
        self.location = prev.location
        self.rule_alarms = prev.rule_alarms
        self.rule_last_at = prev.rule_last_at


class DeviceRegistry:
    def __init__(self) -> None:
        self.devices: Dict[str, Optional[Device]] = {MONITOR: None, GUARD: None}
        # Penjaga yang terputus saat sedang menjaga; dipantau agar pemantau diberi tahu.
        self._lost_guard: Optional[Device] = None

    # Sambungan

    def assign(self, device_id: str, websocket, role: str) -> Tuple[Optional[Device], Optional[object], Optional[str]]:
        """Pasang HP ke peran pilihannya.

        Mengembalikan (Device baru, websocket lama yang harus ditutup, kode galat).
        Galat: "peran" (tidak valid) atau "peran_terpakai" (dipegang HP lain yang masih aktif)."""
        if role not in ROLES:
            return None, None, "peran"

        old = self.devices[role]
        if old and old.device_id != device_id and time.time() - old.last_message_at < STALE_AFTER_S:
            return None, None, "peran_terpakai"

        prev = old
        if prev is None and role == GUARD and self._lost_guard and self._lost_guard.device_id == device_id:
            prev = self._lost_guard

        new_dev = Device(role, device_id, websocket)
        if prev and prev.device_id == device_id:
            new_dev.inherit(prev)

        self.devices[role] = new_dev
        if role == GUARD:
            self._lost_guard = None
        return new_dev, (old.ws if old else None), None

    def release(self, role: str, websocket) -> None:
        dev = self.devices[role]
        if dev and dev.ws is websocket:
            self.devices[role] = None
            if role == GUARD and dev.guarding:
                dev.lost_at = time.time()
                dev.timeout_notified = False
                self._lost_guard = dev

    def touch(self, role: str) -> None:
        dev = self.devices[role]
        if dev:
            dev.last_message_at = time.time()

    # Pesan dari HP penjaga (B)

    def handle_guard(self, role: str, active: bool) -> bool:
        """Penjaga memulai atau menghentikan penjagaan. True bila status penjagaan benar-benar berubah."""
        dev = self.devices[role]
        if role != GUARD or not dev:
            return False
        changed = dev.guarding != bool(active)
        dev.guarding = bool(active)
        dev.guard_since = time.time() if active else None
        dev.timeout_notified = False
        dev.ai.reset()        # kalibrasi baseline diulang tiap penjagaan dimulai
        dev.detector.reset()
        return changed

    def handle_sensor(self, role: str, message: dict) -> Optional[dict]:
        """Proses satu sampel sensor. Hanya HP penjaga yang diterima."""
        dev = self.devices[role]
        if role != GUARD or not dev:
            return None

        now = time.time()
        if not dev.guarding:
            dev.guarding = True
            dev.guard_since = now
        dev.last_sensor_at = now
        dev.last_message_at = now
        dev.timeout_notified = False
        if dev.alarm_sent == "WARN":
            dev.alarm_sent = None   # data kembali normal; peringatan berikutnya boleh dikirim lagi

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

        threshold = config.LUX_THRESHOLD if source == "ambient_sensor" else config.LUMA_THRESHOLD

        dev.last_sensor = {
            "light": {"source": source, "value": value},
            "bright": value >= threshold,
            "accel_mag": mag,
            "deviation": round(delta, 2),
            "shaking": delta > config.ACCEL_THRESHOLD,
            "battery": message.get("battery"),
            "ts": message.get("ts", now),
        }

        rule_alarm = dev.detector.evaluate(message)
        if rule_alarm:
            dev.rule_alarms += 1
            dev.rule_last_at = now

        if config.AI_ENABLED:
            alarm = dev.ai.process(message, now)   # keputusan alarm berasal dari AI
        else:
            alarm = rule_alarm                     # mode lama (AI_ENABLED=false)

        if not alarm:
            return None

        dev.alarms += 1
        dev.last_alarm = {"level": alarm["level"], "reason": REASON_HIGH, "ts": round(now, 3)}
        if alarm["level"] == "HIGH":
            dev.status = "danger"
        alarm.update({"from": GUARD, "reason": REASON_HIGH, "server_ts": round(now, 3)})
        return alarm

    def handle_location(self, role: str, message: dict) -> None:
        """Lokasi penjaga (dikirim saat status tas bahaya)."""
        dev = self.devices[role]
        if role != GUARD or not dev:
            return

        try:
            lat = float(message.get("lat"))
            lng = float(message.get("lng"))
        except (TypeError, ValueError):
            return

        if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
            return

        try:
            accuracy = float(message.get("acc", 0) or 0)
        except (TypeError, ValueError):
            accuracy = 0.0

        now = time.time()
        dev.last_message_at = now
        dev.location = {
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "accuracy": round(accuracy, 1),
            "ts": round(now, 3),
        }

    # Pesan dari HP pemantau (A)

    def dismiss(self) -> None:
        """Pemantau menutup peringatan: status tas kembali aman."""
        dev = self.devices[GUARD] or self._lost_guard
        if dev:
            dev.status = "safe"
            dev.alarm_sent = None

    # Pemantauan koneksi penjaga

    def _warn(self, dev: Device, reason: str, now: float) -> dict:
        dev.alarms += 1
        dev.last_alarm = {"level": "WARN", "reason": reason, "ts": round(now, 3)}
        return {"type": "alarm", "level": "WARN", "from": GUARD, "reason": reason,
                "server_ts": round(now, 3), "ts": now}

    def check_timeouts(self) -> List[dict]:
        """Peringatan WARN bila penjaga yang sedang menjaga berhenti mengirim data atau terputus."""
        alarms: List[dict] = []
        now = time.time()

        dev = self.devices[GUARD]
        if dev and dev.guarding and not dev.timeout_notified:
            ref = dev.last_sensor_at or dev.guard_since or dev.connected_at
            if now - ref > config.SENSOR_TIMEOUT_S:
                dev.timeout_notified = True
                alarms.append(self._warn(dev, REASON_SILENT, now))

        lost = self._lost_guard
        if lost and not lost.timeout_notified and now - (lost.lost_at or now) > config.SENSOR_TIMEOUT_S:
            lost.timeout_notified = True
            alarms.append(self._warn(lost, REASON_LOST, now))
        return alarms

    # Status

    def _link(self, dev: Device, connected: bool, now: float) -> str:
        """Kondisi tautan penjaga: idle (belum menjaga), ok, silent (data berhenti), lost (terputus)."""
        if not dev.guarding:
            return "idle"
        if not connected:
            return "lost"
        ref = dev.last_sensor_at or dev.guard_since or dev.connected_at
        return "silent" if now - ref > config.SENSOR_TIMEOUT_S else "ok"

    def _guard_snapshot(self, dev: Device, connected: bool, now: float) -> dict:
        ls = dev.last_sensor or {}
        age = round(now - dev.last_sensor_at, 1) if dev.last_sensor_at else None
        location = None
        if dev.location:
            location = {**dev.location, "age_s": round(now - dev.location["ts"], 1)}
        return {
            "role": GUARD,
            "connected": connected,
            "device_id": dev.device_id,
            "guarding": dev.guarding,
            "status": dev.status,
            "link": self._link(dev, connected, now),
            "light": ls.get("light", {"source": "none", "value": 0}),
            "bright": ls.get("bright", False),
            "accel_mag": ls.get("accel_mag", 9.81),
            "deviation": ls.get("deviation", 0.0),
            "shaking": ls.get("shaking", False),
            "battery": ls.get("battery"),
            "location": location,
            "last_seen_s": age,
            "alarms": dev.alarms,
            "last_alarm": dev.last_alarm,
            "ai": dev.ai.status(),
            "rule_shadow": {
                "count": dev.rule_alarms,
                "last_ago_s": round(now - dev.rule_last_at, 1) if dev.rule_last_at else None,
            },
        }

    def snapshot(self, role: str) -> dict:
        now = time.time()
        dev = self.devices[role]

        if role == MONITOR:
            if not dev:
                return {"role": MONITOR, "connected": False}
            return {
                "role": MONITOR,
                "connected": True,
                "device_id": dev.device_id,
                "last_seen_s": round(now - dev.last_message_at, 1),
            }

        if dev:
            return self._guard_snapshot(dev, True, now)
        if self._lost_guard:
            return self._guard_snapshot(self._lost_guard, False, now)
        return {"role": GUARD, "connected": False, "guarding": False, "status": "offline", "link": "idle",
                "alarms": 0, "last_alarm": None, "location": None, "ai": None, "battery": None,
                "last_seen_s": None}

    # Pengiriman

    async def broadcast_alarm(self, alarm: dict) -> None:
        """Kirim peringatan HANYA ke pemantau (HP A). Penjaga tidak diberi tahu agar tetap senyap."""
        sender = self.devices[GUARD] or self._lost_guard

        # Alarm HIGH selalu diteruskan (laju dibatasi cooldown AI) agar kejadian yang berlanjut
        # tidak diam-diam terlewat. Peringatan WARN hanya sekali sampai kondisinya pulih.
        if sender:
            if alarm["level"] != "HIGH" and sender.alarm_sent in ("HIGH", "WARN"):
                return
            sender.alarm_sent = alarm["level"]

        monitor = self.devices[MONITOR]
        if monitor:
            await monitor.send(alarm)

    async def broadcast_peer_updates(self) -> None:
        """Kirim snapshot pasangan dan snapshot diri sendiri ke tiap HP yang tersambung."""
        for role in ROLES:
            dev = self.devices[role]
            if dev:
                payload = {"type": "peer", "peer": self.snapshot(other_role(role)), "self": self.snapshot(role),
                           "label": current_label()}
                await dev.send(payload)

    def status_api(self) -> dict:
        guard = self.devices[GUARD] or self._lost_guard
        return {
            "server_ts": round(time.time(), 3),
            "devices": {
                MONITOR: self.snapshot(MONITOR),
                GUARD: self.snapshot(GUARD),
            },
            "total_alarms": guard.alarms if guard else 0,
            "ai_model": model_info(),
            "label": current_label(),
            "labels": labels_payload(),
        }
