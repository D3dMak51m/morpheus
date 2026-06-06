"""
DAEDALUS — Database Session & Engine Configuration
====================================================
Provides the async-compatible SQLAlchemy engine, session factory,
and a FastAPI dependency for injecting DB sessions into route handlers.
"""

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base


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
    """Create all tables defined in the ORM metadata if they don't exist."""
    Base.metadata.create_all(bind=engine)
