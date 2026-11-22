"""AvailabilityController logic (FR-04, FR-05, FR-06)."""
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import Floor, Garage, Reservation, ReservationStatus, Spot, Zone


def blocking_filter(start: datetime, end: datetime, now: datetime):
    """Reservations that occupy a spot for an overlapping period.
    A hold only blocks while it has not expired."""
    return and_(
        Reservation.start_time < end,
        Reservation.end_time > start,
        or_(Reservation.status.in_([ReservationStatus.CONFIRMED, ReservationStatus.IN_USE]),
            and_(Reservation.status == ReservationStatus.HELD, Reservation.hold_expires_at > now)),
    )


def busy_spot_ids(db: Session, garage_id: int, start: datetime, end: datetime, exclude_res: int | None = None) -> set[int]:
    q = select(Reservation.spot_id).where(Reservation.garage_id == garage_id,
                                          blocking_filter(start, end, clock.now()))
    if exclude_res:
        q = q.where(Reservation.reservation_id != exclude_res)
    return set(db.scalars(q).all())


def garage_spots(db: Session, garage_id: int, spot_type: str | None = None, floor_id: int | None = None):
    q = (select(Spot).join(Zone).join(Floor)
         .where(Floor.garage_id == garage_id, Spot.is_active.is_(True)))
    if spot_type:
        q = q.where(Spot.spot_type == spot_type)
    if floor_id:
        q = q.where(Floor.floor_id == floor_id)
    return db.scalars(q.order_by(Floor.level_no, Spot.code)).all()


def find_free_spot(db: Session, garage_id: int, spot_type: str, start: datetime, end: datetime,
                   floor_id: int | None = None, exclude_res: int | None = None) -> Spot | None:
    busy = busy_spot_ids(db, garage_id, start, end, exclude_res)
    now = clock.now()
    for s in garage_spots(db, garage_id, spot_type, floor_id):
        if s.spot_id in busy:
            continue
        if start <= now + timedelta(minutes=15) and s.sensor_occupied:
            continue                                  # physically taken right now
        return s
    return None


def count_free(db: Session, garage_id: int, spot_type: str, start: datetime, end: datetime) -> int:
    busy = busy_spot_ids(db, garage_id, start, end)
    return sum(1 for s in garage_spots(db, garage_id, spot_type) if s.spot_id not in busy)


def garage_availability(db: Session, garage: Garage, allowed_types=("standard", "ev", "accessible")) -> dict:
    """Free spots right now per floor and per type; recomputed on every request (FR-05)."""
    now = clock.now()
    busy = busy_spot_ids(db, garage.garage_id, now, now + timedelta(minutes=1))
    floors, totals = [], {"standard": [0, 0], "ev": [0, 0], "accessible": [0, 0]}
    for f in garage.floors:
        per = {"standard": [0, 0], "ev": [0, 0], "accessible": [0, 0]}
        for z in f.zones:
            for s in z.spots:
                if not s.is_active:
                    continue
                per[s.spot_type][1] += 1
                if s.spot_id not in busy and not s.sensor_occupied:
                    per[s.spot_type][0] += 1
        for t in per:
            totals[t][0] += per[t][0]
            totals[t][1] += per[t][1]
        floors.append({"floor_id": f.floor_id, "name": f.name, "level_no": f.level_no,
                       "free": {t: per[t][0] for t in allowed_types},
                       "total": {t: per[t][1] for t in allowed_types}})
    capacity = sum(v[1] for v in totals.values())
    free = sum(v[0] for v in totals.values())
    return {
        "garage_id": garage.garage_id, "name": garage.name, "area": garage.area,
        "address": garage.address, "latitude": garage.latitude, "longitude": garage.longitude,
        "capacity": capacity, "free_total": free,
        "free": {t: totals[t][0] for t in allowed_types},
        "total": {t: totals[t][1] for t in allowed_types},
        "occupancy_pct": round(100 * (capacity - free) / capacity, 1) if capacity else 0,
        "floors": floors, "as_of": now.isoformat(),
    }
