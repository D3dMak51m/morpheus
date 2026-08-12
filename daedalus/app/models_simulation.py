"""
DAEDALUS — SIMULATION domain model (isolated test polygon)
==========================================================
A completely separate sandbox world used to test agents/souls, missions, RAG,
system prompts, comments, reactions and mass generation **without touching
production**: no real Telegram channel, no real account, no real mission and no
production table is ever written from here.

Isolation contract (enforced by construction):
  * Every table lives in its own ``sim_`` namespace and hangs off ``SimBase`` —
    a declarative base separate from the production ``app.models.Base``. Nothing
    here has a ForeignKey into a production table, so a simulation row can never
    cascade into or constrain real data.
  * Production data can only be *read* (import), never written. Imports copy
    values into ``sim_*`` rows; the source rows stay untouched.
  * Simulation execution uses its own Redis queue/namespace (see
    ``app.sim_generator``), never ``queue:execution_tasks`` — so nothing the
    operator does in the simulation can reach a real channel.

Entity separation (mirrors the operator's model):
  Мир (SimWorld)      — one isolated polygon; everything below belongs to one.
  Канал (SimChannel)  — source of posts.
  Пост (SimPost)      — object of discussion; belongs to a channel, not an account.
  Комментарий (SimComment) — reaction to a post or to another comment (tree).
  Аккаунт (SimAccount)— MANUAL executor, driven by the human operator.
  Агент/душа (SimPersona) — AI personality under test, driven by the LLM.
  Миссия (SimMission) — scenario grouping several agents; simulation-only.
  Активность (SimEvent) — journal of everything that happened.
  Знания (SimKnowledge) — imported RAG / context / facts / prompts / rules.
  Ландшафт (SimLandscapeSource) — external-environment import definitions.
  Задание (SimJob)    — a mass generation/publication run.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on PostgreSQL, plain JSON on SQLite — so the suite can exercise the real
# models on an in-memory database without a Postgres/pgvector server.
JSONCol = JSONB().with_variant(JSON(), "sqlite")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SimBase(DeclarativeBase):
    """Declarative base for the simulation ONLY. Never mixed with production."""
    pass


# ── Canonical vocabularies ────────────────────────────────────────────────

# Lifecycle of a generated/manual artefact, as shown in the activity feed.
SIM_STATUSES = ("draft", "generated", "published", "scheduled", "error", "done")
# Who authored a comment.
SIM_AUTHOR_KINDS = ("account", "persona", "external")
# How it came to be.
SIM_ORIGINS = ("manual", "ai", "imported")
# Mass-generation execution modes.
SIM_JOB_MODES = ("generate", "generate_publish", "draft")
# Knowledge buckets (what kind of context this row carries).
SIM_KNOWLEDGE_KINDS = ("fact", "news", "rule", "prompt", "channel_profile", "history")
# Landscape source kinds.
SIM_LANDSCAPE_KINDS = ("rss", "web", "tg_preview", "knowledge", "landscape", "manual")
# Media attachment kinds a post can carry.
SIM_MEDIA_KINDS = ("image", "video", "audio", "document", "link")


class SimWorld(SimBase):
    """An isolated polygon. Deleting a world removes everything inside it."""
    __tablename__ = "sim_worlds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Free-form knobs (default tone, generation defaults, …) — fully operator-editable.
    settings: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    channels: Mapped[list["SimChannel"]] = relationship(
        "SimChannel", back_populates="world", cascade="all, delete-orphan")
    accounts: Mapped[list["SimAccount"]] = relationship(
        "SimAccount", back_populates="world", cascade="all, delete-orphan")
    personas: Mapped[list["SimPersona"]] = relationship(
        "SimPersona", back_populates="world", cascade="all, delete-orphan")
    missions: Mapped[list["SimMission"]] = relationship(
        "SimMission", back_populates="world", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SimWorld(id={self.id}, name='{self.name}')>"


class SimChannel(SimBase):
    """Источник постов. Telegram-like: @username, title, avatar colour, subscribers."""
    __tablename__ = "sim_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(200), nullable=False)   # @tashkent_news
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_color: Mapped[str] = mapped_column(String(20), default="indigo", nullable=False)
    subscribers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    geo_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    tags: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    # 'manual' | 'landscape' | 'import' — where this channel came from.
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    external_ref: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    world: Mapped["SimWorld"] = relationship("SimWorld", back_populates="channels")
    posts: Mapped[list["SimPost"]] = relationship(
        "SimPost", back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("world_id", "username", name="uq_sim_channel_username"),)

    def __repr__(self) -> str:
        return f"<SimChannel(id={self.id}, {self.username})>"


class SimPost(SimBase):
    """
    Объект обсуждения. A post belongs to a CHANNEL (never to an account) — the
    author shown in the UI is the channel itself, exactly like Telegram.
    """
    __tablename__ = "sim_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_channels.id", ondelete="CASCADE"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # [{kind: image|video|audio|document|link, url, name, caption}] — several per post.
    media: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    # {"👍": 12, "🔥": 3} — fully editable by hand.
    reactions: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Optional override of the displayed author (e.g. a channel's signed post).
    author_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="manual", nullable=False)
    external_ref: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    # Editable timeline position (any time can be set by hand).
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    channel: Mapped["SimChannel"] = relationship("SimChannel", back_populates="posts")
    comments: Mapped[list["SimComment"]] = relationship(
        "SimComment", back_populates="post", cascade="all, delete-orphan")
    revisions: Mapped[list["SimPostRevision"]] = relationship(
        "SimPostRevision", back_populates="post", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SimPost(id={self.id}, channel={self.channel_id})>"


class SimPostRevision(SimBase):
    """Полная история изменений поста — one immutable snapshot per edit."""
    __tablename__ = "sim_post_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    # Full snapshot of the post BEFORE the change (text/media/reactions/channel/…).
    snapshot: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    note: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    post: Mapped["SimPost"] = relationship("SimPost", back_populates="revisions")

    def __repr__(self) -> str:
        return f"<SimPostRevision(post={self.post_id}, at={self.created_at})>"


class SimAccount(SimBase):
    """
    Аккаунт — РУЧНОЙ исполнитель. A human operator acts through it; it is NOT an
    agent and is never driven by the LLM. Used for crowd/atmosphere simulation.
    """
    __tablename__ = "sim_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    handle: Mapped[str] = mapped_column(String(120), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    initials: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="blue", nullable=False)
    # active | muted | banned — cosmetic lifecycle for realistic scenarios.
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    world: Mapped["SimWorld"] = relationship("SimWorld", back_populates="accounts")

    __table_args__ = (UniqueConstraint("world_id", "handle", name="uq_sim_account_handle"),)

    def __repr__(self) -> str:
        return f"<SimAccount(id={self.id}, {self.handle})>"


class SimPersona(SimBase):
    """
    Агент / душа / ИИ-личность — the object under test, driven by the LLM.

    Deliberately self-contained: the whole persona (identity, interests, style,
    system prompt) is stored here and shipped INLINE to ORPHEUS at generation
    time, so the simulation never reads or mutates a production AgentProfile.
    ``source_agent_id`` only records which real soul it was copied from.
    """
    __tablename__ = "sim_personas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    agent_key: Mapped[str] = mapped_column(String(80), nullable=False)   # sim-local id
    codename: Mapped[str] = mapped_column(String(120), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    caste: Mapped[str] = mapped_column(String(20), default="alpha", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    color: Mapped[str] = mapped_column(String(20), default="violet", nullable=False)

    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    core_mission: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    interests: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    # {tone_level, vocab_level, emoji_frequency, aggression, quirks[], language}
    style: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    # The system prompt under test. Empty → the engine's default persona prompt.
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Behaviour patterns the operator tunes; autosaved from the UI.
    settings: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    # Which production soul this was copied from (informational only, no FK).
    source_agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    world: Mapped["SimWorld"] = relationship("SimWorld", back_populates="personas")

    __table_args__ = (UniqueConstraint("world_id", "agent_key", name="uq_sim_persona_key"),)

    def __repr__(self) -> str:
        return f"<SimPersona(id={self.id}, {self.agent_key}, caste={self.caste})>"


class SimComment(SimBase):
    """
    Комментарий — a reply to a post or to another comment (Telegram-like tree).
    Authored either by a manual ACCOUNT, by an AI PERSONA, or by an EXTERNAL
    (imported / synthetic bystander) voice.
    """
    __tablename__ = "sim_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_comments.id", ondelete="CASCADE"), nullable=True, index=True)

    author_kind: Mapped[str] = mapped_column(String(20), default="account", nullable=False)
    account_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_accounts.id", ondelete="SET NULL"), nullable=True)
    persona_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_personas.id", ondelete="SET NULL"), nullable=True)
    # Fallback / override label (external voices, or a renamed author).
    author_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reactions: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    # draft | generated | published | scheduled | error
    status: Mapped[str] = mapped_column(String(20), default="published", nullable=False, index=True)
    # manual | ai | imported — distinguishes hand-written from machine work in the UI.
    origin: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    mission_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_missions.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_jobs.id", ondelete="SET NULL"), nullable=True)
    # Generation trace: prompt, model, tactic, rag facts, error reason…
    meta: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    post: Mapped["SimPost"] = relationship("SimPost", back_populates="comments")
    # Self-referential tree: deleting a comment deletes the branch under it.
    parent: Mapped[Optional["SimComment"]] = relationship(
        "SimComment", remote_side=[id], back_populates="children")
    children: Mapped[list["SimComment"]] = relationship(
        "SimComment", back_populates="parent", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<SimComment(id={self.id}, post={self.post_id}, status={self.status})>"


class SimMission(SimBase):
    """
    Миссия — a scenario that groups several agents. Simulation-only: it has no
    link to ``missions`` and is never picked up by MYRMIDON's target engine, so
    it can never act on a real channel.
    """
    __tablename__ = "sim_missions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # цель
    stance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # мировоззрение / сторона
    worldview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # дополнительный контекст
    # Stage 41 — the mission as an explicit POSITION, mirroring production `missions`.
    # Without these the polygon tested a strictly weaker mission than the live engine
    # runs: free-text goal+stance makes the model guess whose side it is on, which is
    # exactly the failure Stage 38 introduced these fields to remove. A polygon that
    # cannot express the production position cannot predict production behaviour.
    our_side: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    opponent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_points: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    red_lines: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    # dynamic | soft_support | aggressive_displacement | sentiment_shift | amplify
    tactic: Mapped[str] = mapped_column(String(50), default="dynamic", nullable=False)
    # comment | reply | mixed — what the mission's agents produce.
    mode: Mapped[str] = mapped_column(String(20), default="comment", nullable=False)
    # active | paused
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False, index=True)
    # {"channel_ids": [...], "post_ids": [...], "comment_ids": [...]} — where it operates.
    scope: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    settings: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    world: Mapped["SimWorld"] = relationship("SimWorld", back_populates="missions")
    agents: Mapped[list["SimMissionAgent"]] = relationship(
        "SimMissionAgent", back_populates="mission", cascade="all, delete-orphan", lazy="selectin")

    def __repr__(self) -> str:
        return f"<SimMission(id={self.id}, '{self.title}', {self.status})>"


class SimMissionAgent(SimBase):
    """Enlistment of a simulation persona into a simulation mission."""
    __tablename__ = "sim_mission_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_missions.id", ondelete="CASCADE"), nullable=False, index=True)
    persona_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_personas.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), default="alpha", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    mission: Mapped["SimMission"] = relationship("SimMission", back_populates="agents")

    __table_args__ = (UniqueConstraint("mission_id", "persona_id", name="uq_sim_mission_agent"),)


class SimMissionDossier(SimBase):
    """
    The polygon's copy of a mission's shared case file (production `mission_dossier`).

    The polygon exists to predict production, so it has to reproduce the thing that
    makes a roster a team rather than three individuals: one memory. Without it the
    polygon's alpha, beta and gamma each write as if the others had not spoken, which
    is precisely the behaviour production was fixed to stop — so a polygon run would
    flatter or damn a configuration for the wrong reason.

    `kind`: fact | opponent | counter | said. `said` is scoped to a post, because
    repeating yourself in one thread is what gives a swarm away, while reusing a good
    argument in another thread is normal.
    """
    __tablename__ = "sim_mission_dossier"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_missions.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    added_by: Mapped[str] = mapped_column(String(50), default="system", nullable=False)
    related_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    post_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    times_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class SimMissionOutcome(SimBase):
    """
    What a polygon run achieved, measured the same way production measures it.

    Success is a change of tone plus real people engaging. In the polygon the "after"
    reading needs no waiting: the thread is ours, so the same 3-way verdict is taken
    over the conversation before our comment and after it, in one pass — which is what
    makes the polygon the right place to compare mission wordings, and the live channel
    only the place to verify delivery.
    """
    __tablename__ = "sim_mission_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_missions.id", ondelete="CASCADE"), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_posts.id", ondelete="CASCADE"), nullable=False, index=True)
    label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    mood_before: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    mood_after: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    thread_size_before: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    our_comments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class SimKnowledge(SimBase):
    """
    Знания — the simulation's own RAG base. Facts, news, system rules, prompts,
    channel profiles and historical data live here; retrieval at generation time
    reads ONLY this table, so a prompt can never be grounded on production data
    by accident (importing copies rows in explicitly).
    """
    __tablename__ = "sim_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="fact", nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list] = mapped_column(JSONCol, nullable=False, default=list)
    source: Mapped[Optional[str]] = mapped_column(String(700), nullable=True)
    # manual | import:knowledge | import:landscape | import:channel_profile | scrape:<kind>
    origin: Mapped[str] = mapped_column(String(50), default="manual", nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<SimKnowledge(id={self.id}, kind={self.kind})>"


class SimLandscapeSource(SimBase):
    """
    Ландшафт — a definition of an external environment to pull into the polygon
    (RSS feed, web page, public Telegram web preview, or an internal production
    source read read-only). Running it materialises sim channels/posts/knowledge.
    """
    __tablename__ = "sim_landscape_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(30), default="rss", nullable=False)
    url: Mapped[str] = mapped_column(String(700), nullable=False, default="")
    title: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    # Where imported posts land; NULL → a channel is created from the source title.
    target_channel_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_channels.id", ondelete="SET NULL"), nullable=True)
    # {limit, as_knowledge, tags[], with_comments}
    options: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    items_imported: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<SimLandscapeSource(id={self.id}, kind={self.kind}, url='{self.url}')>"


class SimJob(SimBase):
    """
    Массовая генерация — one batch run (N comments / replies / posts, from M
    personas and accounts). Tracks progress so the UI can show it live.
    """
    __tablename__ = "sim_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    # comments | replies | post — what is being produced.
    kind: Mapped[str] = mapped_column(String(30), default="comments", nullable=False)
    # generate | generate_publish | draft
    mode: Mapped[str] = mapped_column(String(30), default="generate_publish", nullable=False)
    # queued | running | done | error | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    params: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    post_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_posts.id", ondelete="SET NULL"), nullable=True)
    mission_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("sim_missions.id", ondelete="SET NULL"), nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    done: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<SimJob(id={self.id}, {self.kind}/{self.mode}, {self.status})>"


class SimEvent(SimBase):
    """
    Активность — the journal the left column renders. Every state transition in
    the polygon (created / generated / published / scheduled / error / done) is
    recorded here with the actor that caused it.
    """
    __tablename__ = "sim_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("sim_worlds.id", ondelete="CASCADE"), nullable=False, index=True)
    # channel | post | comment | reaction | mission | agent | account | knowledge |
    # landscape | generation | system
    kind: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="done", nullable=False, index=True)
    actor_kind: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)  # operator|account|persona|mission|system
    actor_label: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    channel_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    post_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    comment_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mission_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSONCol, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    def __repr__(self) -> str:
        return f"<SimEvent(id={self.id}, {self.kind}/{self.status})>"


# Convenience: every table the simulation owns (used by tests to assert that the
# module never reaches outside its own namespace).
SIM_TABLES = tuple(sorted(SimBase.metadata.tables.keys()))
