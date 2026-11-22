"""ReportController logic (UC-13, FR-28) and corporate invoice (FR-30)."""
import csv
import io
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import (CorporateAccount, Garage, OccupancyHistory, Payment, Refund, Reservation,
                      ReservationStatus, User)
from . import availability_service

COUNTED = (ReservationStatus.CONFIRMED, ReservationStatus.IN_USE, ReservationStatus.COMPLETED,
           ReservationStatus.NO_SHOW)


def _reservations(db, garage_id, start, end):
    q = select(Reservation).where(Reservation.start_time < end, Reservation.end_time > start)
    if garage_id:
        q = q.where(Reservation.garage_id == garage_id)
    return db.scalars(q).all()


def summary(db: Session, garage_id: int | None = None, days: int = 14) -> dict:
    end = clock.now()
    start = end - timedelta(days=days)
    garages = [db.get(Garage, garage_id)] if garage_id else db.scalars(select(Garage)).all()
    capacity = sum(len(availability_service.garage_spots(db, g.garage_id)) for g in garages)
    res = _reservations(db, garage_id, start, end)
    booked = [r for r in res if r.status in COUNTED]

    # average occupancy by hour of day, measured by the occupancy sensors (includes walk-in traffic)
    gids = [g.garage_id for g in garages]
    sums, counts = defaultdict(float), defaultdict(int)
    for h in db.scalars(select(OccupancyHistory).where(OccupancyHistory.hour_start >= start,
                                                       OccupancyHistory.garage_id.in_(gids))):
        sums[h.hour_start.hour] += h.occupancy_pct
        counts[h.hour_start.hour] += 1
    occ = [{"hour": h, "occupancy_pct": round(sums[h] / counts[h], 1) if counts[h] else 0} for h in range(24)]
    peak = sorted(occ, key=lambda o: -o["occupancy_pct"])[:3]

    # revenue per day = successful payments - refunds
    ids = {r.reservation_id for r in res}
    pays = [p for p in db.scalars(select(Payment).where(Payment.status == "succeeded",
                                                        Payment.created_at >= start)) if p.reservation_id in ids or not garage_id]
    pay_ids = {p.payment_id for p in pays}
    refunds = [r for r in db.scalars(select(Refund).where(Refund.created_at >= start)) if r.payment_id in pay_ids]
    by_day = defaultdict(float)
    for p in pays:
        by_day[p.created_at.date().isoformat()] += p.amount
    for r in refunds:
        by_day[r.created_at.date().isoformat()] -= r.amount
    revenue_series = [{"date": (start + timedelta(days=i + 1)).date().isoformat(),
                       "revenue": round(by_day[(start + timedelta(days=i + 1)).date().isoformat()], 2)}
                      for i in range(days)]
    finished = [r for r in res if r.status in (ReservationStatus.COMPLETED, ReservationStatus.NO_SHOW)]
    no_shows = sum(1 for r in finished if r.status == ReservationStatus.NO_SHOW)
    total_rev = round(sum(p.amount for p in pays) - sum(r.amount for r in refunds), 2)
    by_type = defaultdict(int)
    for r in booked:
        by_type[r.spot.spot_type] += 1
    return {
        "period": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "garage": garages[0].name if garage_id else "All garages",
        "kpis": {"reservations": len(booked), "revenue_aed": total_rev,
                 "avg_booking_aed": round(total_rev / len(booked), 2) if booked else 0,
                 "no_show_rate_pct": round(100 * no_shows / len(finished), 1) if finished else 0,
                 "cancellations": sum(1 for r in res if r.status == ReservationStatus.CANCELLED),
                 "capacity": capacity},
        "occupancy_by_hour": occ, "peak_hours": [p["hour"] for p in peak],
        "revenue_by_day": revenue_series, "bookings_by_type": dict(by_type),
    }


def export_csv(db: Session, garage_id: int | None = None, days: int = 14) -> str:
    end = clock.now()
    rows = _reservations(db, garage_id, end - timedelta(days=days), end + timedelta(days=7))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["booking_ref", "garage", "floor", "spot", "spot_type", "start_time", "end_time",
                "status", "base_amount", "demand_adjustment", "vat", "fees", "total_aed"])
    for r in sorted(rows, key=lambda r: r.start_time):
        w.writerow([r.booking_ref, r.garage.name, r.spot.zone.floor.name, r.spot.code, r.spot.spot_type,
                    r.start_time.isoformat(), r.end_time.isoformat(), r.status, r.base_amount,
                    r.demand_adjustment, r.vat, r.fees, r.total])
    return buf.getvalue()


def corporate_invoice(db: Session, account: CorporateAccount, month: str) -> dict:
    """FR-30: one consolidated invoice for every user of a corporate account."""
    y, m = map(int, month.split("-"))
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), m % 12 + 1, 1)
    users = db.scalars(select(User).where(User.corporate_account_id == account.account_id)).all()
    uids = {u.user_id for u in users}
    lines, total = [], 0.0
    for p in db.scalars(select(Payment).where(Payment.status == "succeeded", Payment.created_at >= start,
                                              Payment.created_at < end).order_by(Payment.created_at)):
        if p.user_id in uids:
            res = db.get(Reservation, p.reservation_id)
            refunded = sum(r.amount for r in db.scalars(select(Refund).where(Refund.payment_id == p.payment_id)))
            net = round(p.amount - refunded, 2)
            if net <= 0:
                continue
            total += net
            lines.append({"date": p.created_at.date().isoformat(), "receipt_no": p.receipt_no,
                          "booking_ref": res.booking_ref, "plate": res.vehicle.plate_number,
                          "kind": p.kind, "amount": net})
    return {"invoice_no": f"INV-{account.account_id:03d}-{month}", "company": account.company_name,
            "billing_email": account.billing_email, "month": month, "lines": lines,
            "total_aed": round(total, 2)}
