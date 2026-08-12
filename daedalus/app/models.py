"""
DAEDALUS — Database Models (SQLAlchemy ORM)
============================================
PostgreSQL schema for the MORPHEUS admin portal.
Tables: admin_users, roles, role_permissions, souls_accounts.
"""

import os
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
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, Session

# Stage 21 — pgvector native vector column type for cosine-similarity RAG.
from pgvector.sqlalchemy import Vector

# Embedding dimensionality (nomic-embed-text → 768). Kept in sync with EMBED_DIM.
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))

# Canonical landscape layers used across HUGINN ingestion, MUNINN storage,
# AgentProfile subscriptions and ORPHEUS RAG filtering.
LANDSCAPE_LAYERS = ("global", "regional", "state", "city", "personal")


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
    # Stage 23 — DECOUPLED IDENTITY: agent_id is a soft link (plain String, NOT a
    # ForeignKey). A SoulAccount (access keys/hardware) can float unbound, and an
    # AgentProfile (psychology) can exist with no account. Binding is an explicit
    # operator action, not a schema constraint.
    agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    auth_cookies: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    device_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    assigned_proxy: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Lifecycle: unbound (floating, no soul) | active (bound) | suspended.
    status: Mapped[str] = mapped_column(String(20), default="unbound", nullable=False)
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
    # Stage 23 — lifecycle of the psychological profile, decoupled from accounts:
    # unbound (no account linked) | active (bound to ≥1 SoulAccount) | suspended.
    status: Mapped[str] = mapped_column(String(20), default="unbound", nullable=False)

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

    # Stage 21 — Cognitive RAG subscriptions. The set of landscape layers whose
    # KnowledgeFacts ORPHEUS is allowed to retrieve and inject for this agent.
    # Stored as a JSONB array of layer keys, e.g. ["global", "state", "city"].
    context_subscriptions: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=lambda: ["global"]
    )

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
    # Stage 22 — default landscape layers seeded onto every fact scraped from this
    # source (JSONB array). The LLM auto-classifier may add more layers at ingest.
    default_layers: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["global"])
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Stage 39 — source health, reported by HUGINN after every scrape pass.
    # `health`: unknown | ok | degraded | dead. A feed that answers HTTP 200 with an
    # HTML page yields 0 entries forever and used to look exactly like a quiet feed.
    last_scraped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_item_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    consecutive_empty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    health: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    health_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    # Stage 42 — which mission caused this. Without it a mission could not see its own
    # output: 46 published comments existed and not one was attributable, so a mission
    # could neither remember what it had already argued nor tell whether it worked.
    mission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
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
    # Stage 34 — a Mission is now a PERMANENT goal, not a one-shot DAG campaign.
    # target_url is legacy/optional (real targets live in mission_targets).
    target_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", nullable=False)
    narrative_goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # what to advance
    # The mission's worldview / "truth" / side — its stance the agents argue from.
    stance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Default/fallback tactic; per-post tactic is chosen dynamically at runtime.
    tactic: Mapped[str] = mapped_column(String(50), default="soft_support", nullable=False)
    # Permanent lifecycle: 'active' (in-progress) | 'paused'. Never "completed".
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    # Roster mode: 'manual' (operator-picked squad) | 'dynamic' (auto-fill to count).
    agent_mode: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    dynamic_count: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Stage 38 — a mission as a POSITION, not two free-text fields.
    #
    # With only `narrative_goal` + `stance` the model had to infer which side it was
    # on, and a contradiction between them ("Аргентина должна была выиграть" vs
    # "Аргентина проиграла из-за тренера") produced comments that argued against the
    # mission's own goal. These fields state it explicitly:
    #   our_side     — кто «мы» (за кого/что выступаем)
    #   opponent     — чья позиция нам противостоит
    #   key_points   — 3-5 тезисов, которыми аргументируем (JSONB array of strings)
    #   red_lines    — чего никогда не говорим (JSONB array of strings)
    our_side: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opponent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_points: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    red_lines: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    alpha_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    forced_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

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
    targets: Mapped[list["MissionTarget"]] = relationship(
        "MissionTarget",
        back_populates="mission",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Mission(id={self.id}, title='{self.title}', status='{self.status}')>"


class MissionTarget(Base):
    """
    Stage 34 — A channel or post a Mission operates on. Targets can be added by the
    operator or PROPOSED by agents (who read their channels) for approval.

    kind   : 'channel' (whole channel) | 'post' (a specific t.me post)
    status : 'active' (agents work it) | 'suggested' (awaiting approval) | 'rejected'
    source : 'operator' | 'agent'
    """
    __tablename__ = "mission_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default="channel", nullable=False)
    identifier: Mapped[str] = mapped_column(String(500), nullable=False)  # @username / t.me url / chat_id
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), default="operator", nullable=False)
    proposed_by: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Stage 38 — target HEALTH. A target can be perfectly "active" and still be
    # impossible to comment on (comments disabled, no linked discussion group, the
    # account isn't allowed to write there, channel unresolvable). Without this the
    # engine burns cycles on it every tick and the operator only sees silence.
    #   unknown  — not checked yet
    #   ok       — a comment was posted / the discussion group is writable
    #   blocked  — permanently unusable (reason below); engine skips until re-check
    #   degraded — transient trouble (flood/cooldown); engine keeps trying
    health: Mapped[str] = mapped_column(String(20), default="unknown", nullable=False)
    health_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    health_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    mission: Mapped["Mission"] = relationship("Mission", back_populates="targets")

    __table_args__ = (UniqueConstraint("mission_id", "identifier", name="uq_mission_target"),)

    def __repr__(self) -> str:
        return f"<MissionTarget(mission={self.mission_id}, id='{self.identifier}', status='{self.status}')>"


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


