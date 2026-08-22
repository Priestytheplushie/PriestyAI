import re
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
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

CODE_EXEC_PATTERN = re.compile(r"```(python|js|javascript|bash|sh|rust|cpp|c)\b", re.IGNORECASE)
LATEX_MATH_PATTERN = re.compile(r"(\$\$[\s\S]+?\$\$|\\[a-zA-Z]+|\b(integral|derivative|matrix|eigenvector)\b)", re.IGNORECASE)
IMAGE_GEN_PATTERN = re.compile(r"^(draw|generate|paint|create an image of|show me an image of)\b", re.IGNORECASE)

class ModelRouter:
    def __init__(self, key_rotator: KeyRotator):
        self.key_rotator = key_rotator

    def deterministic_route(self, prompt: str) -> Optional[RouteDecision]:
        cleaned = prompt.strip()
        length = len(cleaned)

        if IMAGE_GEN_PATTERN.search(cleaned):
            return RouteDecision(
                target_model="gemini-3.1-flash-lite",
                thinking_level="minimal",
                requires_tools=["generate_image"],
                is_deterministic=True,
                reason="image_generation_intent"
            )

        if LATEX_MATH_PATTERN.search(cleaned):
            return RouteDecision(
                target_model="gemini-3.7-flash",
                thinking_level="high",
                requires_tools=["render_latex_math"],
                is_deterministic=True,
                reason="latex_math_detected"
            )

        if CODE_EXEC_PATTERN.search(cleaned):
            return RouteDecision(
                target_model="gemini-3.7-flash",
                thinking_level="high",
                requires_tools=["run_sandbox_code"],
                is_deterministic=True,
                reason="code_execution_detected"
            )

        if length < 50 and "http://" not in cleaned and "https://" not in cleaned:
            return RouteDecision(
                target_model="gemini-3.1-flash-lite",
                thinking_level="minimal",
                requires_tools=[],
                is_deterministic=True,
                reason="short_casual_chat"
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
            "You are an AI router. Analyze the user's prompt and output a strict JSON object deciding the best model.\n"
            "Options:\n"
            "- 'gemini-3.7-flash' (for deep reasoning, heavy multi-step logic, complex coding, or difficult problems)\n"
            "- 'gemma-4-31b-it' (for creative writing, banter, long roleplay, or general chat)\n"
            "- 'gemini-3.1-flash-lite' (for standard QA, summaries, and medium queries)\n\n"
            "Output format:\n"
            '{"target_model": "...", "thinking_level": "minimal"|"medium"|"high", "reason": "..."}'
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
                target_model=data.get("target_model", "gemini-3.1-flash-lite"),
                thinking_level=data.get("thinking_level", "medium"),
                requires_tools=[],
                is_deterministic=False,
                reason=data.get("reason", "semantic_eval")
            )
            logger.info(f"Semantic Router: {decision.target_model} [Thinking: {decision.thinking_level}] ({decision.reason})")
            return decision

        except Exception as e:
            logger.warning(f"Semantic routing failed ({e}), falling back to gemini-3.1-flash-lite.")
            await self.key_rotator.report_error(api_key, e)
            return RouteDecision(
                target_model="gemini-3.1-flash-lite",
                thinking_level="minimal",
                requires_tools=[],
                is_deterministic=False,
                reason="router_fallback"
            )