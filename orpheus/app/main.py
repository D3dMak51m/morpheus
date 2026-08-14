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
from app.guardrails import OutputGuardrails, content_words, normalize, script_mismatch
from app.coordination import generate_beta_subtasks
from app.simulation import handle_simulation_generation
from app import textutil
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


def _channel_alignment(channel_profile: Optional[dict], entities: list[str]) -> bool:
    """
    Is the CHANNEL itself substantially on the mission's theme?

    Used only as a TIE-BREAKER: a merely "weak" post on a thematic channel is still
    worth acting on. It deliberately does NOT license a blanket "yes" — a general
    city channel that happens to list «пробки» among seven topics made the gate accept
    everything on it (measured live: a story about a fleeing security guard was judged
    "прямо наша тема" for a public-transport mission). Hence: at least TWO distinct
    mission words must appear in the channel's own vocabulary.
    """
    if not channel_profile or len(entities) < 2:
        return False
    blob = " ".join([
        " ".join(str(t) for t in (channel_profile.get("topics") or [])),
        " ".join(str(t.get("theme", "")) for t in (channel_profile.get("recent_themes") or [])),
        str(channel_profile.get("summary") or ""),
    ]).lower()
    return textutil.keyword_hit(blob, entities, min_hits=2)


def _parse_verdict(answer: str) -> str:
    """Map the model's word to yes | weak | no. Robust to case/punctuation/garbling."""
    a = re.sub(r"[^а-яёa-z]", " ", (answer or "").lower())
    tokens = a.split()
    head = tokens[0] if tokens else ""
    for tok in tokens[:3]:
        if tok.startswith(("слаб", "weak", "част", "möglich", "maybe")):
            return "weak"
        if tok.startswith(("да", "yes")):
            return "yes"
        if tok.startswith(("нет", "no", "не")):
            return "no"
    if head.startswith("1"):
        return "yes"
    return "no"


def _build_relevance_prompt(goal: str, stance: str, post: str, channel_ctx: str,
                            thread: str, entities: list[str], aligned: bool) -> str:
    """
    The gate's question, rewritten (Stage 38).

    The old prompt asked "is this message ABOUT the mission's subject?" — for a channel
    with a general news flow the honest answer is almost always "no" (measured: НЕТ in
    50/50 live calls), so the swarm sat silent on posts it could easily have joined.
    Influence work is the other question: *can our person plausibly enter THIS
    conversation from our position?* Three graded answers give the engine something to
    rank candidates by instead of a coin flip.
    """
    ent_line = ("Ключевые слова темы: " + ", ".join(entities) + ".\n") if entities else ""
    # NB: channel alignment is context, never a licence to accept everything — telling
    # the model "any post here will do" made it call a random crime story "прямо наша
    # тема" for a transport mission. The bonus lives in the caller's ranking instead.
    aligned_line = (
        "Аудитория канала близка нашей теме — пиши для неё.\n" if aligned else ""
    )
    thread_block = f"\nЧто уже пишут в комментариях:\n{thread[:600]}\n" if thread else ""
    return (
        "Ты — редактор, который решает, стоит ли нашему человеку вступать в обсуждение.\n\n"
        + (f"О канале: {channel_ctx[:400]}\n" if channel_ctx else "")
        + f"Наша тема: {goal[:250]}\n"
        + (f"Наша позиция: {stance[:250]}\n" if stance else "")
        + ent_line + aligned_line
        + f"\nПост:\n\"{post[:600]}\"\n"
        + thread_block
        + "\nМожет ли наш человек естественно и по делу вступить в обсуждение этого поста: "
        "поддержать, поспорить, привести довод, пример или личный опыт со своей позиции?\n"
        "Ответь ОДНИМ словом:\n"
        "ДА — тема прямо наша, есть что сказать;\n"
        "СЛАБО — связь есть, но косвенная;\n"
        "НЕТ — говорить не о чем (реклама, анонс, программа передач, чужая тема)."
    )


