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

Full scraper implementations (Telethon parsers, web scrapers) will be
built in Stage 2. This entrypoint establishes the Redis connection,
validates queue health, and runs the polling loop skeleton.
"""

import json
import logging
import os
import signal
import sys
import time
from typing import Optional

import redis

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
    "telegram_channel": 24 * 3600,  # 24 hours for Telegram channels (more generous)
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


# ── Layer classification ──────────────────────────────────────────────────

def classify_layers(text_content: str, source_metadata: Optional[dict] = None) -> dict:
    """
    Assign geographic and thematic layers to a piece of content.
    Layers: Global, Region (Central Asia), State, City, Personal.

    Full NLP-based classification will be implemented in Stage 2.
    This skeleton uses metadata hints when available.
    """
    layers = {
        "global": None,
        "region": None,
        "state": None,
        "city": None,
        "personal_tags": [],
    }

    if source_metadata:
        layers["state"] = source_metadata.get("state")
        layers["city"] = source_metadata.get("city")
        layers["personal_tags"] = source_metadata.get("personal_tags", [])

    return layers


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

def main() -> None:
    """
    HUGINN main entrypoint.

    1. Connect to Redis and verify queue accessibility.
    2. Enter the polling loop (scrapers will be added in Stage 2).
    3. On each cycle: run scrapers → filter by TTL → classify layers → publish to Redis.
    """
    logger.info("=" * 60)
    logger.info("HUGINN News Aggregator — Starting Up")
    logger.info("=" * 60)

    redis_client = connect_redis()

    # Verify queue is accessible
    queue_len = redis_client.llen(RAW_EVENTS_QUEUE)
    logger.info(
        "Queue '%s' accessible — current depth: %d events.",
        RAW_EVENTS_QUEUE,
        queue_len,
    )

    logger.info(
        "Entering main polling loop (interval=%ds). "
        "Scrapers will be activated in Stage 2.",
        POLL_INTERVAL_SEC,
    )

    while not _shutdown_requested:
        try:
            # ── Stage 2 integration point ──────────────────────────
            # Here the Telethon and BS4 scrapers will:
            #   1. Fetch new posts from monitored channels/pages
            #   2. Download media to /app/data_lake/raw_media/
            #   3. Apply TTL filter via is_content_expired()
            #   4. Classify layers via classify_layers()
            #   5. Publish via publish_raw_event()
            # ───────────────────────────────────────────────────────

            # Periodic TTL cleanup of stale cached posts
            cached_count = redis_client.llen("cache:post_ids")
            if cached_count > 0:
                logger.debug("Post ID cache size: %d", cached_count)

            time.sleep(POLL_INTERVAL_SEC)

        except redis.ConnectionError as exc:
            logger.error("Redis connection lost: %s — attempting reconnect...", exc)
            redis_client = connect_redis()
        except Exception:
            logger.exception("Unexpected error in HUGINN main loop")
            time.sleep(POLL_INTERVAL_SEC)

    logger.info("HUGINN shutdown complete.")


if __name__ == "__main__":
    main()
