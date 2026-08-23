import json
import asyncio
import logging
from pydantic import BaseModel, Field
from google.genai import types
from config.settings import (
    ROUTER_PRIMARY,
    ROUTER_FALLBACK,
    WORKHORSE_MODEL
)
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.Router")

class RouteDecision(BaseModel):
    target_model: str = Field(
        description="Chosen model: 'gemini-3.7-flash', 'gemma-4-31b-it', or 'gemini-3.5-flash-lite'"
    )
    thinking_level: str = Field(
        description="Reasoning level: 'MINIMAL', 'LOW', 'MEDIUM', or 'HIGH'"
    )
    witty_statuses: list[str] = Field(
        description="5 to 7 query-specific, witty, humorous 3-5 word loading messages"
    )
    reasoning_summary: str = Field(
        description="Brief 1-sentence technical reason for this route decision"
    )

ROUTER_SYSTEM_INSTRUCTION = """
You are the routing and complexity classifier for PriestyAI.
Analyze the user's message and context, then output a JSON object adhering strictly to the schema.

Routing Guidelines:
1. CASUAL / MEMORY STORAGE / CHITCHAT / QUICK (Greetings, saving preferences, simple Q&A):
   - target_model: "gemma-4-31b-it" | thinking_level: "MINIMAL"

2. CONCEPTUAL / EXPLANATIONS / RESEARCH (e.g. 'explain X', search questions, multi-source synthesis):
   - target_model: "gemma-4-31b-it" | thinking_level: "HIGH"  (or "gemini-3.5-flash-lite" | "MEDIUM")

3. HEAVY CODING / ARCHITECTURE / DEBUGGING / COMPLEX MATH (Programming in Docker sandbox, formal proofs):
   - target_model: "gemini-3.7-flash" | thinking_level: "HIGH"

Witty Statuses:
Generate exactly 5 to 7 dynamic, humorous, contextual 3-5 word phrases relevant to the query.
"""

class Router:
    @staticmethod
    async def route(user_prompt: str, context_summary: str = "") -> RouteDecision:
        payload = f"Context:\n{context_summary}\n\nUser Query:\n{user_prompt}"

        for router_model in [ROUTER_PRIMARY, ROUTER_FALLBACK]:
            client, key_idx, active_model = client_manager.get_client_for_model(router_model)
            try:
                config = types.GenerateContentConfig(
                    system_instruction=ROUTER_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=RouteDecision,
                    temperature=0.2
                )
                
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=active_model,
                        contents=payload,
                        config=config
                    ),
                    timeout=5
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
                err_desc = "Router timeout (>5.0s)" if isinstance(e, asyncio.TimeoutError) else str(e)
                client_manager.report_error(key_idx, active_model, Exception(err_desc))
                logger.warning(f"Router attempt failed on {active_model} (Key #{key_idx}): {err_desc}")

        logger.warning("Router fallback activated: Using default route.")
        return RouteDecision(
            target_model="gemma-4-31b-it",
            thinking_level="MINIMAL",
            witty_statuses=[
                "Herding digital sheep",
                "Warming up synaptic cores",
                "Analyzing query vectors",
                "Consulting databanks",
                "Formulating optimal answer"
            ],
            reasoning_summary="Router fallback triggered."
        )