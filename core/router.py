import re
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
        description="Chosen model: 'gemini-3.5-flash-lite' (Instant Utility / Image / Video / Edits), 'gemma-4-26b-a4b-it' (Fast MoE General), 'gemma-4-31b-it' (Dense Coding/Math), or 'gemini-3.7-flash' (Flagship Deep Analysis)"
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
    is_quiz: bool = Field(
        default=False,
        description="True ONLY if the user prompt explicitly requests a quiz, exam, trivia, knowledge check, or active recall test. Otherwise False."
    )

ROUTER_SYSTEM_INSTRUCTION = """You are the intelligent traffic router and complexity classifier for PriestyAI.
Analyze the user's message, attached media, and context to select the most optimal model and thinking level.

QUIZ DETECTION:
- Set is_quiz = true if the user asks for a quiz, test, exam, trivia, knowledge check, or active recall test (e.g. "make a quiz", "test my knowledge", "quiz me on...", "create 10 trivia questions").
- Otherwise set is_quiz = false.

ROUTING HIERARCHY & COMPLEXITY PRINCIPLES:

1. INSTANT / UTILITY / VISUAL & MEDIA ROUTE -> target_model: "gemini-3.5-flash-lite" | thinking_level: "MINIMAL" (or "LOW")
   - Image Editing & Stylizing ("edit this image", "turn this into a sketch", "make this 3D", "anime-fy this", "apply watercolor style").
   - Video & Animation Creation ("animate this", "make a video of...", "create a gif of...", "animate this thinking animation").
   - Image Generation ("draw me a...", "generate an artwork of...", "paint a picture of...").
   - Real-World Image Lookups ("show me a picture of...", "what does X look like?").
   - Short conversational banter, greetings, simple trivia, translations, or quick utility questions.
   - CRITICAL DIRECTIVE: For all visual tools (edit_image, create_video, generate_image, search_image), you MUST select "gemini-3.5-flash-lite" with "MINIMAL" thinking so the tool executes immediately without spending 15+ seconds overthinking.

2. FAST WORKHORSE ROUTE (MoE) -> target_model: "gemma-4-26b-a4b-it" | thinking_level: "MEDIUM" or "HIGH"
   - Standard conversational reasoning, long document explanations, science/general knowledge, short scripting, and fast agentic queries.
   - 26B total capacity with 4B active execution speed (~2x faster token generation than dense).
   - Constraint: If audio/video media is attached, do NOT use Gemma (route to Gemini Flash).

3. DEEP WORKHORSE ROUTE (Dense) -> target_model: "gemma-4-31b-it" | thinking_level: "HIGH"
   - Quizzes & Knowledge Checks: Deep technical quizzes requiring nuanced questions, subtle distractor options, and rigorous rule explanations.
   - Heavy software engineering, full script / application builds, intricate code refactoring, complex bug debugging, and mathematical derivations / proofs.
   - Constraint: If audio/video media is attached, do NOT use Gemma (route to Gemini Flash).

4. FLAGSHIP SPECIALIST ROUTE -> target_model: "gemini-3.7-flash" | thinking_level: "HIGH"
   - Complex full-scale multi-file software projects, deep architectural system design, heavy video/audio analysis, or advanced escalated verification.
   - NEVER route simple image generation, image editing, or animation prompts to 3.7-flash.

Witty Statuses:
Generate exactly 5 to 7 dynamic, humorous, query-specific 3-5 word phrases relevant to the topic.
"""

class Router:
    @staticmethod
    async def route(user_prompt: str, context_summary: str = "", has_media: bool = False) -> RouteDecision:
        media_context = f"\n[MEDIA ATTACHMENTS PRESENT: {has_media}]" if has_media else ""
        payload = f"Context:\n{context_summary}{media_context}\n\nUser Query:\n{user_prompt}"

        prompt_lower = user_prompt.lower()
        is_visual_tool_intent = any(kw in prompt_lower for kw in [
            "animate", "animation", "make a gif", "create video", "make video",
            "edit image", "turn this into", "sketch", "draw", "generate image",
            "make this 3d", "anime-fy", "pixel art", "show me a picture", "find an image"
        ])

        is_quiz_keyword = any(kw in prompt_lower for kw in [
            "quiz", "make a quiz", "create a quiz", "test my knowledge",
            "quiz me", "trivia", "give me a test", "exam on", "test on"
        ])

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
                    
                    if is_quiz_keyword:
                        decision.is_quiz = True

                    if is_visual_tool_intent and decision.target_model in ["gemini-3.7-flash", "gemini-3.6-flash"]:
                        logger.info(f"[Router Override] Visual tool intent detected. Overriding '{decision.target_model}' to 'gemini-3.5-flash-lite' (MINIMAL).")
                        decision.target_model = "gemini-3.5-flash-lite"
                        decision.thinking_level = "MINIMAL"

                    if has_media and "gemma" in decision.target_model:
                        logger.warning(f"[Router Override] '{decision.target_model}' selected with rich media. Ensuring 'gemini-3.5-flash' fallback.")
                        decision.target_model = "gemini-3.5-flash"
                        decision.thinking_level = "MEDIUM"

                    logger.info(
                        f"[Route Success] Model: '{decision.target_model}' | "
                        f"Thinking: '{decision.thinking_level}' | is_quiz: {decision.is_quiz} | Key: #{key_idx}"
                    )
                    return decision
            except Exception as e:
                err_desc = "Router timeout (>4.0s)" if isinstance(e, asyncio.TimeoutError) else str(e)
                client_manager.report_error(key_idx, active_model, Exception(err_desc))
                logger.warning(f"Router attempt failed on {active_model} (Key #{key_idx}): {err_desc}")

        fallback_model = "gemini-3.5-flash-lite" if (has_media or is_visual_tool_intent) else WORKHORSE_MOE_MODEL
        logger.warning(f"Router fallback activated: Using safe route '{fallback_model}'.")
        return RouteDecision(
            target_model=fallback_model,
            thinking_level="MINIMAL" if is_visual_tool_intent else ("MEDIUM" if "gemma" in fallback_model else "MINIMAL"),
            witty_statuses=[
                "Herding digital sheep",
                "Warming up synaptic cores",
                "Analyzing query vectors",
                "Consulting databanks",
                "Formulating optimal answer"
            ],
            reasoning_summary="Router fallback triggered.",
            is_quiz=is_quiz_keyword
        )