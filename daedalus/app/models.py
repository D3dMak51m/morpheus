"""
DAEDALUS — Database Models (SQLAlchemy ORM)
============================================
PostgreSQL schema for the MORPHEUS admin portal.
Tables: admin_users, roles, role_permissions, souls_accounts.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session


class Base(DeclarativeBase):
    """Declarative base for all MORPHEUS ORM models."""
    pass


# ── Association table: many-to-many between admin_users and roles ──────────
user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


class AdminUser(Base):
    """
    Represents an operator/admin who can log in to the DAEDALUS control panel.
    The `is_superadmin` flag grants unconditional bypass of all RBAC checks.
    """
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    roles: Mapped[list["Role"]] = relationship(
        "Role", secondary=user_roles, back_populates="users", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<AdminUser(id={self.id}, username='{self.username}', superadmin={self.is_superadmin})>"


class Role(Base):
    """
    A dynamic role that groups a set of atomic permissions.
    Created and modified at runtime by the SuperAdmin via the DAEDALUS UI.
    """
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    users: Mapped[list["AdminUser"]] = relationship(
        "AdminUser", secondary=user_roles, back_populates="roles", lazy="selectin"
    )
    permissions: Mapped[list["RolePermission"]] = relationship(
        "RolePermission", back_populates="role", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Role(id={self.id}, name='{self.name}')>"


class RolePermission(Base):
    """
    Atomic permission bound to a role.
    Examples: 'db:edit', 'monitoring:view', 'agents:manage', 'campaigns:create'.
    """
    __tablename__ = "role_permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False)
    permission: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    role: Mapped["Role"] = relationship("Role", back_populates="permissions")

    def __repr__(self) -> str:
        return f"<RolePermission(role_id={self.role_id}, permission='{self.permission}')>"


class SoulAccount(Base):
    """
    Social media account record for an AI agent persona.
    Accounts are registered manually by the SuperAdmin on a physical device,
    then entered into this table via the DAEDALUS UI.

    The `auth_cookies` JSONB column stores exported session tokens.
    MYRMIDON reads this table to obtain credentials for Appium automation.
    """
    __tablename__ = "souls_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_cookies: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    assigned_proxy: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return (
            f"<SoulAccount(id={self.id}, agent='{self.agent_id}', "
            f"platform='{self.platform}', user='{self.username}')>"
        )
