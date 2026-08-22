import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from google.genai import types

import config
from core.key_pool import KeyPoolManager

logger = logging.getLogger("PriestyAI.Router")

ROUTER_SYSTEM_PROMPT = """
You are the routing and status synthesis engine for PriestyAI.
Evaluate the incoming Discord chat context and produce a strict JSON response.

Routing Guidelines:
1. Needs Web / Real-time / Tools / Deep Math (e.g. game patch notes, Fortnite updates, Marvel Rivals meta, live news, coding execution, complex math):
   - Target: "gemini-3.5-flash" or "gemini-3.7-flash".
2. Casual banter, server greetings, simple chat:
   - Target: "gemma-4-31b-it".

Status Generation:
- Generate 'status_cycle': A list of exactly 5 to 7 concise, witty loading status lines (max 6 words each, NO emojis).

Output ONLY valid JSON matching this schema:
{
  "complexity": 4,
  "intent": "web_search",
  "target_model": "gemini-3.5-flash",
  "status_cycle": [
    "Searching the live web archives...",
    "Extracting patch notes...",
    "Analyzing battle pass data...",
    "Formulating the intel...",
    "Polishing the final answer..."
  ]
}
"""

class RouteDecision:
    def __init__(
        self,
        target_model: str,
        complexity: int,
        intent: str,
        status_cycle: List[str]
    ):
        self.target_model = target_model
        self.complexity = complexity
        self.intent = intent
        self.status_cycle = status_cycle

class FastRouter:
    def __init__(self, key_pool: KeyPoolManager):
        self.key_pool = key_pool
        self.default_cycle = [
            "Herding digital sheep...",
            "Consulting server archives...",
            "Untangling neural pathways...",
            "Syncing with Discord gateway...",
            "Polishing the response..."
        ]

    async def route(self, xml_context: str, has_media: bool = False) -> RouteDecision:
        if not has_media and len(xml_context.strip()) < 140 and any(w in xml_context.lower() for w in ["good morning", "gm", "hello", "hi", "hey", "good afternoon", "yo", "sup"]):
            return RouteDecision(
                target_model=config.WORKHORSE_MODEL,
                complexity=1,
                intent="casual",
                status_cycle=[
                    "Waking up the neural circuits...",
                    "Syncing with channel context...",
                    "Formulating greeting...",
                    "Ready to roll..."
                ]
            )

        try:
            client_state = self.key_pool._get_next_available_key()
            if not client_state:
                client_state = self.key_pool.keys[0]

            prompt = f"Analyze this context and output routing JSON:\n\n{xml_context[-1500:]}"
            
            gen_config = types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM_PROMPT,
                temperature=0.2,
                response_mime_type="application/json",
                max_output_tokens=400
            )

            response = await client_state.client.aio.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=[prompt],
                config=gen_config
            )

            data = json.loads(response.text)
            
            complexity = int(data.get("complexity", 3))
            intent = str(data.get("intent", "chat"))
            target_model = data.get("target_model", "gemini-3.5-flash")

            if target_model not in config.FULL_FALLBACK_CASCADE:
                target_model = "gemini-3.5-flash"

            status_cycle = data.get("status_cycle", self.default_cycle)
            if not isinstance(status_cycle, list) or len(status_cycle) < 3:
                status_cycle = self.default_cycle

            logger.info(f"Router Decision: Model='{target_model}' | Complexity={complexity} | Intent='{intent}'")
            return RouteDecision(target_model, complexity, intent, status_cycle)

        except Exception as e:
            logger.warning(f"Flash-Lite Router fallback ({e}), routing to gemini-3.5-flash.")
            return RouteDecision(
                target_model="gemini-3.5-flash",
                complexity=3,
                intent="chat",
                status_cycle=self.default_cycle
            )