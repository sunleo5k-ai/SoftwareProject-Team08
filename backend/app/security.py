"""Password hashing, session tokens and role-based access control (FR-02, NFR-08, NFR-09)."""
import secrets
from datetime import timedelta

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from . import clock
from .config import SESSION_IDLE_MINUTES
from .database import get_db
from .models import User, UserSession


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_session(db: Session, user: User) -> str:
    token = secrets.token_hex(32)
    now = clock.now()
    db.add(UserSession(token=token, user_id=user.user_id, created_at=now, last_seen=now))
    db.commit()
    return token


def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    """Sliding session: rejected after SESSION_IDLE_MINUTES without activity (NFR-09)."""
    token = authorization.removeprefix("Bearer ").strip()
    sess = db.get(UserSession, token) if token else None
    if sess is None:
        raise HTTPException(401, "Not logged in")
    now = clock.now()
    if now - sess.last_seen > timedelta(minutes=SESSION_IDLE_MINUTES):
        db.delete(sess)
        db.commit()
        raise HTTPException(401, "Session expired after 15 minutes of inactivity")
    sess.last_seen = now
    user = db.get(User, sess.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Account disabled")
    db.commit()
    return user


def require_roles(*roles: str):
    """Dependency factory: only the listed roles may call the endpoint."""
    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, "Your role is not allowed to do this")
        return user
    return checker
