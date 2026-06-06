"""
ORPHEUS — Persona Engine & Prompt Assembly
=============================================
Loads YAML agent profiles and orchestrates memory retrieval from MUNINN.
Assembles the final complex prompt for the LLM.
"""

import logging
import os
import yaml
import httpx
from typing import Optional, Dict

logger = logging.getLogger("orpheus.persona")

CONFIG_DIR = os.getenv("GLOBAL_CONFIG_DIR", "/app/global_config/personalities")
MUNINN_URL = "http://muninn:8002"

class PersonaEngine:
    def __init__(self):
        self.profiles: Dict[str, dict] = {}
        self.load_profiles()

    def load_profiles(self):
        """Loads all YAML profiles from the configuration directory."""
        if not os.path.exists(CONFIG_DIR):
            logger.warning("Config directory %s not found.", CONFIG_DIR)
            return

        for filename in os.listdir(CONFIG_DIR):
            if filename.endswith(".yaml") or filename.endswith(".yml"):
                filepath = os.path.join(CONFIG_DIR, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = yaml.safe_load(f)
                        agent_id = data.get("agent_id")
                        if agent_id:
                            self.profiles[agent_id] = data
                            logger.info("Loaded profile for agent %s (%s)", agent_id, data.get("name"))
                except Exception as e:
                    logger.error("Failed to load profile %s: %s", filename, e)

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
        """
        profile = self.profiles.get(agent_id)
        if not profile:
            logger.warning("Agent profile %s not found. Cannot assemble prompt.", agent_id)
            return None

        # Extract event parts
        target_text = event.get("text_content", "")
        opponent = event.get("source_target", "Unknown")
        layers = event.get("layers", {})

        # Fetch Memory
        memory_context = self.fetch_memory(agent_id, opponent, target_text)

        # Build System Prompt
        identity = profile.get("identity", {})
        personality = profile.get("personality", {})
        
        sys_prompt = f"Tone: {personality.get('tone')}. Humor: {personality.get('humor_style')}."
        biography = f"Name: {identity.get('full_name')}, Age: {identity.get('age')}, City: {identity.get('city')}. Occupation: {identity.get('occupation')}."
        
        rules_dict = profile.get("behavioral_rules", {})
        if isinstance(rules_dict, dict):
            rules = "\n- ".join([f"{k}: {v}" for k, v in rules_dict.items()])
        elif isinstance(rules_dict, list):
            rules = "\n- ".join(str(r) for r in rules_dict)
        else:
            rules = str(rules_dict)

        prompt = f"""You are acting as the following persona. You must strictly adhere to your biography, language style, and behavioral rules.
Never break character. Never state that you are an AI. Reply exactly as this persona would.

[System Biography]
{sys_prompt}
{biography}

[Behavioral Rules]
- {rules}

[Geographic & Contextual Layers]
Region: {layers.get('region', 'N/A')}
State: {layers.get('state', 'N/A')}
City: {layers.get('city', 'N/A')}
Active Tags: {', '.join(layers.get('personal_tags', []))}

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
