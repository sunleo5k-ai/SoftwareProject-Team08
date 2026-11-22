"""ReservationController + ReservationScheduler logic
(UC-04, UC-05, UC-06; FR-07 to FR-11, FR-14 to FR-16, FR-25; NFR-06; Specification 1)."""
import math
import secrets
import threading
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import (HOLD_MINUTES, MAX_ACTIVE_RESERVATIONS, MAX_DURATION_HOURS,
                      MAX_PAYMENT_ATTEMPTS, MIN_LEAD_MINUTES)
from ..models import (Garage, Payment, Refund, Reservation, ReservationStatus, Spot, User,
                      Vehicle)
from . import (availability_service, forecast_service, notification_service, payment_gateway,
               pricing_service)

# One booking at a time per process: the availability check and the insert happen
# atomically, so two drivers can never get the same spot (FR-08, NFR-06).
_booking_lock = threading.Lock()


def _ref(prefix: str) -> str:
    return prefix + secrets.token_hex(4).upper()


def _fmt(t: datetime) -> str:
    return t.strftime("%d %b %H:%M")


def spot_label(spot: Spot) -> str:
    return f"{spot.zone.floor.name}, spot {spot.code}"


def validate_request(db: Session, user: User, vehicle: Vehicle | None, spot_type: str,
                     start: datetime, end: datetime) -> None:
    """Specification 1 'Requires' + UC-04 business rules."""
    now = clock.now()
    if vehicle is None or vehicle.user_id != user.user_id:
        raise HTTPException(400, "The vehicle does not belong to you")
    if spot_type not in ("standard", "ev", "accessible"):
        raise HTTPException(400, "Unknown spot type")
    if spot_type == "accessible" and not vehicle.has_accessible_permit:
        raise HTTPException(403, "Accessible spots need a vehicle with an accessible permit")
    if end <= start:
        raise HTTPException(400, "End time must be after start time")
    if start < now + timedelta(minutes=MIN_LEAD_MINUTES):
        raise HTTPException(400, f"Start time must be at least {MIN_LEAD_MINUTES} minutes from now")
    if end - start > timedelta(hours=MAX_DURATION_HOURS):
        raise HTTPException(400, f"A reservation can last at most {MAX_DURATION_HOURS} hours")
    active = db.scalar(select(func.count()).select_from(Reservation).where(
        Reservation.user_id == user.user_id,
        Reservation.status.in_([ReservationStatus.CONFIRMED, ReservationStatus.IN_USE]) |
        ((Reservation.status == ReservationStatus.HELD) & (Reservation.hold_expires_at > now))))
    if active >= MAX_ACTIVE_RESERVATIONS:
        raise HTTPException(409, f"You already have {MAX_ACTIVE_RESERVATIONS} active reservations")


def alternatives(db: Session, garage_id: int, spot_type: str, start: datetime, end: datetime) -> dict:
    """UC-04 A1: other times (forecast) and other garages nearby."""
    hours = (end - start).total_seconds() / 3600
    times = []
    for opt in forecast_service.suggest_times(garage_id, start, hours):
        s = datetime.fromisoformat(opt["start_time"])
        if availability_service.count_free(db, garage_id, spot_type, s, s + (end - start)) > 0:
            times.append(opt)
    garages = []
    for g in db.scalars(select(Garage).where(Garage.garage_id != garage_id, Garage.is_active.is_(True))):
        n = availability_service.count_free(db, g.garage_id, spot_type, start, end)
        if n:
            garages.append({"garage_id": g.garage_id, "name": g.name, "free": n})
    return {"other_times": times, "other_garages": garages}


def create_hold(db: Session, user: User, vehicle_id: int, garage_id: int, spot_type: str,
                start: datetime, end: datetime, floor_id: int | None = None) -> Reservation:
    """UC-04 steps 1-4: check availability, hold a matching spot for 5 minutes, quote the price."""
    vehicle = db.get(Vehicle, vehicle_id)
    validate_request(db, user, vehicle, spot_type, start, end)
    garage = db.get(Garage, garage_id)
    if garage is None or not garage.is_active:
        raise HTTPException(404, "Garage not found")
    with _booking_lock:
        spot = availability_service.find_free_spot(db, garage_id, spot_type, start, end, floor_id)
        if spot is None:
            raise HTTPException(409, detail={"message": "No spot of this type is free for the whole period",
                                             **alternatives(db, garage_id, spot_type, start, end)})
        q = pricing_service.quote(db, garage_id, spot_type, start, end)
        now = clock.now()
        res = Reservation(
            booking_ref=_ref("SP-"), qr_token=secrets.token_urlsafe(18), user_id=user.user_id,
            vehicle_id=vehicle_id, garage_id=garage_id, spot_id=spot.spot_id,
            pricing_rule_id=q["pricing_rule_id"], start_time=start, end_time=end,
            status=ReservationStatus.HELD, hold_expires_at=now + timedelta(minutes=HOLD_MINUTES),
            base_amount=q["base_amount"], demand_adjustment=q["demand_adjustment"], vat=q["vat"],
            fees=q["fees"], total=q["total"], avg_multiplier=q["avg_multiplier"],
            forecast_used=q["forecast_used"], created_at=now)
        db.add(res)
        db.commit()
    return res


