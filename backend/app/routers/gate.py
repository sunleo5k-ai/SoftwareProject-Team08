"""gate-access module - UC-08 Enter, UC-09 Exit, UC-10 Manual Override (FR-20 to FR-23).

Lane devices (ANPR camera, QR reader, occupancy sensors) authenticate with a device key;
the attendant console uses the attendant's login session.
"""
import os

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..database import get_db
from ..models import GateEvent, Role, Spot, User, UserSession
from ..security import current_user, require_roles
from ..services import gate_service

router = APIRouter(prefix="/api", tags=["gate-access"])
DEVICE_KEY = os.getenv("DEVICE_KEY", "demo-device-key")
staff_only = require_roles(Role.ATTENDANT, Role.ADMIN)


def device_or_staff(x_device_key: str = Header(default=""), authorization: str = Header(default=""),
                    db: Session = Depends(get_db)):
    if x_device_key and x_device_key == DEVICE_KEY:
        return "device"
    user = current_user(authorization, db)
    if user.role not in (Role.ATTENDANT, Role.ADMIN):
        raise HTTPException(403, "Only lane devices or staff can send gate events")
    return user


class LaneEvent(BaseModel):
    garage_id: int
    lane: str = "E1"
    plate: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    qr_token: str | None = None


class OverrideIn(BaseModel):
    garage_id: int
    plate: str
    decision: str
    reason: str


class SensorIn(BaseModel):
    spot_code: str
    garage_id: int
    occupied: bool


def _qr(token: str | None) -> str | None:
    return token.removeprefix("SMARTPARK:").strip() if token else None


@router.post("/gate/entry")
def gate_entry(ev: LaneEvent, who=Depends(device_or_staff), db: Session = Depends(get_db)):
    return gate_service.entry(db, ev.garage_id, ev.lane, ev.plate, ev.confidence, _qr(ev.qr_token))


@router.post("/gate/exit")
def gate_exit(ev: LaneEvent, who=Depends(device_or_staff), db: Session = Depends(get_db)):
    return gate_service.exit_(db, ev.garage_id, ev.lane, ev.plate, ev.confidence, _qr(ev.qr_token))


@router.post("/gate/override")
def gate_override(body: OverrideIn, user: User = Depends(staff_only), db: Session = Depends(get_db)):
    return gate_service.override(db, user, body.garage_id, body.plate, body.decision, body.reason)


@router.get("/gate/{garage_id}/arrivals")
def arrivals(garage_id: int, user: User = Depends(staff_only), db: Session = Depends(get_db)):
    return gate_service.expected_arrivals(db, garage_id)


@router.get("/gate/{garage_id}/events")
def events(garage_id: int, user: User = Depends(staff_only), db: Session = Depends(get_db)):
    rows = db.scalars(select(GateEvent).where(GateEvent.garage_id == garage_id)
                      .order_by(GateEvent.event_id.desc()).limit(15)).all()
    return [{"time": e.created_at.isoformat(), "lane": e.lane, "direction": e.direction,
             "plate": e.plate_text, "confidence": e.confidence, "qr": e.qr_used,
             "decision": e.decision, "reason": e.reason} for e in rows]


@router.post("/sensors/event")
def sensor_event(body: SensorIn, who=Depends(device_or_staff), db: Session = Depends(get_db)):
    """Occupancy sensor: spot occupied / free (feeds FR-05 availability)."""
    from ..models import Floor, Zone
    spot = db.scalar(select(Spot).join(Zone).join(Floor).where(Floor.garage_id == body.garage_id,
                                                               Spot.code == body.spot_code))
    if spot is None:
        raise HTTPException(404, "Spot not found")
    spot.sensor_occupied = body.occupied
    db.commit()
    return {"spot": spot.code, "occupied": spot.sensor_occupied, "at": clock.now().isoformat()}
