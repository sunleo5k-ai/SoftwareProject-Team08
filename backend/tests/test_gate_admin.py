"""gate-access, forecast, admin, reporting and corporate tests
TC-17..TC-23, TC-26..TC-30, ML-01, SEC-04 (FR-17..23, FR-26..30; NFR-03, NFR-11, NFR-14)."""
import time
from datetime import timedelta

from sqlalchemy import select

from backend.app import clock
from backend.app.database import SessionLocal
from backend.app.models import AuditLog, ForecastModelInfo, GateEvent, OverrideLog

from .conftest import book, login, slot

DEVICE = {"X-Device-Key": "demo-device-key"}
PLATE = "AD 1 23456"            # the demo driver's car


def _entry(client, **kw):
    body = {"garage_id": 1, "lane": "E1", **kw}
    return client.post("/api/gate/entry", headers=DEVICE, json=body).json()


# ---------------------------------------------------------------- forecast
def test_TC17_24_hour_forecast(client):
    h = login(client, "driver@smartpark.ae")
    f = client.get("/api/garages/1/forecast", headers=h).json()
    assert len(f["hours"]) == 24
    assert all(0 <= x["occupancy_pct"] <= 100 for x in f["hours"])
    assert {x["level"] for x in f["hours"]} <= {"quiet", "normal", "busy", "very busy"}


def test_TC18_busy_quiet_indicator_and_best_times(client):
    h = login(client, "driver@smartpark.ae")
    f = client.get("/api/garages/1/forecast", headers=h).json()
    assert len(f["best_times"]) == 3
    quiet_prices = [b["price_standard"] for b in f["best_times"]]
    assert max(quiet_prices) <= max(x["price_standard"] for x in f["hours"])


def test_TC19_retrain_now_and_scheduled(client):
    admin = login(client, "admin@smartpark.ae")
    r = client.post("/api/admin/forecast/retrain", headers=admin).json()
    assert r["status"] == "Active" and r["mae_pct"] is not None
    run_at = (clock.now() + timedelta(minutes=30)).replace(second=0).isoformat()
    s = client.post("/api/admin/forecast/schedule", headers=admin, json={"run_at": run_at}).json()
    assert s["status"] == "Scheduled"
    clock.advance(31)
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["scheduled_retrains"] == 1
    db = SessionLocal()
    assert db.scalar(select(ForecastModelInfo).where(ForecastModelInfo.status == "Active")).model_id > r["model_id"]
    db.close()


def test_ML01_forecast_mae_within_10_points():
    db = SessionLocal()
    m = db.scalar(select(ForecastModelInfo).where(ForecastModelInfo.status == "Active"))
    db.close()
    print(f"\nML-01 hold-out MAE = {m.mae_pct} percentage points ({m.n_samples} hourly samples)")
    assert m.mae_pct <= 10


# -------------------------------------------------------------- gate access
def test_TC20_anpr_entry_admits_matching_reservation_once(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, hours_from_now=0.25)              # starts in 15 min -> inside early window
    t = time.perf_counter()
    r = _entry(client, plate=PLATE, confidence=0.97)
    elapsed = time.perf_counter() - t
    assert r["decision"] == "ADMIT" and r["barrier"] == "OPEN" and r["spot"] == res["spot_code"]
    assert elapsed < 2.0                                     # NFR-03
    again = _entry(client, plate=PLATE, confidence=0.97)
    assert again["decision"] == "DENY" and again["reason"] == "ALREADY_USED"


def test_TC20_entry_denials(client):
    assert _entry(client, plate="ZZ 999", confidence=0.99)["reason"] == "NO_RESERVATION"
    h = login(client, "driver@smartpark.ae")
    book(client, h, garage_id=2, hours_from_now=0.25)
    assert _entry(client, plate=PLATE, confidence=0.99)["reason"] == "WRONG_GARAGE"
    book(client, h, garage_id=1, hours_from_now=3)
    assert _entry(client, plate=PLATE, confidence=0.99)["reason"] == "TOO_EARLY"


