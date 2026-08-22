import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Callable, Awaitable
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
    tools_executed: List[Dict[str, Any]] = field(default_factory=list)

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
        tool_dispatcher: Optional[Callable[[types.FunctionCall], Awaitable[Dict[str, Any]]]] = None,
        status_callback: Optional[Callable[[str], Any]] = None
    ) -> AIResponse:
        session = ThinkingSession.start(model_name=model_name, thinking_level=thinking_level)
        max_attempts = 4
        current_model = model_name
        last_exception: Optional[Exception] = None

        contents: List[types.Content] = []
        for msg in conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            parts = [types.Part.from_text(text=msg["content"])]
            contents.append(types.Content(role=role, parts=parts))

        for attempt in range(max_attempts):
            api_key = await self.key_rotator.get_key_for_model(current_model)
            client = genai.Client(api_key=api_key)

            thinking_config = None
            if "flash" in current_model.lower():
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

                text_content = ""
                thought_text = ""
                executed_tools_log: List[Dict[str, Any]] = []

                for turn in range(4):
                    response = await client.aio.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=config_params
                    )

                    await self.key_rotator.report_success(api_key, current_model)

                    if not response.candidates or not response.candidates[0].content:
                        break

                    candidate_content = response.candidates[0].content
                    contents.append(candidate_content)

                    turn_function_calls: List[types.FunctionCall] = []
                    for part in candidate_content.parts:
                        if getattr(part, "thought", False) and part.text:
                            thought_text += part.text
                        elif part.text:
                            text_content += part.text
                        elif part.function_call:
                            turn_function_calls.append(part.function_call)

                    if not turn_function_calls or not tool_dispatcher:
                        break

                    response_parts: List[types.Part] = []
                    for func_call in turn_function_calls:
                        tool_result = await tool_dispatcher(func_call)
                        executed_tools_log.append({
                            "name": func_call.name,
                            "args": func_call.args or {},
                            "result": tool_result
                        })

                        clean_result = {k: v for k, v in tool_result.items() if not k.startswith("_")}
                        response_parts.append(
                            types.Part.from_function_response(
                                name=func_call.name,
                                response={"result": clean_result}
                            )
                        )

                    contents.append(types.Content(role="user", parts=response_parts))

                duration = session.finish(thought_content=thought_text if thought_text else None)

                return AIResponse(
                    content=text_content.strip(),
                    model_used=current_model,
                    duration=duration,
                    thought_content=thought_text if thought_text else None,
                    tools_executed=executed_tools_log
                )

            except Exception as e:
                logger.warning(f"Generation attempt {attempt + 1} failed on {current_model} (Key: ...{api_key[-6:]}): {e}")
                await self.key_rotator.report_error(api_key, e)
                last_exception = e

                if attempt >= 2 and current_model != "gemma-4-31b-it":
                    logger.info("Flash keys exhausted. Failing over to gemma-4-31b-it.")
                    current_model = "gemma-4-31b-it"

        duration = session.finish()
        logger.error(f"All {max_attempts} attempts failed. Last error: {last_exception}")
        return AIResponse(
            content="Sorry, I hit an upstream API glitch while thinking. Give me a second and try again!",
            model_used=current_model,
            duration=duration,
            thought_content=None,
            tools_executed=[]
        )