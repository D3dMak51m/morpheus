"""
HUGINN — News Aggregator Service Entrypoint
=============================================
Background worker that connects to the Redis broker, verifies queue
connectivity, and runs the main event loop for content aggregation.

Data flow:
  1. Scrape Telegram channels (Telethon), websites (BS4/curl_cffi)
  2. Classify content by layers: Global → Region → State → City → Personal
  3. Apply TTL filtering
  4. Serialize events to Redis queue 'queue:raw_events' for ORPHEUS
"""

import asyncio
import json
import logging
import os
import signal
import sys
import time
from typing import Optional, Dict, List

import httpx
import redis

from app.router import classify_layers
from app.scrapers.tg_scraper import run_tg_scraper
from app.scrapers.web_scraper import run_web_scraper
from app.scrapers.social_feed_scraper import run_social_feed_scraper
from app.scrapers.gamma_noise import run_gamma_noise_scheduler
from app.scrapers.scouting_engine import run_scouting_engine
from test_rss import run_rss_scraper

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

DAEDALUS_URL = "http://daedalus:8000"
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")

# TTL thresholds (seconds) for discarding stale content
TTL_THRESHOLDS = {
    "twitter": 12 * 3600,     # 12 hours for X/Twitter
    "threads": 12 * 3600,     # 12 hours for Threads
    "telegram_group": 2 * 3600,  # 2 hours for Telegram groups
    "telegram_channel": 24 * 3600,  # 24 hours for Telegram channels
    "instagram": 24 * 3600,
    "facebook": 24 * 3600,
    "youtube": 48 * 3600,
    "web": 12 * 3600,
}

# Global State for Dynamic Targets
ACTIVE_TARGETS: Dict[str, List[Dict[str, str]]] = {
    "telegram": [],
    "instagram": [],
    "twitter": [],
    "threads": [],
    "web": [],
    "rss": [],
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


# ── Landscape Target Synchronization ──────────────────────────────────────

async def sync_landscape_loop() -> None:
    """
    Background worker that dynamically polls DAEDALUS for active scraping targets.
    Mutates the global ACTIVE_TARGETS dict for scrapers to consume instantly.
    """
    logger.info("Starting landscape target synchronization loop...")
    headers = {"X-Internal-Token": INTERNAL_API_TOKEN}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while not _shutdown_requested:
            try:
                resp = await client.get(f"{DAEDALUS_URL}/api/v1/landscape/internal/sync-targets", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                
                targets_by_platform = data.get("targets", {})
                
                # Clear all first to handle deletions properly
                for pf in ACTIVE_TARGETS.keys():
                    ACTIVE_TARGETS[pf] = []
                    
                # Update global state atomically
                for platform, targets in targets_by_platform.items():
                    pf = platform.lower()
                    if pf in ACTIVE_TARGETS:
                        ACTIVE_TARGETS[pf] = targets
                    else:
                        ACTIVE_TARGETS[pf] = targets
                        
                logger.debug("Landscape targets synchronized: %s", {k: len(v) for k, v in ACTIVE_TARGETS.items()})
                
            except httpx.HTTPError as exc:
                logger.error("Failed to sync targets from DAEDALUS: %s. Retaining last known memory state.", exc)
            except Exception as exc:
                logger.exception("Unexpected error during target synchronization. Retaining last known state.")
                
            await asyncio.sleep(60)


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
    
    # Push to ORPHEUS via Redis
    redis_client.lpush(RAW_EVENTS_QUEUE, json.dumps(event, ensure_ascii=False))
    
    # Additionally push to DAEDALUS for administrative control (CapturedEvents view)
    try:
        import httpx
        httpx.post(
            "http://daedalus:8000/api/v1/huginn/internal/capture",
            json=event,
            headers={"X-Internal-Token": "morpheus-internal-sync-key"},
            timeout=5.0
        )
    except Exception as e:
        logger.error("Failed to push event to Daedalus: %s", e)
        
    logger.info(
        "Event published to %s: id=%s, platform=%s, target=%s",
        RAW_EVENTS_QUEUE,
        event_id,
        source_platform,
        source_target,
    )


# ── Worker Pools ──────────────────────────────────────────────────────────

async def individual_target_worker_pool(redis_client: redis.Redis) -> None:
    """
    Spawns and manages dynamic tasks for explicitly targeted channels and websites.
    """
    logger.info("Starting individual_target_worker_pool...")

    tg_task = asyncio.create_task(run_tg_scraper(redis_client, RAW_EVENTS_QUEUE, is_content_expired, publish_raw_event, ACTIVE_TARGETS))
    web_task = asyncio.create_task(run_web_scraper(redis_client, RAW_EVENTS_QUEUE, is_content_expired, publish_raw_event, ACTIVE_TARGETS))
    # Stage 22 — RSS scraper routes exclusively to MUNINN's knowledge base.
    rss_task = asyncio.create_task(run_rss_scraper(redis_client, ACTIVE_TARGETS))

    while not _shutdown_requested:
        await asyncio.sleep(5)

    tg_task.cancel()
    web_task.cancel()
    rss_task.cancel()

async def timeline_feed_worker_pool(redis_client: redis.Redis) -> None:
    """
    Scans global home timelines/feeds ("лента") for authenticated proxy sessions.
    """
    logger.info("Starting timeline_feed_worker_pool...")
    
    feed_task = asyncio.create_task(run_social_feed_scraper(redis_client, RAW_EVENTS_QUEUE, is_content_expired, publish_raw_event, ACTIVE_TARGETS))
    
    while not _shutdown_requested:
        await asyncio.sleep(5)
        
    feed_task.cancel()


# ── Main loop ─────────────────────────────────────────────────────────────

async def main_loop() -> None:
    """
    HUGINN main entrypoint (Async).
    """
    logger.info("=" * 60)
    logger.info("HUGINN News Aggregator — Starting Up (Stage 11)")
    logger.info("=" * 60)

    redis_client = connect_redis()

    # Verify queue is accessible
    queue_len = redis_client.llen(RAW_EVENTS_QUEUE)
    logger.info(
        "Queue '%s' accessible — current depth: %d events.",
        RAW_EVENTS_QUEUE,
        queue_len,
    )

    logger.info("Starting scraper and synchronization tasks...")
    
    # Spawn the three concurrent non-blocking loops requested
    sync_task = asyncio.create_task(sync_landscape_loop())
    individual_targets_task = asyncio.create_task(individual_target_worker_pool(redis_client))
    timeline_feed_task = asyncio.create_task(timeline_feed_worker_pool(redis_client))
    
    # Also keep gamma noise running
    gamma_task = asyncio.create_task(run_gamma_noise_scheduler(redis_client, publish_raw_event))

    # Stage 18: authenticated scouting engine (velocity-based viral discovery)
    scouting_task = asyncio.create_task(run_scouting_engine())
    
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
    sync_task.cancel()
    individual_targets_task.cancel()
    timeline_feed_task.cancel()
    gamma_task.cancel()
    scouting_task.cancel()
    logger.info("HUGINN shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main_loop())