def test_TC21_low_confidence_needs_qr(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, hours_from_now=0.25)
    assert _entry(client, plate=PLATE, confidence=0.72)["reason"] == "LOW_CONFIDENCE"
    qr = client.get(f"/api/reservations/{res['reservation_id']}", headers=h).json()["qr_token"]
    r = _entry(client, qr_token="SMARTPARK:" + qr)
    assert r["decision"] == "ADMIT"


def test_TC22_exit_on_time_and_with_overstay(client):
    h = login(client, "driver@smartpark.ae")
    book(client, h, hours_from_now=0.25, duration_h=1)
    _entry(client, plate=PLATE, confidence=0.95)
    clock.advance(60)
    out = client.post("/api/gate/exit", headers=DEVICE, json={"garage_id": 1, "plate": PLATE, "confidence": 0.95}).json()
    assert out["barrier"] == "OPEN" and out["overstay_fee"] == 0
    # second booking, leave 50 minutes late -> 1 started hour x base 10 x 1.5
    book(client, login(client, "driver@smartpark.ae"), hours_from_now=0.25, duration_h=1)
    _entry(client, plate=PLATE, confidence=0.95)
    clock.advance(15 + 60 + 50)
    out = client.post("/api/gate/exit", headers=DEVICE, json={"garage_id": 1, "plate": PLATE, "confidence": 0.95}).json()
    assert out["overstay_minutes"] >= 50 and out["overstay_fee"] == 15.0


def test_TC23_attendant_override_is_logged(client):
    att = login(client, "attendant@smartpark.ae")
    assert client.post("/api/gate/override", headers=att, json={"garage_id": 1, "plate": "AD 7 1111",
                       "decision": "ADMIT", "reason": "ok"}).status_code == 400          # reason too short
    r = client.post("/api/gate/override", headers=att, json={"garage_id": 1, "plate": "AD 7 1111",
                    "decision": "ADMIT", "reason": "Plate damaged, booking shown on phone"})
    assert r.status_code == 200 and r.json()["barrier"] == "OPEN"
    drv = login(client, "driver@smartpark.ae")
    assert client.post("/api/gate/override", headers=drv, json={"garage_id": 1, "plate": "X", "decision": "ADMIT",
                       "reason": "let me in please"}).status_code == 403
    db = SessionLocal()
    assert db.scalar(select(OverrideLog)).reason.startswith("Plate damaged")
    assert db.scalar(select(AuditLog).where(AuditLog.action == "manual_override")) is not None
    db.close()


def test_SEC04_plate_reads_purged_after_30_days(client):
    _entry(client, plate="AD 3 3333", confidence=0.99)
    db = SessionLocal()
    ev = db.scalar(select(GateEvent))
    ev.created_at = clock.now() - timedelta(days=31)
    disputed = GateEvent(garage_id=1, lane="E1", direction="entry", plate_text="AD 4 4444", confidence=0.9,
                         decision="DENY", reason="NO_RESERVATION", dispute_open=True,
                         created_at=clock.now() - timedelta(days=40))
    db.add(disputed)
    db.commit()
    db.close()
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["plate_reads_purged"] == 1
    db = SessionLocal()
    plates = [e.plate_text for e in db.scalars(select(GateEvent))]
    db.close()
    assert "AD3333" not in plates and "AD 4 4444" in plates


# ------------------------------------------------------------------- admin
def test_TC26_manage_garages_floors_zones_spots(client):
    admin = login(client, "admin@smartpark.ae")
    g = client.post("/api/admin/garages", headers=admin, json={"name": "Reem Island Tower Garage",
                    "area": "Al Reem Island", "address": "Al Reem Island, Abu Dhabi",
                    "latitude": 24.49, "longitude": 54.40, "base_standard": 9}).json()
    f = client.post(f"/api/admin/garages/{g['garage_id']}/floors", headers=admin, json={"name": "Level 1", "level_no": 1}).json()
    z = client.post(f"/api/admin/floors/{f['floor_id']}/zones", headers=admin,
                    json={"name": "A", "standard": 8, "ev": 2, "accessible": 1}).json()
    assert z["spots_created"] == 11
    drv = login(client, "driver@smartpark.ae")
    found = client.get("/api/garages", headers=drv, params={"q": "reem"}).json()[0]
    assert found["capacity"] == 11 and found["base_rate"] == 9
    spot_id = client.get("/api/admin/garages", headers=admin).json()[-1]["floors"][0]["zones"][0]["spots"][0]["spot_id"]
    assert client.patch(f"/api/admin/spots/{spot_id}", headers=admin, json={"is_active": False}).status_code == 200
    assert client.get("/api/garages", headers=drv, params={"q": "reem"}).json()[0]["capacity"] == 10
    assert client.post("/api/admin/garages", headers=drv, json={}).status_code in (403, 422)


