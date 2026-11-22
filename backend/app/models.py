"""Entity classes (Phase 3 class diagram) mapped to database tables (Phase 4 data design)."""
from datetime import datetime

from sqlalchemy import (Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class ReservationStatus:
    HELD = "Held"
    CONFIRMED = "Confirmed"
    IN_USE = "InUse"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"
    NO_SHOW = "NoShow"
    EXPIRED = "Expired"          # hold ran out before payment
    ACTIVE = (HELD, CONFIRMED, IN_USE)   # statuses that block a spot


class Role:
    DRIVER = "driver"
    CORPORATE = "corporate"
    ATTENDANT = "attendant"
    ADMIN = "admin"
    ALL = (DRIVER, CORPORATE, ATTENDANT, ADMIN)


# ---------------------------------------------------------------- auth module
class CorporateAccount(Base):
    __tablename__ = "corporate_accounts"
    account_id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(120), nullable=False)
    billing_email: Mapped[str] = mapped_column(String(120), nullable=False)
    monthly_pass: Mapped[bool] = mapped_column(Boolean, default=False)


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default=Role.DRIVER, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    otp_code: Mapped[str | None] = mapped_column(String(6))
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    notify_channel: Mapped[str] = mapped_column(String(10), default="push")
    corporate_account_id: Mapped[int | None] = mapped_column(ForeignKey("corporate_accounts.account_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    vehicles = relationship("Vehicle", back_populates="owner")


class UserSession(Base):
    __tablename__ = "user_sessions"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = (UniqueConstraint("user_id", "plate_number", name="uq_vehicle_owner_plate"),)
    vehicle_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    plate_number: Mapped[str] = mapped_column(String(15), nullable=False)
    plate_source: Mapped[str] = mapped_column(String(20), default="Abu Dhabi")
    vehicle_type: Mapped[str] = mapped_column(String(15), default="car")   # car / ev
    has_accessible_permit: Mapped[bool] = mapped_column(Boolean, default=False)

    owner = relationship("User", back_populates="vehicles")


# ------------------------------------------------------ garage-catalog module
class Garage(Base):
    __tablename__ = "garages"
    garage_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    area: Mapped[str] = mapped_column(String(60), nullable=False)
    address: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    floors = relationship("Floor", back_populates="garage", cascade="all, delete-orphan",
                          order_by="Floor.level_no")


class Floor(Base):
    __tablename__ = "floors"
    __table_args__ = (UniqueConstraint("garage_id", "level_no", name="uq_floor_level"),)
    floor_id: Mapped[int] = mapped_column(primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    level_no: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)

    garage = relationship("Garage", back_populates="floors")
    zones = relationship("Zone", back_populates="floor", cascade="all, delete-orphan")


class Zone(Base):
    __tablename__ = "zones"
    zone_id: Mapped[int] = mapped_column(primary_key=True)
    floor_id: Mapped[int] = mapped_column(ForeignKey("floors.floor_id"), nullable=False)
    name: Mapped[str] = mapped_column(String(20), nullable=False)

    floor = relationship("Floor", back_populates="zones")
    spots = relationship("Spot", back_populates="zone", cascade="all, delete-orphan")


class Spot(Base):
    __tablename__ = "spots"
    spot_id: Mapped[int] = mapped_column(primary_key=True)
    zone_id: Mapped[int] = mapped_column(ForeignKey("zones.zone_id"), nullable=False)
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    spot_type: Mapped[str] = mapped_column(String(12), default="standard")  # standard / ev / accessible
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sensor_occupied: Mapped[bool] = mapped_column(Boolean, default=False)
    map_x: Mapped[int] = mapped_column(Integer, default=0)
    map_y: Mapped[int] = mapped_column(Integer, default=0)

    zone = relationship("Zone", back_populates="spots")


# ------------------------------------------------------------- pricing module
class PricingRule(Base):
    __tablename__ = "pricing_rules"
    __table_args__ = (UniqueConstraint("garage_id", "version", name="uq_rule_version"),)
    rule_id: Mapped[int] = mapped_column(primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    base_standard: Mapped[float] = mapped_column(Float, nullable=False)
    base_ev: Mapped[float] = mapped_column(Float, nullable=False)
    base_accessible: Mapped[float] = mapped_column(Float, nullable=False)
    mult_low: Mapped[float] = mapped_column(Float, default=0.90)      # < 40 %
    mult_normal: Mapped[float] = mapped_column(Float, default=1.00)   # 40-69 %
    mult_high: Mapped[float] = mapped_column(Float, default=1.15)     # 70-84 %
    mult_peak: Mapped[float] = mapped_column(Float, default=1.30)     # >= 85 %
    floor_multiplier: Mapped[float] = mapped_column(Float, default=0.8)
    cap_multiplier: Mapped[float] = mapped_column(Float, default=1.5)
    grace_minutes: Mapped[int] = mapped_column(Integer, default=15)
    free_cancel_minutes: Mapped[int] = mapped_column(Integer, default=60)
    late_refund_pct: Mapped[int] = mapped_column(Integer, default=50)
    overstay_multiplier: Mapped[float] = mapped_column(Float, default=1.5)
    effective_from: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    def base_for(self, spot_type: str) -> float:
        return {"standard": self.base_standard, "ev": self.base_ev,
                "accessible": self.base_accessible}[spot_type]


# --------------------------------------------------------- reservation module
class Reservation(Base):
    __tablename__ = "reservations"
    reservation_id: Mapped[int] = mapped_column(primary_key=True)
    booking_ref: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    qr_token: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.vehicle_id"), nullable=False)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    spot_id: Mapped[int] = mapped_column(ForeignKey("spots.spot_id"), nullable=False)
    pricing_rule_id: Mapped[int] = mapped_column(ForeignKey("pricing_rules.rule_id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    hold_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    payment_attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Quote (Specification 2) - kept with the reservation, never re-priced
    base_amount: Mapped[float] = mapped_column(Float, nullable=False)
    demand_adjustment: Mapped[float] = mapped_column(Float, nullable=False)
    vat: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    avg_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    forecast_used: Mapped[bool] = mapped_column(Boolean, default=True)
    payment_token: Mapped[str | None] = mapped_column(String(40))   # gateway token only (NFR-10)
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)

    user = relationship("User")
    vehicle = relationship("Vehicle")
    garage = relationship("Garage")
    spot = relationship("Spot")


# ------------------------------------------------------------- payment module
class Payment(Base):
    __tablename__ = "payments"
    payment_id: Mapped[int] = mapped_column(primary_key=True)
    receipt_no: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.reservation_id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(12), default="booking")      # booking / overstay / adjustment
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)       # succeeded / declined
    gateway_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    payment_token: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Refund(Base):
    __tablename__ = "refunds"
    refund_id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("payments.payment_id"), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    gateway_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# --------------------------------------------------------- gate-access module
class ParkingSession(Base):
    __tablename__ = "parking_sessions"
    session_id: Mapped[int] = mapped_column(primary_key=True)
    reservation_id: Mapped[int] = mapped_column(ForeignKey("reservations.reservation_id"), unique=True, nullable=False)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    entry_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime)
    entry_method: Mapped[str] = mapped_column(String(10), nullable=False)   # ANPR / QR / OVERRIDE
    status: Mapped[str] = mapped_column(String(10), default="Active")       # Active / Closed
    overstay_fee: Mapped[float] = mapped_column(Float, default=0.0)

    reservation = relationship("Reservation")


class GateEvent(Base):
    """Every plate read / QR scan at a lane. Plate data is purged after 30 days (NFR-11)."""
    __tablename__ = "gate_events"
    event_id: Mapped[int] = mapped_column(primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    lane: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(5), nullable=False)       # entry / exit
    plate_text: Mapped[str | None] = mapped_column(String(15))
    confidence: Mapped[float | None] = mapped_column(Float)
    qr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(6), nullable=False)        # ADMIT / DENY / OPEN
    reason: Mapped[str] = mapped_column(String(30), nullable=False)
    dispute_open: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class OverrideLog(Base):
    __tablename__ = "override_logs"
    override_id: Mapped[int] = mapped_column(primary_key=True)
    attendant_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    plate_text: Mapped[str] = mapped_column(String(15), nullable=False)
    decision: Mapped[str] = mapped_column(String(6), nullable=False)        # ADMIT / DENY
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    reservation_id: Mapped[int | None] = mapped_column(ForeignKey("reservations.reservation_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# -------------------------------------------------------- notification module
class Notification(Base):
    __tablename__ = "notifications"
    notification_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# -------------------------------------------------------------- admin module
class AuditLog(Base):
    __tablename__ = "audit_logs"
    audit_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.user_id"))
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    entity: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(20))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# ------------------------------------------------------------ forecast module
class OccupancyHistory(Base):
    __tablename__ = "occupancy_history"
    history_id: Mapped[int] = mapped_column(primary_key=True)
    garage_id: Mapped[int] = mapped_column(ForeignKey("garages.garage_id"), nullable=False)
    hour_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    occupancy_pct: Mapped[float] = mapped_column(Float, nullable=False)


class ForecastModelInfo(Base):
    __tablename__ = "forecast_models"
    model_id: Mapped[int] = mapped_column(primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(40), nullable=False)
    trained_at: Mapped[datetime | None] = mapped_column(DateTime)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    mae_pct: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(12), nullable=False)          # Active / Scheduled / Retired
