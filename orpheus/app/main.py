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
import re
import signal
import sys
import time
import uuid
import httpx
from typing import Optional

import redis

from app.media_enricher import MediaEnricher
from app.persona import (
    PersonaEngine,
    periodically_update_profiles_cache,
    build_mood_prompt,
    tactic_from_mood,
    TACTIC_LABELS_RU,
    DYNAMIC_TACTIC,
)
from app.guardrails import OutputGuardrails
from app.coordination import generate_beta_subtasks
from app.simulation import handle_simulation_generation
from app.telemetry import emit as emit_event
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
# Stage 25 — targeted mission comment generation (request/reply over Redis).
# MYRMIDON pushes a request (with the live post context) here; ORPHEUS generates
# a persona/RAG/memory/mission-aware comment and LPUSHes it to req["reply_key"].
MISSION_GEN_QUEUE = "queue:mission_gen"
# SIMULATION — the isolated test polygon (DAEDALUS ``/api/v1/simulation``). Its own
# queue on purpose: a polygon run must never enter the live mission path, and its
# handler persists nothing (no MUNINN memory, no recent-output history, no metrics).
SIM_GEN_QUEUE = "queue:sim_gen"

PROCESSING_TIMEOUT_SEC = 5
MAX_REGENERATION_ATTEMPTS = 2
# Mission/reply path retries more: a weak LLM needs several tries to stop echoing.
MISSION_REGEN_ATTEMPTS = int(os.getenv("MISSION_REGEN_ATTEMPTS", "4"))

# Anti-repeat: a short per-agent history of the comments it actually posted, so the
# next generation can be told NOT to reuse the same openings / phrasing / talking
# points (the main thing that makes the bot read as a canned robot).
RECENT_OUTPUTS_KEY = "morpheus:recent_outputs:"
RECENT_OUTPUTS_MAX = int(os.getenv("RECENT_OUTPUTS_MAX", "8"))
RECENT_OUTPUTS_TTL = int(os.getenv("RECENT_OUTPUTS_TTL_SEC", str(7 * 24 * 3600)))

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

def generate_text(prompt: str, max_tokens: Optional[int] = None,
                  temperature: Optional[float] = None, penalties: bool = True) -> str:
    """Sends the assembled prompt to Ollama's Text LLM with keep_alive=0.
    ``max_tokens`` caps output length (used by the cheap beta 'lite' path);
    ``temperature`` overrides the default. Set ``penalties=False`` for short
    CLASSIFICATION calls (YES/NO relevance, tactic word): the anti-parroting
    repeat/frequency penalties otherwise push the model OFF the clean answer tokens
    ('да'/'нет') and produce garbled output like 'дятьнет'."""
    logger.info("Querying Text LLM (%s)...", TEXT_MODEL_NAME)
    try:
        options = {
            "temperature": 0.8 if temperature is None else temperature,
            "top_p": 0.9,
        }
        if penalties:
            # Discourage the model from parroting the prompt / its own tokens.
            options["repeat_penalty"] = 1.3
            options["frequency_penalty"] = 0.5
        if max_tokens:
            options["num_predict"] = max_tokens
        payload = {
            "model": TEXT_MODEL_NAME,
            "prompt": prompt,
            "stream": False,
            "keep_alive": 0,  # Unload immediately after inference
            "options": options,
        }

        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            response.raise_for_status()
            return response.json().get("response", "").strip()

    except Exception as e:
        logger.error("LLM text generation failed: %s", e)
        return ""


# ── Mission comment generation (request/reply) ─────────────────────────────

def _persist_dialog_memory(persona_engine, req: dict, final_text: str) -> None:
    """
    Close the memory loop: store a compact summary of *this* exchange in MUNINN,
    scoped to the person/channel, so the agent remembers it next time. Without
    this the long-term memory never fills and recall is always empty.
    """
    agent_id = req.get("agent_id")
    author = req.get("author") or "the channel"
    opponent_key = req.get("opponent_id") or author
    mode = req.get("mode") or "comment"
    post_text = (req.get("post_text") or "").strip()
    incoming_text = (req.get("incoming_text") or "").strip()

    if mode == "reply":
        summary = f'{author} said: "{incoming_text[:240]}". I replied: "{final_text[:240]}".'
    else:
        ctx = post_text[:240] or "(a post)"
        summary = f'Under {author}\'s post: "{ctx}". I commented: "{final_text[:240]}".'
    try:
        persona_engine.save_memory(agent_id, opponent_key, summary)
    except Exception as exc:
        logger.error("Failed to persist dialog memory: %s", exc)