def handle_mood(req: dict, redis_client) -> None:
    """
    Re-read a discussion and judge the crowd's stance toward OUR position.

    This is the "after" half of the mission's success measure: the same 3-way verdict
    taken when we entered, re-run on the same thread later, so the pair is the tone
    delta the operator actually cares about. Deliberately the SAME prompt and the same
    penalties-off classification settings as the entry reading — a delta between two
    differently-asked questions would be noise.
    """
    reply_key = req.get("reply_key")
    result = {"status": "ok", "mood": "NEUTRAL"}
    try:
        side = (req.get("our_side") or req.get("stance") or "").strip()
        raw = generate_text(
            build_mood_prompt(side, (req.get("thread_context") or "").strip(),
                              (req.get("post_text") or "").strip()),
            max_tokens=6, temperature=0.2, penalties=False,
        )
        up = (raw or "").strip().upper()
        for m in ("AGREE", "OPPOSE", "NEUTRAL"):
            if m in up:
                result["mood"] = m
                break
        logger.info("Mood re-measure %s → %s", req.get("request_id"), result["mood"])
    except Exception as exc:
        logger.warning("Mood re-measure failed: %s", exc)
        result = {"status": "error", "mood": None, "reason": str(exc)[:120]}
    _reply(redis_client, reply_key, result, ttl=120)


def handle_relevance(req: dict, redis_client, persona_engine) -> None:
    """
    Relevance gate for the target engine: can our agent join this discussion?

    Returns ``{status, relevant, verdict, reason}`` on the request's reply_key, where
    ``verdict`` is ``yes|weak|no`` so the engine can rank several candidate posts and
    pick the best one instead of taking the first that scraped a "yes".
    """
    reply_key = req.get("reply_key")
    agent_id = req.get("agent_id")
    raw_post = (req.get("post_text") or "").strip()
    result = {"status": "ok", "relevant": False, "verdict": "no", "reason": ""}
    try:
        profile = persona_engine.get_all_profiles().get(agent_id) or {}
        m_goal = (req.get("goal") or "").strip()
        m_stance = (req.get("stance") or "").strip()
        thread = (req.get("thread_context") or "").strip()
        channel_profile = req.get("channel_profile")

        # Hygiene first: promo tails, links and OCR'd TV schedules are what made the
        # weak model answer "нет" to everything.
        post = textutil.judging_text(raw_post, req.get("media_context") or "")
        if not post:
            result["reason"] = "нечего обсуждать (реклама/расписание/пусто)"
            logger.info("Relevance %s agent=%s → no (empty after cleaning)",
                        req.get("request_id"), agent_id)
            _reply(redis_client, reply_key, result, ttl=120)
            return

        if m_goal or m_stance:
            entities = textutil.keywords(m_goal, m_stance)
            aligned = _channel_alignment(channel_profile, entities)
            prompt = _build_relevance_prompt(
                m_goal, m_stance, post, _channel_context(channel_profile),
                thread, entities, aligned)
        else:
            entities = textutil.keywords(
                profile.get("core_mission") or "",
                " ".join(i for i in (profile.get("interests") or []) if isinstance(i, str)),
            )
            aligned = _channel_alignment(channel_profile, entities)
            prompt = _build_relevance_prompt(
                " ".join(i for i in (profile.get("interests") or []) if isinstance(i, str)),
                profile.get("core_mission") or "", post,
                _channel_context(channel_profile), thread, entities, aligned)

        answer = generate_text(prompt, max_tokens=6, temperature=0.1, penalties=False)
        verdict = _parse_verdict(answer)

        # Recall-override: an explicit mention of the mission's own vocabulary is a
        # concrete opening even when the model hedged — but it can only raise a "no"
        # to "weak", never fabricate a strong "yes".
        kw_hit = bool(entities) and textutil.keyword_hit(post, entities)
        if verdict == "no" and kw_hit:
            verdict = "weak"
        # On a channel dedicated to our theme, a "weak" is worth acting on.
        result["verdict"] = verdict
        result["relevant"] = verdict == "yes" or (verdict == "weak" and (aligned or kw_hit))
        result["reason"] = {
            "yes": "тема прямо наша",
            "weak": "связь косвенная" + (" (канал по нашей теме)" if aligned else ""),
            "no": "говорить не о чем",
        }[verdict]
        logger.info(
            "Relevance %s agent=%s → %s (verdict=%s llm=%r kw=%s aligned=%s)",
            req.get("request_id"), agent_id, result["relevant"], verdict,
            (answer or "").strip()[:20], kw_hit, aligned,
        )
    except Exception as exc:
        logger.warning("Relevance check failed: %s", exc)
        result = {"status": "error", "relevant": False, "verdict": "no", "reason": str(exc)[:120]}
    _reply(redis_client, reply_key, result, ttl=120)


