"""EntryController / ExitController logic (UC-08, UC-09, UC-10; FR-20 to FR-23; NFR-03, NFR-11;
Specification 3). The ANPR camera and barrier are simulated in the prototype."""
import math
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import EARLY_ARRIVAL_MINUTES, PLATE_CONFIDENCE_THRESHOLD, PLATE_RETENTION_DAYS
from ..models import (GateEvent, OverrideLog, ParkingSession, Payment, Reservation,
                      ReservationStatus, User, Vehicle)
from . import audit, notification_service, payment_gateway
from .reservation_service import _record_payment, pricing_service_rule, spot_label

MESSAGES = {
    "ADMITTED": "Welcome - barrier opening",
    "LOW_CONFIDENCE": "Plate not read clearly - please scan your QR pass",
    "NO_RESERVATION": "No reservation found - please see the attendant",
    "WRONG_GARAGE": "Your reservation is for another garage",
    "TOO_EARLY": "Too early - entry opens 15 minutes before your booking",
    "EXPIRED": "Reservation expired - please see the attendant",
    "ALREADY_USED": "This reservation has already been used to enter",
    "SPOT_NOT_FREE": "Your spot is still occupied - please wait or see the attendant",
    "NO_ACTIVE_SESSION": "No active parking session - please see the attendant",
    "EXIT_OK": "Thank you - barrier opening",
}


def norm_plate(p: str | None) -> str | None:
    return "".join(ch for ch in p.upper() if ch.isalnum()) if p else None


def _log(db, garage_id, lane, direction, plate, conf, qr, decision, reason) -> GateEvent:
    ev = GateEvent(garage_id=garage_id, lane=lane, direction=direction, plate_text=plate,
                   confidence=conf, qr_used=bool(qr), decision=decision, reason=reason,
                   created_at=clock.now())
    db.add(ev)
    return ev


def _match(db: Session, plate: str | None, qr_token: str | None, statuses) -> list[Reservation]:
    q = select(Reservation).join(Vehicle).where(Reservation.status.in_(statuses))
    if qr_token:
        q = q.where(Reservation.qr_token == qr_token)
    else:
        rows = db.scalars(q).all()
        return [r for r in rows if norm_plate(r.vehicle.plate_number) == plate]
    return db.scalars(q).all()


def _result(decision, reason, **extra):
    return {"decision": decision, "reason": reason, "lane_message": MESSAGES[reason],
            "barrier": "OPEN" if decision == "ADMIT" else "CLOSED", **extra}


def entry(db: Session, garage_id: int, lane: str, plate: str | None = None,
          confidence: float | None = None, qr_token: str | None = None) -> dict:
    """Specification 3 - validate vehicle entry."""
    now = clock.now()
    plate = norm_plate(plate)
    if not qr_token and (plate is None or (confidence or 0) < PLATE_CONFIDENCE_THRESHOLD):
        _log(db, garage_id, lane, "entry", plate, confidence, None, "DENY", "LOW_CONFIDENCE")
        db.commit()
        return _result("DENY", "LOW_CONFIDENCE")

    all_matches = _match(db, plate, qr_token, [ReservationStatus.CONFIRMED, ReservationStatus.IN_USE,
                                               ReservationStatus.COMPLETED, ReservationStatus.NO_SHOW])
    here = [r for r in all_matches if r.garage_id == garage_id]
    confirmed = sorted([r for r in here if r.status == ReservationStatus.CONFIRMED and r.end_time >= now],
                       key=lambda r: r.start_time)
    in_window = [r for r in confirmed if r.start_time - timedelta(minutes=EARLY_ARRIVAL_MINUTES) <= now]

    reason = None
    if not all_matches:
        reason = "NO_RESERVATION"
    elif not here:
        reason = "WRONG_GARAGE"
    elif in_window:
        res = in_window[0]
        if now < res.start_time and res.spot.sensor_occupied:
            reason = "SPOT_NOT_FREE"                                  # UC-08 A2
        else:
            session = ParkingSession(reservation_id=res.reservation_id, garage_id=garage_id,
                                     entry_time=now, entry_method="QR" if qr_token else "ANPR")
            res.status = ReservationStatus.IN_USE
            db.add(session)
            _log(db, garage_id, lane, "entry", plate, confidence, qr_token, "ADMIT", "ADMITTED")
            db.flush()
            notification_service.send(db, res.user, "entry",
                f"Welcome to {res.garage.name}. Please park at {spot_label(res.spot)}.")
            db.commit()
            return _result("ADMIT", "ADMITTED", session_id=session.session_id,
                           booking_ref=res.booking_ref, spot=res.spot.code, floor=res.spot.zone.floor.name,
                           reservation_id=res.reservation_id)
    elif confirmed:
        reason = "TOO_EARLY"
    elif any(r.status == ReservationStatus.IN_USE for r in here):
        reason = "ALREADY_USED"
    else:
        reason = "EXPIRED"
    _log(db, garage_id, lane, "entry", plate, confidence, qr_token, "DENY", reason)
    db.commit()
    return _result("DENY", reason, attendant_alerted=True)


