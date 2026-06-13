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

    # Cosine ANN index on KnowledgeFact embeddings (idempotent).
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS idx_knowledge_facts_embedding "
                    "ON knowledge_facts USING ivfflat (embedding vector_cosine_ops) "
                    "WITH (lists = 100)"
                )
            )
        logger.info("KnowledgeFact IVFFlat cosine index ensured.")
    except Exception as exc:  # pragma: no cover - index is an optimisation, not a hard dep
        logger.warning("Could not create IVFFlat index (continuing without it): %s", exc)

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
        logger.info("KnowledgeFact landscape_layers GIN index ensured.")
    except Exception as exc:  # pragma: no cover
        logger.warning("Could not create landscape_layers GIN index: %s", exc)