def _recent_outputs(redis_client, agent_id: str) -> list:
    """The agent's last few posted comments (newest first) — for anti-repeat."""
    try:
        return redis_client.lrange(RECENT_OUTPUTS_KEY + agent_id, 0, RECENT_OUTPUTS_MAX - 1) or []
    except Exception:
        return []


def _remember_output(redis_client, agent_id: str, text: str) -> None:
    """Record a comment the agent just produced so it won't rehash it next time."""
    if not (text or "").strip():
        return
    try:
        k = RECENT_OUTPUTS_KEY + agent_id
        redis_client.lpush(k, text.strip())
        redis_client.ltrim(k, 0, RECENT_OUTPUTS_MAX - 1)
        redis_client.expire(k, RECENT_OUTPUTS_TTL)
    except Exception:
        pass


def _channel_context(cp: Optional[dict]) -> str:
    """Render a channel profile into a short Russian context line for the relevance
    (and, later, comment) prompt — so a post is judged IN the channel's context."""
    if not cp:
        return ""
    parts = []
    title = (cp.get("title") or "").strip()
    geo = (cp.get("geo_label") or "").strip()
    topics = [t for t in (cp.get("topics") or []) if t][:8]
    themes = [t.get("theme") for t in (cp.get("recent_themes") or []) if t.get("theme")][:6]
    head = f"Канал «{title}»" if title else "Канал"
    if geo:
        head += f" ({geo})"
    parts.append(head + ".")
    if topics:
        parts.append("Тематика канала: " + ", ".join(topics) + ".")
    if themes:
        parts.append("Сейчас в канале активно обсуждают: " + ", ".join(themes) + ".")
    summary = (cp.get("summary") or "").strip()
    if summary:
        parts.append(summary)
    return " ".join(parts)


# Generic mission vocabulary that carries no topical signal — excluded from the
# relevance entity anchors / keyword override so it can't trigger false matches
# (e.g. "против", "развитие" appearing in an unrelated post).
_MISSION_STOPWORDS = {
    "поддерживать", "поддержка", "поддержки", "против", "продвигать", "продвижение",
    "развитие", "развития", "развитой", "удобный", "удобного", "системно", "системный",
    "решаются", "решать", "проблема", "проблемы", "нужно", "важно", "нельзя", "также",
    "всегда", "будет", "более", "менее", "очень", "может", "чтобы", "потому", "вместе",
    "целью", "цель", "миссия", "миссии", "наша", "наши", "сторонник", "позиция",
}


def _mission_entities(goal: str, stance: str, limit: int = 12) -> list[str]:
    """Topical anchor words for a mission (its subject + adversaries), drawn from the
    goal and stance. Used both to GROUND the relevance prompt (so the weak LLM judges
    against concrete terms, not an abstract goal) and as a recall-override keyword set
    (a post mentioning a mission entity is relevant even if the LLM hedges to НЕТ).
    Generic verbs/prepositions are stripped; the channel's own geo is NOT included
    here (that would match purely local off-topic posts like weather)."""
    seen: list[str] = []
    for tok in re.findall(r"[а-яёa-z]{5,}", f"{goal} {stance}".lower()):
        if tok in _MISSION_STOPWORDS or tok in seen:
            continue
        seen.append(tok)
        if len(seen) >= limit:
            break
    return seen


def _entity_hit(post_text: str, entities: list[str]) -> bool:
    """Recall-override: does the post mention any mission entity (declension-tolerant
    via a 5-char stem)? Deliberately lenient — the channel is an operator-chosen
    mission target, so missing an on-topic post is worse than a loose match (rate
    caps prevent spam)."""
    t = (post_text or "").lower()
    for ent in entities:
        stem = ent[:5]
        if len(stem) >= 5 and stem in t:
            return True
    return False


