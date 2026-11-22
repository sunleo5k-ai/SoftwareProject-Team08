"""auth module tests - TC-01, TC-02, TC-03, SEC-01, SEC-02 (FR-01..03, NFR-08, NFR-09)."""
from datetime import timedelta

from backend.app import clock
from backend.app.database import SessionLocal
from backend.app.models import User
from sqlalchemy import select

from .conftest import login


def test_TC01_register_verify_with_otp_then_login(client):
    r = client.post("/api/auth/register", json={"name": "Lina K", "email": "lina@test.ae",
                                               "phone": "0507654321", "password": "Parking2026"})
    assert r.status_code == 201
    otp = r.json()["demo_otp"]
    # cannot log in before verification
    assert client.post("/api/auth/login", json={"email": "lina@test.ae", "password": "Parking2026"}).status_code == 403
    assert client.post("/api/auth/verify", json={"email": "lina@test.ae", "code": "000000" if otp != "000000" else "111111"}).status_code == 400
    assert client.post("/api/auth/verify", json={"email": "lina@test.ae", "code": otp}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "lina@test.ae", "password": "Parking2026"}).status_code == 200


def test_TC01_register_rejects_bad_input(client):
    bad = {"name": "X Y", "email": "not-an-email", "phone": "0507654321", "password": "Parking2026"}
    assert client.post("/api/auth/register", json=bad).status_code == 400
    bad.update(email="ok@test.ae", password="short")
    assert client.post("/api/auth/register", json=bad).status_code == 400
    bad.update(password="Parking2026", email="driver@smartpark.ae")
    assert client.post("/api/auth/register", json=bad).status_code == 409


def test_TC02_lockout_after_five_failed_logins(client):
    for i in range(4):
        r = client.post("/api/auth/login", json={"email": "driver@smartpark.ae", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"email": "driver@smartpark.ae", "password": "wrong"})
    assert r.status_code == 423
    # even the right password is refused while locked
    assert client.post("/api/auth/login", json={"email": "driver@smartpark.ae", "password": "Driver@1234"}).status_code == 423
    clock.advance(16)
    assert client.post("/api/auth/login", json={"email": "driver@smartpark.ae", "password": "Driver@1234"}).status_code == 200


def test_TC02_role_based_access(client):
    driver = login(client, "driver@smartpark.ae")
    attendant = login(client, "attendant@smartpark.ae")
    assert client.get("/api/admin/reports/summary", headers=driver).status_code == 403
    assert client.get("/api/admin/reports/summary", headers=attendant).status_code == 403
    assert client.get("/api/gate/1/arrivals", headers=driver).status_code == 403
    assert client.get("/api/gate/1/arrivals", headers=attendant).status_code == 200
    assert client.get("/api/admin/reports/summary", headers=login(client, "admin@smartpark.ae")).status_code == 200


def test_TC03_manage_vehicles(client):
    h = login(client, "driver@smartpark.ae")
    r = client.post("/api/vehicles", headers=h, json={"plate_number": "dxb q 12345", "vehicle_type": "ev"})
    assert r.status_code == 201 and r.json()["plate_number"] == "DXB Q 12345"
    assert client.post("/api/vehicles", headers=h, json={"plate_number": "DXB Q 12345"}).status_code == 409
    plates = [v["plate_number"] for v in client.get("/api/vehicles", headers=h).json()]
    assert "DXB Q 12345" in plates and len(plates) == 4


def test_SEC01_passwords_are_hashed_with_bcrypt():
    db = SessionLocal()
    for u in db.scalars(select(User)):
        assert u.password_hash.startswith("$2b$") and "1234" not in u.password_hash
    db.close()


def test_SEC02_session_expires_after_15_minutes_idle(client):
    h = login(client, "driver@smartpark.ae")
    clock.advance(14)
    assert client.get("/api/me", headers=h).status_code == 200      # activity refreshes the session
    clock.advance(14)
    assert client.get("/api/me", headers=h).status_code == 200
    clock.advance(16)
    assert client.get("/api/me", headers=h).status_code == 401


def test_SEC03_cannot_read_another_users_reservation(client):
    from .conftest import book
    d = login(client, "driver@smartpark.ae")
    res = book(client, d)
    other = login(client, "corporate@smartpark.ae")
    assert client.get(f"/api/reservations/{res['reservation_id']}", headers=other).status_code == 404
    assert client.post(f"/api/reservations/{res['reservation_id']}/cancel", headers=other).status_code == 404
