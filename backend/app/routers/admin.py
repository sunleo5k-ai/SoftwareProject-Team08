"""admin, pricing, reporting and forecast-management modules
(UC-11 to UC-14; FR-19, FR-26 to FR-29) plus demo tools for the live demonstration."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import DEMO_MODE
from ..database import get_db
from ..models import (AuditLog, Floor, ForecastModelInfo, Garage, PricingRule, Reservation,
                      ReservationStatus, Role, Spot, User, Zone)
from ..security import require_roles
from ..services import (audit, auth_service, forecast_service, gate_service, pricing_service,
                        report_service, reservation_service)

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_only = require_roles(Role.ADMIN)
staff = require_roles(Role.ADMIN, Role.ATTENDANT)


# --------------------------------------------------- UC-11 Manage Garages & Spots
@router.get("/garages")
def garages(user: User = Depends(staff), db: Session = Depends(get_db)):
    out = []
    for g in db.scalars(select(Garage).order_by(Garage.garage_id)):
        floors = []
        for f in g.floors:
            zones = [{"zone_id": z.zone_id, "name": z.name,
                      "spots": [{"spot_id": s.spot_id, "code": s.code, "type": s.spot_type, "active": s.is_active,
                                 "occupied": s.sensor_occupied} for s in sorted(z.spots, key=lambda s: s.code)]}
                     for z in f.zones]
            floors.append({"floor_id": f.floor_id, "name": f.name, "level_no": f.level_no, "zones": zones})
        out.append({"garage_id": g.garage_id, "name": g.name, "area": g.area, "address": g.address,
                    "is_active": g.is_active, "floors": floors})
    return out


class GarageIn(BaseModel):
    name: str = Field(min_length=3)
    area: str = Field(min_length=2)
    address: str = Field(min_length=3)
    latitude: float = Field(ge=22, le=27)
    longitude: float = Field(ge=51, le=57)
    base_standard: float = Field(default=8.0, gt=0)


class FloorIn(BaseModel):
    name: str
    level_no: int


class ZoneIn(BaseModel):
    name: str = Field(min_length=1, max_length=5)
    standard: int = Field(ge=0, le=60)
    ev: int = Field(default=0, ge=0, le=30)
    accessible: int = Field(default=0, ge=0, le=10)


class SpotPatch(BaseModel):
    is_active: bool | None = None
    spot_type: str | None = None


@router.post("/garages", status_code=201)
def create_garage(body: GarageIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if db.scalar(select(Garage).where(Garage.name == body.name)):
        raise HTTPException(409, "A garage with this name already exists")
    g = Garage(name=body.name, area=body.area, address=body.address, latitude=body.latitude,
               longitude=body.longitude)
    db.add(g)
    db.flush()
    now = clock.now()
    db.add(PricingRule(garage_id=g.garage_id, version=1, base_standard=body.base_standard,
                       base_ev=body.base_standard + 2, base_accessible=body.base_standard,
                       effective_from=now, created_by=user.user_id, created_at=now))
    audit.record(db, user.user_id, "create", "garage", g.garage_id, new=body.model_dump())
    db.commit()
    return {"garage_id": g.garage_id}


@router.patch("/garages/{garage_id}")
def toggle_garage(garage_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    g = db.get(Garage, garage_id)
    if g is None:
        raise HTTPException(404, "Garage not found")
    g.is_active = not g.is_active
    audit.record(db, user.user_id, "activate" if g.is_active else "deactivate", "garage", garage_id)
    db.commit()
    return {"is_active": g.is_active}


@router.post("/garages/{garage_id}/floors", status_code=201)
def add_floor(garage_id: int, body: FloorIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if db.get(Garage, garage_id) is None:
        raise HTTPException(404, "Garage not found")
    if db.scalar(select(Floor).where(Floor.garage_id == garage_id, Floor.level_no == body.level_no)):
        raise HTTPException(409, "This level already exists")
    f = Floor(garage_id=garage_id, level_no=body.level_no, name=body.name)
    db.add(f)
    audit.record(db, user.user_id, "create", "floor", garage_id, new=body.model_dump())
    db.commit()
    return {"floor_id": f.floor_id}


@router.post("/floors/{floor_id}/zones", status_code=201)
def add_zone(floor_id: int, body: ZoneIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    f = db.get(Floor, floor_id)
    if f is None:
        raise HTTPException(404, "Floor not found")
    z = Zone(floor_id=floor_id, name=body.name.upper())
    db.add(z)
    db.flush()
    row = len(f.zones) - 1
    i = 0
    for t, n in (("standard", body.standard), ("ev", body.ev), ("accessible", body.accessible)):
        for _ in range(n):
            i += 1
            db.add(Spot(zone_id=z.zone_id, code=f"{f.level_no}{z.name}{i:02d}", spot_type=t,
                        map_x=i - 1, map_y=row))
    audit.record(db, user.user_id, "create", "zone", z.zone_id, new=body.model_dump())
    db.commit()
    return {"zone_id": z.zone_id, "spots_created": i}


@router.patch("/spots/{spot_id}")
def patch_spot(spot_id: int, body: SpotPatch, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    s = db.get(Spot, spot_id)
    if s is None:
        raise HTTPException(404, "Spot not found")
    old = {"is_active": s.is_active, "spot_type": s.spot_type}
    if body.spot_type is not None:
        if body.spot_type not in ("standard", "ev", "accessible"):
            raise HTTPException(400, "Unknown spot type")
        s.spot_type = body.spot_type
    if body.is_active is not None:
        if not body.is_active and db.scalar(select(Reservation).where(
                Reservation.spot_id == spot_id, Reservation.status.in_(ReservationStatus.ACTIVE),
                Reservation.end_time > clock.now())):
            raise HTTPException(409, "This spot has upcoming reservations")
        s.is_active = body.is_active
    audit.record(db, user.user_id, "update", "spot", spot_id, old=old,
                 new={"is_active": s.is_active, "spot_type": s.spot_type})
    db.commit()
    return {"spot_id": s.spot_id, "is_active": s.is_active, "spot_type": s.spot_type}


# --------------------------------------------------- UC-12 Configure Pricing Rules
RULE_FIELDS = ("base_standard", "base_ev", "base_accessible", "mult_low", "mult_normal", "mult_high",
               "mult_peak", "floor_multiplier", "cap_multiplier", "grace_minutes", "free_cancel_minutes",
               "late_refund_pct", "overstay_multiplier")


class RuleIn(BaseModel):
    base_standard: float
    base_ev: float
    base_accessible: float
    mult_low: float = 0.9
    mult_normal: float = 1.0
    mult_high: float = 1.15
    mult_peak: float = 1.3
    floor_multiplier: float = 0.8
    cap_multiplier: float = 1.5
    grace_minutes: int = 15
    free_cancel_minutes: int = 60
    late_refund_pct: int = 50
    overstay_multiplier: float = 1.5
    effective_from: datetime | None = None
    expected_version: int | None = None


def _rule_dict(r: PricingRule) -> dict:
    d = {k: getattr(r, k) for k in RULE_FIELDS}
    d.update({"rule_id": r.rule_id, "version": r.version, "effective_from": r.effective_from.isoformat(),
              "created_at": r.created_at.isoformat()})
    return d


@router.get("/garages/{garage_id}/pricing")
def get_pricing(garage_id: int, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    rule = pricing_service.active_rule(db, garage_id)
    versions = db.scalars(select(PricingRule).where(PricingRule.garage_id == garage_id)
                          .order_by(PricingRule.version.desc())).all()
    return {"active": _rule_dict(rule), "preview": pricing_service.preview(rule),
            "versions": [_rule_dict(v) for v in versions]}


@router.post("/garages/{garage_id}/pricing/preview")
def preview_pricing(garage_id: int, body: RuleIn, user: User = Depends(admin_only)):
    vals = body.model_dump()
    errors = pricing_service.validate_rule(vals)
    tmp = PricingRule(**{k: vals[k] for k in RULE_FIELDS})
    return {"errors": errors, "preview": [] if errors else pricing_service.preview(tmp)}


@router.put("/garages/{garage_id}/pricing")
def save_pricing(garage_id: int, body: RuleIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    """UC-12 steps 3-7: validate, save a new version, write the audit log (FR-27)."""
    vals = body.model_dump()
    errors = pricing_service.validate_rule(vals)
    if errors:
        raise HTTPException(422, detail={"message": "Invalid pricing rules", "errors": errors})
    latest = db.scalars(select(PricingRule).where(PricingRule.garage_id == garage_id)
                        .order_by(PricingRule.version.desc())).first()
    if latest is None:
        raise HTTPException(404, "Garage not found")
    if body.expected_version is not None and body.expected_version != latest.version:
        raise HTTPException(409, "Another administrator saved a change first. Please reload.")   # E2
    now = clock.now()
    eff = body.effective_from or now
    new = PricingRule(garage_id=garage_id, version=latest.version + 1, effective_from=eff,
                      created_by=user.user_id, created_at=now, **{k: vals[k] for k in RULE_FIELDS})
    db.add(new)
    audit.record(db, user.user_id, "update_pricing", "pricing_rule", garage_id,
                 old={k: getattr(latest, k) for k in RULE_FIELDS}, new={k: vals[k] for k in RULE_FIELDS})
    db.commit()
    return {"message": "Pricing rules saved" + (" (scheduled)" if eff > now else ""), "version": new.version}


# ------------------------------------------------------ UC-13 Reports & Dashboard
@router.get("/reports/summary")
def report_summary(garage_id: int | None = None, days: int = 14, user: User = Depends(admin_only),
                   db: Session = Depends(get_db)):
    return report_service.summary(db, garage_id, days)


@router.get("/reports/export.csv", response_class=PlainTextResponse)
def report_csv(garage_id: int | None = None, days: int = 14, user: User = Depends(admin_only),
               db: Session = Depends(get_db)):
    audit.record(db, user.user_id, "export_csv", "report", garage_id)
    db.commit()
    return PlainTextResponse(report_service.export_csv(db, garage_id, days), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=smartpark_report.csv"})


# --------------------------------------------------------- FR-29 users and audit
class UserPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None


@router.get("/users")
def users(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [auth_service.user_dict(u) for u in db.scalars(select(User).order_by(User.user_id))
            if not u.email.endswith("@example.com")]


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "User not found")
    if u.user_id == user.user_id:
        raise HTTPException(400, "You cannot change your own account")
    old = {"role": u.role, "is_active": u.is_active}
    if body.role is not None:
        if body.role not in Role.ALL:
            raise HTTPException(400, "Unknown role")
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    audit.record(db, user.user_id, "update_user", "user", user_id, old=old,
                 new={"role": u.role, "is_active": u.is_active})
    db.commit()
    return auth_service.user_dict(u)


@router.get("/audit")
def audit_log(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.audit_id.desc()).limit(40)).all()
    names = {u.user_id: u.name for u in db.scalars(select(User))}
    return [{"time": a.created_at.isoformat(), "user": names.get(a.user_id, "system"), "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id, "old": a.old_value, "new": a.new_value} for a in rows]


# ------------------------------------------------------ UC-14 Manage Forecast Model
class ScheduleIn(BaseModel):
    run_at: datetime


def _model_dict(m: ForecastModelInfo) -> dict:
    return {"model_id": m.model_id, "algorithm": m.algorithm, "status": m.status, "n_samples": m.n_samples,
            "mae_pct": m.mae_pct, "trained_at": m.trained_at.isoformat() if m.trained_at else None,
            "scheduled_for": m.scheduled_for.isoformat() if m.scheduled_for else None}


@router.get("/forecast/models")
def models(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return [_model_dict(m) for m in db.scalars(select(ForecastModelInfo).order_by(ForecastModelInfo.model_id.desc()).limit(10))]


@router.post("/forecast/retrain")
def retrain(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    info = forecast_service.train(db)
    audit.record(db, user.user_id, "retrain_model", "forecast_model", info.model_id, new={"mae_pct": info.mae_pct})
    db.commit()
    return _model_dict(info)


@router.post("/forecast/schedule")
def schedule(body: ScheduleIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if body.run_at <= clock.now():
        raise HTTPException(400, "Choose a time in the future")
    info = forecast_service.schedule_retrain(db, body.run_at)
    audit.record(db, user.user_id, "schedule_retrain", "forecast_model", info.model_id,
                 new={"run_at": body.run_at.isoformat()})
    db.commit()
    return _model_dict(info)


# ------------------------------------------------------------- demo / scheduler
def run_jobs(db: Session) -> dict:
    """ReservationScheduler: one pass of every timed job."""
    return {"holds_expired": reservation_service.expire_holds(db),
            "no_shows_released": reservation_service.release_no_shows(db),
            "reminders_sent": reservation_service.send_reminders(db),
            "plate_reads_purged": gate_service.purge_plate_data(db),
            "scheduled_retrains": forecast_service.run_scheduled(db)}


class ClockIn(BaseModel):
    minutes: int = Field(ge=-1440, le=1440)


@router.get("/demo/clock")
def get_clock(user: User = Depends(staff)):
    return {"now": clock.now().isoformat(), "offset_minutes": clock.offset_minutes(), "demo_mode": DEMO_MODE}


@router.post("/demo/clock")
def move_clock(body: ClockIn, user: User = Depends(admin_only), db: Session = Depends(get_db)):
    if not DEMO_MODE:
        raise HTTPException(403, "Demo tools are disabled")
    clock.advance(body.minutes)
    return {"now": clock.now().isoformat(), "offset_minutes": clock.offset_minutes(), "jobs": run_jobs(db)}


@router.post("/demo/clock/reset")
def reset_clock(user: User = Depends(admin_only)):
    clock.reset()
    return {"now": clock.now().isoformat(), "offset_minutes": 0}


@router.post("/jobs/run")
def jobs(user: User = Depends(admin_only), db: Session = Depends(get_db)):
    return run_jobs(db)
