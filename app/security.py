"""认证、授权与操作日志。

用标准库 pbkdf2_hmac 做口令哈希（不引入 passlib/bcrypt，避免二进制依赖与
版本兼容问题），用 itsdangerous 做会话 Cookie 签名。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Iterable

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import SECRET_KEY, SESSION_COOKIE, SESSION_MAX_AGE
from app.database import get_db
from app.models import OperationLog, User

_ITERATIONS = 200_000
_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="asset-session")

# 角色权限：数值越大权限越高
ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}
ROLE_LABELS = {"viewer": "只读查看", "operator": "业务操作", "admin": "系统管理员"}


# ==================================================================== 口令
def hash_password(password: str, salt: str | None = None) -> str:
    salt_bytes = base64.b64encode(os.urandom(16)) if salt is None else salt.encode()
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt_bytes.decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or stored.count("$") != 3:
        return False
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             salt.encode(), int(iterations))
    return hmac.compare_digest(base64.b64encode(dk).decode(), digest)


# ==================================================================== 会话
def issue_token(user: User) -> str:
    return _serializer.dumps({"uid": user.id, "u": user.username, "r": user.role})


def read_token(token: str) -> dict | None:
    try:
        return _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ==================================================================== 依赖
def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = read_token(token)
    if not payload:
        return None
    user = db.get(User, payload.get("uid"))
    return user if (user and user.is_active) else None


def require_login(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER,
                            headers={"Location": "/login"})
    return user


def require_role(min_role: str):
    """生成一个依赖：要求当前用户角色不低于 min_role。"""

    def _dep(user: User = Depends(require_login)) -> User:
        if ROLE_LEVEL.get(user.role, 0) < ROLE_LEVEL.get(min_role, 99):
            raise HTTPException(status_code=403, detail="权限不足，需要更高角色")
        return user

    return _dep


def has_role(user: User | None, min_role: str) -> bool:
    if user is None:
        return False
    return ROLE_LEVEL.get(user.role, 0) >= ROLE_LEVEL.get(min_role, 99)


# ==================================================================== 操作日志
def log_operation(db: Session, user: User | None | str, action: str,
                  target: str | None = None, detail: dict | None = None,
                  ip: str | None = None) -> None:
    username = user.username if isinstance(user, User) else (user or None)
    db.add(OperationLog(username=username, action=action, target=target,
                        detail=detail, ip=ip))
    db.flush()


def client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ==================================================================== 初始化账号
DEFAULT_USERS: list[dict] = [
    {"username": "admin", "password": "admin123", "display_name": "系统管理员",
     "role": "admin"},
    {"username": "operator", "password": "operator123", "display_name": "业务运营",
     "role": "operator"},
    {"username": "viewer", "password": "viewer123", "display_name": "只读账号",
     "role": "viewer"},
]


def ensure_default_users(db: Session) -> int:
    created = 0
    for spec in DEFAULT_USERS:
        exists = db.execute(
            select(User).where(User.username == spec["username"])
        ).scalar_one_or_none()
        if exists:
            continue
        db.add(User(username=spec["username"],
                    display_name=spec["display_name"],
                    role=spec["role"],
                    password_hash=hash_password(spec["password"])))
        created += 1
    db.flush()
    return created


def list_users(db: Session) -> list[User]:
    return list(db.execute(select(User).order_by(User.id)).scalars().all())


def role_options() -> Iterable[tuple[str, str]]:
    return ROLE_LABELS.items()