# ── The objection actually raised ─────────────────────────────────────────
#
# Stage 46. Until now the per-post tactic came from one 3-way mood reading: the crowd
# agrees / is neutral / opposes. That answers "what is the temperature here", never
# "what are they actually arguing", so the roster answered a mood instead of a person.
# A technique can only be chosen against a concrete objection, so the objection has to
# be extracted first — and, like geography in the knowledge pipeline, VERIFIED against
# the source text, because a 3B model will happily invent a plausible counter-argument
# nobody in the thread made.

# One word each, from a short closed list — the measured way to ask this model a
# classification question (asking for JSON made it answer `false` for everything).
OBJECTION_TECHNIQUES = ("факт", "рамка", "уступка", "основание")
TECHNIQUE_BY_WORD = {
    "факт": "factual_correction",
    "рамка": "reframe",
    "уступка": "concede_and_redirect",
    "основание": "ask_evidence",
}
# Minimum share of the objection's content words that must appear in the thread for us
# to believe the model read it there rather than composed it.
OBJECTION_GROUNDING = float(os.getenv("OBJECTION_GROUNDING", "0.5"))


def _build_objection_prompt(side: str, thread: str) -> str:
    """
    Ask the model to QUOTE the person arguing against us, not to judge whether anyone is.

    Measured on a real imported thread (polygon post #21, 27 comments): the judging
    form — "find the strongest argument against our position, or answer НЕТ if nobody
    objects" — answered «НЕТ» 2/2, the same refusal reflex that made the old relevance
    gate say «нет» in 50 of 50 calls. Asking for a QUOTE ("who is arguing with us and
    in what words?") returned the strongest opposing line 2/2, stably. Copying is a
    much easier task for a 3B model than deciding.

    "Nobody objects" is therefore NOT this prompt's job: the caller only asks after the
    mood reading says the crowd is not already on our side.
    """
    return (
        f"Наша позиция: {(side or '(поддержка темы обсуждения)')[:300]}\n\n"
        f"Комментарии:\n{thread[:900]}\n\n"
        "Кто здесь спорит с нашей позицией и какими словами? Процитируй его реплику "
        "дословно, одной строкой. Только цитата, ничего больше."
    )


# The model quotes the line with its author still attached («KXX_007: Все смотрят…»).
_AUTHOR_PREFIX_RE = re.compile(r"^[^:\n]{1,40}:\s+")


def _grounded_objection(candidate: str, thread: str) -> str:
    """
    Keep the extracted objection only if the thread actually contains it.

    The check is the same shape as `places_in_text` in the knowledge pipeline: an
    argument the source never made is a hallucination, not evidence — and answering
    an invented objection is worse than answering none, because it puts words in the
    opponent's mouth in public.
    """
    text = " ".join((candidate or "").split()).strip(" \"'«»")
    text = _AUTHOR_PREFIX_RE.sub("", text).strip(" \"'«»")
    if not text or len(text) < 12:
        return ""
    low = text.lower()
    if low.startswith("нет") or low in ("no", "none", "-"):
        return ""
    # The tokeniser is guardrails' own: two definitions of "content word" would drift
    # apart, and this check has to agree with the anti-echo one to mean anything.
    words = content_words(normalize(text))
    if not words:
        return ""
    in_thread = content_words(normalize(thread))
    shared = words & in_thread
    if len(shared) / len(words) < OBJECTION_GROUNDING:
        logger.info("Objection discarded as ungrounded (%d/%d words in thread): %r",
                    len(shared), len(words), text[:80])
        return ""
    return text[:300]


def _crowd_thread(req: dict) -> str:
    """
    The discussion as the CROWD wrote it — our own comments removed when the reader
    could tell them apart. Judging "what do they think of us" over a thread containing
    our own replies reads as agreement with ourselves.
    """
    return ((req.get("crowd_context") or "").strip()
            or (req.get("thread_context") or "").strip())


def _extract_objection(req: dict) -> str:
    """The strongest argument against our side that was actually made in this thread."""
    thread = _crowd_thread(req)
    if not thread:
        return ""
    position = req.get("position") or {}
    side = (position.get("our_side") or "").strip() or (req.get("stance") or "").strip()
    raw = generate_text(_build_objection_prompt(side, thread),
                        max_tokens=80, temperature=0.2, penalties=False)
    return _grounded_objection(raw, thread)