def exit_(db: Session, garage_id: int, lane: str, plate: str | None = None,
          confidence: float | None = None, qr_token: str | None = None) -> dict:
    """UC-09 Exit Garage & Close Session (FR-22): overstay fee, close session, open barrier."""
    now = clock.now()
    plate = norm_plate(plate)
    if not qr_token and (plate is None or (confidence or 0) < PLATE_CONFIDENCE_THRESHOLD):
        _log(db, garage_id, lane, "exit", plate, confidence, None, "DENY", "LOW_CONFIDENCE")
        db.commit()
        return _result("DENY", "LOW_CONFIDENCE")
    matches = [r for r in _match(db, plate, qr_token, [ReservationStatus.IN_USE]) if r.garage_id == garage_id]
    if not matches:
        _log(db, garage_id, lane, "exit", plate, confidence, qr_token, "DENY", "NO_ACTIVE_SESSION")
        db.commit()
        return _result("DENY", "NO_ACTIVE_SESSION", attendant_alerted=True)
    res = matches[0]
    session = db.scalars(select(ParkingSession).where(ParkingSession.reservation_id == res.reservation_id)).one()
    rule = pricing_service_rule(res)
    fee, over_min = 0.0, 0
    limit = res.end_time + timedelta(minutes=rule.grace_minutes)
    if now > limit:
        over_min = int((now - res.end_time).total_seconds() // 60)
        fee = round(math.ceil(over_min / 60) * rule.base_for(res.spot.spot_type) * rule.overstay_multiplier, 2)
        result = payment_gateway.charge(res.payment_token, fee, f"Overstay {res.booking_ref}")
        _record_payment(db, res, res.payment_token, fee, "overstay", result)
    session.exit_time, session.status, session.overstay_fee = now, "Closed", fee
    res.status = ReservationStatus.COMPLETED
    _log(db, garage_id, lane, "exit", plate, confidence, qr_token, "OPEN", "EXIT_OK")
    msg = f"Thank you for parking at {res.garage.name}. Session closed at {now:%H:%M}."
    if fee:
        msg += f" Overstay of {over_min} min charged: AED {fee:.2f}."
    notification_service.send(db, res.user, "exit_receipt", msg)
    db.commit()
    return _result("ADMIT", "EXIT_OK", overstay_minutes=over_min, overstay_fee=fee, booking_ref=res.booking_ref)


def override(db: Session, attendant: User, garage_id: int, plate: str, decision: str, reason: str) -> dict:
    """UC-10 Manual Entry Override (FR-23): attendant admits or denies with a recorded reason."""
    if decision not in ("ADMIT", "DENY"):
        raise HTTPException(400, "Decision must be ADMIT or DENY")
    if len(reason.strip()) < 5:
        raise HTTPException(400, "Please enter a reason (at least 5 characters)")
    plate_n = norm_plate(plate)
    res = next((r for r in _match(db, plate_n, None, [ReservationStatus.CONFIRMED]) if r.garage_id == garage_id), None)
    session_id = None
    if decision == "ADMIT" and res is not None:
        s = ParkingSession(reservation_id=res.reservation_id, garage_id=garage_id,
                           entry_time=clock.now(), entry_method="OVERRIDE")
        res.status = ReservationStatus.IN_USE
        db.add(s)
        db.flush()
        session_id = s.session_id
    log = OverrideLog(attendant_id=attendant.user_id, garage_id=garage_id, plate_text=plate_n,
                      decision=decision, reason=reason.strip(),
                      reservation_id=res.reservation_id if res else None, created_at=clock.now())
    db.add(log)
    _log(db, garage_id, "manual", "entry", plate_n, None, None, decision, "ADMITTED" if decision == "ADMIT" else "NO_RESERVATION")
    audit.record(db, attendant.user_id, "manual_override", "gate", garage_id,
                 new={"plate": plate_n, "decision": decision, "reason": reason})
    db.commit()
    return {"decision": decision, "barrier": "OPEN" if decision == "ADMIT" else "CLOSED",
            "override_id": log.override_id, "session_id": session_id,
            "matched_booking": res.booking_ref if res else None}


def expected_arrivals(db: Session, garage_id: int) -> dict:
    now = clock.now()
    upcoming = db.scalars(select(Reservation).where(
        Reservation.garage_id == garage_id, Reservation.status == ReservationStatus.CONFIRMED,
        Reservation.start_time <= now + timedelta(hours=3), Reservation.end_time >= now)
        .order_by(Reservation.start_time)).all()
    inside = db.scalars(select(Reservation).where(Reservation.garage_id == garage_id,
                                                  Reservation.status == ReservationStatus.IN_USE)
                        .order_by(Reservation.end_time)).all()
    row = lambda r: {"booking_ref": r.booking_ref, "plate": r.vehicle.plate_number, "spot": r.spot.code,
                     "floor": r.spot.zone.floor.name, "start_time": r.start_time.isoformat(),
                     "end_time": r.end_time.isoformat(), "driver": r.user.name, "qr_token": r.qr_token}
    return {"expected": [row(r) for r in upcoming], "inside": [row(r) for r in inside]}


def purge_plate_data(db: Session) -> int:
    """NFR-11: plate reads older than 30 days are deleted unless linked to an open dispute."""
    cutoff = clock.now() - timedelta(days=PLATE_RETENTION_DAYS)
    rows = db.scalars(select(GateEvent).where(GateEvent.created_at < cutoff, GateEvent.plate_text.is_not(None),
                                              GateEvent.dispute_open.is_(False))).all()
    for ev in rows:
        ev.plate_text, ev.confidence = None, None
    db.commit()
    return len(rows)
