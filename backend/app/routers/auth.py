"""auth module - UC-01 Register / Log In (FR-01, FR-02, FR-03)."""
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DEMO_MODE
from ..database import get_db
from ..models import Reservation, ReservationStatus, User, UserSession, Vehicle
from ..security import current_user
from ..services import auth_service, notification_service

router = APIRouter(prefix="/api", tags=["auth"])


class RegisterIn(BaseModel):
    name: str
    email: str
    phone: str
    password: str


class VerifyIn(BaseModel):
    email: str
    code: str


class LoginIn(BaseModel):
    email: str
    password: str


class ProfileIn(BaseModel):
    notify_channel: str


class VehicleIn(BaseModel):
    plate_number: str
    plate_source: str = "Abu Dhabi"
    vehicle_type: str = "car"
    has_accessible_permit: bool = False


@router.post("/auth/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    user = auth_service.register(db, body.name, body.email, body.phone, body.password)
    out = {"message": f"A 6-digit code was sent by SMS to {user.phone}", "email": user.email}
    if DEMO_MODE:
        out["demo_otp"] = user.otp_code     # no real SMS gateway in the prototype
    return out


@router.post("/auth/verify")
def verify(body: VerifyIn, db: Session = Depends(get_db)):
    user = auth_service.verify_otp(db, body.email, body.code)
    return {"message": "Account verified. You can now log in.", "user": auth_service.user_dict(user)}


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user, token = auth_service.login(db, body.email, body.password)
    return {"token": token, "user": auth_service.user_dict(user)}


@router.post("/auth/logout")
def logout(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    sess = db.get(UserSession, authorization.removeprefix("Bearer ").strip())
    if sess:
        db.delete(sess)
        db.commit()
    return {"message": "Logged out"}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return auth_service.user_dict(user)


@router.patch("/me")
def update_me(body: ProfileIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.notify_channel not in ("push", "sms", "email"):
        raise HTTPException(400, "Channel must be push, sms or email")
    user.notify_channel = body.notify_channel
    db.commit()
    return auth_service.user_dict(user)


def _veh(v: Vehicle) -> dict:
    return {"vehicle_id": v.vehicle_id, "plate_number": v.plate_number, "plate_source": v.plate_source,
            "vehicle_type": v.vehicle_type, "has_accessible_permit": v.has_accessible_permit}


@router.get("/vehicles")
def list_vehicles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [_veh(v) for v in db.scalars(select(Vehicle).where(Vehicle.user_id == user.user_id))]


@router.post("/vehicles", status_code=201)
def add_vehicle(body: VehicleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    plate = " ".join(body.plate_number.upper().split())
    if len(plate.replace(" ", "")) < 3:
        raise HTTPException(400, "Invalid plate number")
    if body.vehicle_type not in ("car", "ev"):
        raise HTTPException(400, "Vehicle type must be car or ev")
    if db.scalar(select(Vehicle).where(Vehicle.user_id == user.user_id, Vehicle.plate_number == plate)):
        raise HTTPException(409, "You already saved this plate")
    v = Vehicle(user_id=user.user_id, plate_number=plate, plate_source=body.plate_source,
                vehicle_type=body.vehicle_type, has_accessible_permit=body.has_accessible_permit)
    db.add(v)
    db.commit()
    return _veh(v)


@router.delete("/vehicles/{vehicle_id}")
def delete_vehicle(vehicle_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = db.get(Vehicle, vehicle_id)
    if v is None or v.user_id != user.user_id:
        raise HTTPException(404, "Vehicle not found")
    if db.scalar(select(Reservation).where(Reservation.vehicle_id == vehicle_id,
                                           Reservation.status.in_(ReservationStatus.ACTIVE))):
        raise HTTPException(409, "This vehicle has an active reservation")
    if db.scalar(select(Reservation).where(Reservation.vehicle_id == vehicle_id)):
        raise HTTPException(409, "This vehicle has booking history and cannot be removed")
    db.delete(v)
    db.commit()
    return {"message": "Vehicle removed"}
