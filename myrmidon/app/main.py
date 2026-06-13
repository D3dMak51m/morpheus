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
import uuid
from typing import Optional

import httpx
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

DAEDALUS_URL = "http://daedalus:8000"
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

EXECUTION_TASKS_QUEUE = "queue:execution_tasks"
# Stage 25 — ask ORPHEUS to write a real, context-aware mission comment.
MISSION_GEN_QUEUE = "queue:mission_gen"
ORPHEUS_GEN_TIMEOUT = int(os.getenv("ORPHEUS_GEN_TIMEOUT_SEC", "150"))
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


# Only the main thread may install signal handlers. The entrypoint runs this
# module as ``__main__``, so a late ``from app.main import ...`` (e.g. from the
# dialogue engine's daemon thread) re-imports it as ``app.main`` and would re-run
# these lines off the main thread → "signal only works in main thread". Guard it.
import threading as _threading
if _threading.current_thread() is _threading.main_thread():
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


# ── Activity Logging ─────────────────────────────────────────────────────

def _log_activity_to_daedalus(task: dict, status: str) -> None:
    """
    Executes an authorized POST request directly to the DAEDALUS activity logging system
    to instantly update the PostgreSQL agent_activity_logs table.
    """
    payload = {
        "agent_id": task.get("agent_id", "unknown"),
        "platform": task.get("target_platform", "unknown"),
        "action_type": task.get("action_type", "comment"),
        "target_url": task.get("target_url", ""),
        "text_content": task.get("text_to_publish", ""),
        "status": status,
    }
    
    headers = {"X-Internal-Token": INTERNAL_API_TOKEN}
    
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{DAEDALUS_URL}/api/v1/analytics/internal/activity", json=payload, headers=headers)
            resp.raise_for_status()
            logger.debug("Activity logged to Daedalus: %s (status=%s)", task.get("task_id"), status)
    except Exception as exc:
        logger.error("Failed to log activity to Daedalus: %s", exc)


# ── Cognitive comment generation (ORPHEUS request/reply over Redis) ────────

_gen_redis_client: Optional[redis.Redis] = None


def _get_gen_redis() -> redis.Redis:
    """Lazy Redis client for the ORPHEUS generation round-trip (long socket timeout)."""
    global _gen_redis_client
    if _gen_redis_client is None:
        _gen_redis_client = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=0,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=ORPHEUS_GEN_TIMEOUT + 15,
        )
    return _gen_redis_client


def generate_comment_via_orpheus(task: dict, post_text: str, author: str, thread_context: str = "") -> str:
    """
    Ask ORPHEUS to write a real comment from the post context, the *mood of the
    discussion thread*, and the mission's persona/role/tactic/objective (+
    ORPHEUS-side RAG knowledge & memory). Blocking request/reply over Redis.
    Returns '' on timeout/failure so the caller can fall back to deterministic text.
    """
    request_id = str(uuid.uuid4())
    reply_key = f"reply:missiongen:{request_id}"
    req = {
        "request_id": request_id,
        "reply_key": reply_key,
        "mode": "comment",
        "agent_id": task.get("agent_id"),
        "platform": task.get("target_platform"),
        "target_url": task.get("target_url"),
        "post_text": post_text or "",
        "author": author or "",
        "thread_context": thread_context or "",
        "narrative_goal": task.get("narrative_goal") or "",
        "tactic": task.get("tactic") or "soft_support",
        "role": task.get("role") or "alpha",
        "forced_context": task.get("forced_context"),
        "alpha_context": task.get("alpha_context"),
    }
    try:
        client = _get_gen_redis()
        client.lpush(MISSION_GEN_QUEUE, json.dumps(req, ensure_ascii=False))
        logger.info("Task %s — requested ORPHEUS comment (req=%s); awaiting reply…", task.get("task_id"), request_id)
        res = client.brpop(reply_key, timeout=ORPHEUS_GEN_TIMEOUT)
        if not res:
            logger.warning("Task %s — ORPHEUS generation timed out (%ds); using fallback.", task.get("task_id"), ORPHEUS_GEN_TIMEOUT)
            return ""
        _, raw = res
        data = json.loads(raw)
        if data.get("status") == "ok" and data.get("text"):
            logger.info("Task %s — ORPHEUS comment ready (%d chars).", task.get("task_id"), len(data["text"]))
            return data["text"]
        logger.warning("Task %s — ORPHEUS returned no text (%s); using fallback.", task.get("task_id"), data.get("reason"))
        return ""
    except Exception as exc:
        logger.error("Task %s — ORPHEUS generation round-trip failed: %s", task.get("task_id"), exc)
        return ""


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
        _log_activity_to_daedalus(task, "FAILED")
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
        _log_activity_to_daedalus(task, "FAILED")


