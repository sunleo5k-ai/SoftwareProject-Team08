"""PricingController logic - Specification 2: demand-based price calculation (FR-12, FR-13, NFR-20)."""
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import PLATFORM_MAX_MULTIPLIER, SERVICE_FEE_AED, VAT_RATE
from ..models import PricingRule
from . import forecast_service


def active_rule(db: Session, garage_id: int, at: datetime | None = None) -> PricingRule:
    at = at or clock.now()
    rule = db.scalars(select(PricingRule).where(PricingRule.garage_id == garage_id,
                                                PricingRule.effective_from <= at)
                      .order_by(PricingRule.version.desc())).first()
    if rule is None:
        raise HTTPException(409, "No pricing rules for this garage")
    return rule


def multiplier_for(rule: PricingRule, occupancy_pct: float | None, spot_type: str) -> float:
    """Steps 1-3 of Specification 2."""
    if spot_type == "accessible":
        return 1.0                                   # never a surcharge on accessible spots
    if occupancy_pct is None:
        m = 1.0                                      # no forecast -> neutral multiplier
    elif occupancy_pct < 40:
        m = rule.mult_low
    elif occupancy_pct < 70:
        m = rule.mult_normal
    elif occupancy_pct < 85:
        m = rule.mult_high
    else:
        m = rule.mult_peak
    cap = min(rule.cap_multiplier, PLATFORM_MAX_MULTIPLIER)
    return max(rule.floor_multiplier, min(cap, m))


def quote(db: Session, garage_id: int, spot_type: str, start: datetime, end: datetime) -> dict:
    """Returns the full price breakdown (FR-12). Part of an hour is charged in proportion."""
    rule = active_rule(db, garage_id)
    base_rate = rule.base_for(spot_type)
    base_amount = adjustment = 0.0
    used_forecast = True
    hourly = []
    t = start
    while t < end:
        hour_end = min(t.replace(minute=0, second=0) + timedelta(hours=1), end)
        fraction = (hour_end - t).total_seconds() / 3600
        occ = forecast_service.predict(garage_id, t)
        if occ is None:
            used_forecast = False
        m = multiplier_for(rule, occ, spot_type)
        base_amount += base_rate * fraction
        adjustment += base_rate * fraction * (m - 1)
        hourly.append({"hour": t.isoformat(), "occupancy_pct": occ, "multiplier": m,
                       "price": round(base_rate * fraction * m, 2)})
        t = hour_end
    subtotal = base_amount + adjustment
    vat = subtotal * VAT_RATE
    total = subtotal + vat + SERVICE_FEE_AED
    hours = (end - start).total_seconds() / 3600
    return {
        "garage_id": garage_id, "spot_type": spot_type, "pricing_rule_id": rule.rule_id,
        "base_rate": base_rate, "hours": round(hours, 2),
        "base_amount": round(base_amount, 2), "demand_adjustment": round(adjustment, 2),
        "vat": round(vat, 2), "fees": SERVICE_FEE_AED, "total": round(total, 2),
        "avg_multiplier": round(subtotal / base_amount, 3) if base_amount else 1.0,
        "forecast_used": used_forecast, "note": None if used_forecast else "no forecast",
        "hourly": hourly, "currency": "AED",
        "valid_until": (clock.now() + timedelta(minutes=5)).isoformat(),
    }


def preview(rule: PricingRule) -> list[dict]:
    """UC-12 step 4: price per hour at 30 %, 60 % and 90 % predicted occupancy."""
    out = []
    for occ in (30, 60, 90):
        row = {"occupancy_pct": occ}
        for t in ("standard", "ev", "accessible"):
            row[t] = round(rule.base_for(t) * multiplier_for(rule, occ, t), 2)
        out.append(row)
    return out


def validate_rule(values: dict) -> list[str]:
    """UC-12 step 3 checks."""
    errors = []
    for k in ("base_standard", "base_ev", "base_accessible"):
        if values[k] <= 0:
            errors.append(f"{k} must be greater than 0")
    if values["cap_multiplier"] < values["floor_multiplier"]:
        errors.append("Maximum multiplier cannot be less than the minimum multiplier")
    if values["cap_multiplier"] > PLATFORM_MAX_MULTIPLIER:
        errors.append(f"Maximum multiplier cannot exceed the platform limit of {PLATFORM_MAX_MULTIPLIER}")
    if values["floor_multiplier"] <= 0:
        errors.append("Minimum multiplier must be greater than 0")
    for k in ("mult_low", "mult_normal", "mult_high", "mult_peak"):
        if not 0 < values[k] <= PLATFORM_MAX_MULTIPLIER:
            errors.append(f"{k} must be between 0 and {PLATFORM_MAX_MULTIPLIER}")
    if values["base_accessible"] > values["base_standard"]:
        errors.append("Accessible spots cannot cost more than standard spots (no surcharge)")
    if not 0 <= values["late_refund_pct"] <= 100:
        errors.append("Late cancellation refund must be 0-100 %")
    if values["grace_minutes"] < 0 or values["free_cancel_minutes"] < 0:
        errors.append("Minutes cannot be negative")
    return errors
