"""
ORPHEUS — Cognitive Brain Service Entrypoint
==============================================
Consumes raw events from Redis (queue:raw_events), processes them
through the media enrichment and text generation pipeline, and
publishes execution tasks to Redis (queue:execution_tasks) for MYRMIDON.

VRAM Scheduling Algorithm (from tech_spec.md):
  - Qwen 2.5 3B (text LLM) is held in VRAM permanently.
  - On image/video tasks: pause text queue → unload LLM → load Moondream2
    → generate description → unload VLM → reload LLM → resume.
  - faster-whisper (STT) runs exclusively on CPU threads.

This entrypoint establishes Redis connectivity, verifies Ollama
model availability, and runs the event processing loop.
"""

import json
import logging
import os
import signal
import sys
import time
import uuid
from typing import Any, Optional

import httpx
import redis

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
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "moondream:latest")
MUNINN_BASE_URL = os.getenv("MUNINN_BASE_URL", "http://muninn:8002")

RAW_EVENTS_QUEUE = "queue:raw_events"
EXECUTION_TASKS_QUEUE = "queue:execution_tasks"
PROCESSING_TIMEOUT_SEC = 5  # BRPOP timeout

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


# ── Ollama health check ──────────────────────────────────────────────────

def verify_ollama_connectivity() -> bool:
    """
    Verify that the Ollama API is reachable and the required models
    are available. Logs warnings if models are missing but does not
    block startup (models may be pulled later).
    """
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if response.status_code == 200:
                data = response.json()
                available_models = [m["name"] for m in data.get("models", [])]
                logger.info("Ollama reachable — available models: %s", available_models)

                if TEXT_MODEL_NAME not in available_models:
                    logger.warning(
                        "Text model '%s' not found in Ollama. "
                        "Pull it with: ollama pull %s",
                        TEXT_MODEL_NAME,
                        TEXT_MODEL_NAME,
                    )
                if VISION_MODEL_NAME not in available_models:
                    logger.warning(
                        "Vision model '%s' not found in Ollama. "
                        "Pull it with: ollama pull %s",
                        VISION_MODEL_NAME,
                        VISION_MODEL_NAME,
                    )
                return True
            else:
                logger.warning("Ollama returned status %d.", response.status_code)
                return False
    except httpx.ConnectError:
        logger.warning(
            "Ollama not reachable at %s — will retry on first task.",
            OLLAMA_BASE_URL,
        )
        return False
    except Exception as exc:
        logger.warning("Ollama health check failed: %s", exc)
        return False


# ── MUNINN memory interface ──────────────────────────────────────────────

def query_memory(agent_id: str, opponent_id: str, query_text: str) -> list[dict]:
    """
    Query MUNINN for past dialog context between agent and opponent.
    Returns a list of matching memory fragments.
    """
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{MUNINN_BASE_URL}/api/v1/memory/search",
                json={
                    "agent_id": agent_id,
                    "opponent_id": opponent_id,
                    "query_text": query_text,
                },
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("matches", [])
            else:
                logger.warning("MUNINN search returned status %d.", response.status_code)
                return []
    except Exception as exc:
        logger.warning("MUNINN query failed: %s", exc)
        return []


def save_memory(agent_id: str, opponent_id: str, dialog_summary: str) -> bool:
    """Save a dialog summary to MUNINN long-term memory."""
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(
                f"{MUNINN_BASE_URL}/api/v1/memory/save",
                json={
                    "agent_id": agent_id,
                    "opponent_id": opponent_id,
                    "dialog_summary": dialog_summary,
                },
            )
            return response.status_code == 200
    except Exception as exc:
        logger.warning("MUNINN save failed: %s", exc)
        return False


# ── Text generation via Ollama ────────────────────────────────────────────

def generate_text(prompt: str, model: str = TEXT_MODEL_NAME) -> Optional[str]:
    """
    Send a prompt to the Ollama LLM API and return the generated text.
    """
    try:
        with httpx.Client(timeout=120) as client:
            response = client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.8,
                        "top_p": 0.9,
                        "num_predict": 512,
                    },
                },
            )
            if response.status_code == 200:
                return response.json().get("response", "")
            else:
                logger.error("Ollama generate failed with status %d.", response.status_code)
                return None
    except Exception as exc:
        logger.error("Ollama text generation error: %s", exc)
        return None


# ── Event processing ─────────────────────────────────────────────────────

