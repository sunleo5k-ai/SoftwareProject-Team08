"""catalog, availability, reservation, pricing and payment tests
TC-04 .. TC-16, TC-24, TC-25, CT-01, UT-02, PT-01 (FR-04..16, FR-24, FR-25; NFR-01, NFR-06, NFR-10, NFR-20)."""
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select

from backend.app import clock
from backend.app.database import SessionLocal
from backend.app.models import PricingRule, Reservation, Spot, User, Vehicle
from backend.app.services import pricing_service, reservation_service

from .conftest import book, login, slot, vehicle


# ---------------------------------------------------------------- search
def test_TC04_search_by_name_and_sort(client):
    h = login(client, "driver@smartpark.ae")
    r = client.get("/api/garages", headers=h, params={"q": "yas"}).json()
    assert [g["name"] for g in r] == ["Yas Mall Parking West"]
    by_price = client.get("/api/garages", headers=h, params={"sort": "price"}).json()
    rates = [g["current_rate"] for g in by_price]
    assert rates == sorted(rates) and len(rates) == 3


def test_TC05_free_spots_per_floor_follow_sensor_events(client):
    h = login(client, "driver@smartpark.ae")
    before = client.get("/api/garages/1", headers=h).json()
    assert before["floors"][0]["free"]["standard"] > 0
    r = client.post("/api/sensors/event", headers={"X-Device-Key": "demo-device-key"},
                    json={"garage_id": 1, "spot_code": "1A01", "occupied": True})
    assert r.status_code == 200
    after = client.get("/api/garages/1", headers=h).json()
    assert after["floors"][0]["free"]["standard"] == before["floors"][0]["free"]["standard"] - 1
    assert after["free_total"] == before["free_total"] - 1


def test_TC06_accessible_spots_only_for_permit_holders(client):
    corp = login(client, "corporate@smartpark.ae")             # no permit vehicle
    g = client.get("/api/garages/1", headers=corp).json()
    assert "accessible" not in g["free"]
    assert client.get("/api/garages", headers=corp, params={"spot_type": "accessible"}).status_code == 403
    drv = login(client, "driver@smartpark.ae")                 # has a permit vehicle
    assert client.get("/api/garages/1", headers=drv).json()["free"]["accessible"] >= 1
    # booking an accessible spot with a vehicle that has no permit is refused (UC-04 A3)
    s, e = slot(2)
    r = client.post("/api/reservations", headers=drv, json={"garage_id": 1, "spot_type": "accessible", "start_time": s,
                    "end_time": e, "vehicle_id": vehicle(client, drv, permit=False)})
    assert r.status_code == 403


# ----------------------------------------------------------- reservation
def test_TC07_reserve_and_pay_confirms_booking(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h)
    assert res["status"] == "Held" and res["payment"]["status"] == "confirmed"
    got = client.get(f"/api/reservations/{res['reservation_id']}", headers=h).json()
    assert got["status"] == "Confirmed" and got["qr_token"]


def test_TC07_business_rules(client):
    h = login(client, "driver@smartpark.ae")
    vid = vehicle(client, h)
    s, e = slot(0.05)          # 3 minutes from now: too soon
    assert client.post("/api/reservations", headers=h, json={"garage_id": 1, "start_time": s, "end_time": e,
                       "vehicle_id": vid}).status_code == 400
    s, e = slot(2, 25)         # longer than 24 h
    assert client.post("/api/reservations", headers=h, json={"garage_id": 1, "start_time": s, "end_time": e,
                       "vehicle_id": vid}).status_code == 400
    for i in range(3):
        book(client, h, hours_from_now=2 + 3 * i)
    book(client, h, hours_from_now=12, expect=409)          # max 3 active reservations


def test_TC08_same_spot_never_double_booked(client):
    # Khalifa City Hub has exactly 2 accessible spots; three permit holders try the same hour
    users = ["driver@smartpark.ae", "demo1@example.com", "demo7@example.com"]
    results = []
    for email in users:
        h = login(client, email)
        results.append(book(client, h, garage_id=3, spot_type="accessible", hours_from_now=3,
                            expect=201 if len(results) < 2 else 409))
    assert results[0]["spot_id"] != results[1]["spot_id"]
    assert "other_times" in results[2]["detail"] and "other_garages" in results[2]["detail"]   # UC-04 A1


