"""
HUGINN — Gamma-Caste White Noise Generator
============================================
Injects neutral, innocuous background activity for Gamma-caste agents
to establish a realistic operational footprint.
"""

import asyncio
import json
import logging
import random
import time
import uuid

logger = logging.getLogger("huginn.gamma_noise")

# A small static database of neutral templates
# Real implementation might fetch this from a DB or file.
NOISE_TEMPLATES = [
    # Generic Tech / Daily Life
    "I've been thinking about how much technology has changed just in the last 5 years. It's crazy.",
    "Is it just me or is time moving way faster these days?",
    "Need to organize my digital life this weekend. So many unread emails.",
    "Just read an interesting article about AI. The future is going to be wild.",
    "Anyone have good podcast recommendations? Commute is getting boring.",
    "Coffee is literally the only thing keeping me going today.",
    
    # Weather / Location independent
    "Can't believe this weather we've been having lately.",
    "Finally some sun! Going to try and spend some time outside today.",
    "I swear it was just raining five minutes ago.",
    "Perfect weather for staying inside and watching a movie.",
    
    # Memes / Internet Culture (Neutral)
    "Just saw the funniest meme. The internet remains undefeated.",
    "Scrolling through my feed and realizing I need a social media break.",
    "Why does the algorithm know exactly what I want to buy?",
    "That feeling when you finally close 50 browser tabs.",
    "I am officially too old to understand these new slang terms."
]

async def run_gamma_noise_scheduler(redis_client, publish_raw_event_func):
    """
    Async loop that periodically injects neutral events.
    Checks time windows to ensure activity looks organic (e.g., 08:00 - 22:00).
    """
    logger.info("Gamma Noise Scheduler started.")
    
    while True:
        try:
            # Random wait between 2 to 4 hours to appear organic
            wait_time_sec = random.randint(7200, 14400)
            logger.debug("Gamma noise scheduler sleeping for %d seconds...", wait_time_sec)
            await asyncio.sleep(wait_time_sec)
            
            # Check if current local time is within "waking hours" (8 AM to 10 PM)
            current_hour = time.localtime().tm_hour
            if current_hour < 8 or current_hour >= 22:
                logger.debug("Outside waking hours (%d:00). Skipping gamma noise.", current_hour)
                continue
                
            # Synthesize a neutral event
            event_id = f"gamma-noise-{uuid.uuid4().hex[:8]}"
            text = random.choice(NOISE_TEMPLATES)
            
            # Usually we'd want to find a gamma agent from DB. 
            # For this injection, we route a generic event that ORPHEUS can process.
            # ORPHEUS will match it to available agents based on platform.
            
            # Randomize target platform for variety
            target_platform = random.choice(["twitter", "threads", "telegram"])
            
            # Inject to queue
            publish_raw_event_func(
                redis_client=redis_client,
                event_id=event_id,
                source_platform=target_platform,
                # Stage 44 — "Self" was written for the mobile path (a bot posting to
                # its own feed), which is broken and out of scope. On Telegram it is
                # unresolvable, so every one of these became an execution task that
                # slept its full human-pacing delay in the single consumer loop and
                # then failed — blocking real mission comments behind it.
                source_target="Self",
                post_id=event_id,
                text_content=text,
                media_type=None,
                media_path=None,
                layers={"personal_tags": ["Personal: WhiteNoise", "RandomThought"]},
                timestamp=int(time.time())
            )
            logger.info("Injected Gamma white-noise event %s for platform %s", event_id, target_platform)
            
        except asyncio.CancelledError:
            logger.info("Gamma Noise Scheduler cancelled.")
            break
        except Exception as e:
            logger.error("Error in Gamma Noise Scheduler: %s", e)
            await asyncio.sleep(60) # Wait a bit before retrying on error
