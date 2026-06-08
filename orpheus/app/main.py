"""
ORPHEUS — Cognitive Brain Service Entrypoint
==============================================
The core reasoning loop. Consumes from queue:raw_events, enriches media,
orchestrates memory retrieval, assembles prompts, queries the local LLM,
validates output via Guardrails, and publishes to queue:execution_tasks.

Strict Sequential VRAM Execution:
1. Load VLM -> Extract Video/Image -> Unload VLM
2. Load LLM -> Generate Text -> Unload LLM
"""

import json
import logging
import os
import signal
import sys
import time
import uuid
import httpx
from typing import Optional

import redis

from app.media_enricher import MediaEnricher
from app.persona import PersonaEngine, periodically_update_profiles_cache
from app.guardrails import OutputGuardrails
from app.coordination import generate_beta_subtasks
import threading
import asyncio

# ── Logging ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("orpheus")

# ── Configuration ─────────────────────────────────────────────────────────

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
TEXT_MODEL_NAME = os.getenv("TEXT_MODEL_NAME", "qwen2.5:3b")

RAW_EVENTS_QUEUE = "queue:raw_events"
EXECUTION_TASKS_QUEUE = "queue:execution_tasks"

PROCESSING_TIMEOUT_SEC = 5
MAX_REGENERATION_ATTEMPTS = 2

# ── Graceful shutdown ─────────────────────────────────────────────────────

_shutdown_requested = False

def _handle_signal(signum: int, frame) -> None:
    global _shutdown_requested
    logger.info("Received signal %d — initiating graceful shutdown...", signum)
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)

# ── Background Task Runner ────────────────────────────────────────────────

def run_async_background_tasks():
    """Runs the asyncio event loop in a separate thread for background tasks."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # We can add more background async tasks here if needed
    tasks = [
        loop.create_task(periodically_update_profiles_cache())
    ]
    
    try:
        loop.run_until_complete(asyncio.gather(*tasks))
    except Exception as e:
        logger.error("Background async loop crashed: %s", e)
    finally:
        loop.close()


# ── Redis connectivity ────────────────────────────────────────────────────

def connect_redis(max_retries: int = 10, retry_delay: float = 3.0) -> redis.Redis:
    for attempt in range(1, max_retries + 1):
        try:
            client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=30,  # BRPOP needs longer timeout
                retry_on_timeout=True,
            )
            if client.ping():
                logger.info("Redis connection established (host=%s, port=%d).", REDIS_HOST, REDIS_PORT)
                return client
        except redis.ConnectionError as exc:
            logger.warning("Redis connection attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(retry_delay)

    logger.critical("Failed to connect to Redis after %d attempts. Exiting.", max_retries)
    sys.exit(1)


# ── Text Generation (LLM) ─────────────────────────────────────────────────

def generate_text(prompt: str) -> str:
    """Sends the assembled prompt to Ollama's Text LLM with keep_alive=0."""
    logger.info("Querying Text LLM (%s)...", TEXT_MODEL_NAME)
    try:
        payload = {
            "model": TEXT_MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "keep_alive": 0,  # Unload immediately after inference
            "options": {
                "temperature": 0.7,
                "top_p": 0.9,
            }
        }

        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            response.raise_for_status()
            return response.json().get("response", "").strip()

    except Exception as e:
        logger.error("LLM text generation failed: %s", e)
        return ""


# ── Main Event Loop ───────────────────────────────────────────────────────

