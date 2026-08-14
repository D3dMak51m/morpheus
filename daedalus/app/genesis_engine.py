"""
DAEDALUS — Soul Genesis Engine (Stage 23)
==========================================
Programmatic synthesis of agent profiles via local Ollama inference.

Stage 23 overhaul:
  • Deep psychological prompt — cognitive-bias exploitation, antifragile debate
    tactics, structured intelligence analysis.
  • Mandatory multilingual engagement rules: the persona must seamlessly read &
    reply in Russian, Uzbek (Latin) and Uzbek (Cyrillic) matching the target
    post's language.
  • Output is coerced through the Stage 16 Pydantic schemas
    (CommunicationStyle / BehavioralRules) so the payload is ALWAYS compliant —
    no weak/malformed JSON can reach the database.
"""

import json
import logging
import os
import httpx
from typing import Dict, Any
from pydantic import BaseModel

logger = logging.getLogger("daedalus.genesis_engine")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
DEFAULT_MODEL = os.getenv("TEXT_MODEL_NAME", os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))

# Default RAG layer subscriptions for a deep Alpha persona (Stage 21 cognition).
DEFAULT_ALPHA_SUBSCRIPTIONS = ["global", "regional", "state", "city"]


class GenesisSeed(BaseModel):
    caste: str  # "alpha", "beta", "gamma"
    agent_id: str
    codename: str
    focus: str  # e.g., "Urban planner, Samarkand"
    platforms: list[str]


# ── Compliance coercion (guarantees Stage 16 Pydantic shapes) ──────────────

def _coerce_communication_style(raw: Any) -> dict:
    """Force the LLM payload into the strict CommunicationStyle schema."""
    from app.souls import CommunicationStyle  # lazy import to avoid circular deps
    raw = raw if isinstance(raw, dict) else {}

    def _clamp(value: Any, default: int) -> int:
        try:
            return max(1, min(10, int(value)))
        except (TypeError, ValueError):
            return default

    quirks = raw.get("quirks") or raw.get("typing_quirks") or []
    if not isinstance(quirks, list):
        quirks = [str(quirks)]

    model = CommunicationStyle(
        tone_level=_clamp(raw.get("tone_level"), 5),
        vocab_level=_clamp(raw.get("vocab_level"), 6),
        emoji_frequency=_clamp(raw.get("emoji_frequency"), 3),
        aggression=_clamp(raw.get("aggression"), 4),
        quirks=[str(q) for q in quirks][:6],
    )
    return model.model_dump()


