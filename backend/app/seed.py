"""Demo data: 3 Abu Dhabi garages, users of every role, 8 weeks of occupancy history,
14 days of past bookings for the reports, and a trained forecast model."""
import random
import secrets
from datetime import timedelta

from sqlalchemy import select

from . import clock
from .database import SessionLocal, init_db
from .models import (CorporateAccount, Floor, Garage, Payment, PricingRule, Refund, Reservation,
                     ReservationStatus, Role, Spot, User, Vehicle, Zone, ParkingSession)
from .security import hash_password
from .services import forecast_service, pricing_service

DEMO_USERS = [
    # name, email, password, role, phone
    ("Platform Admin", "admin@smartpark.ae", "Admin@1234", Role.ADMIN, "0501110001"),
    ("Yousef (Attendant)", "attendant@smartpark.ae", "Attend@1234", Role.ATTENDANT, "0501110002"),
    ("Sara Ahmed", "driver@smartpark.ae", "Driver@1234", Role.DRIVER, "0501110003"),
    ("Omar Hassan", "corporate@smartpark.ae", "Corp@1234", Role.CORPORATE, "0501110004"),
]

GARAGES = [
    # name, area, address, lat, lng, floors, base rate standard
    ("Al Maryah Central Garage", "Al Maryah Island", "Al Maryah Island, Abu Dhabi", 24.5009, 54.3890, 3, 10.0),
    ("Yas Mall Parking West", "Yas Island", "Yas Island, Abu Dhabi", 24.4886, 54.6087, 3, 8.0),
    ("Khalifa City Hub Garage", "Khalifa City", "Khalifa City A, Abu Dhabi", 24.4196, 54.5781, 2, 7.0),
]


def _build_garage(db, name, area, address, lat, lng, n_floors, base):
    g = Garage(name=name, area=area, address=address, latitude=lat, longitude=lng)
    db.add(g)
    db.flush()
    for lvl in range(1, n_floors + 1):
        f = Floor(garage_id=g.garage_id, level_no=lvl, name=f"Level {lvl}")
        db.add(f)
        db.flush()
        for zi, zname in enumerate(("A", "B")):
            z = Zone(floor_id=f.floor_id, name=zname)
            db.add(z)
            db.flush()
            for i in range(1, 13):
                if i <= 9:
                    t = "standard"
                elif i <= 11:
                    t = "ev"
                else:
                    t = "accessible"
                if lvl > 1 and t == "accessible":
                    t = "standard"      # accessible spots are on the ground level, next to the lifts
                db.add(Spot(zone_id=z.zone_id, code=f"{lvl}{zname}{i:02d}", spot_type=t,
                            map_x=i - 1, map_y=zi))
    now = clock.now()
    db.add(PricingRule(garage_id=g.garage_id, version=1, base_standard=base, base_ev=base + 2,
                       base_accessible=base, effective_from=now - timedelta(days=60), created_at=now))
    return g


def _past_bookings(db, drivers, rnd):
    """Simulated completed bookings for the last 14 days so the dashboard has data."""
    now = clock.now()
    timeline = {}
    for g in db.scalars(select(Garage)).all():
        spots = db.scalars(select(Spot).join(Zone).join(Floor).where(Floor.garage_id == g.garage_id)).all()
        rule = pricing_service.active_rule(db, g.garage_id)
        for day in range(14, 0, -1):
            date = (now - timedelta(days=day)).replace(hour=0, minute=0, second=0)
            for _ in range(rnd.randint(28, 46)):
                hour = rnd.choices(range(6, 23), weights=[1, 3, 6, 8, 7, 5, 5, 6, 6, 5, 5, 6, 7, 6, 4, 3, 2])[0]
                start = date + timedelta(hours=hour, minutes=rnd.choice((0, 15, 30, 45)))
                end = start + timedelta(minutes=rnd.choice((60, 90, 120, 180, 240, 300)))
                spot = rnd.choice(spots)
                busy = timeline.setdefault(spot.spot_id, [])
                if any(s < end and e > start for s, e in busy):
                    continue
                busy.append((start, end))
                user, veh = rnd.choice(drivers)
                if spot.spot_type == "accessible" and not veh.has_accessible_permit:
                    continue
                occ = forecast_service.predict(g.garage_id, start)
                m = pricing_service.multiplier_for(rule, occ, spot.spot_type)
                hours = (end - start).total_seconds() / 3600
                base = rule.base_for(spot.spot_type) * hours
                sub = base * m
                total = round(sub * 1.05 + 1, 2)
                roll = rnd.random()
                status = (ReservationStatus.NO_SHOW if roll < 0.07 else
                          ReservationStatus.CANCELLED if roll < 0.12 else ReservationStatus.COMPLETED)
                created = start - timedelta(hours=rnd.randint(1, 30))
                r = Reservation(booking_ref="SP-" + secrets.token_hex(4).upper(), qr_token=secrets.token_urlsafe(18),
                                user_id=user.user_id, vehicle_id=veh.vehicle_id, garage_id=g.garage_id,
                                spot_id=spot.spot_id, pricing_rule_id=rule.rule_id, start_time=start, end_time=end,
                                status=status, base_amount=round(base, 2), demand_adjustment=round(sub - base, 2),
                                vat=round(sub * 0.05, 2), fees=1.0, total=total, avg_multiplier=m,
                                forecast_used=True, payment_token="pm_card_visa", reminder_sent=True,
                                created_at=created, cancelled_at=created + timedelta(minutes=30)
                                if status == ReservationStatus.CANCELLED else None)
                db.add(r)
                db.flush()
                p = Payment(receipt_no="RC-" + secrets.token_hex(4).upper(), reservation_id=r.reservation_id,
                            user_id=user.user_id, kind="booking", amount=total, status="succeeded",
                            gateway_ref="ch_" + secrets.token_hex(8), payment_token="pm_card_visa",
                            created_at=created)
                db.add(p)
                db.flush()
                if status == ReservationStatus.CANCELLED:
                    db.add(Refund(payment_id=p.payment_id, amount=total, gateway_ref="re_" + secrets.token_hex(8),
                                  reason="cancellation", created_at=r.cancelled_at))
                if status == ReservationStatus.COMPLETED:
                    db.add(ParkingSession(reservation_id=r.reservation_id, garage_id=g.garage_id,
                                          entry_time=start + timedelta(minutes=rnd.randint(0, 10)),
                                          exit_time=end - timedelta(minutes=rnd.randint(0, 10)),
                                          entry_method=rnd.choice(("ANPR", "ANPR", "ANPR", "QR")), status="Closed"))


