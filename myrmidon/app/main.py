"""
MYRMIDON — Execution Swarm Service Entrypoint
================================================
Consumes execution tasks from Redis (queue:execution_tasks) and
physically executes social media actions via Appium (mobile) or
Pyrogram (Telegram MTProto).

Responsibilities:
  - Read agent credentials from PostgreSQL (souls_accounts table).
  - Route tasks to the appropriate execution driver (Appium or Pyrogram).
  - Enforce per-agent proxy isolation (SOCKS5).
  - Emulate human timing (typing speed ~200 chars/min, scroll delays).
  - Handle DM bypass rule (ignore or send stub text).

Full Appium Page Object drivers will be implemented in Stage 4.
This entrypoint establishes Redis + PostgreSQL connectivity and
runs the task consumption loop.
"""

import json
import logging
import os
import signal
import sys
import time
from typing import Optional

import redis
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("myrmidon")

# ── Configuration ─────────────────────────────────────────────────────────

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
DB_USER = os.getenv("DB_USER", "morpheus_admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "morpheus_secure_pass")
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "morpheus_db")

EXECUTION_TASKS_QUEUE = "queue:execution_tasks"
PROCESSING_TIMEOUT_SEC = 5

# Human emulation constants
TYPING_SPEED_CPM = 200  # Characters per minute (~3.3 chars/sec)
MIN_READ_DELAY_SEC = 3
MAX_READ_DELAY_SEC = 8
DM_STUB_TEXT = "Редко читаю ЛС, пишите в комментарии под постами"

# ── Graceful shutdown ─────────────────────────────────────────────────────

_shutdown_requested = False


def _handle_signal(signum: int, frame) -> None:
    global _shutdown_requested
    logger.info("Received signal %d — initiating graceful shutdown...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Redis connectivity ────────────────────────────────────────────────────

def connect_redis(max_retries: int = 10, retry_delay: float = 3.0) -> redis.Redis:
    """
    Establish a connection to Redis with retry logic.
    Verifies connectivity with PING.
    """
    for attempt in range(1, max_retries + 1):
        try:
            client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=30,
                retry_on_timeout=True,
            )
            if client.ping():
                logger.info(
                    "Redis connection established (host=%s, port=%d).",
                    REDIS_HOST,
                    REDIS_PORT,
                )
                return client
        except redis.ConnectionError as exc:
            logger.warning(
                "Redis connection attempt %d/%d failed: %s",
                attempt,
                max_retries,
                exc,
            )
            if attempt < max_retries:
                time.sleep(retry_delay)

    logger.critical("Failed to connect to Redis after %d attempts. Exiting.", max_retries)
    sys.exit(1)


# ── PostgreSQL connectivity ──────────────────────────────────────────────

