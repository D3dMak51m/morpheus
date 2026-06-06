"""
DAEDALUS — Dynamic RBAC (Role-Based Access Control)
=====================================================
Implements JWT authentication, the SuperAdmin absolute bypass rule,
and dynamic atomic-permission checking middleware.

Security model:
  User → Roles → Atomic Permissions (e.g. 'db:edit', 'agents:manage')
  If user.is_superadmin == True → ALL permission checks return True.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AdminUser

# ── Configuration ──────────────────────────────────────────────────────────

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "CHANGE_ME_TO_A_RANDOM_64_CHAR_HEX_STRING")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

# ── Base atom permissions used by MORPHEUS ─────────────────────────────────

ATOM_PERMISSIONS = [
    "db:read",
    "db:edit",
    "monitoring:view",
    "agents:manage",
    "agents:view",
    "campaigns:create",
    "campaigns:view",
    "campaigns:edit",
    "campaigns:delete",
    "accounts:manage",
    "accounts:view",
    "roles:manage",
    "roles:view",
    "system:settings",
]


# ── Pydantic schemas for auth ─────────────────────────────────────────────

class TokenData(BaseModel):
    username: str
    user_id: int
    is_superadmin: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Password utilities ────────────────────────────────────────────────────

def hash_password(plain_password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT token operations ──────────────────────────────────────────────────

def create_access_token(user: AdminUser) -> str:
    """
    Create a JWT access token encoding the user's ID, username,
    and superadmin status.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": user.username,
        "uid": user.id,
        "sa": user.is_superadmin,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> TokenData:
    """
    Decode and validate a JWT token. Raises HTTPException 401 on failure.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: Optional[str] = payload.get("sub")
        user_id: Optional[int] = payload.get("uid")
        is_superadmin: bool = payload.get("sa", False)
        if username is None or user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token payload",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return TokenData(username=username, user_id=user_id, is_superadmin=is_superadmin)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependencies for auth & RBAC ──────────────────────────────────

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> AdminUser:
    """
    Dependency: decode the JWT, look up the user in the database,
    and return the AdminUser ORM instance.
    """
    token_data = decode_access_token(token)
    user = db.query(AdminUser).filter(AdminUser.id == token_data.user_id).first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )
    return user


def require_permission(permission: str):
    """
    Factory: returns a FastAPI dependency that enforces
    a specific atomic permission on the current user.

    SUPERADMIN BYPASS RULE:
      If the user's `is_superadmin` flag is True, the permission
      check immediately returns True — no further inspection needed.
    """
    def _checker(current_user: AdminUser = Depends(get_current_user)) -> AdminUser:
        # ── Absolute bypass for SuperAdmin ─────────────────────────
        if current_user.is_superadmin:
            return current_user

        # ── Collect all atomic permissions from all assigned roles ──
        user_permissions: set[str] = set()
        for role in current_user.roles:
            for rp in role.permissions:
                user_permissions.add(rp.permission)

        if permission not in user_permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: '{permission}'",
            )
        return current_user

    return _checker


def authenticate_user(db: Session, username: str, password: str) -> Optional[AdminUser]:
    """
    Validate credentials against the database.
    Returns the AdminUser if valid, None otherwise.
    """
    user = db.query(AdminUser).filter(AdminUser.username == username).first()
    if user is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    if not user.is_active:
        return None
    return user