def _technique_for(objection: str, side: str, has_facts: bool, avoid: str = "") -> str:
    """
    Pick the persuasion technique that answers THIS objection.

    Deliberately one short word out of four, and `factual_correction` is offered only
    when the dossier actually holds facts — telling a weak model to "correct with a
    fact" it does not have is how you get an invented one. ``avoid`` drops the
    technique a teammate already used on this objection: two people answering the same
    objection the same way is the echo the roles exist to end.
    """
    options = [w for w in OBJECTION_TECHNIQUES
               if (w != "факт" or has_facts) and TECHNIQUE_BY_WORD[w] != avoid]
    if not options:
        options = [w for w in OBJECTION_TECHNIQUES if w != "факт" or has_facts]
    explain = {
        "факт": "факт — возражение утверждает неверное, это опровергается фактами;",
        "рамка": "рамка — спор о ценностях или критерии оценки, надо сменить угол;",
        "уступка": "уступка — возражение частично справедливо, надо признать и перевести к своему;",
        "основание": "основание — это голословное обобщение, надо спросить, на чём оно основано.",
    }
    prompt = (
        "Наш человек отвечает на возражение в обсуждении. Выбери, КАК отвечать.\n\n"
        f"Наша позиция: {(side or '(поддержка темы)')[:250]}\n"
        f"Возражение: {objection[:250]}\n\n"
        "Ответь ОДНИМ словом:\n"
        + "\n".join(explain[w] for w in options)
    )
    raw = generate_text(prompt, max_tokens=6, temperature=0.2, penalties=False)
    tokens = re.sub(r"[^а-яёa-z]", " ", (raw or "").lower()).split()
    for tok in tokens[:3]:
        for word in options:
            if tok.startswith(word[:5]):
                return TECHNIQUE_BY_WORD[word]
    # Unreadable answer — or the one technique a teammate already spent. Fall back to
    # the safest move STILL AVAILABLE: conceding-and-redirecting neither invents a fact
    # nor escalates, but if that is the one being avoided, repeating it would rebuild
    # the echo the roles exist to end.
    for word in ("уступка", "рамка", "основание"):
        if word in options:
            return TECHNIQUE_BY_WORD[word]
    return TECHNIQUE_BY_WORD[options[0]]


# ── Going and finding out (Stage 47) ──────────────────────────────────────
#
# A model cannot know a score, a price or today's news: those are absent from its
# training data by definition, and from the swarm's corpus until some feed happens to
# mention them. Answering anyway is invention — the exact failure the operator named.
# So before writing, the agent may go and look, through DAEDALUS (which files whatever
# it reads, so the whole swarm learns it, not just this one comment).

DAEDALUS_URL = os.getenv("DAEDALUS_URL", "http://daedalus:8000")
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
LOOKUP_URL = f"{DAEDALUS_URL}/api/v1/knowledge/internal/lookup"
LOOKUP_TIMEOUT = float(os.getenv("LOOKUP_TIMEOUT_SEC", "90"))
LOOKUP_CACHE_TTL = int(os.getenv("LOOKUP_CACHE_TTL_SEC", "1800"))
# Words that mark a question about something that CHANGES. A cheap pre-filter: no
# point spending an LLM call, a search and two page reads on a post that carries no
# such question at all.
_VOLATILE_MARKERS = (
    "счёт", "счет", "сколько", "цена", "цены", "стоит", "курс", "когда", "сегодня",
    "вчера", "результат", "выиграл", "проиграл", "победил", "who won", "score",
    "price", "статистика", "данные", "правда что", "уже", "последн",
)


def _needs_fresh_data(req: dict) -> bool:
    """
    Would answering here require something that changes — and that we may not know?

    Two gates on purpose. A keyword pre-filter costs nothing and rejects the ordinary
    post; only then is the model asked, one word, penalties off (the measured way to
    ask this model anything short).
    """
    if req.get("lite"):
        return False
    subject = " ".join([(req.get("incoming_text") or ""), (req.get("post_text") or "")]).lower()
    if not subject.strip():
        return False
    if not any(m in subject for m in _VOLATILE_MARKERS):
        return False
    answer = generate_text(
        "Вот сообщение, на которое наш человек собирается ответить.\n\n"
        f"«{subject[:400]}»\n\n"
        "Чтобы ответить по существу, нужны ли СВЕЖИЕ данные из интернета — то, что "
        "меняется со временем: счёт матча, цена, курс, результат, свежая новость?\n"
        "Ответь одним словом: ДА или НЕТ.",
        max_tokens=4, temperature=0.1, penalties=False,
    )
    verdict = _parse_verdict(answer) == "yes"
    logger.info("Fresh-data gate → %s (llm=%r)", verdict, (answer or "").strip()[:16])
    return verdict


