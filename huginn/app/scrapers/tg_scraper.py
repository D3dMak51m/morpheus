"""
HUGINN — Telegram Scraper
===========================
Uses Telethon to poll Telegram channels with native FloodWait handling.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Any

import redis
from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError

from app.knowledge_ingest import ingest_knowledge

logger = logging.getLogger("huginn.scrapers.tg_scraper")

# Get these from environment variables or provide dummy values for development
API_ID = int(os.getenv("TG_API_ID", "12345"))
API_HASH = os.getenv("TG_API_HASH", "dummy_hash")


async def run_tg_scraper(redis_client: redis.Redis, raw_events_queue: str, is_content_expired_func, publish_func, active_targets: Dict[str, List[Any]]):
    """
    Main async loop for Telegram scraping using dynamic targets.
    Strictly enforces real Telethon network sessions. Sandbox/Mock modes are purged.
    """
    if API_HASH == "dummy_hash" or API_ID == 12345:
        logger.error("CRITICAL: TG_API_ID and TG_API_HASH must be provided for production Telethon client. Will attempt to start anyway but it will likely fail.")
        
    # Real Telethon client
    session_path = os.path.join(os.getenv("GLOBAL_CONFIG_DIR", "/app/global_config"), "huginn_tg.session")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(session_path), exist_ok=True)
    
    client = TelegramClient(session_path, API_ID, API_HASH)
    
    try:
        await client.start()
        logger.info("Telegram client started successfully.")
    except Exception as e:
        logger.error("Failed to start Telegram client. Cannot proceed with Telegram scraping: %s", e)
        return
        
    while True:
        try:
            current_targets = active_targets.get("telegram", [])
            for channel_item in current_targets:
                if isinstance(channel_item, dict):
                    channel_name = channel_item.get("target_identifier")
                    channel_layers = channel_item.get("default_layers", ["global"])
                else:
                    channel_name = channel_item
                    channel_layers = ["global"]
                if not channel_name:
                    continue
                    
                try:
                    async for message in client.iter_messages(channel_name, limit=5):
                        post_id = str(message.id)
                        
                        # Simple deduplication using Redis cache
                        if redis_client.get(f"cache:tg:{channel_name}:{post_id}"):
                            continue
                            
                        redis_client.setex(f"cache:tg:{channel_name}:{post_id}", 86400, "1") # cache for 1 day
                        
                        timestamp = int(message.date.timestamp())

                        if is_content_expired_func("telegram_channel", timestamp):
                            continue

                        text_content = message.message or ""
                        # Stage 22 — generic Telegram is a KNOWLEDGE source only.
                        # Media-less text is all MUNINN needs; we no longer download
                        # media or publish to the execution queue (pipeline leakage).
                        if not text_content.strip():
                            continue

                        # STRICT ENFORCEMENT: route ONLY to MUNINN's knowledge base.
                        ingest_knowledge(
                            text=text_content,
                            source_url=f"https://t.me/{str(channel_name).lstrip('@')}/{post_id}",
                            default_layers=channel_layers,
                        )
                        
                except FloodWaitError as e:
                    logger.warning("Telegram FloodWaitError: waiting %d seconds.", e.seconds)
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    logger.error("Failed to fetch messages for %s: %s", channel_name, e)
            
        except Exception as e:
            logger.error("Error in TG Scraper loop: %s", e)
            
        await asyncio.sleep(120) # Poll every 2 minutes
