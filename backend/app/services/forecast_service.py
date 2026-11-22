"""AI demand prediction (FR-17, FR-18, FR-19, NFR-14).

A scikit-learn RandomForestRegressor predicts the hourly occupancy (% of capacity)
of each garage from the garage, hour of day, day of week and weekend flag.
The model is trained on the occupancy_history table; the last 7 days are held out
to measure the mean absolute error (NFR-14 target: <= 10 percentage points).
"""
import math
import random
from datetime import datetime, timedelta

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import MODEL_PATH
from ..models import ForecastModelInfo, Garage, OccupancyHistory

ALGORITHM = "RandomForestRegressor"
_model = None
_cache: dict = {}


# ------------------------------------------------------------ training data
def _profile(hour: int, weekend: bool, kind: str) -> float:
    """Typical occupancy curve used to generate the simulated history."""
    office = [12, 10, 8, 8, 8, 12, 25, 55, 80, 88, 86, 80, 74, 78, 82, 78, 70, 58, 45, 35, 28, 22, 18, 14]
    mall = [8, 6, 5, 5, 5, 6, 8, 12, 20, 30, 42, 55, 62, 64, 66, 70, 76, 84, 90, 88, 76, 55, 30, 15]
    mixed = [10, 8, 7, 7, 7, 9, 18, 40, 62, 70, 72, 72, 74, 76, 78, 76, 72, 70, 66, 60, 48, 36, 24, 15]
    base = {"office": office, "mall": mall, "mixed": mixed}[kind][hour]
    if weekend:
        base = base * (0.45 if kind == "office" else 1.12 if kind == "mall" else 0.85)
    return base


GARAGE_KIND = {0: "office", 1: "mall", 2: "mixed"}


def generate_history(db: Session, days: int = 56, seed: int = 7) -> int:
    rnd = random.Random(seed)
    end = clock.now().replace(minute=0, second=0)
    garages = db.scalars(select(Garage).order_by(Garage.garage_id)).all()
    rows = 0
    for idx, g in enumerate(garages):
        kind = GARAGE_KIND.get(idx % 3)
        t = end - timedelta(days=days)
        while t < end:
            weekend = t.weekday() in (5, 6)          # Saturday / Sunday in the UAE
            pct = _profile(t.hour, weekend, kind) + rnd.gauss(0, 5)
            db.add(OccupancyHistory(garage_id=g.garage_id, hour_start=t,
                                    occupancy_pct=round(max(0, min(100, pct)), 1)))
            rows += 1
            t += timedelta(hours=1)
    db.commit()
    return rows


def _features(garage_id: int, t: datetime) -> list[float]:
    return [garage_id, t.hour, t.weekday(), 1.0 if t.weekday() in (5, 6) else 0.0,
            math.sin(2 * math.pi * t.hour / 24), math.cos(2 * math.pi * t.hour / 24)]


# ------------------------------------------------------------------ training
def train(db: Session) -> ForecastModelInfo:
    """Train (or retrain) the model and record its accuracy (FR-19, NFR-14)."""
    global _model
    hist = db.scalars(select(OccupancyHistory).order_by(OccupancyHistory.hour_start)).all()
    if not hist:
        raise ValueError("No occupancy history to train on")
    cutoff = hist[-1].hour_start - timedelta(days=7)
    train_rows = [h for h in hist if h.hour_start <= cutoff]
    test_rows = [h for h in hist if h.hour_start > cutoff]
    X = np.array([_features(h.garage_id, h.hour_start) for h in train_rows])
    y = np.array([h.occupancy_pct for h in train_rows])
    model = RandomForestRegressor(n_estimators=80, min_samples_leaf=3, random_state=42, n_jobs=-1)
    model.fit(X, y)
    Xt = np.array([_features(h.garage_id, h.hour_start) for h in test_rows])
    yt = np.array([h.occupancy_pct for h in test_rows])
    mae = float(np.mean(np.abs(model.predict(Xt) - yt))) if len(test_rows) else None
    # final model is fitted on all data
    model.fit(np.vstack([X, Xt]) if len(test_rows) else X, np.concatenate([y, yt]) if len(test_rows) else y)
    model.set_params(n_jobs=1)      # single-row predictions are faster without worker threads
    joblib.dump(model, MODEL_PATH)
    _model = model
    _cache.clear()
    for old in db.scalars(select(ForecastModelInfo).where(ForecastModelInfo.status == "Active")):
        old.status = "Retired"
    info = ForecastModelInfo(algorithm=ALGORITHM, trained_at=clock.now(), n_samples=len(hist),
                             mae_pct=round(mae, 2) if mae is not None else None, status="Active")
    db.add(info)
    db.commit()
    return info


def schedule_retrain(db: Session, when: datetime) -> ForecastModelInfo:
    info = ForecastModelInfo(algorithm=ALGORITHM, scheduled_for=when, status="Scheduled")
    db.add(info)
    db.commit()
    return info


def run_scheduled(db: Session) -> int:
    due = db.scalars(select(ForecastModelInfo).where(ForecastModelInfo.status == "Scheduled",
                                                     ForecastModelInfo.scheduled_for <= clock.now())).all()
    for d in due:
        d.status = "Retired"
        train(db)
    return len(due)


def _get_model():
    global _model
    if _model is None and MODEL_PATH.exists():
        _model = joblib.load(MODEL_PATH)
        _model.set_params(n_jobs=1)
    return _model


def reset_cache():
    global _model
    _model = None
    _cache.clear()


# ----------------------------------------------------------------- inference
def predict(garage_id: int, t: datetime) -> float | None:
    """Predicted occupancy % for the hour that contains t, or None if no model."""
    key = (garage_id, t.replace(minute=0, second=0, microsecond=0))
    if key in _cache:
        return _cache[key]
    model = _get_model()
    if model is None:
        return None
    if len(_cache) > 20000:
        _cache.clear()
    _cache[key] = round(float(model.predict(np.array([_features(garage_id, key[1])]))[0]), 1)
    return _cache[key]


def level(pct: float | None) -> str:
    """Busy/quiet indicator shown to drivers (FR-18)."""
    if pct is None:
        return "unknown"
    if pct < 40:
        return "quiet"
    if pct < 70:
        return "normal"
    if pct < 85:
        return "busy"
    return "very busy"


def forecast_24h(garage_id: int, start: datetime | None = None) -> list[dict]:
    """Next 24 hourly predictions for one garage (FR-17)."""
    start = (start or clock.now()).replace(minute=0, second=0)
    out = []
    for i in range(24):
        t = start + timedelta(hours=i)
        p = predict(garage_id, t)
        out.append({"hour": t.isoformat(), "occupancy_pct": p, "level": level(p)})
    return out


def suggest_times(garage_id: int, start: datetime, hours: float, window: int = 3) -> list[dict]:
    """Suggest quieter start times within +/- window hours of the requested time (FR-18)."""
    options = []
    for d in range(-window, window + 1):
        s = start + timedelta(hours=d)
        if s < clock.now():
            continue
        p = predict(garage_id, s)
        if p is not None:
            options.append({"start_time": s.isoformat(), "occupancy_pct": p, "level": level(p), "shift_hours": d})
    options.sort(key=lambda o: o["occupancy_pct"])
    return options[:3]