def handle_relevance(req: dict, redis_client, persona_engine) -> None:
    """
    Cheap LLM relevance gate for the target engine: given the agent's mission +
    interests, decide whether a candidate post is worth commenting on. A tiny
    YES/НЕТ generation (num_predict≈5) — much cheaper than a full comment. Returns
    {status, relevant} on the request's reply_key.
    """
    reply_key = req.get("reply_key")
    agent_id = req.get("agent_id")
    post_text = (req.get("post_text") or "").strip()
    result = {"status": "ok", "relevant": False}
    try:
        profile = persona_engine.get_all_profiles().get(agent_id) or {}
        # Mission-driven relevance (preferred): judge against the mission's goal/stance.
        m_goal = (req.get("goal") or "").strip()
        m_stance = (req.get("stance") or "").strip()
        entities = _mission_entities(m_goal, m_stance) if (m_goal or m_stance) else []
        if m_goal or m_stance:
            cp_ctx = _channel_context(req.get("channel_profile"))
            # Ground the question in CONCRETE mission terms (subject + adversaries),
            # not the abstract goal/stance — a weak 3B model reliably says НЕТ to an
            # ideological "позиция" salad but answers a plain "связано ли с темой?".
            ent_hint = (" или упоминает: " + ", ".join(entities)) if entities else ""
            prompt = (
                (cp_ctx + "\n\n" if cp_ctx else "")
                + f"Тема миссии: {m_goal[:300]}\n\n"
                f"Сообщение в этом канале: \"{post_text[:400]}\"\n\n"
                "Связано ли это сообщение с темой миссии — с её предметом, сторонниками "
                "или противниками, причинами или последствиями" + ent_hint + " — хотя бы "
                "косвенно, как новость, мнение, жалоба или эмоция? Ответь одним словом: ДА или НЕТ."
            )
        else:
            mission = profile.get("core_mission") or ""
            interests = profile.get("interests") or []
            interests_str = ", ".join(i for i in interests if isinstance(i, str))[:200]
            occupation = (profile.get("identity", {}) or {}).get("occupation") or ""
            prompt = (
                f"Темы и интересы человека: {interests_str}.\n"
                f"Профессия: {occupation}.\n"
                f"Цель: {mission[:200]}\n\n"
                f"Пост: \"{post_text[:400]}\"\n\n"
                "Может ли этот человек ЕСТЕСТВЕННО вступить в обсуждение этого поста, "
                "исходя из своих интересов или профессии? Даже косвенная связь считается «да». "
                "Ответь СТРОГО одним словом: ДА или НЕТ."
            )
        answer = generate_text(prompt, max_tokens=5, temperature=0.2, penalties=False).strip().lower()
        llm_yes = ("да" in answer) or ("yes" in answer) or answer.startswith("1")
        # Recall-override: a post mentioning a mission entity is relevant even if the
        # weak model hedged to НЕТ (operator-chosen target → bias toward engaging).
        kw_yes = bool(entities) and _entity_hit(post_text, entities)
        result["relevant"] = llm_yes or kw_yes
        logger.info("Relevance %s agent=%s → %s (llm=%r kw=%s)", req.get("request_id"), agent_id,
                    result["relevant"], answer[:20], kw_yes)
    except Exception as exc:
        logger.warning("Relevance check failed: %s", exc)
        result = {"status": "error", "relevant": False}
    if reply_key:
        try:
            redis_client.lpush(reply_key, json.dumps(result, ensure_ascii=False))
            redis_client.expire(reply_key, 120)
        except Exception:
            pass


def _resolve_dynamic_tactic(req: dict) -> Optional[str]:
    """
    When a mission leaves its tactic as 'dynamic', pick a per-post tactic from the
    mood of the post + existing thread comments judged against the mission's stance.
    Cognitive (alpha) seed path only — a beta runs the cheap 'lite' branch and
    inherits the alpha's tactic via the seed task, and replies keep their default.
    Returns the chosen tactic, or None when no selection was made (keep as-is).
    """
    tactic = (req.get("tactic") or "").strip().lower()
    if tactic not in ("", DYNAMIC_TACTIC):
        return None  # operator pinned an explicit tactic — respect it
    if req.get("lite") or req.get("mode") == "reply":
        return None
    post_text = (req.get("post_text") or "").strip()
    thread = (req.get("thread_context") or "").strip()
    if not post_text and not thread:
        return "soft_support"  # no signal to read → gentle default, skip the LLM call
    raw = generate_text(
        build_mood_prompt((req.get("stance") or "").strip(), thread, post_text),
        max_tokens=6, temperature=0.2, penalties=False,
    )
    return tactic_from_mood(raw, post_text, thread)