def seed(with_history: bool = True) -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)):
            return
        rnd = random.Random(2026)
        now = clock.now()
        corp = CorporateAccount(company_name="Falcon Logistics LLC", billing_email="accounts@falconlogistics.example",
                                monthly_pass=True)
        db.add(corp)
        db.flush()
        users = {}
        for name, email, pw, role, phone in DEMO_USERS:
            u = User(name=name, email=email, phone=phone, password_hash=hash_password(pw), role=role,
                     is_verified=True, created_at=now,
                     corporate_account_id=corp.account_id if role == Role.CORPORATE else None)
            db.add(u)
            users[email] = u
        db.flush()
        d = users["driver@smartpark.ae"]
        db.add_all([Vehicle(user_id=d.user_id, plate_number="AD 1 23456", vehicle_type="car"),
                    Vehicle(user_id=d.user_id, plate_number="AD 5 77001", vehicle_type="ev"),
                    Vehicle(user_id=d.user_id, plate_number="AD 2 50505", vehicle_type="car",
                            has_accessible_permit=True)])
        c = users["corporate@smartpark.ae"]
        db.add_all([Vehicle(user_id=c.user_id, plate_number="AD 14 3300", vehicle_type="car"),
                    Vehicle(user_id=c.user_id, plate_number="AD 14 3301", vehicle_type="ev")])
        for gdef in GARAGES:
            _build_garage(db, *gdef)
        db.commit()
        if not with_history:
            return
        forecast_service.generate_history(db)
        forecast_service.train(db)
        # extra simulated drivers so the history looks like a real customer base
        drivers = []
        for i in range(12):
            u = User(name=f"Demo Driver {i + 1}", email=f"demo{i + 1}@example.com", phone=f"05022200{i:02d}",
                     password_hash=d.password_hash, is_verified=True, created_at=now - timedelta(days=30))
            db.add(u)
            db.flush()
            v = Vehicle(user_id=u.user_id, plate_number=f"AD {rnd.randint(1, 20)} {rnd.randint(10000, 99999)}",
                        has_accessible_permit=i % 6 == 0, vehicle_type="ev" if i % 4 == 0 else "car")
            db.add(v)
            db.flush()
            drivers.append((u, v))
        _past_bookings(db, drivers, rnd)
        db.flush()
        # a few of this month's completed bookings belong to the corporate fleet (FR-30 invoice)
        corp_vehicles = db.scalars(select(Vehicle).where(Vehicle.user_id == c.user_id)).all()
        month_start = now.replace(day=1, hour=0, minute=0, second=0)
        recent = db.scalars(select(Reservation).join(Spot).where(
            Reservation.status == ReservationStatus.COMPLETED, Reservation.start_time >= month_start,
            Spot.spot_type != "accessible").order_by(Reservation.reservation_id)).all()
        for i, r in enumerate(rnd.sample(recent, min(9, len(recent)))):
            r.user_id, r.vehicle_id = c.user_id, corp_vehicles[i % len(corp_vehicles)].vehicle_id
            for p in db.scalars(select(Payment).where(Payment.reservation_id == r.reservation_id)):
                p.user_id = c.user_id
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("Demo data created.")
