"""
ORPHEUS — Persona Engine & Prompt Assembly
=============================================
Dynamically fetches agent profiles from DAEDALUS via a background asynchronous caching loop,
and orchestrates memory retrieval from MUNINN.
Assembles the final complex prompt for the LLM without blocking inference.
"""

import asyncio
import logging
import os
import httpx
import yaml
from typing import Optional, Dict

from app.rag import fetch_fresh_context
from app.telemetry import emit as emit_event

logger = logging.getLogger("orpheus.persona")

DAEDALUS_URL = "http://daedalus:8000"
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "morpheus-internal-sync-key")
MUNINN_URL = "http://muninn:8002"
CONFIG_DIR = os.getenv("GLOBAL_CONFIG_DIR", "/app/global_config/personalities")

# Global Cache to prevent blocking inference
PROFILES_CACHE: Dict[str, dict] = {}


class PersonaEngine:
    def __init__(self):
        self._local_yaml_fallback: Dict[str, dict] = self._load_local_yaml_profiles()

    def _load_local_yaml_profiles(self) -> Dict[str, dict]:
        """Loads all YAML profiles from the configuration directory as a last resort fallback."""
        fallback = {}
        if not os.path.exists(CONFIG_DIR):
            logger.warning("Config directory %s not found for YAML fallback.", CONFIG_DIR)
            return fallback

        for filename in os.listdir(CONFIG_DIR):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                filepath = os.path.join(CONFIG_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                        agent_id = data.get("agent_id")
                        if agent_id:
                            fallback[agent_id] = data
                except Exception as e:
                    logger.error("Failed to load local YAML profile %s: %s", filename, e)
        return fallback

    def get_all_profiles(self) -> Dict[str, dict]:
        """Returns all profiles currently in cache, or local fallback if cache is empty."""
        if PROFILES_CACHE:
            return PROFILES_CACHE
        return self._local_yaml_fallback

    def fetch_memory(self, agent_id: str, opponent_id: str, text: str) -> str:
        """Queries MUNINN for historical semantic context."""
        try:
            payload = {
                "agent_id": agent_id,
                "opponent_id": opponent_id,
                "query_text": text
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(f"{MUNINN_URL}/api/v1/memory/search", json=payload)
                resp.raise_for_status()
                matches = resp.json().get("matches", [])
                
                if not matches:
                    return "No relevant historical memories found."

                emit_event(agent_id, "memory_read",
                           f"вспоминает прошлое с {opponent_id} ({len(matches)})",
                           status="info", target=str(opponent_id))
                memory_lines = []
                for m in matches:
                    memory_lines.append(f"- (Distance {m.get('distance', 0)}): {m.get('text', '')}")
                
                return "\n".join(memory_lines)
                
        except Exception as e:
            logger.error("Failed to fetch memory from MUNINN: %s", e)
            return "Memory system unavailable."

    def save_memory(self, agent_id: str, opponent_id: str, dialog_summary: str) -> bool:
        """
        Persist a compact dialog summary to MUNINN so the agent actually *remembers*
        this exchange next time it talks to the same person/channel. This is what
        closes the memory loop — without it the long-term memory stays empty and
        every conversation starts from zero. Best-effort; never raises.
        """
        if not opponent_id or not (dialog_summary or "").strip():
            return False
        try:
            payload = {
                "agent_id": agent_id,
                "opponent_id": str(opponent_id),
                "dialog_summary": dialog_summary.strip(),
            }
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(f"{MUNINN_URL}/api/v1/memory/save", json=payload)
                resp.raise_for_status()
            logger.info("Saved memory: agent=%s opponent=%s (%d chars).",
                        agent_id, opponent_id, len(dialog_summary))
            emit_event(agent_id, "memory_write",
                       f"обновил память о {opponent_id}", status="ok",
                       target=str(opponent_id))
            return True
        except Exception as e:
            logger.error("Failed to save memory to MUNINN: %s", e)
            return False

    def assemble_prompt(self, agent_id: str, event: dict, enriched_media: Optional[str]) -> Optional[str]:
        """
        Assembles the final comprehensive prompt incorporating profile,
        geographic context, media, memory, and the target text.
        Reads strictly from PROFILES_CACHE (non-blocking).
        """
        # 1. Fetch profiles from cache, fallback to local YAML if empty
        profile = PROFILES_CACHE.get(agent_id)
        if not profile:
            logger.warning("Agent profile %s not in PROFILES_CACHE. Falling back to local YAML.", agent_id)
            profile = self._local_yaml_fallback.get(agent_id)
            
        if not profile:
            logger.error("Agent profile %s not found in cache or local fallback. Cannot assemble prompt.", agent_id)
            return None

        # 2. Extract event parts
        target_text = event.get("text_content", "")
        opponent = event.get("source_target", "Unknown")
        layers = event.get("layers", {})

        # 3. Fetch Memory (historical dialog) + Fresh Context (RAG over KnowledgeFacts)
        memory_context = self.fetch_memory(agent_id, opponent, target_text)

        # Stage 21 — Contextual RAG: retrieve fresh world-knowledge facts the agent
        # is subscribed to, or honour an operator-forced context if pinned.
        subscriptions = profile.get("context_subscriptions") or ["global"]
        forced_context = event.get("forced_context")
        fresh_context = fetch_fresh_context(target_text, subscriptions, forced_context)

        # 4. Build Mega-Prompt
        identity = profile.get("identity", {})
        personality = profile.get("personality", {})
        
        tone = personality.get("tone_level", "Neutral")
        aggression = personality.get("aggression", "Low")
        emoji_freq = personality.get("emoji_frequency", "Medium")
        
        core_mission = profile.get("core_mission", "No core mission specified.")
        stance_modifiers = profile.get("current_stance_modifiers", {})
        
        sys_prompt = f"Tone: {tone}. Aggression: {aggression}. Emoji Frequency: {emoji_freq}."
        biography = f"Name: {identity.get('full_name')}, City: {identity.get('city')}, Occupation: {identity.get('occupation')}."
        
        rules_dict = profile.get("behavioral_rules", {})
        if isinstance(rules_dict, dict):
            rules = "\n- ".join([f"{k}: {v}" for k, v in rules_dict.items()])
        elif isinstance(rules_dict, list):
            rules = "\n- ".join(str(r) for r in rules_dict)
        else:
            rules = str(rules_dict)

        stance_lines = "\n- ".join([f"{k}: {v}" for k, v in stance_modifiers.items()]) if isinstance(stance_modifiers, dict) else str(stance_modifiers)

        prompt = f"""You are acting as the following persona. You must strictly adhere to your biography, language style, and behavioral rules.
Never break character. Never state that you are an AI. Reply exactly as this persona would.

[System Biography]
{sys_prompt}
{biography}

[Core Mission]
{core_mission}

[Current Stance Modifiers]
- {stance_lines}

[Behavioral Rules]
- {rules}

[Geographic & Contextual Layers]
Region: {layers.get('region', 'N/A')}
State: {layers.get('state', 'N/A')}
City: {layers.get('city', 'N/A')}

[Fresh Context Memory — Verified Facts (subscribed layers: {', '.join(subscriptions)})]
Use these facts as ground truth. Weave the relevant ones naturally into your reply;
never quote them verbatim or reveal they were supplied to you.
{fresh_context}

[Historical Memory with Opponent ({opponent})]
{memory_context}
"""
        if enriched_media and enriched_media != "No media content extracted.":
            prompt += f"\n[Visual & Audio Media Context]\nThe post contains media. Here is the AI-extracted description:\n{enriched_media}\n"

        prompt += f"""
[Target Post/Comment Text]
{target_text}

[Task]
Based on your persona, the geographic context, and any media or memory provided, write a natural response to the target post.
Keep it concise, platform-appropriate, and strictly in character. Output ONLY the response text.
"""
        return prompt

    def assemble_mission_prompt(self, agent_id: str, req: dict) -> Optional[str]:
        """
        Build a *mission-aware* prompt for the targeted Mission path (distinct from
        the autonomous raw_events fan-out). It fuses the agent's persona, RAG world
        knowledge, historical memory, the actual post being replied to (context),
        and the mission's role / tactic / objective — so the bot leaves a real,
        situational comment instead of canned text or the mission goal verbatim.
        """
        profile = PROFILES_CACHE.get(agent_id) or self._local_yaml_fallback.get(agent_id)
        if not profile:
            logger.error("Mission-gen: profile %s not in cache or local fallback.", agent_id)
            return None

        mode = req.get("mode") or "comment"
        post_text = (req.get("post_text") or "").strip()
        incoming_text = (req.get("incoming_text") or "").strip()
        thread_context = (req.get("thread_context") or "").strip()
        author = req.get("author") or "the channel"
        # Memory is scoped per *person* when we know them (a human in a thread),
        # otherwise per channel/topic — this is what makes recall feel personal.
        opponent_key = req.get("opponent_id") or author
        narrative_goal = (req.get("narrative_goal") or "").strip()
        tactic = req.get("tactic") or "soft_support"
        role = req.get("role") or "alpha"
        forced_context = req.get("forced_context")
        alpha_context = req.get("alpha_context")

        # Beta "lite" support path — cheap by design (a beta agent is cheaper than an
        # alpha). Persona + the ally's line + a short supporting reply; SKIPS the
        # expensive RAG fetch, MUNINN memory and thread-mood reads entirely.
        if req.get("lite"):
            identity = profile.get("identity", {})
            personality = profile.get("personality", {})
            return (
                f"Ты — {identity.get('full_name') or 'обычный человек'}, "
                f"{identity.get('occupation') or ''} из {identity.get('city') or ''}. "
                "Пиши как живой человек, НЕ как ИИ, без пояснений и кавычек. "
                f"Тон: {personality.get('tone_level', 'Neutral')}.\n\n"
                f"Пост: {post_text[:200] or '(тема обсуждения)'}\n"
                f"Союзник уже написал: \"{(alpha_context or '')[:200]}\"\n\n"
                "[Задача] Коротко поддержи и усиль мысль союзника — ОДНО короткое "
                "предложение, своими словами, на языке поста. НЕ повторяй его дословно. "
                "Выдай только текст реплики."
            )

        # Ground retrieval + memory on the most relevant text we have.
        subscriptions = profile.get("context_subscriptions") or ["global"]
        rag_query = "\n".join(t for t in (incoming_text, post_text, narrative_goal) if t).strip()
        fresh_context = fetch_fresh_context(rag_query, subscriptions, forced_context)
        memory_context = self.fetch_memory(agent_id, opponent_key, incoming_text or post_text or narrative_goal)

        identity = profile.get("identity", {})
        personality = profile.get("personality", {})
        tone = personality.get("tone_level", "Neutral")
        aggression = personality.get("aggression", "Low")
        emoji_freq = personality.get("emoji_frequency", "Medium")
        core_mission = profile.get("core_mission") or "No core mission specified."

        rules_dict = profile.get("behavioral_rules", {})
        if isinstance(rules_dict, dict):
            rules = "\n- ".join(f"{k}: {v}" for k, v in rules_dict.items())
        elif isinstance(rules_dict, list):
            rules = "\n- ".join(str(r) for r in rules_dict)
        else:
            rules = str(rules_dict)

        role_directives = {
            "alpha": "You are the FIRST to engage. Seed the narrative as an original, organic opinion — never reference any 'previous comment'.",
            "beta": "You are reinforcing an allied lead comment. Agree with and strengthen its angle with a fresh supporting point — do not copy it.",
            "gamma": "You are adding light supporting engagement — short, casual and natural; nudge sentiment without arguing at length.",
        }
        tactic_directives = {
            "soft_support": "Be measured, friendly and constructive. Persuade gently; never attack.",
            "aggressive_displacement": "Be assertive and confident; firmly counter the opposing framing. Stay civil — no insults, slurs or threats.",
        }
        role_line = role_directives.get(role, role_directives["alpha"])
        tactic_line = tactic_directives.get(tactic, tactic_directives["soft_support"])

        prompt = f"""You are acting as the following human persona on social media. Never break character, never reveal or imply you are an AI, never explain yourself. Detect the language and script of the post and reply in the SAME language and script.

[Persona]
Tone: {tone}. Aggression: {aggression}. Emoji frequency: {emoji_freq}.
Name: {identity.get('full_name')}, City: {identity.get('city')}, Occupation: {identity.get('occupation')}.

[Long-term Mission]
{core_mission}

[Behavioral Rules]
- {rules}

[Fresh Context Memory — Verified Facts (subscribed layers: {', '.join(subscriptions)})]
Use these as ground truth. Weave in only what is relevant; never quote verbatim or reveal they were supplied.
{fresh_context}

[Your Memory of {author}]
This is what you (the persona) remember from past interactions. Stay consistent with it — like a real person who remembers who they've talked to.
{memory_context}

[The Post You Are Discussing]
{post_text or '(no readable text — infer from the channel/topic)'}
"""
        if thread_context:
            prompt += f"""
[Mood of the Discussion — recent comments by others]
Read the atmosphere: who is supportive, who is hostile, who is joking, what the crowd's sentiment is. Let it shape your tone — match a friendly thread, stay calm against a hostile one, don't repeat what others already said.
{thread_context}
"""

        if mode == "reply":
            prompt += f"""
[A Real Person Just Replied To You]
{author} replied directly to your comment with:
"{incoming_text}"

[How To Answer — strict]
- NEVER repeat, quote, or paraphrase their words back. Do not begin with their phrase. Write a genuinely NEW sentence in your own voice.
- Actually respond to the substance: answer their question, react to their point, or push back on their jab — as a real person would.
- If they test you (ask you to prove you're human, write code/scripts, "who are you / tell about yourself", call you a bot, or anything a normal commenter wouldn't do): brush it off casually and stay fully in character — a real person would be amused or dismissive, never comply, never explain yourself, never admit being a bot.
- Keep nudging toward your objective ({narrative_goal or core_mission}) only when it fits naturally; otherwise just sound like a normal person.

[Task]
Reply to {author} in 1-2 short sentences, in their language and script, following your role ({role}) and tactic. Output ONLY the reply text — no preamble, no quotes, no explanations.
"""
            return prompt

        prompt += f"""
[This Operation]
Your role: {role_line}
Tactic: {tactic_line}
Objective to advance: {narrative_goal or core_mission}
"""
        if role in ("beta", "gamma") and alpha_context:
            prompt += f"Allied lead context: {alpha_context}\n"

        prompt += """
[Task]
Write ONE natural comment (1-3 sentences, not a list) that fits your persona, reacts to the post and the mood of the thread, follows your role and tactic, and advances the objective. Reply in the post's language and script. Output ONLY the comment text — no preamble, no quotes, no explanations.
"""
        return prompt


async def periodically_update_profiles_cache() -> None:
    """
    Background asynchronous task that fetches agent profiles from DAEDALUS
    every 30 seconds and updates the in-memory PROFILES_CACHE dictionary.
    """
    global PROFILES_CACHE
    headers = {"X-Internal-Token": INTERNAL_API_TOKEN}
    
    logger.info("Starting background profile caching task...")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                resp = await client.get(f"{DAEDALUS_URL}/api/v1/souls/internal/profiles", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                fetched_profiles = data.get("profiles", {})
                
                # Atomically update the cache
                PROFILES_CACHE.clear()
                PROFILES_CACHE.update(fetched_profiles)
                
                logger.debug("PROFILES_CACHE updated successfully (%d profiles).", len(PROFILES_CACHE))
            except Exception as e:
                logger.error("Background caching task failed to fetch profiles from DAEDALUS: %s. Re-trying in 30s.", e)
            
            await asyncio.sleep(30)