# ── Stage 21 — Cognitive RAG: Knowledge & Targets bifurcation ──────────────

class KnowledgeFact(Base):
    """
    Stage 21 — A clustered, deduplicated unit of world knowledge (MUNINN's
    semantic memory) extracted by HUGINN from the news landscape.

    Each fact carries a native pgvector ``embedding`` (cosine space) so ORPHEUS
    can retrieve the most relevant facts at prompt-assembly time. DAEDALUS merges
    genuinely-duplicate stories into a single fact rather than creating duplicates —
    ``sources`` accumulates every URL the cluster has been observed at, and
    ``source_count`` tracks the cluster size.

    Stage 39 — merging is deliberately conservative. `nomic-embed-text` encodes
    language/style as much as topic on this corpus: measured, unrelated same-language
    stories score up to 0.85 cosine while true duplicates sit at 0.92+. The old 0.85
    threshold therefore merged unrelated news (one stored fact was 16 different posts)
    and discarded 37% of all ingested bodies. A merge now needs BOTH a high cosine and
    shared concrete vocabulary, and the superseded wording is preserved in ``variants``
    instead of being thrown away.
    """
    __tablename__ = "knowledge_facts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)

    # Stage 22 — multi-dimensional auto-classification (LLM-extracted).
    # landscape_layers: JSONB array, subset of LANDSCAPE_LAYERS (a fact can span
    #   several geographic scopes, e.g. ["state", "city"]).
    # categories: thematic buckets (e.g. ["politics", "infrastructure"]).
    # tags: free-form salient keywords/entities.
    landscape_layers: Mapped[list] = mapped_column(JSONB, nullable=False, default=lambda: ["global"])
    categories: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Stage 39 — PLACES only, canonicalised to their Russian name (see app/geo.py).
    # `tags` mixes places with people/topics and arrived in whatever language the
    # source used, so geo lookups matched almost nothing; this is the field
    # /knowledge/internal/by-geo actually queries.
    geo_tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    embedding: Mapped[list] = mapped_column(Vector(EMBED_DIM), nullable=False)

    # Cluster bookkeeping — every distinct source URL merged into this fact.
    sources: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)
    source_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Stage 39 — the other wordings this cluster absorbed: [{content, source_url, at}].
    # A merge used to drop the incoming text entirely, so a false merge destroyed a
    # real news item. Keeping the variant makes merging recoverable and auditable.
    variants: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True, default=list)

    # Stage 39 — when the SOURCE published the story, as opposed to when we happened
    # to scrape it. Freshness filters used `created_at` and therefore called a May
    # article "fresh" because a homepage still linked to it today. NULL when the
    # source gives no date; callers fall back to created_at.
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    timestamp: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return (
            f"<KnowledgeFact(id={self.id}, layers={self.landscape_layers}, "
            f"sources={self.source_count})>"
        )


