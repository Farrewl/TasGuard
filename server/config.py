import os
from dotenv import load_dotenv

load_dotenv()

PORT = int(os.getenv("PORT", "8000"))
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
LUX_THRESHOLD = float(os.getenv("LUX_THRESHOLD", "15"))
LUMA_THRESHOLD = float(os.getenv("LUMA_THRESHOLD", "40"))
LIGHT_HOLD_MS = int(os.getenv("LIGHT_HOLD_MS", "500"))
ACCEL_THRESHOLD = float(os.getenv("ACCEL_THRESHOLD", "2.5"))
SHAKE_MIN_SAMPLES = int(os.getenv("SHAKE_MIN_SAMPLES", "3"))
COINCIDENCE_WINDOW_S = float(os.getenv("COINCIDENCE_WINDOW_S", "2"))
ALARM_COOLDOWN_S = float(os.getenv("ALARM_COOLDOWN_S", "30"))
SENSOR_TIMEOUT_S = float(os.getenv("SENSOR_TIMEOUT_S", "10"))
LOG_CSV = os.getenv("LOG_CSV", "true").lower() == "true"

# AI (Random Forest). AI_ENABLED=false mengembalikan ke detektor threshold lama.
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() == "true"
AI_MODEL_PATH = os.getenv("AI_MODEL_PATH", "")  # kosong = ai/models/tasguard_rf.joblib
AI_CALIBRATION_S = float(os.getenv("AI_CALIBRATION_S", "5"))  # lama HP harus diam agar baseline diambil
AI_COOLDOWN_S = float(os.getenv("AI_COOLDOWN_S", "10"))        # jeda minimum antar alarm AI
AI_REQUIRE_LIGHT = os.getenv("AI_REQUIRE_LIGHT", "true").lower() == "true"  # alarm hanya bila tas terbuka (cahaya naik)
AI_THRESHOLD = os.getenv("AI_THRESHOLD", "")      # kosong = pakai ambang dari model
AI_CONSECUTIVE = os.getenv("AI_CONSECUTIVE", "")  # kosong = pakai nilai dari model
