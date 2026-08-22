import re
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from google import genai
from google.genai import types
from src.core.config import config
from src.core.key_rotator import KeyRotator

logger = logging.getLogger("PriestyAI.Router")

@dataclass
class RouteDecision:
    target_model: str
    thinking_level: str
    requires_tools: List[str] = field(default_factory=list)
    is_deterministic: bool = False
    reason: str = "default"

IMAGE_GEN_PATTERN = re.compile(r"^(draw|generate|paint|create an image of|show me an image of)\b", re.IGNORECASE)
CODE_EXEC_PATTERN = re.compile(r"```(python|js|javascript|bash|sh|rust|cpp|c)\b|execute this code|run this code", re.IGNORECASE)

class ModelRouter:
    def __init__(self, key_rotator: KeyRotator):
        self.key_rotator = key_rotator

    def deterministic_route(self, prompt: str) -> Optional[RouteDecision]:
        cleaned = prompt.strip()

        if IMAGE_GEN_PATTERN.search(cleaned):
            return RouteDecision(
                target_model="gemma-4-31b-it",
                thinking_level="minimal",
                requires_tools=["generate_image"],
                is_deterministic=True,
                reason="image_generation"
            )

        if CODE_EXEC_PATTERN.search(cleaned):
            return RouteDecision(
                target_model="gemini-3.7-flash",
                thinking_level="high",
                requires_tools=["run_sandbox_code"],
                is_deterministic=True,
                reason="code_execution"
            )

        return None

    async def route(self, prompt: str) -> RouteDecision:
        fast_decision = self.deterministic_route(prompt)
        if fast_decision:
            logger.info(f"Deterministic Router: {fast_decision.target_model} ({fast_decision.reason})")
            return fast_decision

        router_model = "gemini-3.1-flash-lite"
        api_key = await self.key_rotator.get_key_for_model(router_model)
        client = genai.Client(api_key=api_key)

        router_system_prompt = (
            "You are an AI router. Categorize the user's prompt into the appropriate model.\n"
            "Rules:\n"
            "- 'gemini-3.7-flash': Use ONLY for complex multi-step reasoning, logic puzzles/riddles, university-level mathematics, proofs, advanced code debugging, or deep analysis. Set thinking_level to 'medium' or 'high'.\n"
            "- 'gemma-4-31b-it': Use for EVERYTHING ELSE (casual banter, jokes, gaming talk, simple arithmetic/algebra, roleplay, summaries, general QA). Set thinking_level to 'minimal'.\n\n"
            "Output strict JSON:\n"
            '{"target_model": "gemini-3.7-flash"|"gemma-4-31b-it", "thinking_level": "minimal"|"medium"|"high", "reason": "..."}'
        )

        try:
            response = await client.aio.models.generate_content(
                model=router_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=router_system_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=150
                )
            )

            await self.key_rotator.report_success(api_key, router_model)
            data = json.loads(response.text)

            decision = RouteDecision(
                target_model=data.get("target_model", "gemma-4-31b-it"),
                thinking_level=data.get("thinking_level", "minimal"),
                requires_tools=[],
                is_deterministic=False,
                reason=data.get("reason", "semantic_eval")
            )
            logger.info(f"Semantic Router: {decision.target_model} [Thinking: {decision.thinking_level}] ({decision.reason})")
            return decision

        except Exception as e:
            logger.warning(f"Semantic routing failed ({e}), defaulting to gemma-4-31b-it.")
            await self.key_rotator.report_error(api_key, e)
            return RouteDecision(
                target_model="gemma-4-31b-it",
                thinking_level="minimal",
                requires_tools=[],
                is_deterministic=False,
                reason="router_fallback"
            )