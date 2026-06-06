"""
HUGINN — News Aggregator Service Entrypoint
=============================================
Background worker that connects to the Redis broker, verifies queue
connectivity, and runs the main event loop for content aggregation.

Data flow:
  1. Scrape Telegram channels (Telethon), websites (BS4/requests)
  2. Classify content by layers: Global → Region → State → City → Personal
  3. Apply TTL filtering (X/Threads > 12h → discard, TG group > 2h → discard)
  4. Serialize events to Redis queue 'queue:raw_events' for ORPHEUS
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from typing import Optional

import redis

from app.router import classify_layers
from app.scrapers.tg_scraper import run_tg_scraper
from app.scrapers.web_scraper import run_web_scraper
from app.scrapers.gamma_noise import run_gamma_noise_scheduler

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("huginn")

# ── Configuration ─────────────────────────────────────────────────────────

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
RAW_EVENTS_QUEUE = "queue:raw_events"
POLL_INTERVAL_SEC = int(os.getenv("HUGINN_POLL_INTERVAL", "30"))

# TTL thresholds (seconds) for discarding stale content
TTL_THRESHOLDS = {
    "twitter": 12 * 3600,     # 12 hours for X/Twitter
    "threads": 12 * 3600,     # 12 hours for Threads
    "telegram_group": 2 * 3600,  # 2 hours for Telegram groups
    "telegram_channel": 24 * 3600,  # 24 hours for Telegram channels
    "instagram": 24 * 3600,
    "facebook": 24 * 3600,
    "youtube": 48 * 3600,
}

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
    Verifies connectivity by issuing a PING command.
    """
    for attempt in range(1, max_retries + 1):
        try:
            client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
            )
            pong = client.ping()
            if pong:
                logger.info(
                    "Redis connection established (host=%s, port=%d) — PING OK.",
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


# ── TTL filtering ─────────────────────────────────────────────────────────

def is_content_expired(platform: str, timestamp: int) -> bool:
    """
    Check if a piece of content has exceeded its platform-specific TTL.
    Returns True if the content is stale and should be discarded.
    """
    threshold = TTL_THRESHOLDS.get(platform, 24 * 3600)
    age = int(time.time()) - timestamp
    return age > threshold


# ── Event publishing ──────────────────────────────────────────────────────

def publish_raw_event(
    redis_client: redis.Redis,
    event_id: str,
    source_platform: str,
    source_target: str,
    post_id: str,
    text_content: str,
    media_type: Optional[str],
    media_path: Optional[str],
    layers: dict,
    timestamp: int,
) -> None:
    """
    Serialize and push a raw event into the Redis queue for ORPHEUS.
    Follows the exact JSON contract from bootstrap_protocols.md.
    """
    event = {
        "event_id": event_id,
        "source_platform": source_platform,
        "source_target": source_target,
        "post_id": post_id,
        "text_content": text_content,
        "media_type": media_type,
        "media_path": media_path,
        "layers": layers,
        "timestamp": timestamp,
    }
    redis_client.lpush(RAW_EVENTS_QUEUE, json.dumps(event, ensure_ascii=False))
    logger.info(
        "Event published to %s: id=%s, platform=%s, target=%s",
        RAW_EVENTS_QUEUE,
        event_id,
        source_platform,
        source_target,
    )


# ── Main loop ─────────────────────────────────────────────────────────────

async def main_loop() -> None:
    """
    HUGINN main entrypoint (Async).
    """
    logger.info("=" * 60)
    logger.info("HUGINN News Aggregator — Starting Up (Stage 2)")
    logger.info("=" * 60)

    redis_client = connect_redis()

    # Verify queue is accessible
    queue_len = redis_client.llen(RAW_EVENTS_QUEUE)
    logger.info(
        "Queue '%s' accessible — current depth: %d events.",
        RAW_EVENTS_QUEUE,
        queue_len,
    )

    logger.info("Starting scraper tasks...")
    
    # Run scrapers concurrently
    tg_task = asyncio.create_task(run_tg_scraper(redis_client, RAW_EVENTS_QUEUE, is_content_expired, publish_raw_event))
    web_task = asyncio.create_task(run_web_scraper(redis_client, RAW_EVENTS_QUEUE, is_content_expired, publish_raw_event))
    gamma_task = asyncio.create_task(run_gamma_noise_scheduler(redis_client, publish_raw_event))
    
    while not _shutdown_requested:
        try:
            # Periodic TTL cleanup of stale cached posts
            cached_count = redis_client.llen("cache:post_ids")
            if cached_count > 0:
                logger.debug("Post ID cache size: %d", cached_count)

            await asyncio.sleep(POLL_INTERVAL_SEC)
        except redis.ConnectionError as exc:
            logger.error("Redis connection lost: %s — attempting reconnect...", exc)
            redis_client = connect_redis()
        except Exception:
            logger.exception("Unexpected error in HUGINN main loop")
            await asyncio.sleep(POLL_INTERVAL_SEC)

    # Cancel tasks on shutdown
    tg_task.cancel()
    web_task.cancel()
    gamma_task.cancel()
    logger.info("HUGINN shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main_loop())