class SocialPostTarget(Base):
    """
    Stage 21 — A concrete social post that the swarm acts *against* (tactics),
    decoupled from world knowledge (epistemology). ORPHEUS reads the target's
    text, RAG-queries KnowledgeFacts for fresh context, and crafts a response.
    """
    __tablename__ = "social_post_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    author: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str] = mapped_column(String(30), default="web", nullable=False, index=True)
    url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def __repr__(self) -> str:
        return f"<SocialPostTarget(id={self.id}, platform='{self.platform}', author='{self.author}')>"


class AgentChannelPref(Base):
    """
    Per-agent classification of a Telegram channel the bound account is subscribed
    to. The account's subscriptions are the agent's universe of channels; this row
    records how the operator wants each one treated:
      role     — 'target' (engage there) | 'news' (gather from it) | 'ignored'
      watching — whether the swarm should currently monitor/act on it
    Enumerated live from the session; only the operator's choices are persisted.
    """
    __tablename__ = "agent_channel_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    chat_id: Mapped[str] = mapped_column(String(40), nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="target", nullable=False)
    watching: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # Cached enumeration metadata (so the panel opens instantly from DB instead of
    # re-querying the slow live Telegram session every time).
    chat_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    members: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("agent_id", "chat_id", name="uq_agent_channel"),)

    def __repr__(self) -> str:
        return f"<AgentChannelPref(agent='{self.agent_id}', chat='{self.chat_id}', role='{self.role}')>"


class ChannelProfile(Base):
    """
    Channel Profiling — an independent, per-channel characterization (NOT per agent).
    Built from the channel's own posts and linked to the geo-layered news base
    (``knowledge_facts.landscape_layers`` shares the same closed set), so the swarm can
    judge a post IN the channel's context (topics / geo / what's discussed now) instead
    of in a vacuum. Platform-agnostic; Telegram is implemented first.

    Updated on a HYBRID cadence: the heavy profile (geo/topics/summary) rarely, the
    live ``recent_themes`` ("hot topics now") often. See CHANNEL_PROFILING.md.
    """
    __tablename__ = "channel_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", nullable=False)
    channel_ref: Mapped[str] = mapped_column(String(500), nullable=False)  # @username / id / url
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    # Geo: same closed set as knowledge_facts.landscape_layers (cross-queryable).
    geo_layers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    geo_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Themes.
    topics: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    recent_themes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Characterization.
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audience_tone: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)

    # Bookkeeping.
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    posts_seen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_profiled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_themes_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (UniqueConstraint("platform", "channel_ref", name="uq_channel_profile"),)

    def __repr__(self) -> str:
        return f"<ChannelProfile(platform='{self.platform}', ref='{self.channel_ref}', geo={self.geo_layers})>"


class MissionOutcome(Base):
    """
    Stage 42 — what a mission actually ACHIEVED in one discussion.

    The operator's definition of success is: the tone of the discussion changed, and
    real people were drawn into dialogue. Both were already computable and both were
    thrown away — the 3-way mood verdict (AGREE/NEUTRAL/OPPOSE) was calculated to pick
    a tactic and discarded, and human replies were logged without any link to the
    mission that provoked them.

    One row per (mission, discussion). ``mood_before`` is the verdict recorded at the
    moment we entered; ``mood_after`` is the same judgement re-run on the same thread
    later, so the pair is the tone delta. ``thread_grew`` separates "the tone did not
    move" from "nobody said anything at all" — collapsing those two into one number
    would quietly report failure as success, and vice versa.
    """
    __tablename__ = "mission_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", nullable=False)
    channel_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    post_url: Mapped[str] = mapped_column(String(500), nullable=False, index=True)

    # AGREE | NEUTRAL | OPPOSE — the crowd's stance toward OUR position.
    mood_before: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    mood_after: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Comments in the thread when we entered / when we re-measured.
    thread_size_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    thread_size_after: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    thread_grew: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Engagement: replies by real humans to our comments in this thread.
    our_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    human_replies: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    entered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    measured_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("mission_id", "post_url", name="uq_mission_outcome"),)

    def __repr__(self) -> str:
        return (f"<MissionOutcome(mission={self.mission_id}, {self.mood_before}→"
                f"{self.mood_after}, replies={self.human_replies})>")


