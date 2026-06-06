"""
HUGINN — Telegram Scraper
===========================
Uses Telethon to poll Telegram channels.
"""

import asyncio
import logging
import os
import time
import uuid

import redis
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from app.router import classify_layers

logger = logging.getLogger("huginn.scrapers.tg_scraper")

# Get these from environment variables or provide dummy values for development
API_ID = int(os.getenv("TG_API_ID", "12345"))
API_HASH = os.getenv("TG_API_HASH", "dummy_hash")

# Monitored channels
TARGET_CHANNELS = ["@tashkent_news", "@uzbekistan_live"]

async def run_tg_scraper(redis_client: redis.Redis, raw_events_queue: str, is_content_expired_func, publish_func):
    """
    Main async loop for Telegram scraping.
    """
    # For development, we might not have a real session. We just mock the client if API_HASH is dummy.
    if API_HASH == "dummy_hash":
        logger.warning("TG_API_HASH not set. Running TG Scraper in Mock Mode.")
        while True:
            await asyncio.sleep(60)
            mock_text = "Новая дорожная развязка открыта на Юнусабаде. #tashkent #yunusabad"
            timestamp = int(time.time())
            
            if not is_content_expired_func("telegram_channel", timestamp):
                layers = classify_layers(mock_text)
                publish_func(
                    redis_client=redis_client,
                    event_id=str(uuid.uuid4()),
                    source_platform="telegram",
                    source_target="@tashkent_news",
                    post_id=str(int(time.time())),
                    text_content=mock_text,
                    media_type=None,
                    media_path=None,
                    layers=layers,
                    timestamp=timestamp
                )
    else:
        # Real Telethon client
        session_path = os.path.join(os.getenv("GLOBAL_CONFIG_DIR", "/app/global_config"), "huginn_tg.session")
        client = TelegramClient(session_path, API_ID, API_HASH)
        
        await client.start()
        logger.info("Telegram client started.")
        
        while True:
            try:
                for channel in TARGET_CHANNELS:
                    async for message in client.iter_messages(channel, limit=5):
                        post_id = str(message.id)
                        
                        # Simple deduplication using Redis cache
                        if redis_client.get(f"cache:tg:{channel}:{post_id}"):
                            continue
                            
                        redis_client.setex(f"cache:tg:{channel}:{post_id}", 86400, "1") # cache for 1 day
                        
                        timestamp = int(message.date.timestamp())
                        
                        if is_content_expired_func("telegram_channel", timestamp):
                            continue
                            
                        text_content = message.message or ""
                        if not text_content and not message.media:
                            continue
                            
                        media_path = None
                        media_type = None
                        
                        if message.media:
                            file_path = await message.download_media(file="/app/data_lake/raw_media/")
                            if file_path:
                                media_path = file_path
                                if "video" in file_path or "mp4" in file_path:
                                    media_type = "video"
                                else:
                                    media_type = "image"
                        
                        layers = classify_layers(text_content)
                        
                        publish_func(
                            redis_client=redis_client,
                            event_id=str(uuid.uuid4()),
                            source_platform="telegram",
                            source_target=channel,
                            post_id=post_id,
                            text_content=text_content,
                            media_type=media_type,
                            media_path=media_path,
                            layers=layers,
                            timestamp=timestamp
                        )
                
            except Exception as e:
                logger.error("Error in TG Scraper loop: %s", e)
                
            await asyncio.sleep(120) # Poll every 2 minutes