def _lookup_query(req: dict) -> str:
    """What to search for: the concrete thing being discussed, in its own place."""
    text = " ".join([(req.get("incoming_text") or ""), (req.get("post_text") or "")])
    terms = textutil.keywords(text, limit=6, min_len=4)
    profile = req.get("channel_profile") or {}
    place = (profile.get("geo_label") or "").split(",")[0].strip()
    return " ".join(x for x in ([place] + terms) if x)[:120]


def _fetch_fresh(req: dict, redis_client) -> list:
    """
    Search the web for what this discussion is about; return compact findings.

    Cached per query for half an hour: a roster answering the same post must not pay
    for the same search three times, and the second agent asking the same question a
    minute later would get the same answer anyway.
    """
    query = _lookup_query(req)
    if len(query) < 6:
        return []
    cache_key = "morpheus:lookup:" + str(abs(hash(query)))
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    try:
        with httpx.Client(timeout=LOOKUP_TIMEOUT) as client:
            r = client.post(LOOKUP_URL, json={"query": query, "read_pages": 2, "recent": True},
                            headers={"X-Internal-Token": INTERNAL_API_TOKEN})
            r.raise_for_status()
            findings = (r.json() or {}).get("findings") or []
    except Exception as exc:
        # No search is not a reason to stall — it is a reason to answer from what we
        # already know, and to say nothing about what we do not.
        logger.warning("Lookup failed for %r: %s", query[:60], exc)
        return []
    try:
        redis_client.setex(cache_key, LOOKUP_CACHE_TTL, json.dumps(findings, ensure_ascii=False))
    except Exception:
        pass
    logger.info("Lookup %r → %d finding(s)", query[:60], len(findings))
    return findings


def _reply(redis_client, reply_key: Optional[str], payload: dict, ttl: int = 300) -> None:
    """Push a request/reply answer back to the caller (best-effort)."""
    if not reply_key:
        return
    try:
        redis_client.lpush(reply_key, json.dumps(payload, ensure_ascii=False))
        redis_client.expire(reply_key, ttl)
    except Exception as exc:
        logger.error("Failed to push reply to %s: %s", reply_key, exc)