class MissionDossier(Base):
    """
    Stage 43 — the mission's shared case file: what the team has established, what the
    other side argues, and what our own people have already said.

    The swarm was a set of individuals holding the same prompt fragment. Anti-repeat
    lived in ``morpheus:recent_outputs:<agent>`` — per AGENT — so three roster members
    could deploy the same argument in the same thread and none of them would know. A
    team needs one memory, not three.

    ``kind``:
      * ``fact``     — something established to be true, with a source;
      * ``opponent`` — an argument the other side actually makes here;
      * ``counter``  — our answer to one (``related_id`` points at the opponent entry);
      * ``said``     — an argument our side has already used, so it is not repeated.

    Entries are per mission, not per agent, and carry who added them, so the operator
    can see how the case was built and by whom.
    """
    __tablename__ = "mission_dossier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    # operator | agent_id | system — who put this in the file.
    added_by: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    # For a `counter`: which opponent argument it answers.
    related_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Where it was used/observed, so a `said` entry is scoped to a discussion.
    post_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)

    def __repr__(self) -> str:
        return f"<MissionDossier(mission={self.mission_id}, {self.kind}: {self.content[:40]})>"


class DecisionEvent(Base):
    """
    A durable record of WHY the swarm did (or didn't) act on a post — so the operator
    can see the decision chain, not just the final comment: what the bot recognized
    (text incl. media transcript/OCR), the relevance verdict, and skips (e.g. the
    hourly rate cap). The capped Live Ops stream shows these live; this table keeps
    history (queryable/filterable). See CHANNEL_PROFILING.md (Phase 2b).
    """
    __tablename__ = "decision_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    mission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    platform: Mapped[str] = mapped_column(String(30), default="telegram", nullable=False)
    channel_ref: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, index=True)
    post_url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    # kind: 'relevance' (judged a post) | 'skip' (relevant but throttled) | 'comment'
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # judged text / reason
    verdict: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)  # relevance yes/no
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )

    def __repr__(self) -> str:
        return f"<DecisionEvent(kind='{self.kind}', channel='{self.channel_ref}', verdict={self.verdict})>"


# ── Stage 23 — Identity binding (decoupled accounts ⇄ souls) ───────────────

def bind_account_to_soul(db: Session, account_id: int, agent_id: str) -> "SoulAccount":
    """
    Bind a floating SoulAccount (access keys/hardware) to an AgentProfile (soul).

    Both entities are independent; this is the explicit linking operation. It
    flips both sides to 'active', records the change in the audit log, and
    returns the bound account. Raises ValueError if either side is missing.
    """
    account = db.query(SoulAccount).filter(SoulAccount.id == account_id).first()
    if account is None:
        raise ValueError(f"SoulAccount {account_id} not found.")

    profile = db.query(AgentProfile).filter(AgentProfile.agent_id == agent_id).first()
    if profile is None:
        raise ValueError(f"AgentProfile '{agent_id}' not found.")

    previous = account.agent_id
    account.agent_id = agent_id
    account.status = "active"
    profile.status = "active"

    db.add(AccountAuditLog(
        account_id=account.id,
        action=f"Bound to soul '{agent_id}'",
        details=f"Previous binding: {previous or 'none'}",
    ))
    db.commit()
    db.refresh(account)
    return account


def unbind_account(db: Session, account_id: int) -> "SoulAccount":
    """
    Detach a SoulAccount from its soul. The account becomes 'unbound'; the
    formerly-linked profile reverts to 'unbound' only if it has no other
    accounts still bound to it. Returns the now-floating account.
    """
    account = db.query(SoulAccount).filter(SoulAccount.id == account_id).first()
    if account is None:
        raise ValueError(f"SoulAccount {account_id} not found.")

    previous = account.agent_id
    account.agent_id = None
    account.status = "unbound"
    db.add(AccountAuditLog(
        account_id=account.id,
        action="Unbound from soul",
        details=f"Previous binding: {previous or 'none'}",
    ))
    # autoflush is disabled on this session — flush so the count below sees the
    # just-cleared agent_id (otherwise this account still appears bound).
    db.flush()

    if previous:
        remaining = db.query(SoulAccount).filter(SoulAccount.agent_id == previous).count()
        if remaining == 0:
            profile = db.query(AgentProfile).filter(AgentProfile.agent_id == previous).first()
            if profile is not None and profile.status != "suspended":
                profile.status = "unbound"

    db.commit()
    db.refresh(account)
    return account
