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
                
                memory_lines = []
                for m in matches:
                    memory_lines.append(f"- (Distance {m.get('distance', 0)}): {m.get('text', '')}")
                
                return "\n".join(memory_lines)
                
        except Exception as e:
            logger.error("Failed to fetch memory from MUNINN: %s", e)
            return "Memory system unavailable."

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

