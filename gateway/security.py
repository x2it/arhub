"""鉴权与 RBAC：bcrypt 密码、JWT 签发/校验、角色常量。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

import bcrypt

from shared.settings import get_settings

ROLE_VIEWER = "viewer"
ROLE_ARCHIVIST = "archivist"
ROLE_AUDITOR = "auditor"
ROLE_ADMIN = "admin"
ALL_ROLES = [ROLE_VIEWER, ROLE_ARCHIVIST, ROLE_AUDITOR, ROLE_ADMIN]

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except Exception:
        return False


def create_access_token(user_id: int, username: str, roles: list[str], clearance: int) -> tuple[str, int]:
    s = get_settings()
    expires = s.jwt_expire_minutes
    payload = {
        "sub": str(user_id),
        "username": username,
        "roles": roles,
        "clearance": clearance,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM), expires * 60


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except JWTError:
        return None


def has_role(roles: list[str], *needed: str) -> bool:
    return any(r in roles for r in needed)
