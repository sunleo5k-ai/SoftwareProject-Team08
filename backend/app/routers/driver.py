"""Driver-facing modules: garage-catalog, availability, forecast, reservation, payment,
navigation, notification, corporate (UC-02 to UC-07; FR-04 to FR-18, FR-24, FR-25, FR-30)."""
import base64
import io
import math
from datetime import datetime

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..database import get_db
from ..models import (CorporateAccount, Garage, Notification, Payment, Refund, Reservation,
                      ReservationStatus, Role, User, Vehicle)
from ..security import current_user, require_roles
from ..services import (availability_service, forecast_service, pricing_service,
                        report_service, reservation_service)

router = APIRouter(prefix="/api", tags=["driver"])
drivers_only = require_roles(Role.DRIVER, Role.CORPORATE)


def _allowed_types(db: Session, user: User | None) -> tuple:
    """FR-06: accessible spots are only shown to drivers with a permit vehicle."""
    if user is None:
        return ("standard", "ev")
    has_permit = db.scalar(select(Vehicle).where(Vehicle.user_id == user.user_id,
                                                 Vehicle.has_accessible_permit.is_(True)))
    return ("standard", "ev", "accessible") if has_permit or user.role == Role.ADMIN else ("standard", "ev")


def _distance_km(lat1, lng1, lat2, lng2) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return round(2 * r * math.asin(math.sqrt(a)), 1)


