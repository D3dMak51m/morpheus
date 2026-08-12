"""
DAEDALUS — Database Session & Engine Configuration
====================================================
Provides the async-compatible SQLAlchemy engine, session factory,
and a FastAPI dependency for injecting DB sessions into route handlers.
"""

import logging
import os
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base
from app.models_simulation import SimBase

logger = logging.getLogger("daedalus.database")


def _build_database_url() -> str:
    """Construct the PostgreSQL connection URL from environment variables."""
    user = os.getenv("DB_USER", "morpheus_admin")
    password = os.getenv("DB_PASSWORD", "morpheus_secure_pass")
    host = os.getenv("DB_HOST", "postgres")
    port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "morpheus_db")
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


DATABASE_URL = _build_database_url()

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=os.getenv("ENVIRONMENT", "development") == "development",
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency — yields a SQLAlchemy session and ensures
    it is closed after the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_tables() -> None:
    """
    Create all tables defined in the ORM metadata if they don't exist.

    Stage 21 — the ``vector`` extension must exist *before* create_all runs, or
    the KnowledgeFact.embedding column (Vector type) cannot be created. We also
    build an IVFFlat cosine index for fast approximate nearest-neighbour search.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        logger.info("pgvector extension ensured.")

    Base.metadata.create_all(bind=engine)

    # SIMULATION — an isolated test polygon living in its own ``sim_`` namespace
    # with its own declarative base. Created alongside production but never
    # linked to it (no cross-FKs), so it can be dropped without touching real data.
    SimBase.metadata.create_all(bind=engine)
    logger.info("Simulation tables ensured (%d isolated sim_* tables).", len(SimBase.metadata.tables))

    # Cosine ANN index on KnowledgeFact embeddings (idempotent).
    # Stage 39 — HNSW replaces IVFFlat, because the IVFFlat index was silently
    # returning the WRONG nearest neighbour.
    #
    # `lists = 100` over a corpus of ~800 rows puts ~8 rows in each list, and with the
    # default `ivfflat.probes = 1` a search examined a single list — i.e. ~1% of the
    # table. Measured on live data: the indexed top-1 matched the true top-1 in only
    # 3 of 14 probes (21% recall), and a genuine duplicate at cosine 0.968 came back as
    # a 0.845 stranger. That corrupts BOTH consumers — dedup never saw the duplicate it
    # was meant to merge, and RAG ranked facts it had not actually compared.
    #
    # HNSW has no list/row-count coupling and gives high recall at its defaults.
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS idx_knowledge_facts_embedding"))
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_facts_embedding_hnsw "
                    "ON knowledge_facts USING hnsw (embedding vector_cosine_ops)"
                )
            )
        logger.info("KnowledgeFact HNSW cosine index ensured (IVFFlat dropped).")
    except Exception as exc:  # pragma: no cover - index is an optimisation, not a hard dep
        logger.warning("Could not create HNSW index (continuing without it): %s", exc)

    # Stage 23 — lightweight idempotent column migrations. create_all() never
    # ALTERs existing tables, so new columns on already-created tables (e.g. the
    # decoupled-identity `status`) must be added explicitly. Safe to re-run.
    _STAGE23_COLUMNS = [
        ("agent_profiles", "status", "VARCHAR(20) NOT NULL DEFAULT 'unbound'"),
        ("souls_accounts", "status", "VARCHAR(20) NOT NULL DEFAULT 'unbound'"),
        # Stage 25 — cached channel-enumeration metadata.
        ("agent_channel_prefs", "chat_type", "VARCHAR(20)"),
        ("agent_channel_prefs", "members", "INTEGER"),
        ("agent_channel_prefs", "synced_at", "TIMESTAMPTZ"),
        # Stage 34 — Mission redesign (permanent goal).
        ("missions", "stance", "TEXT"),
        ("missions", "agent_mode", "VARCHAR(20) NOT NULL DEFAULT 'manual'"),
        ("missions", "dynamic_count", "INTEGER NOT NULL DEFAULT 3"),
        # Stage 38 — target health (can we actually comment there at all?).
        ("mission_targets", "health", "VARCHAR(20) NOT NULL DEFAULT 'unknown'"),
        ("mission_targets", "health_reason", "TEXT"),
        ("mission_targets", "health_checked_at", "TIMESTAMPTZ"),
        # Stage 38 — mission as an explicit position (side / opponent / arguments).
        ("missions", "our_side", "TEXT"),
        ("missions", "opponent", "TEXT"),
        ("missions", "key_points", "JSONB DEFAULT '[]'::jsonb"),
        ("missions", "red_lines", "JSONB DEFAULT '[]'::jsonb"),
        # Stage 39 — non-destructive knowledge merging (superseded wordings).
        ("knowledge_facts", "variants", "JSONB DEFAULT '[]'::jsonb"),
        # Stage 39 — canonical place tags (geo lookups used to match on `tags`, which
        # mixed places with people and split one place across languages).
        ("knowledge_facts", "geo_tags", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
        # Stage 39 — source publication date (freshness must not mean "scraped today").
        ("knowledge_facts", "published_at", "TIMESTAMPTZ"),
        # Stage 39 — scraping source health. A dead feed (kun.uz returned HTML with
        # HTTP 200 → 0 entries) was indistinguishable from "no new items"; HUGINN now
        # reports every pass so the operator can see a source stop producing.
        ("scraping_landscape", "last_scraped_at", "TIMESTAMPTZ"),
        ("scraping_landscape", "last_item_count", "INTEGER"),
        ("scraping_landscape", "consecutive_empty", "INTEGER NOT NULL DEFAULT 0"),
        ("scraping_landscape", "health", "VARCHAR(20) NOT NULL DEFAULT 'unknown'"),
        ("scraping_landscape", "health_reason", "TEXT"),
    ]
    try:
        with engine.begin() as conn:
            for table, column, ddl in _STAGE23_COLUMNS:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ddl}"))
            # Mission target_url is now optional (real targets live in mission_targets).
            conn.execute(text("ALTER TABLE missions ALTER COLUMN target_url DROP NOT NULL"))
        logger.info("Stage 23/34 column migrations ensured.")
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not ensure column migrations: %s", exc)

    # Stage 22 — GIN index on the JSONB landscape_layers array so the `?|`
    # overlap filter used by RAG array-intersection search is fast.
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_facts_layers "
                    "ON knowledge_facts USING gin (landscape_layers)"
                )
            )
            # Stage 39 — by-geo filters on geo_tags with the same `?|` overlap operator.
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_facts_geo_tags "
                    "ON knowledge_facts USING gin (geo_tags)"
                )
            )
        logger.info("KnowledgeFact landscape_layers/geo_tags GIN indexes ensured.")
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not create landscape_layers GIN index: %s", exc)
