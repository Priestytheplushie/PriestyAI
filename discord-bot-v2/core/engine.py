import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Any
from google.genai import types
from config.settings import WORKHORSE_MODEL, FLAGSHIP_MODELS
from core.client_manager import client_manager
from core.router import Router, RouteDecision
from core.memory_manager import memory_manager
from core.config_manager import config_manager
from tools.registry import tool_registry, ToolExecutionContext

import tools.search_tools   # noqa: F401
import tools.discord_tools  # noqa: F401
import tools.media_tools    # noqa: F401
import tools.ui_tools       # noqa: F401
import tools.sandbox_tools  # noqa: F401
import tools.expert_tools   # noqa: F401
import tools.memory_tools   # noqa: F401

logger = logging.getLogger("PriestyAI.Engine")

SYSTEM_INSTRUCTION_TEMPLATE = """
You are PriestyAI, an intelligent, perceptive, helpful, and natural AI assistant inside a Discord server.

Temporal Awareness:
- Current Date and Time: {current_date} UTC.
- Real-time information, gaming seasons, software updates, and current world facts exist up to the present date.

Server Awareness & Custom Emojis:
1. Server Custom Emojis: Look inside <server_emojis> in the context. Whenever appropriate and natural, use the server's custom emojis using their exact format (e.g., <:name:id> or <a:name:id>).
2. Presence & Activity Awareness: Check <user_presence> to see the user's status, platform (mobile/desktop), and activities (playing games, listening to Spotify, streaming). Feel free to make subtle, natural references when relevant.
3. Server Context: Use channel name, topic, and guild information from <server_info> to tailor your tone and knowledge.

Memory System & Personalization:
1. Long-Term Memory: You possess persistent long-term memory divided into 'user' (personal preferences, tech habits) and 'server' (guild lore, server rules).
2. Autonomous Storing: Use the 'remember' tool to store durable facts when users tell you personal details, coding preferences, or important server information.
3. Autonomous Forgetting: If a user states that a previous preference changed (e.g. "I switched to Linux", "I don't use React anymore"), use 'forget' to remove the outdated memory ID.
4. Recalled Context: When relevant memories are recalled in <recalled_memories>, use them naturally to personalize your answers without explicitly quoting the raw XML tags.

Autonomous Proactive Tool Directives:
1. Natural Reactions: You have full freedom to proactively react to user messages using the 'react' tool (e.g. laughing with 😂, agreeing with 👍, hyping with 🔥/🚀).
2. Silent Action Etiquette: If your sole action is reacting or executing a background task with no text needed, you do NOT need to write redundant messages like 'I have reacted!'.
3. Proactive History Search: If users reference past events or conversations in the channel without full context, proactively invoke 'read_message_history' or 'search_channel_history'.
4. Web Search: Always invoke 'search_web' whenever answering questions about recent events, current game updates, or facts you are not 100% sure of.
5. Code Execution: Use 'execute_code' to run Python, JavaScript, C++, Rust, Go, or Bash code in a secure Docker sandbox.
6. Reasoning Escalation: If you hit a reasoning wall on a difficult mathematical proof or algorithm, invoke 'ask_expert'.

STRICT DISCORD FORMATTING DIRECTIVES:
1. NO TABLES: Discord does NOT render Markdown tables (| ... | ... |). NEVER output markdown tables. Instead, use clean bullet points (- **Item**: Description) or numbered lists.
2. NO HORIZONTAL DIVIDERS: NEVER output horizontal rules like '---', '***', or '___'. Use clean blank lines and bold headers for section breaks.
3. HEADINGS: Only use Discord-supported markdown headings (# , ## , ### ). NEVER use #### or #####.
4. NO LATEX: Discord does NOT render LaTeX ($ or $$). NEVER use LaTeX equations (\\frac, \\sqrt). Always write math using standard Unicode symbols (e.g. x², √x, ±, ≈, ≠, π, θ) or enclose multi-line calculations in formatted ```text code blocks.
5. Directness: Never output raw XML context tags. Address users by display name, preferred name, or mention format (<@user_id>).
"""

