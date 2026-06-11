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
    Float,
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
    agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_cookies: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
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


class AgentProfile(Base):
    """
    Full psychological profile for an AI agent persona.
    Replaces static YAML personality files with a database-driven model.
    ORPHEUS fetches these via DAEDALUS REST API at prompt-assembly time.
    """
    __tablename__ = "agent_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    codename: Mapped[str] = mapped_column(String(100), nullable=False)
    caste: Mapped[str] = mapped_column(String(20), default="alpha", nullable=False)

    # Identity
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    birth_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    residence_city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    residence_state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    nationality: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    profession: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    education: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # Language & Interests
    spoken_languages: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    core_interests: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    # Personality (tone, vocab_level, emoji_frequency, humor_style, typing_quirks)
    communication_style: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # Behavioral Rules (max_posts_per_hour, min_delay, max_comment_length, dm_policy, etc.)
    behavioral_rules: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # Platforms this agent operates on (e.g. ["telegram", "instagram"])
    platforms: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    # Geographic layer affinities (global, region, state, city, personal)
    layers_affinity: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    # Activity window (0-23 hours)
    active_hours_start: Mapped[int] = mapped_column(Integer, default=8, nullable=False)
    active_hours_end: Mapped[int] = mapped_column(Integer, default=22, nullable=False)

    # Mission & Stance
    core_mission: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_stance_modifiers: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<AgentProfile(agent_id='{self.agent_id}', codename='{self.codename}', caste='{self.caste}')>"


class ScrapingLandscape(Base):
    """
    Dynamic scraping target registry.
    Replaces hardcoded TARGET_CHANNELS and TARGET_URLS in HUGINN scrapers.
    Managed via DAEDALUS /api/v1/landscape endpoints.
    """
    __tablename__ = "scraping_landscape"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(30), default="channel", nullable=False)
    target_identifier: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    associated_tags: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ScrapingLandscape(platform='{self.platform}', type='{self.type}', target='{self.target_identifier}', active={self.is_active})>"


class AgentActivityLog(Base):
    """
    Audit trail for every action dispatched by the MORPHEUS swarm.
    Records comment text, target URL, agent, platform, and outcome.
    """
    __tablename__ = "agent_activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_url: Mapped[str] = mapped_column(String(500), nullable=False)
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="dispatched", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def __repr__(self) -> str:
        return f"<AgentActivityLog(agent='{self.agent_id}', action='{self.action_type}', status='{self.status}')>"


class CapturedEvent(Base):
    """
    Raw intercepted event from the HUGINN gathering layer before being sent to ORPHEUS.
    Allows SuperAdmins to manually inspect, rewrite, or override data.
    """
    __tablename__ = "captured_raw_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    source_platform: Mapped[str] = mapped_column(String(30), nullable=False)
    source_target: Mapped[str] = mapped_column(String(500), nullable=False)
    post_id: Mapped[str] = mapped_column(String(100), nullable=False)
    text_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    media_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    layers: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)
    timestamp: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<CapturedEvent(id={self.id}, platform='{self.source_platform}', status='{self.status}')>"


class VirtualDevice(Base):
    """
    Registry of physical or virtual devices (e.g. Android emulators).
    Can be assigned or detached from AI agents dynamically.
    """
    __tablename__ = "virtual_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    assigned_agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="idle", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<VirtualDevice(id={self.id}, device_id='{self.device_id}', agent='{self.assigned_agent_id}')>"


class AccountAuditLog(Base):
    """
    Audit trail for account reassignments and profile changes.
    """
    __tablename__ = "account_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<AccountAuditLog(account_id={self.account_id}, action='{self.action}')>"


class ProfileHistory(Base):
    """
    Stores historical versions of AgentProfiles to allow rollback.
    """
    __tablename__ = "profile_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    profile_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<ProfileHistory(id={self.id}, agent_id='{self.agent_id}')>"


class Mission(Base):
    """
    Stage 17 — A coordinated, multi-agent narrative campaign executed as a DAG.

    Lifecycle (status):
        pending   → created, squad assembled, not yet launched.
        running   → Alpha tasks dispatched to Redis; Beta/Gamma locked.
        amplifying→ Alpha reported SUCCESS; Beta/Gamma unlocked & dispatched.
        completed → every squad member reached a terminal state.
        failed    → Alpha failed; downstream waves never released.
    """
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    target_url: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), default="web", nullable=False)
    narrative_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tactic: Mapped[str] = mapped_column(String(50), default="soft_support", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)

    # Context produced by the Alpha wave (link/reference) used to seed amplification.
    alpha_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    launched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    squad: Mapped[list["MissionSquad"]] = relationship(
        "MissionSquad",
        back_populates="mission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Mission(id={self.id}, title='{self.title}', status='{self.status}')>"


class ScoutedTarget(Base):
    """
    Stage 18 — A viral post discovered by HUGINN's authenticated scouting engine.

    HUGINN measures engagement velocity (engagement / hours-since-posted) against
    a threshold; anything classified 'VIRAL' is pushed here for the operator's
    Scouting Radar, where it can be dismissed or converted into a Mission draft.
    """
    __tablename__ = "scouted_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    url: Mapped[str] = mapped_column(String(700), nullable=False, unique=True, index=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Velocity metrics
    velocity_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, index=True)
    engagement: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posted_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # source epoch seconds

    # pending → dismissed | converted_to_mission
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False, index=True)
    mission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<ScoutedTarget(id={self.id}, platform='{self.platform}', "
            f"velocity={self.velocity_score:.1f}, status='{self.status}')>"
        )


class Campaign(Base):
    """
    A persisted information campaign definition (target platforms, enlisted
    agents, geographic layer filter). Created/listed via /api/v1/campaigns.
    """
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    campaign_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    target_platforms: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    agent_ids: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    layers_filter: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="created", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<Campaign(campaign_id='{self.campaign_id}', name='{self.name}', status='{self.status}')>"


class MissionSquad(Base):
    """
    Stage 17 — A single agent's enlistment in a Mission, bound to a DAG role.

    assigned_role: 'alpha' (seeds the narrative), 'beta' (amplifies/defends),
                   'gamma' (creates supporting noise).
    status: 'pending' → 'locked' (awaiting upstream) → 'dispatched' →
            'success' | 'failed'.
    """
    __tablename__ = "mission_squads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    assigned_role: Mapped[str] = mapped_column(String(20), default="alpha", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    task_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    mission: Mapped["Mission"] = relationship("Mission", back_populates="squad")

    def __repr__(self) -> str:
        return (
            f"<MissionSquad(mission_id={self.mission_id}, agent='{self.agent_id}', "
            f"role='{self.assigned_role}', status='{self.status}')>"
        )