def connect_postgres(max_retries: int = 10, retry_delay: float = 3.0) -> sessionmaker:
    """
    Create a SQLAlchemy session factory connected to PostgreSQL.
    Retries on connection failure.
    """
    db_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    for attempt in range(1, max_retries + 1):
        try:
            engine = create_engine(
                db_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
            # Verify connectivity
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            logger.info(
                "PostgreSQL connection established (host=%s, db=%s).",
                DB_HOST,
                DB_NAME,
            )
            return sessionmaker(bind=engine, autocommit=False, autoflush=False)
        except Exception as exc:
            logger.warning(
                "PostgreSQL connection attempt %d/%d failed: %s",
                attempt,
                max_retries,
                exc,
            )
            if attempt < max_retries:
                time.sleep(retry_delay)

    logger.critical("Failed to connect to PostgreSQL after %d attempts. Exiting.", max_retries)
    sys.exit(1)


# ── Agent credentials lookup ─────────────────────────────────────────────

def get_agent_credentials(
    db_session_factory: sessionmaker,
    agent_id: str,
    platform: str,
) -> Optional[dict]:
    """
    Fetch agent credentials from the souls_accounts table.

    Returns a dict with: username, password_hash, auth_cookies,
    assigned_proxy, status. Returns None if not found or disabled.
    """
    session = db_session_factory()
    try:
        result = session.execute(
            text(
                "SELECT id, username, password_hash, auth_cookies, assigned_proxy, status "
                "FROM souls_accounts "
                "WHERE agent_id = :agent_id AND platform = :platform AND status = 'active' "
                "LIMIT 1"
            ),
            {"agent_id": agent_id, "platform": platform},
        )
        row = result.fetchone()
        if row is None:
            logger.warning(
                "No active account found for agent=%s, platform=%s.",
                agent_id,
                platform,
            )
            return None

        return {
            "account_id": row[0],
            "username": row[1],
            "password_hash": row[2],
            "auth_cookies": row[3],
            "assigned_proxy": row[4],
            "status": row[5],
        }
    except Exception as exc:
        logger.error("Credentials lookup failed: %s", exc)
        return None
    finally:
        session.close()


# ── Human timing emulation ───────────────────────────────────────────────

def calculate_typing_delay(text_length: int) -> float:
    """
    Calculate a realistic typing delay based on text length.
    Targets ~200 characters per minute with slight randomization.
    """
    import random

    base_seconds = (text_length / TYPING_SPEED_CPM) * 60
    # Add 10-25% random variation
    jitter = base_seconds * random.uniform(0.10, 0.25)
    return base_seconds + jitter


def simulate_read_delay() -> None:
    """Simulate the delay of a human reading a post before responding."""
    import random

    delay = random.uniform(MIN_READ_DELAY_SEC, MAX_READ_DELAY_SEC)
    logger.debug("Simulating read delay: %.1fs", delay)
    time.sleep(delay)


# ── Task execution (routing) ─────────────────────────────────────────────

def execute_task(task: dict, db_session_factory: sessionmaker) -> None:
    """
    Execute a single task from the execution queue.

    Routes to the appropriate driver based on target_platform:
      - telegram → Pyrogram MTProto (Stage 4)
      - instagram/twitter/threads/facebook/youtube → Appium mobile driver (Stage 4)
    """
    task_id = task.get("task_id", "unknown")
    agent_id = task.get("agent_id", "unknown")
    platform = task.get("target_platform", "unknown")
    action_type = task.get("action_type", "comment")
    text_to_publish = task.get("text_to_publish", "")
    execution_delay = task.get("execution_delay_sec", 45)

    logger.info(
        "Executing task %s: agent=%s, platform=%s, action=%s",
        task_id,
        agent_id,
        platform,
        action_type,
    )

    # Step 1: Fetch agent credentials
    credentials = get_agent_credentials(db_session_factory, agent_id, platform)
    if credentials is None:
        logger.error(
            "Task %s FAILED — no credentials for agent=%s, platform=%s.",
            task_id,
            agent_id,
            platform,
        )
        return

    proxy = credentials.get("assigned_proxy")
    if proxy:
        logger.info(
            "Task %s — using proxy %s for session isolation.",
            task_id,
            proxy,
        )
        from app.proxy_manager import configure_proxy

        configure_proxy(proxy)

    # Step 2: Apply execution delay (mimics human reading + thinking)
    if execution_delay > 0:
        logger.info("Task %s — waiting %ds before execution...", task_id, execution_delay)
        time.sleep(execution_delay)

    # Step 3: Route to platform-specific driver
    if platform == "telegram":
        _execute_telegram(task, credentials)
    elif platform in ("instagram", "twitter", "threads", "facebook", "youtube"):
        _execute_appium(task, credentials)
    else:
        logger.warning("Task %s — unsupported platform '%s'. Skipping.", task_id, platform)


def _execute_telegram(task: dict, credentials: dict) -> None:
    """
    Execute a Telegram action via Pyrogram MTProto.
    Full implementation in Stage 4 (Pyrogram session management).
    """
    task_id = task.get("task_id")
    text_to_publish = task.get("text_to_publish", "")

    logger.info(
        "Task %s — Telegram execution via Pyrogram. "
        "Text length: %d chars. MTProto driver will be activated in Stage 4.",
        task_id,
        len(text_to_publish),
    )

    # Stage 4 integration point:
    # 1. Initialize Pyrogram client with session cookies
    # 2. Navigate to target chat/channel
    # 3. Type and send the message with human-like timing
    # 4. Handle DM bypass if applicable

    typing_delay = calculate_typing_delay(len(text_to_publish))
    logger.debug(
        "Task %s — typing emulation would take %.1fs for %d chars.",
        task_id,
        typing_delay,
        len(text_to_publish),
    )


def _execute_appium(task: dict, credentials: dict) -> None:
    """
    Execute a mobile platform action via Appium.
    Full implementation in Stage 4 (Page Object drivers).
    """
    task_id = task.get("task_id")
    platform = task.get("target_platform")
    text_to_publish = task.get("text_to_publish", "")

    logger.info(
        "Task %s — %s execution via Appium. "
        "Text length: %d chars. Mobile driver will be activated in Stage 4.",
        task_id,
        platform,
        len(text_to_publish),
    )

    # Stage 4 integration point:
    # 1. Launch Android emulator with proxy configuration
    # 2. Open target app via Appium
    # 3. Navigate to target post
    # 4. Simulate human reading delay
    # 5. Type comment at ~200 chars/min
    # 6. Post and verify

    simulate_read_delay()
    typing_delay = calculate_typing_delay(len(text_to_publish))
    logger.debug(
        "Task %s — read delay applied, typing emulation would take %.1fs.",
        task_id,
        typing_delay,
    )


# ── Main loop ─────────────────────────────────────────────────────────────

def main() -> None:
    """
    MYRMIDON main entrypoint.

    1. Connect to Redis and verify queue accessibility.
    2. Connect to PostgreSQL and verify souls_accounts table access.
    3. Enter the execution task processing loop.
    """
    logger.info("=" * 60)
    logger.info("MYRMIDON Execution Swarm — Starting Up")
    logger.info("=" * 60)

    redis_client = connect_redis()
    db_session_factory = connect_postgres()

    # Verify execution queue
    queue_depth = redis_client.llen(EXECUTION_TASKS_QUEUE)
    logger.info(
        "Queue '%s' accessible — current depth: %d tasks.",
        EXECUTION_TASKS_QUEUE,
        queue_depth,
    )

    # Verify database access
    session = db_session_factory()
    try:
        result = session.execute(text("SELECT COUNT(*) FROM souls_accounts"))
        account_count = result.scalar()
        logger.info("Database accessible — %d agent accounts registered.", account_count)
    except Exception as exc:
        logger.warning("souls_accounts table check failed (may not exist yet): %s", exc)
    finally:
        session.close()

    logger.info("Entering execution task loop (BRPOP timeout=%ds).", PROCESSING_TIMEOUT_SEC)

    while not _shutdown_requested:
        try:
            result = redis_client.brpop(EXECUTION_TASKS_QUEUE, timeout=PROCESSING_TIMEOUT_SEC)
            if result is None:
                continue

            _, task_json = result
            try:
                task_data = json.loads(task_json)
            except json.JSONDecodeError as exc:
                logger.error("Malformed task JSON: %s", exc)
                continue

            execute_task(task_data, db_session_factory)

        except redis.ConnectionError as exc:
            logger.error("Redis connection lost: %s — reconnecting...", exc)
            redis_client = connect_redis()
        except Exception:
            logger.exception("Unexpected error in MYRMIDON task loop.")
            time.sleep(2)

    logger.info("MYRMIDON shutdown complete.")


if __name__ == "__main__":
    main()