def _coerce_behavioral_rules(raw: Any) -> dict:
    """Force the LLM payload into the strict BehavioralRules schema."""
    from app.souls import BehavioralRules  # lazy import to avoid circular deps
    raw = raw if isinstance(raw, dict) else {}

    rules = raw.get("rules") or []
    if not isinstance(rules, list):
        rules = [str(rules)]

    def _int(value: Any, default: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    model = BehavioralRules(
        rules=[str(r) for r in rules][:12],
        min_delay_between_posts_sec=_int(raw.get("min_delay_between_posts_sec"), 45),
        max_posts_per_hour=_int(raw.get("max_posts_per_hour"), 5),
    )
    return model.model_dump()


# ── Synthesis ───────────────────────────────────────────────────────────────

def generate_profile(seed: GenesisSeed) -> Dict[str, Any]:
    """Synthesize an agent profile based on caste and focus."""
    logger.info("Genesis Engine: Generating %s profile for %s (%s)", seed.caste, seed.agent_id, seed.codename)

    profile: Dict[str, Any] = {
        "agent_id": seed.agent_id,
        "codename": seed.codename,
        "caste": seed.caste.lower(),
        "platforms": seed.platforms,
        "active_hours_start": 8,
        "active_hours_end": 22,
        "layers_affinity": {"global": 1.0, "region": 1.0, "state": 1.0, "city": 1.0, "personal": 1.0},
        # Stage 21 cognition + Stage 23 lifecycle.
        "context_subscriptions": DEFAULT_ALPHA_SUBSCRIPTIONS,
        "status": "unbound",
    }

    # Fast-path for noise/amplifier castes — still schema-compliant.
    if seed.caste.lower() in ["beta", "gamma"]:
        logger.info("Genesis Engine: Applying fast-path template for %s caste.", seed.caste)
        profile.update({
            "full_name": f"{seed.codename} ({seed.caste.capitalize()})",
            "profession": "Automated Swarm Agent",
            "spoken_languages": ["russian", "uzbek_latin", "uzbek_cyrillic"],
            "communication_style": _coerce_communication_style({
                "tone_level": 7,
                "vocab_level": 4 if seed.caste == "gamma" else 6,
                "emoji_frequency": 5,
                "aggression": 5 if seed.caste == "gamma" else 3,
                "quirks": ["short reactive replies", "mirrors the target's language"],
            }),
            "behavioral_rules": _coerce_behavioral_rules({
                "rules": [
                    "Amplify and reinforce the lead Alpha narrative, never contradict it.",
                    "Detect the target post language (RU / UZ-Latin / UZ-Cyrillic) and reply in the SAME script.",
                    "Keep replies short and reactive to look like organic crowd engagement.",
                ],
                "max_posts_per_hour": 15 if seed.caste == "gamma" else 5,
                "min_delay_between_posts_sec": 20 if seed.caste == "gamma" else 45,
            }),
            "context_subscriptions": ["global", "state", "city"],
            "core_mission": seed.focus,
            "spoken_languages": ["russian", "uzbek_latin", "uzbek_cyrillic"],
        })
        return profile

    # ── Alpha: deep LLM synthesis ──────────────────────────────────────────
    logger.info("Genesis Engine: Calling local LLM for Alpha deep synthesis.")

    # Schema mirrors the Stage 16 AgentProfile Pydantic shapes EXACTLY.
    schema = {
        "full_name": "string (realistic Central-Asian human name)",
        "birth_date": "string (YYYY-MM-DD)",
        "residence_city": "string",
        "residence_state": "string",
        "nationality": "string",
        "profession": "string",
        "education": "string",
        "spoken_languages": ["russian", "uzbek_latin", "uzbek_cyrillic"],
        "core_interests": ["string", "string", "string"],
        "communication_style": {
            "tone_level": "integer 1-10 (1=formal, 10=casual)",
            "vocab_level": "integer 1-10 (1=simple, 10=erudite)",
            "emoji_frequency": "integer 1-10",
            "aggression": "integer 1-10 (1=passive, 10=combative)",
            "quirks": ["string typing/speech idiosyncrasy"],
        },
        "behavioral_rules": {
            "rules": ["string natural-language strategic directive"],
            "min_delay_between_posts_sec": "integer",
            "max_posts_per_hour": "integer",
        },
        "core_mission": "string (paragraph: worldview, objectives, the narrative they advance)",
        "current_stance_modifiers": {"key_topic": "stance description"},
    }

    system_prompt = (
        "You are an elite psychological-operations profiling engine. You craft "
        "deep, believable human personas for strategic narrative engagement. Your "
        "profiles are not caricatures — they are antifragile operators.\n\n"
        "When you author the 'behavioral_rules.rules' array, encode ADVANCED "
        "STRATEGY, not platitudes. Each rule must be concrete and actionable:\n"
        "  • COGNITIVE BIASES: weaponise specific biases (anchoring, social proof, "
        "the bandwagon effect, authority bias, in-group framing) to make the "
        "persona's points land.\n"
        "  • ANTIFRAGILE DEBATE: gain from attacks — reframe hostility, use steel-"
        "manning then pivoting, Socratic questioning, and calibrated concession to "
        "appear reasonable while never ceding the core narrative.\n"
        "  • STRUCTURED INTELLIGENCE ANALYSIS: read the opponent's intent, separate "
        "claims from evidence, and probe weak premises before responding.\n\n"
        "MANDATORY MULTILINGUAL ENGAGEMENT: this persona operates in Uzbekistan. "
        "It MUST seamlessly process and respond in the SAME language/script as the "
        "target post — Russian (Cyrillic), Uzbek (Latin script, e.g. 'Assalomu "
        "alaykum'), and Uzbek (Cyrillic script, e.g. 'Ассалому алайкум'). Include "
        "explicit rules that instruct the persona to detect the target's language "
        "and script and mirror it natively, never mixing scripts within one reply.\n\n"
        "Output STRICT, valid JSON ONLY — no markdown, no commentary — matching the "
        "requested schema exactly. Numeric fields MUST be integers 1-10."
    )

    user_prompt = (
        f"Create a highly realistic human persona from this seed:\n\n"
        f"Codename: {seed.codename}\n"
        f"Focus / Vibe: {seed.focus}\n"
        f"Operating platforms: {', '.join(seed.platforms)}\n\n"
        f"Return ONLY a JSON object with this exact schema:\n{json.dumps(schema, indent=2, ensure_ascii=False)}"
    )

    payload = {
        "model": DEFAULT_MODEL,
        "prompt": user_prompt,
        "system": system_prompt,
        "stream": False,
        "format": "json",
        "keep_alive": 0,
        "options": {"temperature": 0.8, "top_p": 0.9},
    }

    try:
        with httpx.Client(timeout=120.0) as client:
            response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
            response.raise_for_status()
            raw_text = response.json().get("response", "").strip()

        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]

        generated = json.loads(raw_text)

        # Copy through plain scalar/list fields verbatim.
        for key in ("full_name", "birth_date", "residence_city", "residence_state",
                    "nationality", "profession", "education", "spoken_languages",
                    "core_interests", "core_mission", "current_stance_modifiers"):
            if key in generated and generated[key] is not None:
                profile[key] = generated[key]

        # Coerce the two structured blocks into strict, compliant shapes.
        profile["communication_style"] = _coerce_communication_style(generated.get("communication_style"))
        profile["behavioral_rules"] = _coerce_behavioral_rules(generated.get("behavioral_rules"))

        # Guarantee the multilingual capability is always present.
        langs = profile.get("spoken_languages") or []
        for required in ("russian", "uzbek_latin", "uzbek_cyrillic"):
            if required not in [str(l).lower() for l in langs]:
                langs.append(required)
        profile["spoken_languages"] = langs

        return profile

    except Exception as e:
        logger.error("Failed to generate Alpha profile via LLM: %s. Falling back to compliant defaults.", e)
        profile.update({
            "full_name": f"{seed.codename} (Fallback Alpha)",
            "profession": seed.focus,
            "spoken_languages": ["russian", "uzbek_latin", "uzbek_cyrillic"],
            "communication_style": _coerce_communication_style({}),
            "behavioral_rules": _coerce_behavioral_rules({
                "rules": [
                    "Detect the target post's language/script (RU / UZ-Latin / UZ-Cyrillic) and reply in the same.",
                    "Use social proof and calibrated concession; never cede the core narrative.",
                ],
            }),
            "core_mission": f"Advance the narrative around: {seed.focus}",
        })
        return profile