def _record_payment(db, res, token, amount, kind, result) -> Payment:
    p = Payment(receipt_no=_ref("RC-"), reservation_id=res.reservation_id, user_id=res.user_id,
                kind=kind, amount=round(amount, 2), status=result["status"],
                gateway_ref=result["gateway_ref"], payment_token=token, created_at=clock.now())
    db.add(p)
    return p


def pay(db: Session, user: User, reservation_id: int, payment_token: str) -> dict:
    """UC-05 Pay for Reservation (FR-14, FR-15) incl. exceptions E1 and E2 of UC-04."""
    res = db.get(Reservation, reservation_id)
    if res is None or res.user_id != user.user_id:
        raise HTTPException(404, "Reservation not found")
    now = clock.now()
    if res.status != ReservationStatus.HELD:
        raise HTTPException(409, f"Reservation is {res.status}, not waiting for payment")
    if res.hold_expires_at <= now:
        res.status = ReservationStatus.EXPIRED
        db.commit()
        raise HTTPException(410, "The 5-minute hold has expired. Please search again.")
    try:
        result = payment_gateway.charge(payment_token, res.total, f"SmartPark {res.booking_ref}")
    except payment_gateway.GatewayTimeout:
        # E2: keep the hold, driver is not charged
        return {"status": "pending", "message": "Payment gateway is not responding. You have not been charged; "
                                                "your spot is still held until " + _fmt(res.hold_expires_at)}
    pay_rec = _record_payment(db, res, payment_token, res.total, "booking", result)
    if result["status"] != "succeeded":
        res.payment_attempts += 1
        if res.payment_attempts >= MAX_PAYMENT_ATTEMPTS:
            res.status = ReservationStatus.EXPIRED          # E1: release after 3 failures
            db.commit()
            return {"status": "failed", "message": "Payment declined 3 times. The spot has been released."}
        db.commit()
        return {"status": "declined", "attempts_left": MAX_PAYMENT_ATTEMPTS - res.payment_attempts,
                "message": "Payment declined. Try another payment method."}
    res.status = ReservationStatus.CONFIRMED
    res.hold_expires_at = None
    res.payment_token = payment_token
    db.flush()
    notification_service.send(db, user, "booking_confirmed",
        f"Booking {res.booking_ref} confirmed: {res.garage.name}, {spot_label(res.spot)}, "
        f"{_fmt(res.start_time)}-{res.end_time:%H:%M}. Paid AED {res.total:.2f} (receipt {pay_rec.receipt_no}).")
    db.commit()
    return {"status": "confirmed", "receipt_no": pay_rec.receipt_no, "reservation_id": res.reservation_id}


def refund_amount(res: Reservation, now: datetime) -> float:
    """Cancellation policy from the active pricing rule (FR-16)."""
    rule = pricing_service_rule(res)
    if now <= res.start_time - timedelta(minutes=rule.free_cancel_minutes):
        return res.total
    if now < res.start_time:
        return round(res.total * rule.late_refund_pct / 100, 2)
    return 0.0


def pricing_service_rule(res: Reservation):
    from sqlalchemy.orm import object_session
    from ..models import PricingRule
    return object_session(res).get(PricingRule, res.pricing_rule_id)


def _issue_refund(db: Session, res: Reservation, amount: float, reason: str) -> Refund | None:
    if amount <= 0:
        return None
    pay_rec = db.scalars(select(Payment).where(Payment.reservation_id == res.reservation_id,
                                               Payment.status == "succeeded",
                                               Payment.kind == "booking")).first()
    if pay_rec is None:
        return None
    result = payment_gateway.refund(pay_rec.gateway_ref, amount)
    r = Refund(payment_id=pay_rec.payment_id, amount=round(amount, 2), gateway_ref=result["gateway_ref"],
               reason=reason, created_at=clock.now())
    db.add(r)
    return r


def cancel(db: Session, user: User, reservation_id: int) -> dict:
    """UC-06 Cancel Reservation (FR-10, FR-16, FR-25)."""
    res = db.get(Reservation, reservation_id)
    if res is None or res.user_id != user.user_id:
        raise HTTPException(404, "Reservation not found")
    if res.status not in (ReservationStatus.HELD, ReservationStatus.CONFIRMED):
        raise HTTPException(409, f"A {res.status} reservation cannot be cancelled")
    now = clock.now()
    amount = refund_amount(res, now) if res.status == ReservationStatus.CONFIRMED else 0.0
    _issue_refund(db, res, amount, "cancellation")
    res.status = ReservationStatus.CANCELLED
    res.cancelled_at = now
    notification_service.send(db, user, "booking_cancelled",
        f"Booking {res.booking_ref} cancelled. Refund: AED {amount:.2f} to your original payment method.")
    db.commit()
    return {"status": "cancelled", "refund": amount}


