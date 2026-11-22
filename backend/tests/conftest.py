"""Test setup: one seeded template database, copied fresh before every test."""
import os
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="smartpark_test_"))
os.environ["SMARTPARK_DATA_DIR"] = str(TMP)
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["DEMO_MODE"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import clock  # noqa: E402
from backend.app.config import MODEL_PATH  # noqa: E402
from backend.app.database import engine  # noqa: E402
from backend.app.main import app  # noqa: E402
from backend.app.seed import seed  # noqa: E402
from backend.app.services import forecast_service  # noqa: E402

DB = TMP / "smartpark.db"
seed()
engine.dispose()
shutil.copy(DB, TMP / "template.db")
shutil.copy(MODEL_PATH, TMP / "template.joblib")

PASSWORDS = {"admin@smartpark.ae": "Admin@1234", "attendant@smartpark.ae": "Attend@1234",
             "driver@smartpark.ae": "Driver@1234", "corporate@smartpark.ae": "Corp@1234"}


@pytest.fixture(autouse=True)
def fresh_db():
    engine.dispose()
    shutil.copy(TMP / "template.db", DB)
    shutil.copy(TMP / "template.joblib", MODEL_PATH)
    forecast_service.reset_cache()
    clock.reset()
    yield
    clock.reset()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def login(client, email, password=None) -> dict:
    r = client.post("/api/auth/login", json={"email": email,
                                             "password": password or PASSWORDS.get(email, "Driver@1234")})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def iso(dt):
    return dt.replace(second=0, microsecond=0).isoformat()


def slot(hours_from_now: float, duration_h: float = 2):
    start = clock.now() + timedelta(hours=hours_from_now)
    start = start.replace(second=0)
    return iso(start), iso(start + timedelta(hours=duration_h))


def vehicle(client, h, permit=None, vtype=None) -> int:
    for v in client.get("/api/vehicles", headers=h).json():
        if (permit is None or v["has_accessible_permit"] == permit) and (vtype is None or v["vehicle_type"] == vtype):
            return v["vehicle_id"]
    raise AssertionError("no matching vehicle")


def book(client, h, garage_id=1, spot_type="standard", hours_from_now=2, duration_h=2,
         vehicle_id=None, token="pm_card_visa", expect=201) -> dict:
    s, e = slot(hours_from_now, duration_h)
    vid = vehicle_id or vehicle(client, h, permit=True if spot_type == "accessible" else None)
    r = client.post("/api/reservations", headers=h, json={"garage_id": garage_id, "spot_type": spot_type,
                                                          "start_time": s, "end_time": e, "vehicle_id": vid})
    assert r.status_code == expect, r.text
    if expect != 201:
        return r.json()
    res = r.json()
    if token:
        p = client.post(f"/api/reservations/{res['reservation_id']}/pay", headers=h, json={"payment_token": token})
        assert p.status_code == 200, p.text
        res["payment"] = p.json()
    return res
