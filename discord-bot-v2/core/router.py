import json
import logging
from pydantic import BaseModel, Field
from google.genai import types
from config.settings import (
    ROUTER_PRIMARY,
    ROUTER_FALLBACK,
    WORKHORSE_MODEL,
    FLAGSHIP_MODELS,
    LITE_MODELS
)
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.Router")

class RouteDecision(BaseModel):
    target_model: str = Field(
        description="The chosen model: 'gemini-3.7-flash', 'gemini-3.5-flash-lite', or 'gemma-4-31b-it'"
    )
    thinking_level: str = Field(
        description="Reasoning depth: 'MINIMAL', 'LOW', 'MEDIUM', or 'HIGH'"
    )
    witty_statuses: list[str] = Field(
        description="5 to 7 query-specific, witty, humorous 3-5 word loading messages"
    )
    reasoning_summary: str = Field(
        description="A brief 1-sentence technical reason for this route decision"
    )

ROUTER_SYSTEM_INSTRUCTION = """
You are the routing and complexity classifier for PriestyAI.
Analyze the user's message and channel context, then output a JSON object adhering strictly to the schema.

Routing Guidelines:
1. CASUAL / CHITCHAT / QUICK (Greetings, jokes, small talk, translations, simple Q&A):
   - target_model: "gemma-4-31b-it" or "gemini-3.5-flash-lite"
   - thinking_level: "MINIMAL"

2. MODERATE / FACTUAL / MULTI-USER (Context analysis, general questions, explanations):
   - target_model: "gemini-3.5-flash-lite"
   - thinking_level: "MEDIUM"

3. COMPLEX / CODING / LOGIC / MATH (Programming, code review, debugging, multi-step reasoning):
   - target_model: "gemini-3.7-flash"
   - thinking_level: "HIGH"

Witty Statuses:
Generate exactly 5 to 7 dynamic, humorous, contextual 3-5 word phrases relevant to the query.
Examples:
- Programming: ["Untangling pointer arithmetic", "Negotiating with compiler", "Searching for missing semicolons", "Calibrating logic gates", "Praying to memory gods"]
- Gaming: ["Linking the First Flame", "Dodging roll spam", "Consulting ancient scrolls", "Deciphering cryptic NPC lore", "Buffing player stats"]
"""

class Router:
    @staticmethod
    async def route(user_prompt: str, context_summary: str = "") -> RouteDecision:
        payload = f"Context:\n{context_summary}\n\nUser Query:\n{user_prompt}"

        for router_model in [ROUTER_PRIMARY, ROUTER_FALLBACK]:
            client, key_idx, active_model = client_manager.get_client(router_model)
            try:
                config = types.GenerateContentConfig(
                    system_instruction=ROUTER_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=RouteDecision,
                    temperature=0.3
                )
                response = await client.aio.models.generate_content(
                    model=active_model,
                    contents=payload,
                    config=config
                )
                if response.text:
                    decision_data = json.loads(response.text)
                    decision = RouteDecision(**decision_data)
                    logger.info(
                        f"[Route Success] Model: '{decision.target_model}' | "
                        f"Thinking: '{decision.thinking_level}' | Key: #{key_idx}"
                    )
                    return decision
            except Exception as e:
                client_manager.report_error(key_idx, active_model, e)
                logger.warning(f"Router attempt failed on {active_model} (Key #{key_idx}): {e}")

        logger.error("All router models failed. Falling back to default deterministic route.")
        return RouteDecision(
            target_model="gemini-3.5-flash-lite",
            thinking_level="MEDIUM",
            witty_statuses=[
                "Warming up synaptic cores",
                "Herding digital sheep",
                "Analyzing query vectors",
                "Consulting internal databanks",
                "Formulating optimal answer"
            ],
            reasoning_summary="Router fallback triggered due to API unavailability."
        )