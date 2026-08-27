import json
import asyncio
import logging
from pydantic import BaseModel, Field
from google.genai import types
from config.settings import (
    ROUTER_PRIMARY,
    ROUTER_FALLBACK,
    WORKHORSE_DENSE_MODEL,
    WORKHORSE_MOE_MODEL
)
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.Router")

class RouteDecision(BaseModel):
    target_model: str = Field(
        description="Chosen model: 'gemma-4-31b-it' (Dense Coding/Math), 'gemma-4-26b-a4b-it' (Fast MoE General), 'gemini-3.5-flash-lite' (Instant Utility), or 'gemini-3.7-flash' (Flagship Deep Analysis)"
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

ROUTER_SYSTEM_INSTRUCTION = """You are the intelligent traffic router and complexity classifier for PriestyAI.
Analyze the user's message, attached media, and context to select the most optimal model and thinking level.

ROUTING HIERARCHY & COMPLEXITY PRINCIPLES:

1. INSTANT / UTILITY / VISUAL ROUTE -> target_model: "gemini-3.5-flash-lite" | thinking_level: "MINIMAL" or "LOW"
   - Image lookups, picture searches, and visual requests ("show me a picture of...", "what does X look like?").
   - Direct image generation requests ("draw me a...", "generate an artwork of...").
   - Short conversational banter, greetings, simple trivia, translations, or quick utility questions.
   - Goal: Instant execution without latency or wasting API tokens on unnecessary deep reasoning.

2. FAST WORKHORSE ROUTE (MoE) -> target_model: "gemma-4-26b-a4b-it" | thinking_level: "MEDIUM" or "HIGH"
   - Standard conversational reasoning, long document explanations, science/general knowledge, short scripting, and fast agentic queries.
   - 26B total capacity with 4B active execution speed (~2x faster token generation than dense).
   - Constraint: If audio/video media is attached, do NOT use Gemma (route to Gemini Flash).

3. DEEP WORKHORSE ROUTE (Dense) -> target_model: "gemma-4-31b-it" | thinking_level: "HIGH"
   - Heavy software engineering, full script / application builds, intricate code refactoring, complex bug debugging, and mathematical derivations / proofs.
   - Top-tier dense reasoning and algorithmic accuracy.
   - Constraint: If audio/video media is attached, do NOT use Gemma (route to Gemini Flash).

4. FLAGSHIP SPECIALIST ROUTE -> target_model: "gemini-3.7-flash" | thinking_level: "HIGH"
   - Complex full-scale multi-file software projects, deep architectural system design, heavy video/audio analysis, or advanced escalated verification.

Witty Statuses:
Generate exactly 5 to 7 dynamic, humorous, query-specific 3-5 word phrases relevant to the topic.
"""

class Router:
    @staticmethod
    async def route(user_prompt: str, context_summary: str = "", has_media: bool = False) -> RouteDecision:
        media_context = f"\n[MEDIA ATTACHMENTS PRESENT: {has_media}]" if has_media else ""
        payload = f"Context:\n{context_summary}{media_context}\n\nUser Query:\n{user_prompt}"

        for router_model in [ROUTER_PRIMARY, ROUTER_FALLBACK]:
            client, key_idx, active_model = client_manager.get_client_for_model(router_model)
            if not client:
                continue

            try:
                config = types.GenerateContentConfig(
                    system_instruction=ROUTER_SYSTEM_INSTRUCTION,
                    response_mime_type="application/json",
                    response_schema=RouteDecision,
                    temperature=0.1
                )
                
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=active_model,
                        contents=payload,
                        config=config
                    ),
                    timeout=4.0
                )

                if response.text:
                    decision_data = json.loads(response.text)
                    decision = RouteDecision(**decision_data)
                    
                    if has_media and "gemma" in decision.target_model:
                        logger.warning(f"[Router Override] '{decision.target_model}' selected with rich media. Ensuring 'gemini-3.5-flash' fallback for audio/video stability.")
                        decision.target_model = "gemini-3.5-flash"
                        decision.thinking_level = "MEDIUM"

                    logger.info(
                        f"[Route Success] Model: '{decision.target_model}' | "
                        f"Thinking: '{decision.thinking_level}' | Key: #{key_idx}"
                    )
                    return decision
            except Exception as e:
                err_desc = "Router timeout (>4.0s)" if isinstance(e, asyncio.TimeoutError) else str(e)
                client_manager.report_error(key_idx, active_model, Exception(err_desc))
                logger.warning(f"Router attempt failed on {active_model} (Key #{key_idx}): {err_desc}")

        fallback_model = "gemini-3.5-flash-lite" if has_media else WORKHORSE_MOE_MODEL
        logger.warning(f"Router fallback activated: Using safe route '{fallback_model}'.")
        return RouteDecision(
            target_model=fallback_model,
            thinking_level="MEDIUM" if "gemma" in fallback_model else "MINIMAL",
            witty_statuses=[
                "Herding digital sheep",
                "Warming up synaptic cores",
                "Analyzing query vectors",
                "Consulting databanks",
                "Formulating optimal answer"
            ],
            reasoning_summary="Router fallback triggered."
        )