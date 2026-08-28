import asyncio
import time
import logging
from typing import List, Dict, Optional, Tuple, Any, AsyncGenerator
from google import genai
from google.genai import types

import config
from tools.registry import GEMINI_TOOLS, ToolDispatcher
from tools.discord_tools import DiscordToolsContext

logger = logging.getLogger("PriestyAI.KeyPool")

class KeyState:
    def __init__(self, key: str, index: int):
        self.key = key
        self.index = index
        self.client = genai.Client(api_key=key)
        self.cooldown_until: float = 0.0
        self.consecutive_errors: int = 0
        self.total_requests: int = 0
        self.total_failures: int = 0

    def is_available(self) -> bool:
        return time.time() >= self.cooldown_until

    def mark_success(self):
        self.consecutive_errors = 0
        self.total_requests += 1

    def mark_rate_limited(self, base_cooldown: float = 10.0):
        self.consecutive_errors += 1
        self.total_failures += 1
        backoff = min(base_cooldown * (2 ** (self.consecutive_errors - 1)), 300.0)
        self.cooldown_until = time.time() + backoff
        logger.warning(
            f"Key #{self.index + 1} rate-limited (429/503). Backoff applied for {backoff:.1f}s. "
            f"Consecutive errors: {self.consecutive_errors}"
        )


class KeyPoolManager:
    def __init__(self, api_keys: List[str]):
        self.keys: List[KeyState] = [KeyState(key, idx) for idx, key in enumerate(api_keys)]
        self._current_index: int = 0
        self.dispatcher = ToolDispatcher(self)
        logger.info(f"Initialized KeyPool with {len(self.keys)} active API project key(s).")

    def _get_next_available_key(self) -> Optional[KeyState]:
        if not self.keys:
            return None
        
        num_keys = len(self.keys)
        for i in range(num_keys):
            idx = (self._current_index + i) % num_keys
            key_state = self.keys[idx]
            if key_state.is_available():
                self._current_index = (idx + 1) % num_keys
                return key_state
        
        soonest_key = min(self.keys, key=lambda k: k.cooldown_until)
        return soonest_key

    async def generate_with_tools_stream(
        self,
        contents: List[Any],
        target_model: str,
        discord_context: DiscordToolsContext,
        thought_session: Any,
        system_instruction: str = config.SYSTEM_INSTRUCTION,
        max_tool_turns: int = 5
    ) -> AsyncGenerator[Tuple[str, str, str], None]:
        if target_model in config.FULL_FALLBACK_CASCADE:
            start_idx = config.FULL_FALLBACK_CASCADE.index(target_model)
            cascade = config.FULL_FALLBACK_CASCADE[start_idx:] + config.FULL_FALLBACK_CASCADE[:start_idx]
        else:
            cascade = config.FULL_FALLBACK_CASCADE

        last_error: Optional[Exception] = None

        initial_user_parts = []
        for item in contents:
            if isinstance(item, str):
                initial_user_parts.append(types.Part.from_text(text=item))
            elif isinstance(item, types.Part):
                initial_user_parts.append(item)
            else:
                initial_user_parts.append(item)

        base_contents = [
            types.Content(role="user", parts=initial_user_parts)
        ]

        for model_name in cascade:
            for attempt in range(len(self.keys)):
                key_state = self._get_next_available_key()
                if not key_state:
                    await asyncio.sleep(1.0)
                    key_state = self.keys[0]

                working_contents = [
                    types.Content(role=c.role, parts=list(c.parts)) for c in base_contents
                ]

                try:
                    logger.info(f"Executing tool generation on '{model_name}' with Key #{key_state.index + 1}...")
                    has_emitted_text = False

                    for turn in range(max_tool_turns):
                        is_last_turn = (turn == max_tool_turns - 1)
                        current_tools = None if is_last_turn else GEMINI_TOOLS

                        gen_config = types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.7,
                            max_output_tokens=65536,
                            tools=current_tools
                        )

                        response_stream = await key_state.client.aio.models.generate_content_stream(
                            model=model_name,
                            contents=working_contents,
                            config=gen_config
                        )

                        function_calls_to_run = []
                        accumulated_model_parts = []

                        async for chunk in response_stream:
                            text_piece = ""
                            thought_piece = ""

                            if chunk.candidates and chunk.candidates[0].content:
                                for part in chunk.candidates[0].content.parts:
                                    accumulated_model_parts.append(part)
                                    if getattr(part, "thought", False):
                                        thought_piece += part.text or ""
                                    elif part.function_call:
                                        function_calls_to_run.append(part.function_call)
                                    elif part.text:
                                        text_piece += part.text

                            if not text_piece and chunk.text and not function_calls_to_run:
                                text_piece = chunk.text

                            if text_piece:
                                has_emitted_text = True

                            yield (text_piece, thought_piece, model_name)

                        if not function_calls_to_run:
                            key_state.mark_success()
                            return

                        working_contents.append(
                            types.Content(role="model", parts=accumulated_model_parts)
                        )

                        function_response_parts = []
                        for fcall in function_calls_to_run:
                            fname = fcall.name
                            fargs = dict(fcall.args) if fcall.args else {}

                            result = await self.dispatcher.dispatch(fname, fargs, discord_context)

                            thought_session.append_tool_event(fname, fargs, result)

                            function_response_parts.append(
                                types.Part.from_function_response(
                                    name=fname,
                                    response={"result": result}
                                )
                            )

                        working_contents.append(
                            types.Content(role="user", parts=function_response_parts)
                        )

                    if not has_emitted_text:
                        logger.info("Executing guaranteed final synthesis turn...")
                        gen_config = types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.7,
                            max_output_tokens=65536,
                            tools=None
                        )
                        response_stream = await key_state.client.aio.models.generate_content_stream(
                            model=model_name,
                            contents=working_contents,
                            config=gen_config
                        )
                        async for chunk in response_stream:
                            text_piece = ""
                            thought_piece = ""
                            if chunk.candidates and chunk.candidates[0].content:
                                for part in chunk.candidates[0].content.parts:
                                    if getattr(part, "thought", False):
                                        thought_piece += part.text or ""
                                    elif part.text:
                                        text_piece += part.text
                            if not text_piece and chunk.text:
                                text_piece = chunk.text
                            yield (text_piece, thought_piece, model_name)

                    key_state.mark_success()
                    return

                except Exception as e:
                    err_str = str(e).lower()
                    last_error = e

                    if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
                        logger.warning(f"Key #{key_state.index + 1} hit quota on model '{model_name}'.")
                        key_state.mark_rate_limited(base_cooldown=15.0)
                    elif "503" in err_str or "unavailable" in err_str:
                        logger.warning(f"Model '{model_name}' returned 503 on Key #{key_state.index + 1}.")
                        key_state.mark_rate_limited(base_cooldown=5.0)
                    else:
                        logger.error(f"Error on '{model_name}' with Key #{key_state.index + 1}: {e}")
                        key_state.mark_rate_limited(base_cooldown=2.0)

                    continue

            logger.warning(f"Model '{model_name}' exhausted across all keys. Stepping down in cascade...")

        raise RuntimeError(f"All models and keys in cascade failed. Last error: {last_error}")