def handle_mission_generation(req: dict, redis_client, persona_engine, guardrails) -> None:
    """
    Generate a real, context-aware mission comment and return it to MYRMIDON via
    the request's reply_key. Reuses persona + RAG + memory; framed by the mission
    role/tactic/objective and the actual post text MYRMIDON read from the target.
    """
    reply_key = req.get("reply_key")
    agent_id = req.get("agent_id")
    request_id = req.get("request_id")
    logger.info(
        "Mission-gen %s — agent=%s role=%s tactic=%s.",
        request_id, agent_id, req.get("role"), req.get("tactic"),
    )

    is_reply = (req.get("mode") == "reply")

    # A paused agent stays silent even for operator-driven missions/replies.
    prof = persona_engine.get_all_profiles().get(agent_id) or {}
    if prof.get("status") == "suspended":
        logger.info("Mission-gen %s — agent %s is suspended; skipping.", request_id, agent_id)
        if reply_key:
            try:
                redis_client.lpush(reply_key, json.dumps(
                    {"status": "error", "text": "", "reason": "agent_suspended"}, ensure_ascii=False))
                redis_client.expire(reply_key, 300)
            except Exception:
                pass
        return

    emit_event(
        agent_id, "thinking",
        ("сочиняет ответ человеку " + (req.get("author") or "")) if is_reply else "сочиняет комментарий",
        status="active", target=req.get("target_url") or req.get("author") or "",
    )

    result = {"status": "error", "text": "", "reason": ""}
    try:
        # Dynamic per-post tactic: let the model pick the tactic from the thread
        # mood vs the mission stance before we assemble the comment prompt.
        chosen_tactic = _resolve_dynamic_tactic(req)
        if chosen_tactic:
            req["tactic"] = chosen_tactic
            logger.info("Mission-gen %s — dynamic tactic → %s.", request_id, chosen_tactic)
            emit_event(agent_id, "tactic",
                       "тактика по настроению ветки: " + TACTIC_LABELS_RU.get(chosen_tactic, chosen_tactic),
                       status="info", target=req.get("target_url") or req.get("author") or "")

        # Anti-repeat: load the agent's own recent comments, feed them into the
        # prompt, and (alpha path) reject drafts that just rehash them.
        lite = bool(req.get("lite"))
        recent_self = [] if lite else _recent_outputs(redis_client, agent_id)
        req["recent_self"] = recent_self

        prompt = persona_engine.assemble_mission_prompt(agent_id, req)
        if not prompt:
            result["reason"] = "profile_not_found"
        else:
            # Echo references: reject replies that just parrot the human/post back.
            echo_refs = [req.get("incoming_text") or "", req.get("post_text") or ""]
            # Beta 'lite' = cheaper: shorter output, fewer retries.
            attempts_cap = 2 if lite else MISSION_REGEN_ATTEMPTS
            gen_max_tokens = 90 if lite else None
            final_text = ""
            for attempt in range(1, attempts_cap + 1):
                generated = generate_text(prompt, max_tokens=gen_max_tokens)
                ok, reason = guardrails.validate_output(generated)
                if ok and guardrails.is_echo(generated, echo_refs):
                    ok, reason = False, "reply echoes/repeats the incoming message"
                if ok and recent_self and guardrails.is_repeat(generated, recent_self):
                    ok, reason = False, "repeats the agent's own recent comments"
                if ok:
                    final_text = generated
                    logger.info("Mission-gen %s — validated on attempt %d (%d chars).", request_id, attempt, len(final_text))
                    break
                logger.warning("Mission-gen %s — rejected (attempt %d): %s", request_id, attempt, reason)
                emit_event(agent_id, "guardrail_reject",
                           f"отклонил черновик ({reason[:40]}) — переписывает",
                           status="warn")
                redis_client.incr("metrics:guardrail_failures")
                prompt += (
                    f"\n\nSystem Note: your previous reply was rejected because: {reason}. "
                    "Do NOT repeat or quote their words. Write a genuinely new sentence in your "
                    "own voice that answers them. Try again, follow ALL rules."
                )
            if final_text:
                final_text = guardrails.clean_output(final_text)
                redis_client.incr("metrics:comments_sent")
                # Return the resolved tactic so MYRMIDON can propagate it to the
                # mission's beta/gamma amplification (squad coherence).
                result = {"status": "ok", "text": final_text, "reason": "",
                          "tactic": req.get("tactic")}
                emit_event(agent_id, "generated",
                           ("готов ответ: " if is_reply else "готов комментарий: ") + final_text[:60],
                           status="ok", target=req.get("author") or "")
                _persist_dialog_memory(persona_engine, req, final_text)
                _remember_output(redis_client, agent_id, final_text)
            else:
                result["reason"] = "guardrails_failed"
    except Exception as exc:
        logger.exception("Mission-gen %s failed: %s", request_id, exc)
        result["reason"] = str(exc)

    if reply_key:
        try:
            redis_client.lpush(reply_key, json.dumps(result, ensure_ascii=False))
            redis_client.expire(reply_key, 300)
        except Exception as exc:
            logger.error("Mission-gen %s — failed to push reply: %s", request_id, exc)


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
            # Listen on both the autonomous news queue and the targeted mission
            # generation queue; whichever has work is served (sequential VRAM).
            result = redis_client.brpop(
                [RAW_EVENTS_QUEUE, MISSION_GEN_QUEUE, SIM_GEN_QUEUE],
                timeout=PROCESSING_TIMEOUT_SEC,
            )
            if result is None:
                continue

            queue_key, payload = result

            if queue_key == SIM_GEN_QUEUE:
                try:
                    sim_req = json.loads(payload)
                except json.JSONDecodeError as exc:
                    logger.error("Malformed simulation request: %s", exc)
                    continue
                handle_simulation_generation(sim_req, redis_client, guardrails, generate_text)
                continue

            if queue_key == MISSION_GEN_QUEUE:
                try:
                    gen_req = json.loads(payload)
                except json.JSONDecodeError as exc:
                    logger.error("Malformed mission-gen request: %s", exc)
                    continue
                if gen_req.get("mode") == "relevance":
                    handle_relevance(gen_req, redis_client, persona_engine)
                else:
                    handle_mission_generation(gen_req, redis_client, persona_engine, guardrails)
                continue

            event_json = payload

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

                # Paused agents do nothing autonomously.
                if profile.get("status") == "suspended":
                    continue

                # Check if agent monitors this platform
                if event.get("source_platform") not in profile.get("platforms", []):
                    continue

                logger.info("Orchestrating response for agent %s...", agent_id)
                emit_event(agent_id, "reading_news",
                           "читает новость: " + (event.get("text_content", "") or "")[:50],
                           status="active", target=event.get("source_target", ""))

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
                    # Update Daedalus status to rejected
                    try:
                        httpx.put(
                            f"http://daedalus:8000/api/v1/huginn/internal/capture/{event_id}",
                            json={"status": "rejected"},
                            headers={"X-Internal-Token": "morpheus-internal-sync-key"},
                            timeout=5.0
                        )
                    except Exception as e:
                        logger.error("Failed to update Daedalus event status: %s", e)
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
                emit_event(agent_id, "dispatched", "отправил коммент в очередь исполнения",
                           status="ok", target=event.get("source_target", ""))
                logger.info("Successfully pushed execution task for agent %s on %s.", agent_id, event.get("source_platform"))
                
                # Update Daedalus status to approved
                try:
                    httpx.put(
                        f"http://daedalus:8000/api/v1/huginn/internal/capture/{event_id}",
                        json={"status": "approved"},
                        headers={"X-Internal-Token": "morpheus-internal-sync-key"},
                        timeout=5.0
                    )
                except Exception as e:
                    logger.error("Failed to update Daedalus event status: %s", e)

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