def test_TC27_pricing_rules_validation_versioning_and_audit(client):
    admin = login(client, "admin@smartpark.ae")
    cur = client.get("/api/admin/garages/1/pricing", headers=admin).json()["active"]
    bad = {**cur, "cap_multiplier": 0.7, "floor_multiplier": 0.9}
    r = client.put("/api/admin/garages/1/pricing", headers=admin, json=bad)
    assert r.status_code == 422 and r.json()["detail"]["errors"]
    bad = {**cur, "cap_multiplier": 2.5}
    assert client.put("/api/admin/garages/1/pricing", headers=admin, json=bad).status_code == 422
    # a booking made before the change keeps its quoted price
    drv = login(client, "driver@smartpark.ae")
    res = book(client, drv)
    good = {**cur, "base_standard": 14, "base_accessible": 14, "expected_version": cur["version"]}
    prev = client.post("/api/admin/garages/1/pricing/preview", headers=admin, json=good).json()
    assert prev["errors"] == [] and prev["preview"][2]["accessible"] == 14
    ok = client.put("/api/admin/garages/1/pricing", headers=admin, json=good).json()
    assert ok["version"] == cur["version"] + 1
    assert client.put("/api/admin/garages/1/pricing", headers=admin, json=good).status_code == 409   # stale version
    assert client.get(f"/api/reservations/{res['reservation_id']}", headers=drv).json()["quote"]["total"] == res["quote"]["total"]
    log = client.get("/api/admin/audit", headers=admin).json()
    assert log[0]["action"] == "update_pricing" and '"base_standard": 14' in log[0]["new"]


def test_TC28_reports_and_csv_export(client):
    admin = login(client, "admin@smartpark.ae")
    rep = client.get("/api/admin/reports/summary", headers=admin).json()
    assert rep["kpis"]["reservations"] > 100 and rep["kpis"]["revenue_aed"] > 0
    assert 0 < rep["kpis"]["no_show_rate_pct"] < 20
    assert len(rep["occupancy_by_hour"]) == 24 and len(rep["peak_hours"]) == 3
    assert len(rep["revenue_by_day"]) == 14
    csv_text = client.get("/api/admin/reports/export.csv", headers=admin).text
    assert csv_text.startswith("booking_ref,garage,floor,spot") and csv_text.count("\n") > 100


def test_TC29_user_management_with_audit(client):
    admin = login(client, "admin@smartpark.ae")
    users = client.get("/api/admin/users", headers=admin).json()
    corp = next(u for u in users if u["email"] == "corporate@smartpark.ae")
    me = next(u for u in users if u["email"] == "admin@smartpark.ae")
    assert client.patch(f"/api/admin/users/{me['user_id']}", headers=admin, json={"is_active": False}).status_code == 400
    r = client.patch(f"/api/admin/users/{corp['user_id']}", headers=admin, json={"is_active": False})
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"email": "corporate@smartpark.ae", "password": "Corp@1234"}).status_code == 403
    assert client.get("/api/admin/audit", headers=admin).json()[0]["action"] == "update_user"


def test_TC30_corporate_consolidated_invoice(client):
    corp = login(client, "corporate@smartpark.ae")
    r1 = book(client, corp, hours_from_now=2)
    r2 = book(client, corp, hours_from_now=5, vehicle_id=[v["vehicle_id"] for v in client.get("/api/vehicles", headers=corp).json()][1])
    inv = client.get("/api/corporate/invoice", headers=corp).json()
    assert {l["booking_ref"] for l in inv["lines"]} >= {r1["booking_ref"], r2["booking_ref"]}
    assert abs(inv["total_aed"] - sum(l["amount"] for l in inv["lines"])) < 0.01
    assert client.get("/api/corporate/invoice", headers=login(client, "driver@smartpark.ae")).status_code == 403