def main() -> None:
    logger.info("=" * 60)
    logger.info("ORPHEUS Cognitive Brain — Starting Up (Stage 10)")
    logger.info("=" * 60)

    # Start background tasks
    bg_thread = threading.Thread(target=run_async_background_tasks, daemon=True)
    bg_thread.start()
    logger.info("Background async caching thread started.")

    redis_client = connect_redis()

    # Initialize sub-components
    logger.info("Initializing cognitive sub-components...")
    media_enricher = MediaEnricher()
    persona_engine = PersonaEngine()
    guardrails = OutputGuardrails()

    # Verify queues
    raw_len = redis_client.llen(RAW_EVENTS_QUEUE)
    exec_len = redis_client.llen(EXECUTION_TASKS_QUEUE)
    logger.info("Queues accessible — raw_events: %d, execution_tasks: %d", raw_len, exec_len)

    logger.info("Entering event processing loop (BRPOP timeout=%ds).", PROCESSING_TIMEOUT_SEC)

    while not _shutdown_requested:
        try:
            result = redis_client.brpop(RAW_EVENTS_QUEUE, timeout=PROCESSING_TIMEOUT_SEC)
            if result is None:
                continue

            _, event_json = result
            
            try:
                event = json.loads(event_json)
            except json.JSONDecodeError as exc:
                logger.error("Malformed event JSON: %s", exc)
                continue

            event_id = event.get("event_id")
            logger.info("Processing event %s from %s...", event_id, event.get("source_platform"))

            # Step 1: Media Enrichment (Uses VLM + CPU STT)
            media_path = event.get("media_path")
            media_type = event.get("media_type")
            enriched_media_text = None

            if media_path and media_type and os.path.exists(media_path):
                enriched_media_text = media_enricher.process_media(media_path, media_type)
                # Cleanup original media file from data_lake after processing to prevent disk filling
                try:
                    os.unlink(media_path)
                    logger.debug("Cleaned up original media file: %s", media_path)
                except OSError as e:
                    logger.warning("Failed to clean up media file %s: %s", media_path, e)

            # Route to all applicable agents
            for agent_id, profile in persona_engine.get_all_profiles().items():
                
                # Check if agent monitors this platform
                if event.get("source_platform") not in profile.get("platforms", []):
                    continue

                logger.info("Orchestrating response for agent %s...", agent_id)

                # Step 2: Assemble Prompt (Uses MUNINN + Profile)
                prompt = persona_engine.assemble_prompt(agent_id, event, enriched_media_text)
                if not prompt:
                    continue

                # Step 3: Execute Inference & Guardrails (Uses Text LLM)
                final_text = ""
                for attempt in range(1, MAX_REGENERATION_ATTEMPTS + 1):
                    generated_text = generate_text(prompt)
                    
                    is_valid, reason = guardrails.validate_output(generated_text)
                    if is_valid:
                        final_text = generated_text
                        logger.info("Output validated successfully on attempt %d.", attempt)
                        break
                    else:
                        logger.warning("Guardrails failed on attempt %d: %s. Output: %s", attempt, reason, generated_text)
                        redis_client.incr("metrics:guardrail_failures")
                        # Add failure context to the prompt for the next attempt
                        prompt += f"\n\nSystem Note: Your previous response was rejected because: {reason}. Please try again, following all rules."

                if not final_text:
                    logger.error("Failed to generate valid output for event %s after %d attempts.", event_id, MAX_REGENERATION_ATTEMPTS)
                    continue

                redis_client.incr("metrics:guardrail_successes")

                # Step 4: Publish to Execution Swarm
                task = {
                    "task_id": str(uuid.uuid4()),
                    "agent_id": agent_id,
                    "target_platform": event.get("source_platform"),
                    "action_type": "comment",
                    "target_url": event.get("source_target"),  # Approximate target
                    "text_to_publish": final_text,
                    "parent_post_context": event.get("text_content", "")[:200], # Keep context short
                    "execution_delay_sec": profile.get("execution_delay", 45)
                }

                redis_client.lpush(EXECUTION_TASKS_QUEUE, json.dumps(task, ensure_ascii=False))
                redis_client.incr("metrics:comments_sent")
                logger.info("Successfully pushed execution task for agent %s on %s.", agent_id, event.get("source_platform"))
                
                # Step 5: Amplify with Beta sub-tasks
                generate_beta_subtasks(agent_id, task, redis_client, persona_engine)
                
            # Update status in Daedalus after processing all profiles
            try:
                with httpx.Client(timeout=5.0) as client:
                    client.put(
                        f"http://daedalus:8000/api/v1/huginn/internal/capture/{event_id}",
                        json={"status": "Processed"},
                        headers={"X-Internal-Token": "morpheus-internal-sync-key"}
                    )
            except Exception as exc:
                logger.warning("Failed to update event status in Daedalus for %s: %s", event_id, exc)

        except redis.ConnectionError as exc:
            logger.error("Redis connection lost: %s — reconnecting...", exc)
            redis_client = connect_redis()
        except Exception:
            logger.exception("Unexpected error in ORPHEUS event loop.")
            time.sleep(2)

    logger.info("ORPHEUS shutdown complete.")

if __name__ == "__main__":
    main()