# ------------------------------------------------- UC-02 Search Garages & Availability
@router.get("/garages")
def search_garages(q: str = "", spot_type: str | None = None, sort: str = "distance",
                   lat: float = 24.4539, lng: float = 54.3773,
                   user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-04 search by name/area, sort by distance, price or free spots; FR-05 free counts; FR-06 filter."""
    allowed = _allowed_types(db, user)
    if spot_type and spot_type not in allowed:
        raise HTTPException(403, "Accessible spots need a vehicle with an accessible permit")
    out = []
    for g in db.scalars(select(Garage).where(Garage.is_active.is_(True))):
        if q and q.lower() not in (g.name + " " + g.area).lower():
            continue
        av = availability_service.garage_availability(db, g, allowed)
        rule = pricing_service.active_rule(db, g.garage_id)
        now_occ = forecast_service.predict(g.garage_id, clock.now())
        t = spot_type or "standard"
        av.update({
            "distance_km": _distance_km(lat, lng, g.latitude, g.longitude),
            "base_rate": rule.base_for(t),
            "current_rate": round(rule.base_for(t) * pricing_service.multiplier_for(rule, now_occ, t), 2),
            "forecast_now": {"occupancy_pct": now_occ, "level": forecast_service.level(now_occ)},
            "free_selected": av["free"].get(t, 0),
        })
        out.append(av)
    key = {"distance": lambda g: g["distance_km"], "price": lambda g: g["current_rate"],
           "free": lambda g: -g["free_selected"]}.get(sort, lambda g: g["distance_km"])
    return sorted(out, key=key)


@router.get("/garages/{garage_id}")
def garage_detail(garage_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = db.get(Garage, garage_id)
    if g is None:
        raise HTTPException(404, "Garage not found")
    return availability_service.garage_availability(db, g, _allowed_types(db, user))


# --------------------------------------------------------- UC-03 View Demand Forecast
@router.get("/garages/{garage_id}/forecast")
def garage_forecast(garage_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-17 24-hour forecast and FR-18 busy/quiet indicator with cheapest hours."""
    if db.get(Garage, garage_id) is None:
        raise HTTPException(404, "Garage not found")
    rule = pricing_service.active_rule(db, garage_id)
    hours = forecast_service.forecast_24h(garage_id)
    for h in hours:
        h["price_standard"] = round(rule.base_standard * pricing_service.multiplier_for(rule, h["occupancy_pct"], "standard"), 2)
    quiet = sorted(hours, key=lambda h: (h["occupancy_pct"] or 0))[:3]
    return {"garage_id": garage_id, "hours": hours,
            "best_times": [{"hour": h["hour"], "level": h["level"], "price_standard": h["price_standard"]} for h in quiet]}


# --------------------------------------------------------------- UC-04 quote + hold
class QuoteIn(BaseModel):
    garage_id: int
    spot_type: str = "standard"
    start_time: datetime
    end_time: datetime


class HoldIn(QuoteIn):
    vehicle_id: int
    floor_id: int | None = None


@router.post("/quote")
def get_quote(body: QuoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-12 price breakdown before booking."""
    if body.end_time <= body.start_time:
        raise HTTPException(400, "End time must be after start time")
    q = pricing_service.quote(db, body.garage_id, body.spot_type, body.start_time, body.end_time)
    q["free_spots"] = availability_service.count_free(db, body.garage_id, body.spot_type,
                                                      body.start_time, body.end_time)
    return q


@router.post("/reservations", status_code=201)
def create_reservation(body: HoldIn, user: User = Depends(drivers_only), db: Session = Depends(get_db)):
    res = reservation_service.create_hold(db, user, body.vehicle_id, body.garage_id, body.spot_type,
                                          body.start_time, body.end_time, body.floor_id)
    return reservation_service.to_dict(res)


class PayIn(BaseModel):
    payment_token: str


@router.post("/reservations/{reservation_id}/pay")
def pay(reservation_id: int, body: PayIn, user: User = Depends(drivers_only), db: Session = Depends(get_db)):
    """UC-05 Pay for Reservation."""
    if any(ch.isdigit() for ch in body.payment_token) and len(body.payment_token.replace(" ", "")) >= 12:
        raise HTTPException(400, "Send a gateway payment token, never a card number")   # NFR-10
    return reservation_service.pay(db, user, reservation_id, body.payment_token)


@router.get("/reservations")
def my_reservations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Reservation).where(Reservation.user_id == user.user_id,
                                                Reservation.status != ReservationStatus.EXPIRED)
                      .order_by(Reservation.start_time.desc()).limit(50)).all()
    return [reservation_service.to_dict(r) for r in rows]


def _own(db, user, reservation_id) -> Reservation:
    r = db.get(Reservation, reservation_id)
    if r is None or r.user_id != user.user_id:
        raise HTTPException(404, "Reservation not found")     # also hides other users' data (NFR-09)
    return r


@router.get("/reservations/{reservation_id}")
def get_reservation(reservation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reservation_service.to_dict(_own(db, user, reservation_id))


@router.get("/reservations/{reservation_id}/qr")
def reservation_qr(reservation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-09 QR pass (PNG as data URL)."""
    r = _own(db, user, reservation_id)
    if r.status not in (ReservationStatus.CONFIRMED, ReservationStatus.IN_USE):
        raise HTTPException(409, "QR pass is only available for confirmed bookings")
    img = qrcode.make(f"SMARTPARK:{r.qr_token}", box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {"booking_ref": r.booking_ref, "qr_token": r.qr_token,
            "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()}


@router.post("/reservations/{reservation_id}/cancel")
def cancel(reservation_id: int, user: User = Depends(drivers_only), db: Session = Depends(get_db)):
    return reservation_service.cancel(db, user, reservation_id)


class ModifyIn(BaseModel):
    start_time: datetime
    end_time: datetime


@router.put("/reservations/{reservation_id}")
def modify(reservation_id: int, body: ModifyIn, user: User = Depends(drivers_only), db: Session = Depends(get_db)):
    return reservation_service.modify(db, user, reservation_id, body.start_time, body.end_time)


# ----------------------------------------------------------- UC-07 Navigate to Spot
@router.get("/reservations/{reservation_id}/map")
def floor_map(reservation_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """FR-24 floor map with the assigned spot and the path from the entrance."""
    r = _own(db, user, reservation_id)
    floor = r.spot.zone.floor
    spots = []
    for z in floor.zones:
        for s in z.spots:
            spots.append({"code": s.code, "type": s.spot_type, "x": s.map_x, "y": s.map_y,
                          "zone": z.name, "mine": s.spot_id == r.spot_id, "active": s.is_active})
    target = r.spot
    return {"garage": r.garage.name, "floor": floor.name, "spot": target.code, "spots": spots,
            "path": [{"x": -1, "y": 0.5}, {"x": target.map_x, "y": 0.5}, {"x": target.map_x, "y": target.map_y}],
            "directions": [f"Enter at the ground-level gate and take ramp to {floor.name}" if floor.level_no > 1
                           else "Enter at the ground-level gate", f"Drive along the main aisle to bay {target.map_x + 1}",
                           f"Your spot {target.code} is on the {'left (zone A)' if target.map_y == 0 else 'right (zone B)'}"]}


# --------------------------------------------- FR-15 receipts and payment history
@router.get("/payments")
def payment_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out = []
    for p in db.scalars(select(Payment).where(Payment.user_id == user.user_id)
                        .order_by(Payment.created_at.desc()).limit(50)):
        r = db.get(Reservation, p.reservation_id)
        refunds = db.scalars(select(Refund).where(Refund.payment_id == p.payment_id)).all()
        out.append({"receipt_no": p.receipt_no, "date": p.created_at.isoformat(), "booking_ref": r.booking_ref,
                    "garage": r.garage.name, "kind": p.kind, "amount": p.amount, "status": p.status,
                    "method": p.payment_token.replace("pm_", "").replace("_", " "),
                    "refunded": round(sum(x.amount for x in refunds), 2)})
    return out


# ------------------------------------------------------------ FR-25 notifications
@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.user_id)
                      .order_by(Notification.notification_id.desc()).limit(30)).all()
    return [{"id": n.notification_id, "type": n.type, "channel": n.channel, "message": n.message,
             "is_read": n.is_read, "created_at": n.created_at.isoformat()} for n in rows]


@router.post("/notifications/read")
def mark_read(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.user_id,
                                                   Notification.is_read.is_(False))):
        n.is_read = True
    db.commit()
    return {"message": "ok"}


# ----------------------------------------------------- FR-30 corporate invoice
@router.get("/corporate/invoice")
def corporate_invoice(month: str = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
                      user: User = Depends(require_roles(Role.CORPORATE)), db: Session = Depends(get_db)):
    account = db.get(CorporateAccount, user.corporate_account_id)
    if account is None:
        raise HTTPException(404, "No corporate account")
    return report_service.corporate_invoice(db, account, month or clock.now().strftime("%Y-%m"))