def process_raw_event(event_data: dict, redis_client: redis.Redis) -> None:
    """
    Process a single raw event from HUGINN:
      1. If media is present, run media enrichment (Stage 3 — skeleton ready).
      2. Query MUNINN for context.
      3. Assemble the persona prompt (PersonaEngine — Stage 3).
      4. Generate text via Ollama.
      5. Run Guardrails validation.
      6. Publish execution task to MYRMIDON queue.
    """
    event_id = event_data.get("event_id", "unknown")
    agent_id = event_data.get("agent_id", "001")
    source_platform = event_data.get("source_platform", "unknown")
    text_content = event_data.get("text_content", "")
    media_type = event_data.get("media_type")
    media_path = event_data.get("media_path")

    logger.info(
        "Processing event %s from %s (media_type=%s).",
        event_id,
        source_platform,
        media_type,
    )

    # Step 1: Media enrichment (Stage 3 implementation)
    media_context = ""
    if media_type and media_path:
        logger.info(
            "Event %s contains media (%s) — enrichment pipeline will be active in Stage 3.",
            event_id,
            media_type,
        )
        # Stage 3: media_enricher.enrich(media_path, media_type)

    # Step 2: Query MUNINN for opponent context
    opponent_id = event_data.get("source_target", "@unknown")
    memories = query_memory(agent_id, opponent_id, text_content)
    memory_context = ""
    if memories:
        memory_context = "\n".join(
            f"[Past interaction (relevance {m.get('distance', 0):.2f})]: {m.get('text', '')}"
            for m in memories[:3]
        )
        logger.info(
            "Found %d memory matches for agent %s ↔ %s.",
            len(memories),
            agent_id,
            opponent_id,
        )

    # Step 3: Assemble prompt (PersonaEngine skeleton)
    layers = event_data.get("layers", {})
    prompt = _assemble_prompt(
        agent_id=agent_id,
        text_content=text_content,
        media_context=media_context,
        memory_context=memory_context,
        layers=layers,
    )

    # Step 4: Generate response via Ollama
    generated_text = generate_text(prompt)
    if not generated_text:
        logger.warning("Event %s — text generation returned empty. Skipping.", event_id)
        return

    # Step 5: Guardrails validation (Stage 3 implementation)
    from app.guardrails import validate_text

    is_safe, cleaned_text = validate_text(generated_text)
    if not is_safe:
        logger.warning("Event %s — Guardrails rejected generated text. Skipping.", event_id)
        return

    # Step 6: Publish execution task for MYRMIDON
    execution_task = {
        "task_id": str(uuid.uuid4()),
        "agent_id": agent_id,
        "target_platform": source_platform,
        "action_type": "comment",
        "target_url": event_data.get("source_target", ""),
        "text_to_publish": cleaned_text,
        "parent_post_context": text_content[:200],
        "execution_delay_sec": 45,
    }
    redis_client.lpush(EXECUTION_TASKS_QUEUE, json.dumps(execution_task, ensure_ascii=False))
    logger.info(
        "Execution task published: task_id=%s, agent=%s, platform=%s.",
        execution_task["task_id"],
        agent_id,
        source_platform,
    )

    # Save this interaction to MUNINN
    save_memory(
        agent_id=agent_id,
        opponent_id=opponent_id,
        dialog_summary=f"Post: {text_content[:200]}... → Response: {cleaned_text[:200]}...",
    )


def _assemble_prompt(
    agent_id: str,
    text_content: str,
    media_context: str,
    memory_context: str,
    layers: dict,
) -> str:
    """
    Assemble the full LLM prompt from persona legend, news layers,
    memory context, and the current post content.

    Full PersonaEngine with YAML persona loading will be built in Stage 3.
    This provides a functional prompt structure.
    """
    layer_info = ""
    if layers:
        state = layers.get("state", "Unknown")
        city = layers.get("city", "Unknown")
        tags = ", ".join(layers.get("personal_tags", []))
        layer_info = f"Geographic context: {city}, {state}. Topics: {tags}."

    prompt_parts = [
        f"You are agent {agent_id}, a real person commenting on social media.",
        "Write a natural, human-like response to the following post.",
        "Your response must not contain any AI markers, hashtags, or promotional language.",
        "Keep it conversational and authentic.",
    ]

    if layer_info:
        prompt_parts.append(f"\n{layer_info}")

    if memory_context:
        prompt_parts.append(f"\nRelevant past interactions:\n{memory_context}")

    if media_context:
        prompt_parts.append(f"\nMedia description:\n{media_context}")

    prompt_parts.append(f"\nPost to respond to:\n{text_content}")
    prompt_parts.append("\nYour response (1-3 sentences, conversational tone):")

    return "\n".join(prompt_parts)


# ── Main loop ─────────────────────────────────────────────────────────────

def main() -> None:
    """
    ORPHEUS main entrypoint.

    1. Connect to Redis and verify queue accessibility.
    2. Verify Ollama model availability (non-blocking).
    3. Enter the event processing loop (BRPOP on queue:raw_events).
    """
    logger.info("=" * 60)
    logger.info("ORPHEUS Cognitive Brain — Starting Up")
    logger.info("=" * 60)

    redis_client = connect_redis()

    # Verify queues
    raw_depth = redis_client.llen(RAW_EVENTS_QUEUE)
    exec_depth = redis_client.llen(EXECUTION_TASKS_QUEUE)
    logger.info(
        "Queues accessible — raw_events: %d, execution_tasks: %d",
        raw_depth,
        exec_depth,
    )

    # Check Ollama (non-blocking)
    verify_ollama_connectivity()

    logger.info("Entering event processing loop (BRPOP timeout=%ds).", PROCESSING_TIMEOUT_SEC)

    while not _shutdown_requested:
        try:
            # Blocking pop from the raw events queue
            result = redis_client.brpop(RAW_EVENTS_QUEUE, timeout=PROCESSING_TIMEOUT_SEC)
            if result is None:
                continue

            _, event_json = result
            try:
                event_data = json.loads(event_json)
            except json.JSONDecodeError as exc:
                logger.error("Malformed event JSON: %s", exc)
                continue

            process_raw_event(event_data, redis_client)

        except redis.ConnectionError as exc:
            logger.error("Redis connection lost: %s — reconnecting...", exc)
            redis_client = connect_redis()
        except Exception:
            logger.exception("Unexpected error in ORPHEUS event loop.")
            time.sleep(2)

    logger.info("ORPHEUS shutdown complete.")


if __name__ == "__main__":
    main()