class ChatEngine:
    @staticmethod
    async def get_recalled_memories_data(user_id: int | str, guild_id: int | str | None, prompt: str | list[Any]) -> dict[str, Any]:
        try:
            if isinstance(prompt, list):
                text_query = " ".join([p for p in prompt if isinstance(p, str)]).strip()
            else:
                text_query = str(prompt).strip()

            if not text_query:
                return {"user_memories": [], "server_lore": []}

            return await memory_manager.recall_relevant_memories(
                query=text_query,
                user_id=user_id,
                guild_id=guild_id,
                top_k=3
            )
        except Exception as e:
            logger.warning(f"Failed to recall memories: {e}")
            return {"user_memories": [], "server_lore": []}

    @staticmethod
    def _is_tpm_limit_error(error_str: str) -> bool:
        err_lower = error_str.lower()
        if "429" in err_lower or "resource_exhausted" in err_lower:
            if "requests_per_day" in err_lower or "rpd" in err_lower or "daily quota" in err_lower:
                return False
            return True
        return False

    @staticmethod
    async def stream_fast_answer(
        conversation_contents: list[types.Content],
        formatted_system_prompt: str,
        tool_declarations: list[types.Tool],
        tool_context: ToolExecutionContext
    ) -> AsyncGenerator[tuple[str, Any], None]:
        fast_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", WORKHORSE_MODEL]
        for model_name in fast_models:
            client, key_idx, active_model = client_manager.get_client_for_model(model_name)
            if client is None:
                continue

            logger.info(f"[Answer Now Fast Stream] Invoking '{active_model}' with minimal thinking (Key #{key_idx})")
            try:
                for tool_turn in range(5):
                    config = types.GenerateContentConfig(
                        system_instruction=formatted_system_prompt,
                        thinking_config=types.ThinkingConfig(
                            thinking_level="MINIMAL",
                            include_thoughts=False
                        ),
                        tools=tool_declarations,
                        temperature=0.4
                    )

                    response_stream = await client.aio.models.generate_content_stream(
                        model=active_model,
                        contents=conversation_contents,
                        config=config
                    )

                    tool_calls_to_execute = []
                    model_parts: list[types.Part] = []

                    async for chunk in response_stream:
                        if chunk.candidates and chunk.candidates[0].content:
                            for part in chunk.candidates[0].content.parts:
                                model_parts.append(part)
                                if part.text:
                                    yield ("CONTENT", part.text)
                                elif part.function_call:
                                    tool_calls_to_execute.append(part.function_call)

                    if model_parts:
                        conversation_contents.append(types.Content(role="model", parts=model_parts))

                    if not tool_calls_to_execute:
                        return

                    function_response_parts: list[types.Part] = []
                    for call in tool_calls_to_execute:
                        call_name = call.name
                        call_args = dict(call.args) if call.args else {}
                        yield ("TOOL_START", {"name": call_name, "args": call_args})
                        tool_result = await tool_registry.execute(call_name, call_args, tool_context)
                        yield ("TOOL_END", {"name": call_name, "args": call_args, "result": tool_result})
                        function_response_parts.append(
                            types.Part(
                                function_response=types.FunctionResponse(
                                    name=call_name,
                                    response=tool_result
                                )
                            )
                        )

                    conversation_contents.append(types.Content(role="user", parts=function_response_parts))

                return
            except Exception as e:
                client_manager.report_error(key_idx, active_model, e)
                logger.warning(f"Fast stream fail on '{active_model}': {e}")

        yield ("ERROR", "Fast answer generation failed across all available keys.")

    @staticmethod
    async def stream_chat(
        prompt: str | list[Any],
        context_xml: str,
        bot_user_id: int,
        tool_context: ToolExecutionContext,
        answer_now_event: asyncio.Event | None = None
    ) -> AsyncGenerator[tuple[str, Any], None]:
        author_id = tool_context.author.id if tool_context.author else None
        guild_id = tool_context.guild.id if tool_context.guild else None
        channel_id = getattr(tool_context.channel, "id", None)

        resolved_cfg = config_manager.resolve_effective_config(guild_id, channel_id, author_id)

        has_media = False
        if isinstance(prompt, list):
            text_prompt = " ".join([p for p in prompt if isinstance(p, str)]).strip()
            has_media = any(isinstance(p, types.Part) or not isinstance(p, str) for p in prompt)
        else:
            text_prompt = str(prompt).strip()

        decision = await Router.route(
            user_prompt=text_prompt,
            context_summary=context_xml[:1000],
            has_media=has_media
        )
        yield ("ROUTED", decision)

        current_date_str = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")
        
        custom_instructions = resolved_cfg.get("combined_system_prompt", "")
        preferred_name_note = f"\nThe user's preferred name is '{resolved_cfg['preferred_name']}'. Address them by this name." if resolved_cfg.get("preferred_name") else ""
        
        formatted_system_prompt = (
            SYSTEM_INSTRUCTION_TEMPLATE
            .replace("{current_date}", current_date_str)
            .replace("<@BOT_ID>", f"<@{bot_user_id}>")
        )
        if custom_instructions:
            formatted_system_prompt += f"\n\n[Active Server/Channel Directives]:\n{custom_instructions}"
        if preferred_name_note:
            formatted_system_prompt += preferred_name_note

        user_mems, server_mems = [], []
        if resolved_cfg["user_memory_policy"] != "disabled" or resolved_cfg["server_lore_policy"] != "disabled":
            memories_data = await ChatEngine.get_recalled_memories_data(author_id or 0, guild_id, prompt)
            if resolved_cfg["user_memory_policy"] != "disabled":
                user_mems = memories_data.get("user_memories", [])
            if resolved_cfg["server_lore_policy"] != "disabled":
                server_mems = memories_data.get("server_lore", [])

        total_recalled = len(user_mems) + len(server_mems)
        if total_recalled > 0:
            yield ("RECALLED_MEMORIES", {
                "count": total_recalled,
                "user_memories": user_mems,
                "server_lore": server_mems
            })

        recalled_xml = ""
        if total_recalled > 0:
            lines = ["<recalled_memories>"]
            for m in user_mems:
                lines.append(f'  <user_memory id="{m["id"]}" similarity="{m["similarity"]:.2f}">{m["text"]}</user_memory>')
            for m in server_mems:
                lines.append(f'  <server_lore id="{m["id"]}" similarity="{m["similarity"]:.2f}">{m["text"]}</server_lore>')
            lines.append("</recalled_memories>")
            recalled_xml = "\n".join(lines)

        turn_parts: list[types.Part] = []
        prefix_text = ""
        if recalled_xml:
            prefix_text += f"{recalled_xml}\n\n"
        prefix_text += f"{context_xml}\n\n<current_turn user_id=\"current\">\n"
        turn_parts.append(types.Part(text=prefix_text))

        if isinstance(prompt, list):
            for item in prompt:
                if isinstance(item, types.Part):
                    turn_parts.append(item)
                elif isinstance(item, str) and item:
                    turn_parts.append(types.Part(text=item))
        else:
            turn_parts.append(types.Part(text=str(prompt)))

        turn_parts.append(types.Part(text="\n</current_turn>"))

        requested_model = decision.target_model
        thinking_level = decision.thinking_level
        if resolved_cfg.get("reasoning_level") and resolved_cfg["reasoning_level"] != "AUTO":
            thinking_level = resolved_cfg["reasoning_level"]

        candidate_models = [requested_model]
        if requested_model in FLAGSHIP_MODELS:
            candidate_models += ["gemini-3.6-flash", "gemini-3.5-flash", WORKHORSE_MODEL]
        elif requested_model != WORKHORSE_MODEL:
            candidate_models += [WORKHORSE_MODEL]

        tool_declarations = tool_registry.get_tool_declarations()

        for model_cand in candidate_models:
            conversation_contents: list[types.Content] = [
                types.Content(
                    role="user",
                    parts=turn_parts
                )
            ]

            eff_thinking = thinking_level
            if model_cand == WORKHORSE_MODEL and eff_thinking in ["LOW", "MEDIUM"]:
                eff_thinking = "HIGH"

            attempted_keys: set[int] = set()

            while True:
                if answer_now_event and answer_now_event.is_set():
                    logger.info("[Answer Now Intercept] Switching to fast generator stream.")
                    async for event in ChatEngine.stream_fast_answer(
                        conversation_contents=conversation_contents,
                        formatted_system_prompt=formatted_system_prompt,
                        tool_declarations=tool_declarations,
                        tool_context=tool_context
                    ):
                        yield event
                    return

                client, key_idx, active_model = client_manager.get_client_for_model(
                    model_cand, 
                    exclude_keys=attempted_keys
                )
                
                if client is None or key_idx in attempted_keys:
                    logger.warning(f"Exhausted all available keys for model '{model_cand}'. Cascading to next candidate...")
                    break

                attempted_keys.add(key_idx)
                logger.info(f"Stream generating on '{active_model}' (Key #{key_idx}, Thinking: {eff_thinking})")

                try:
                    for tool_turn in range(10):
                        if answer_now_event and answer_now_event.is_set():
                            logger.info("[Answer Now Intercept] Aborting active thinking and switching to fast stream.")
                            async for event in ChatEngine.stream_fast_answer(
                                conversation_contents=conversation_contents,
                                formatted_system_prompt=formatted_system_prompt,
                                tool_declarations=tool_declarations,
                                tool_context=tool_context
                            ):
                                yield event
                            return

                        config = types.GenerateContentConfig(
                            system_instruction=formatted_system_prompt,
                            thinking_config=types.ThinkingConfig(
                                thinking_level=eff_thinking,
                                include_thoughts=True
                            ),
                            tools=tool_declarations,
                            temperature=0.7
                        )

                        response_stream = await client.aio.models.generate_content_stream(
                            model=active_model,
                            contents=conversation_contents,
                            config=config
                        )

                        stream_iter = response_stream.__aiter__()
                        first_chunk = await asyncio.wait_for(stream_iter.__anext__(), timeout=18.0)

                        model_parts: list[types.Part] = []
                        tool_calls_to_execute = []

                        if first_chunk.candidates and first_chunk.candidates[0].content:
                            for part in first_chunk.candidates[0].content.parts:
                                model_parts.append(part)
                                if getattr(part, 'thought', False) and part.text:
                                    yield ("THOUGHT", part.text)
                                elif part.text:
                                    yield ("CONTENT", part.text)
                                elif part.function_call:
                                    tool_calls_to_execute.append(part.function_call)

                        async for chunk in stream_iter:
                            if answer_now_event and answer_now_event.is_set():
                                logger.info("[Answer Now Intercept] Cutting stream for instant response.")
                                async for event in ChatEngine.stream_fast_answer(
                                    conversation_contents=conversation_contents,
                                    formatted_system_prompt=formatted_system_prompt,
                                    tool_declarations=tool_declarations,
                                    tool_context=tool_context
                                ):
                                    yield event
                                return

                            if chunk.candidates and chunk.candidates[0].content:
                                for part in chunk.candidates[0].content.parts:
                                    model_parts.append(part)
                                    if getattr(part, 'thought', False) and part.text:
                                        yield ("THOUGHT", part.text)
                                    elif part.text:
                                        yield ("CONTENT", part.text)
                                    elif part.function_call:
                                        tool_calls_to_execute.append(part.function_call)

                        if model_parts:
                            conversation_contents.append(types.Content(role="model", parts=model_parts))

                        if not tool_calls_to_execute:
                            return

                        function_response_parts: list[types.Part] = []
                        for call in tool_calls_to_execute:
                            call_name = call.name
                            call_args = dict(call.args) if call.args else {}

                            if call_name in resolved_cfg["disabled_tools"]:
                                tool_result = {"error": f"The tool '{call_name}' has been disabled by server/channel configuration."}
                            else:
                                yield ("TOOL_START", {"name": call_name, "args": call_args})
                                tool_result = await tool_registry.execute(call_name, call_args, tool_context)
                                yield ("TOOL_END", {"name": call_name, "args": call_args, "result": tool_result})

                            function_response_parts.append(
                                types.Part(
                                    function_response=types.FunctionResponse(
                                        name=call_name,
                                        response=tool_result
                                    )
                                )
                            )

                        conversation_contents.append(types.Content(role="user", parts=function_response_parts))

                    return

                except (asyncio.TimeoutError, Exception) as e:
                    err_desc = "First token timeout (>18s)" if isinstance(e, asyncio.TimeoutError) else str(e)
                    client_manager.report_error(key_idx, active_model, Exception(err_desc))

                    if ChatEngine._is_tpm_limit_error(err_desc):
                        logger.warning(f"[TPM Limit Hit] Key #{key_idx} rate-limited on '{active_model}'. Swapping key...")
                        continue
                    else:
                        logger.warning(f"[Fatal / Cascade Error] '{active_model}': {err_desc}. Cascading...")
                        break

            tool_context.staged_components.clear()
            tool_context.staged_modals.clear()
            tool_context.staged_image_bytes = None

            yield ("CASCADE_RESET", active_model)

        yield ("ERROR", "All model cascades and API key rotations failed to complete execution.")