def _resolve_dynamic_tactic(req: dict) -> Optional[str]:
    """
    When a mission leaves its tactic as 'dynamic', pick what this comment should DO.

    Stage 46 — the choice is made against the objection actually raised in the thread,
    and only falls back to the mood-derived tactic when nobody argued against us. The
    mood reading itself stays: it is the "before" half of the outcome measure and must
    keep using the same prompt as the later re-reading.

    Cognitive path only — a `lite` beta inherits the seed's choice, replies keep their
    default. Returns the chosen tactic, or None when no selection was made.
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
    # The mood is judged against OUR SIDE when the mission states it explicitly —
    # a free-text stance is often a tag salad the model cannot read as a position.
    position = req.get("position") or {}
    side = (position.get("our_side") or "").strip() or (req.get("stance") or "").strip()
    # The crowd's stance is read over the CROWD: on a thread the mission has already
    # worked, our own comments would otherwise vote for us (measured in the polygon —
    # nine of ours against eight of theirs flipped the verdict to AGREE).
    raw = generate_text(
        build_mood_prompt(side, _crowd_thread(req), post_text),
        max_tokens=6, temperature=0.2, penalties=False,
    )
    # Stage 42 — keep the verdict itself, not just the tactic derived from it. The
    # operator measures success as a CHANGE of tone, and this is the "before" reading;
    # it used to be computed and thrown away on every single comment.
    verdict = "NEUTRAL"
    up = (raw or "").strip().upper()
    for m in ("AGREE", "OPPOSE", "NEUTRAL"):
        if m in up:
            verdict = m
            break
    req["_mood"] = verdict

    # A teammate may already have established what the other side argues here (the
    # opener's extraction travels to the support member with the seed), in which case
    # we answer the same objection — with a different technique.
    #
    # The mood verdict is the gate for asking at all: the extraction prompt QUOTES the
    # person arguing with us rather than judging whether anyone is (a weak model
    # refuses that judgement — measured 2/2 «НЕТ» on a thread that plainly contained
    # opposition), so on a crowd already agreeing with us it would dutifully quote a
    # friend and invent a fight.
    objection = " ".join((req.get("objection") or "").split())
    if not objection and verdict != "AGREE":
        objection = _extract_objection(req)
        req["objection"] = objection
    if objection:
        has_facts = bool((req.get("dossier") or {}).get("fact"))
        return _technique_for(objection, side, has_facts,
                              avoid=(req.get("avoid_tactic") or "").strip())
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
            logger.info("Mission-gen %s — dynamic tactic → %s (objection=%r).",
                        request_id, chosen_tactic, (req.get("objection") or "")[:60])
            objection = (req.get("objection") or "").strip()
            emit_event(agent_id, "tactic",
                       (f"возражение «{objection[:50]}» → " if objection
                        else "тактика по настроению ветки: ")
                       + TACTIC_LABELS_RU.get(chosen_tactic, chosen_tactic),
                       status="info", target=req.get("target_url") or req.get("author") or "")

        # Stage 47 — if answering here means knowing something that changes (a score, a
        # price, today's news), go and find it out rather than invent it. Everything
        # read is filed, so the swarm knows it next time without searching again.
        if _needs_fresh_data(req):
            emit_event(agent_id, "lookup", "ищет свежие данные в интернете",
                       status="active", target=req.get("target_url") or "")
            findings = _fetch_fresh(req, redis_client)
            req["fresh_findings"] = findings
            if findings:
                emit_event(agent_id, "lookup",
                           f"нашёл {len(findings)}: " + (findings[0].get("title") or "")[:50],
                           status="ok", target=(findings[0].get("url") or "")[:200])
            else:
                # Saying nothing is the correct outcome of a failed search. The prompt
                # gets no block, so the model has nothing to "remember" incorrectly.
                emit_event(agent_id, "lookup", "свежих данных не нашлось — отвечает по памяти",
                           status="warn", target=req.get("target_url") or "")

        # Anti-repeat: load the agent's own recent comments, feed them into the
        # prompt, and (alpha path) reject drafts that just rehash them.
        lite = bool(req.get("lite"))
        recent_self = [] if lite else _recent_outputs(redis_client, agent_id)
        req["recent_self"] = recent_self

        prompt = persona_engine.assemble_mission_prompt(agent_id, req)
        if not prompt:
            result["reason"] = "profile_not_found"
        else:
            # Echo references: reject replies that just parrot the human/post back —
            # and, since Stage 46, the ALLY too. Measured live: the support teammate
            # published the opener's comment word for word, because the ally's line
            # arrives as `alpha_context` and nothing compared the draft against it. The
            # dossier's «наши уже сказали» block is a prompt instruction, and a 3B model
            # ignores instructions it finds inconvenient; this is the structural guard.
            dossier_said = [str(x.get("content", "")) for x in
                            ((req.get("dossier") or {}).get("said") or [])]
            echo_refs = [req.get("incoming_text") or "", req.get("post_text") or "",
                         req.get("alpha_context") or "", *dossier_said]
            # Beta 'lite' = cheaper: shorter output, fewer retries.
            attempts_cap = 2 if lite else MISSION_REGEN_ATTEMPTS
            gen_max_tokens = 90 if lite else None
            final_text = ""
            for attempt in range(1, attempts_cap + 1):
                generated = generate_text(prompt, max_tokens=gen_max_tokens)
                ok, reason = guardrails.validate_output(generated)
                if ok:
                    # Answering a Cyrillic post in Chinese is not a style problem.
                    alien = script_mismatch(
                        generated, (req.get("incoming_text") or "") + " " + (req.get("post_text") or ""))
                    if alien:
                        ok, reason = False, f"written in {alien} while the post is not"
                if ok and guardrails.is_echo(generated, echo_refs):
                    ok, reason = False, "reply echoes the post, the ally or what we already said"
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
                          "tactic": req.get("tactic"),
                          # What the other side is actually arguing here — filed into
                          # the mission's dossier and handed to the teammate who
                          # answers it, so the roster argues with people, not moods.
                          "objection": req.get("objection") or "",
                          # The crowd's stance toward us at the moment we entered.
                          "mood": req.get("_mood"),
                          "thread_size": req.get("thread_size") or 0}
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
                if gen_req.get("mode") == "mood":
                    handle_mood(gen_req, redis_client)
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