def modify(db: Session, user: User, reservation_id: int, start: datetime, end: datetime) -> dict:
    """UC-06 Modify Reservation: new times, re-quote, charge or refund the difference (FR-10)."""
    res = db.get(Reservation, reservation_id)
    if res is None or res.user_id != user.user_id:
        raise HTTPException(404, "Reservation not found")
    if res.status != ReservationStatus.CONFIRMED:
        raise HTTPException(409, "Only a confirmed reservation that has not started can be changed")
    now = clock.now()
    if end <= start or start < now + timedelta(minutes=MIN_LEAD_MINUTES) or \
            end - start > timedelta(hours=MAX_DURATION_HOURS):
        raise HTTPException(400, "Invalid new period")
    with _booking_lock:
        busy = availability_service.busy_spot_ids(db, res.garage_id, start, end, exclude_res=res.reservation_id)
        spot = res.spot if res.spot_id not in busy else availability_service.find_free_spot(
            db, res.garage_id, res.spot.spot_type, start, end, exclude_res=res.reservation_id)
        if spot is None:
            raise HTTPException(409, "No spot is free for the new period")
        q = pricing_service.quote(db, res.garage_id, res.spot.spot_type, start, end)
        diff = round(q["total"] - res.total, 2)
        if diff > 0:
            result = payment_gateway.charge(res.payment_token, diff, f"Change {res.booking_ref}")
            if result["status"] != "succeeded":
                raise HTTPException(402, "Payment for the price difference was declined")
            _record_payment(db, res, res.payment_token, diff, "adjustment", result)
        elif diff < 0:
            _issue_refund(db, res, -diff, "modification")
        res.spot_id, res.start_time, res.end_time = spot.spot_id, start, end
        for k in ("base_amount", "demand_adjustment", "vat", "fees", "total", "avg_multiplier", "forecast_used"):
            setattr(res, k, q[k])
        res.reminder_sent = False
        db.flush()
        db.refresh(res)
        notification_service.send(db, user, "booking_changed",
            f"Booking {res.booking_ref} changed to {_fmt(start)}-{end:%H:%M}, {spot_label(res.spot)}. "
            f"Price difference: AED {diff:+.2f}.")
        db.commit()
    return {"status": "changed", "difference": diff, "total": res.total}


# ------------------------------------------------- ReservationScheduler jobs
def expire_holds(db: Session) -> int:
    now = clock.now()
    rows = db.scalars(select(Reservation).where(Reservation.status == ReservationStatus.HELD,
                                                Reservation.hold_expires_at <= now)).all()
    for r in rows:
        r.status = ReservationStatus.EXPIRED
    db.commit()
    return len(rows)


def release_no_shows(db: Session) -> int:
    """FR-11: no check-in within the grace period -> NoShow, spot released, driver notified."""
    now = clock.now()
    rows = db.scalars(select(Reservation).where(Reservation.status == ReservationStatus.CONFIRMED,
                                                Reservation.start_time < now)).all()
    n = 0
    for r in rows:
        grace = pricing_service_rule(r).grace_minutes
        if now > r.start_time + timedelta(minutes=grace):
            r.status = ReservationStatus.NO_SHOW
            notification_service.send(db, r.user, "grace_expired",
                f"Booking {r.booking_ref}: you did not arrive within {grace} minutes of the start time, "
                f"so the spot has been released.")
            n += 1
    db.commit()
    return n


def send_reminders(db: Session) -> int:
    """FR-25: reminder 30 minutes before the start time."""
    now = clock.now()
    rows = db.scalars(select(Reservation).where(Reservation.status == ReservationStatus.CONFIRMED,
                                                Reservation.reminder_sent.is_(False),
                                                Reservation.start_time <= now + timedelta(minutes=30),
                                                Reservation.start_time > now)).all()
    for r in rows:
        mins = math.ceil((r.start_time - now).total_seconds() / 60)
        notification_service.send(db, r.user, "reminder",
            f"Reminder: booking {r.booking_ref} at {r.garage.name} starts in {mins} minutes ({spot_label(r.spot)}).")
        r.reminder_sent = True
    db.commit()
    return len(rows)


def to_dict(res: Reservation) -> dict:
    return {
        "reservation_id": res.reservation_id, "booking_ref": res.booking_ref, "status": res.status,
        "garage_id": res.garage_id, "garage": res.garage.name, "spot_id": res.spot_id,
        "spot_code": res.spot.code, "spot_type": res.spot.spot_type,
        "floor": res.spot.zone.floor.name, "floor_id": res.spot.zone.floor.floor_id,
        "vehicle_id": res.vehicle_id, "plate": res.vehicle.plate_number,
        "start_time": res.start_time.isoformat(), "end_time": res.end_time.isoformat(),
        "hold_expires_at": res.hold_expires_at.isoformat() if res.hold_expires_at else None,
        "quote": {"base_amount": res.base_amount, "demand_adjustment": res.demand_adjustment,
                  "vat": res.vat, "fees": res.fees, "total": res.total,
                  "avg_multiplier": res.avg_multiplier, "forecast_used": res.forecast_used},
        "qr_token": res.qr_token if res.status in (ReservationStatus.CONFIRMED, ReservationStatus.IN_USE) else None,
        "created_at": res.created_at.isoformat(),
    }
