"""AuthController logic (UC-01; FR-01, FR-02, FR-03)."""
import random
import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import LOCKOUT_MINUTES, MAX_FAILED_LOGINS
from ..models import Role, User
from ..security import create_session, hash_password, verify_password

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)
PHONE_RE = re.compile(r"^\+?971\d{8,9}$|^05\d{8}$")


def register(db: Session, name: str, email: str, phone: str, password: str) -> User:
    """FR-01: account is created inactive until the OTP is verified."""
    email = email.strip().lower()
    if len(name.strip()) < 2:
        raise HTTPException(400, "Please enter your name")
    if not EMAIL_RE.match(email):
        raise HTTPException(400, "Invalid email address")
    if not PHONE_RE.match(phone.replace(" ", "")):
        raise HTTPException(400, "Invalid UAE mobile number (e.g. 0501234567)")
    if len(password) < 8 or not re.search(r"\d", password) or not re.search(r"[A-Za-z]", password):
        raise HTTPException(400, "Password needs at least 8 characters with letters and numbers")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(name=name.strip(), email=email, phone=phone.replace(" ", ""),
                password_hash=hash_password(password), role=Role.DRIVER,
                otp_code=f"{random.randint(0, 999999):06d}", created_at=clock.now())
    db.add(user)
    db.commit()
    return user


def verify_otp(db: Session, email: str, code: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or user.otp_code is None or user.otp_code != code.strip():
        raise HTTPException(400, "Wrong verification code")
    user.is_verified, user.otp_code = True, None
    db.commit()
    return user


def login(db: Session, email: str, password: str) -> tuple[User, str]:
    """FR-02: role-based login; 5 failed attempts lock the account for 15 minutes."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    now = clock.now()
    if user is None:
        raise HTTPException(401, "Wrong email or password")
    if user.locked_until and user.locked_until > now:
        mins = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(423, f"Account locked after {MAX_FAILED_LOGINS} failed attempts. Try again in {mins} min.")
    if not verify_password(password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_logins = 0
            db.commit()
            raise HTTPException(423, f"Account locked for {LOCKOUT_MINUTES} minutes after {MAX_FAILED_LOGINS} failed attempts")
        db.commit()
        raise HTTPException(401, f"Wrong email or password ({MAX_FAILED_LOGINS - user.failed_logins} attempts left)")
    if not user.is_verified:
        raise HTTPException(403, "Please verify your mobile number or email first")
    if not user.is_active:
        raise HTTPException(403, "Account disabled by the administrator")
    user.failed_logins, user.locked_until = 0, None
    db.commit()
    return user, create_session(db, user)


def user_dict(u: User) -> dict:
    return {"user_id": u.user_id, "name": u.name, "email": u.email, "phone": u.phone, "role": u.role,
            "notify_channel": u.notify_channel, "is_active": u.is_active,
            "corporate_account_id": u.corporate_account_id}