def test_CT01_concurrent_requests_for_last_spot(client):
    """NFR-06: 10 drivers request the only free EV spot at the same moment -> exactly 1 succeeds."""
    db = SessionLocal()
    ev = db.scalars(select(Spot).where(Spot.spot_type == "ev")).all()
    garage3 = [s for s in ev if s.zone.floor.garage_id == 3]
    for s in garage3[1:]:
        s.is_active = False
    db.commit()
    users = db.scalars(select(User).where(User.email.like("demo%"))).all()[:10]
    pairs = [(u.user_id, db.scalars(select(Vehicle).where(Vehicle.user_id == u.user_id)).first().vehicle_id) for u in users]
    db.close()
    start = (clock.now() + timedelta(hours=4)).replace(second=0)
    ok, failed = [], []
    barrier = threading.Barrier(len(pairs))

    def attempt(uid, vid):
        s = SessionLocal()
        try:
            barrier.wait()
            reservation_service.create_hold(s, s.get(User, uid), vid, 3, "ev", start, start + timedelta(hours=2))
            ok.append(uid)
        except Exception:
            failed.append(uid)
        finally:
            s.close()
    threads = [threading.Thread(target=attempt, args=p) for p in pairs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(ok) == 1 and len(failed) == len(pairs) - 1


def test_TC09_confirmation_notification_and_qr_pass(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h)
    notes = client.get("/api/notifications", headers=h).json()
    assert notes[0]["type"] == "booking_confirmed" and res["booking_ref"] in notes[0]["message"]
    qr = client.get(f"/api/reservations/{res['reservation_id']}/qr", headers=h).json()
    assert qr["image"].startswith("data:image/png;base64,")


def test_TC10_cancel_and_modify(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, hours_from_now=5)
    s, e = slot(6, 3)
    r = client.put(f"/api/reservations/{res['reservation_id']}", headers=h, json={"start_time": s, "end_time": e})
    assert r.status_code == 200 and r.json()["status"] == "changed"
    got = client.get(f"/api/reservations/{res['reservation_id']}", headers=h).json()
    assert got["start_time"].startswith(s[:16]) and got["quote"]["total"] == r.json()["total"]
    c = client.post(f"/api/reservations/{res['reservation_id']}/cancel", headers=h).json()
    assert c["status"] == "cancelled" and c["refund"] == got["quote"]["total"]       # > 60 min before: full refund


def test_TC11_no_show_released_after_grace_period(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, hours_from_now=0.25)
    clock.advance(15 + 14)                                  # 14 min after start: still inside the 15-min grace
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["no_shows_released"] == 0
    clock.advance(2)
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["no_shows_released"] == 1
    h = login(client, "driver@smartpark.ae")
    assert client.get(f"/api/reservations/{res['reservation_id']}", headers=h).json()["status"] == "NoShow"
    assert client.get("/api/notifications", headers=h).json()[0]["type"] == "grace_expired"


# --------------------------------------------------------------- pricing
def test_TC12_price_breakdown_adds_up(client):
    h = login(client, "driver@smartpark.ae")
    s, e = slot(3, 2.5)
    q = client.post("/api/quote", headers=h, json={"garage_id": 1, "spot_type": "standard",
                                                   "start_time": s, "end_time": e}).json()
    subtotal = q["base_amount"] + q["demand_adjustment"]
    assert abs(q["vat"] - subtotal * 0.05) < 0.02
    assert abs(q["total"] - (subtotal + q["vat"] + q["fees"])) < 0.02
    assert q["hours"] == 2.5 and len(q["hourly"]) >= 3 and q["currency"] == "AED"


def test_TC13_UT02_multiplier_respects_cap_floor_and_accessible():
    rule = PricingRule(base_standard=10, base_ev=12, base_accessible=10, mult_low=0.5, mult_normal=1.0,
                       mult_high=1.6, mult_peak=1.9, floor_multiplier=0.8, cap_multiplier=1.5)
    assert pricing_service.multiplier_for(rule, 95, "standard") == 1.5          # capped
    assert pricing_service.multiplier_for(rule, 10, "standard") == 0.8          # floored
    assert pricing_service.multiplier_for(rule, 60, "ev") == 1.0
    for occ in (0, 30, 55, 75, 99, None):
        assert pricing_service.multiplier_for(rule, occ, "accessible") == 1.0  # never a surcharge
    # 100 % of random combinations stay inside the limits
    import random
    rnd = random.Random(1)
    for _ in range(500):
        m = pricing_service.multiplier_for(rule, rnd.uniform(0, 100), rnd.choice(["standard", "ev"]))
        assert 0.8 <= m <= 1.5


# --------------------------------------------------------------- payment
def test_TC14_payment_decline_timeout_and_no_card_numbers(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, token=None)
    url = f"/api/reservations/{res['reservation_id']}/pay"
    assert client.post(url, headers=h, json={"payment_token": "4111 1111 1111 1111"}).status_code == 400   # NFR-10
    assert client.post(url, headers=h, json={"payment_token": "pm_card_timeout"}).json()["status"] == "pending"
    assert client.post(url, headers=h, json={"payment_token": "pm_card_declined"}).json()["status"] == "declined"
    assert client.post(url, headers=h, json={"payment_token": "pm_card_declined"}).json()["status"] == "declined"
    assert client.post(url, headers=h, json={"payment_token": "pm_card_declined"}).json()["status"] == "failed"
    assert client.post(url, headers=h, json={"payment_token": "pm_card_visa"}).status_code == 409   # hold released


def test_TC14_hold_expires_after_5_minutes(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, token=None)
    clock.advance(6)
    h = login(client, "driver@smartpark.ae")
    r = client.post(f"/api/reservations/{res['reservation_id']}/pay", headers=h, json={"payment_token": "pm_card_visa"})
    assert r.status_code == 410


def test_TC15_receipt_and_payment_history(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h)
    hist = client.get("/api/payments", headers=h).json()
    assert hist[0]["receipt_no"] == res["payment"]["receipt_no"] and hist[0]["status"] == "succeeded"
    assert "card visa" in hist[0]["method"]


def test_TC16_late_cancellation_gives_partial_refund(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h, hours_from_now=0.75)               # starts in 45 min (< 60 min policy)
    c = client.post(f"/api/reservations/{res['reservation_id']}/cancel", headers=h).json()
    total = client.get(f"/api/reservations/{res['reservation_id']}", headers=h).json()["quote"]["total"]
    assert c["refund"] == round(total * 0.5, 2)
    assert client.get("/api/payments", headers=h).json()[0]["refunded"] == c["refund"]


def test_NFR10_no_card_numbers_stored(client):
    h = login(client, "driver@smartpark.ae")
    book(client, h)
    db = SessionLocal()
    from backend.app.models import Payment
    for p in db.scalars(select(Payment)):
        assert p.payment_token.startswith("pm_")
    db.close()


# --------------------------------------------------- navigation / notify
def test_TC24_floor_map_highlights_assigned_spot(client):
    h = login(client, "driver@smartpark.ae")
    res = book(client, h)
    m = client.get(f"/api/reservations/{res['reservation_id']}/map", headers=h).json()
    mine = [s for s in m["spots"] if s["mine"]]
    assert len(mine) == 1 and mine[0]["code"] == res["spot_code"] and len(m["directions"]) == 3


def test_TC25_reminder_30_minutes_before_start(client):
    h = login(client, "driver@smartpark.ae")
    book(client, h, hours_from_now=1)
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["reminders_sent"] == 0
    clock.advance(35)
    admin = login(client, "admin@smartpark.ae")
    assert client.post("/api/admin/jobs/run", headers=admin).json()["reminders_sent"] == 1
    h = login(client, "driver@smartpark.ae")
    assert client.get("/api/notifications", headers=h).json()[0]["type"] == "reminder"


# ------------------------------------------------------------ performance
def test_PT01_availability_search_under_2_seconds(client):
    h = login(client, "driver@smartpark.ae")
    times = []
    for _ in range(40):
        t = time.perf_counter()
        assert client.get("/api/garages", headers=h).status_code == 200
        times.append(time.perf_counter() - t)
    times.sort()
    p95 = times[int(len(times) * 0.95) - 1]
    print(f"\nPT-01 availability search p95 = {p95 * 1000:.0f} ms over {len(times)} requests")
    assert p95 < 2.0
