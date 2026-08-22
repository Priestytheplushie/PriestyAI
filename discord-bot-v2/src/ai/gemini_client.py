import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Callable
from google import genai
from google.genai import types
from src.core.key_rotator import KeyRotator
from src.ai.thinking import ThinkingSession

logger = logging.getLogger("PriestyAI.GeminiClient")

@dataclass
class AIResponse:
    content: str
    model_used: str
    duration: float
    thought_content: Optional[str] = None
    tool_calls: List[types.FunctionCall] = field(default_factory=list)

class GeminiEngine:
    def __init__(self, key_rotator: KeyRotator):
        self.key_rotator = key_rotator

    async def generate_response(
        self,
        model_name: str,
        system_instruction: str,
        conversation_history: List[Dict[str, Any]],
        tools: Optional[List[Any]] = None,
        thinking_level: str = "minimal",
        status_callback: Optional[Callable[[str], Any]] = None
    ) -> AIResponse:
        session = ThinkingSession.start(model_name=model_name, thinking_level=thinking_level)
        max_retries = 3
        last_exception: Optional[Exception] = None

        contents: List[types.Content] = []
        for msg in conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            parts = [types.Part.from_text(text=msg["content"])]
            contents.append(types.Content(role=role, parts=parts))

        for attempt in range(max_retries):
            api_key = await self.key_rotator.get_key_for_model(model_name)
            client = genai.Client(api_key=api_key)

            thinking_config = None
            if "flash" in model_name.lower():
                budget = 0
                if thinking_level == "medium":
                    budget = 2048
                elif thinking_level == "high":
                    budget = 4096
                thinking_config = types.ThinkingConfig(thinking_budget=budget)

            config_params = types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.75,
                max_output_tokens=3000,
                thinking_config=thinking_config,
                tools=tools if tools else None
            )

            try:
                if status_callback and len(session.status_messages) > 0:
                    await status_callback(session.status_messages[min(attempt, len(session.status_messages) - 1)])

                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config_params
                )

                await self.key_rotator.report_success(api_key, model_name)

                text_content = ""
                thought_text = None
                function_calls: List[types.FunctionCall] = []

                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        if getattr(part, "thought", False) and part.text:
                            thought_text = (thought_text or "") + part.text
                        elif part.text:
                            text_content += part.text
                        elif part.function_call:
                            function_calls.append(part.function_call)

                duration = session.finish(thought_content=thought_text)

                return AIResponse(
                    content=text_content.strip(),
                    model_used=model_name,
                    duration=duration,
                    thought_content=thought_text,
                    tool_calls=function_calls
                )

            except Exception as e:
                logger.warning(f"Generation attempt {attempt + 1} failed on {model_name} (Key: ...{api_key[-6:]}): {e}")
                await self.key_rotator.report_error(api_key, e)
                last_exception = e
                if model_name != "gemini-3.1-flash-lite":
                    logger.info("Failing over to gemini-3.1-flash-lite for next attempt.")
                    model_name = "gemini-3.1-flash-lite"

        duration = session.finish()
        logger.error(f"All {max_retries} attempts failed. Last error: {last_exception}")
        return AIResponse(
            content="Sorry, I hit an upstream API glitch while thinking. Give me a second and try again!",
            model_used=model_name,
            duration=duration,
            thought_content=None,
            tool_calls=[]
        )