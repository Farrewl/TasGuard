"""Inference Random Forest TasGuard (dimuat sekali oleh server).

Tidak mengirim alarm sendiri; hanya mengembalikan skor. Keputusan alarm ada di
server/decision_engine.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

try:  # dipakai server (paket ``ai``)
    from ai.feature_extraction import MODEL_FEATURES, feature_vector
except ImportError:  # dipakai sebagai skrip dari folder ai/
    from feature_extraction import MODEL_FEATURES, feature_vector  # type: ignore

DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "tasguard_rf.joblib"


class TasGuardModel:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else DEFAULT_MODEL_PATH
        self.available = False
        self.error = ""
        self.model = None
        self.feature_columns = list(MODEL_FEATURES)
        self.threshold = 0.65
        self.consecutive = 2
        self._load()

    def _load(self) -> None:
        try:
            import joblib

            bundle = joblib.load(self.path)
            self.model = bundle["model"]
            self.feature_columns = list(bundle["feature_columns"])
            if self.feature_columns != list(MODEL_FEATURES):
                raise ValueError("urutan fitur model berbeda dari feature_extraction.MODEL_FEATURES")
            self.threshold = float(bundle.get("threshold", self.threshold))
            self.consecutive = int(bundle.get("consecutive", self.consecutive))
            self.available = True
        except Exception as exc:  # model hilang / sklearn tidak terpasang / versi tidak cocok
            self.available = False
            self.error = f"{type(exc).__name__}: {exc}"

    def predict(self, features: Dict[str, float]) -> Dict[str, object]:
        if not self.available:
            raise RuntimeError("model tidak tersedia")
        vec = [feature_vector(features, self.feature_columns)]
        p_susp = float(self.model.predict_proba(vec)[0][1])
        return {
            "prediction": "SUSPICIOUS" if p_susp >= self.threshold else "NORMAL",
            "probability_normal": round(1.0 - p_susp, 4),
            "probability_suspicious": round(p_susp, 4),
        }