def _execute_telegram(task: dict, credentials: dict) -> None:
    """
    Execute a Telegram action via Pyrogram MTProto.
    """
    task_id = task.get("task_id")
    text_to_publish = task.get("text_to_publish", "")
    target_url = task.get("target_url", "")
    agent_id = task.get("agent_id")

    logger.info(
        "Task %s — Telegram execution via Pyrogram. "
        "Text length: %d chars.",
        task_id,
        len(text_to_publish),
    )

    try:
        from app.drivers.tg_client import TelegramDriver
        driver = TelegramDriver(agent_id, credentials)

        # Mission tasks carry generate=True: the driver reads the post being
        # replied to plus the mood of the discussion and ORPHEUS writes a real,
        # context-aware comment. Falls back to the task's deterministic text if
        # ORPHEUS is slow/unavailable.
        text_provider = None
        if task.get("generate"):
            text_provider = lambda post_text, author, thread_context="": generate_comment_via_orpheus(
                task, post_text, author, thread_context
            )

        # Carry the mission framing so a successful comment starts a dialogue watch
        # (the bot then keeps conversing with anyone who replies to it).
        watch_meta = {
            "narrative_goal": task.get("narrative_goal") or "",
            "tactic": task.get("tactic") or "soft_support",
            "role": task.get("role") or "alpha",
        }

        success = driver.execute_comment(
            target_url, text_to_publish, text_provider=text_provider, watch_meta=watch_meta
        )

        if success:
             logger.info("Task %s completed successfully on Telegram.", task_id)
             _log_activity_to_daedalus(task, "SUCCESS")
        else:
             logger.error("Task %s failed on Telegram.", task_id)
             _log_activity_to_daedalus(task, "FAILED")
    except Exception as e:
        logger.error("Task %s crashed on Telegram: %s", task_id, e)
        _log_activity_to_daedalus(task, "FAILED")


def _execute_appium(task: dict, credentials: dict) -> None:
    """
    Execute a mobile platform action via Appium.
    """
    task_id = task.get("task_id")
    platform = task.get("target_platform")
    text_to_publish = task.get("text_to_publish", "")
    target_url = task.get("target_url", "")
    agent_id = task.get("agent_id")

    logger.info(
        "Task %s — %s execution via Appium. "
        "Text length: %d chars.",
        task_id,
        platform,
        len(text_to_publish),
    )

    simulate_read_delay()

    success = False
    adb_sup = None
    device_id = None

    try:
        from app.adb_supervisor import ADBSupervisor
        adb_sup = ADBSupervisor()
        device_id = adb_sup.get_mapped_device(agent_id)
        
        if device_id:
            # Stage 12: Hardware isolation via Snapshots
            # 1. Boot from clean state (load idle_snap)
            adb_sup.manage_device_snapshot(device_id, "load", "idle_snap")
            time.sleep(2) # Give the emulator a moment to settle
            
            # 2. Hardware spoof
            adb_sup.spoof_device_hardware(device_id, serial=f"SPOOF-{agent_id[:8]}")
            
            # 3. Enforce proxy inside Android via settings
            proxy = credentials.get("assigned_proxy")
            if proxy:
                if "://" in proxy:
                    proxy = proxy.split("://")[1]
                host, port = proxy.split(":")
                adb_sup.enforce_os_level_proxy(device_id, host, int(port))
        
        if platform == "instagram":
             from app.drivers.instagram import InstagramDriver
             driver = InstagramDriver(agent_id, credentials)
             success = driver.execute_comment(target_url, text_to_publish)
        elif platform == "threads":
             from app.drivers.threads import ThreadsDriver
             driver = ThreadsDriver(agent_id, credentials)
             success = driver.execute_comment(target_url, text_to_publish)
        elif platform == "youtube":
             from app.drivers.youtube import YouTubeDriver
             driver = YouTubeDriver(agent_id, credentials)
             success = driver.execute_comment(target_url, text_to_publish)
        else:
             logger.warning("Mobile driver for %s not fully implemented yet.", platform)

        if success:
             logger.info("Task %s completed successfully on %s.", task_id, platform)
             _log_activity_to_daedalus(task, "SUCCESS")
        else:
             logger.error("Task %s failed on %s.", task_id, platform)
             _log_activity_to_daedalus(task, "FAILED")
             
    except Exception as e:
        logger.error("Task %s crashed on %s: %s", task_id, platform, e)
        _log_activity_to_daedalus(task, "FAILED")
    finally:
        # Stage 12 Cleanup: Save snapshot and shutdown (guard against early failures)
        if adb_sup is not None and device_id:
            try:
                adb_sup.manage_device_snapshot(device_id, "save", "idle_snap")
                adb_sup.shutdown_device(device_id)
            except Exception as cleanup_exc:
                logger.error("Task %s — cleanup failed for device %s: %s", task_id, device_id, cleanup_exc)


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

    # Start the Device API HTTP server in a background thread
    try:
        from app.device_api import start_device_api_server
        start_device_api_server()
    except Exception as e:
        logger.warning("Device API server failed to start: %s (non-fatal)", e)

    # Stage 19: start the AVD self-healing monitor (background daemon thread)
    try:
        from app.avd_orchestrator import start_health_monitor
        start_health_monitor()
    except Exception as e:
        logger.warning("AVD self-healing monitor failed to start: %s (non-fatal)", e)

    db_session_factory = connect_postgres()

    # Start the autonomous dialogue engine: polls for human replies to the bot's
    # comments and answers them with memory + thread-mood context (human-like).
    try:
        from app.dialogue_engine import start_dialogue_engine
        start_dialogue_engine(db_session_factory)
    except Exception as e:
        logger.warning("Dialogue engine failed to start: %s (non-fatal)", e)

    # Start the autonomous target-channel engine: polls target+watching channels,
    # relevance-filters new posts, and enqueues comment tasks (P4).
    try:
        from app.target_engine import start_target_engine
        start_target_engine(db_session_factory)
    except Exception as e:
        logger.warning("Target engine failed to start: %s (non-fatal)", e)

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
