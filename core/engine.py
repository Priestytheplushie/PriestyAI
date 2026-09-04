import re
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Any
from google.genai import types
from config.settings import (
    WORKHORSE_DENSE_MODEL,
    WORKHORSE_MOE_MODEL,
    FLAGSHIP_MODELS,
    LITE_MODELS
)
from core.client_manager import client_manager
from core.router import Router, RouteDecision
from core.memory_manager import memory_manager
from core.config_manager import config_manager
from core.custom_tool_manager import custom_tool_manager
from tools.registry import tool_registry, ToolExecutionContext

import tools.search_tools    # noqa: F401
import tools.github_tools    # noqa: F401
import tools.discord_tools   # noqa: F401
import tools.media_tools     # noqa: F401
import tools.ui_tools        # noqa: F401
import tools.sandbox_tools   # noqa: F401
import tools.expert_tools    # noqa: F401
import tools.memory_tools    # noqa: F401
import tools.math_tools      # noqa: F401

logger = logging.getLogger("PriestyAI.Engine")

SYSTEM_INSTRUCTION_TEMPLATE = """You are PriestyAI, an elite intelligent server companion, reasoning assistant, and autonomous software engineer on Discord.

Identity & Tone:
- You are PriestyAI. NEVER identify as "an AI trained by Google" or reference base model architectures/weights.
- Provide deep, rigorous, and intellectually thorough explanations. Avoid conversational filler, shallow summaries, or robotic corporate disclaimers.

CRITICAL TEMPORAL GROUNDING & CURRENT REAL-TIME TIMELINE:
- Current Real-World Date and Time: {current_date} UTC (Current Year: {current_year}).
- STRICT TEMPORAL RULES:
  1. The real-world year is {current_year}. You are actively functioning in real time in {current_year}.
  2. NEVER claim that {current_year} (or recent years like 2024, 2025) is "the future" or that you "cannot predict future events".
  3. Events, software updates, game seasons, hardware releases, and news from 2024, 2025, and {current_year} are in the PRESENT or PAST, NOT the future.
  4. Real-time information, software releases, game updates, and world facts exist up to the present date ({current_date}). You MUST invoke 'search_web' whenever answering questions about recent events, current updates, or documentation. NEVER assume something does not exist or guess without searching first.
  5. When formulating 'search_web' queries, query with active present-day awareness up to {current_year}. Do not bias queries into assuming modern software or events are unreleased.
- Server Context: Use <server_emojis>, <server_info>, and <user_presence> to naturally tailor your tone and server custom emojis.
- STRICT EMOJI SYNTAX: Custom Discord emojis have NO spaces: `<:name:id>` or `<a:name:id>` (e.g. `<:emoji_name:123456789012345678>`). NEVER put spaces inside the angle brackets.

CRITICAL CONVERSATIONAL FOCUS & TOPIC ISOLATION RULES:
- Focus 100% of your attention on the user's latest prompt inside `<current_turn>`.
- STRICT NO TOPIC BLEED: Treat every new query as an independent topic. Do NOT drag, reference, or awkwardly merge previous subjects from `<chat_history>` into your response when the user changes the topic.
- Only reference earlier messages in `<chat_history>` when the user explicitly asks about them or uses referential pronouns (e.g., "what about that?", "continue the previous code", "as we discussed earlier").
- If the user was previously talking about Topic A and is now asking about Topic B, answer Topic B cleanly and directly without mentioning Topic A.

Discord-Flavored Markdown (DFM) & Formatting Standards:
- Callout Alerts: Use GitHub alert syntax (`> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]`) SPARINGLY and with PURPOSE—maximum 1 (or 2) per response for critical gotchas, warnings, or major takeaways. Do NOT wrap standard narrative paragraphs or routine explanations in alert boxes.
- NO MARKDOWN PIPE TABLES (`| ... |`): Discord does not render markdown tables. Structure all comparisons, spec sheets, data overviews, and feature lists as clean bulleted lists with bold keys and backtick pills (e.g. `• `Item` — Description` or `**Subject**\n• **Key:** Value`).
- Task Lists: Use `- [ ]` and `- [x]` for checklists. They compile into custom styled checkboxes.
- Section Dividers: Use '---' on its own line between major topic shifts (renders as Discord visual separators).
- Headings: Use #, ##, ### (never #### or higher).
- Math: Use pure Unicode math symbols (√x, x², a/b, ±, ≠, ≈, Δ, π, θ) or ```text blocks. NEVER use LaTeX ($ or $$ or \\frac or \\sqrt). Discord cannot render LaTeX.

Interactive Form Submissions (<interaction_event>):
- When receiving `<interaction_event type="modal_submit">` or `<interaction_event type="select_option">`, the user has submitted an interactive form or dropdown selection.
- The payload contains the submitted field values with BOTH the unique IDs and human-readable names of any selected roles, channels, users, or dropdown options (e.g. `{"id": "...", "name": "...", "type": "..."}`).
- Prioritize human-readable names and Discord mentions by default (e.g. `• **Selected Role:** @RoleName`, `• **Selected Channel:** #channel-name`, `• **Mentionable Target:** @Username`). Only include numeric IDs if the user explicitly asks for IDs or technical details.
- NEVER output bare, unlabelled IDs alone without their corresponding names.

Interactive Quizzes & Knowledge Checks (<quiz> tags):
- When the user explicitly asks to be quizzed, tested, or given trivia on a topic (e.g. "quiz me on Python", "make a quiz", "test my knowledge on Docker"):
  * Emit an interactive `<quiz title="..." topic="..." difficulty="...">` XML block.
  * Default to generating exactly 10 questions unless the user requested a specific count (e.g. 5 questions).
  * MULTIPLE CHOICE QUESTIONS: 4 options with exactly ONE correct answer (`correct="true"`).
  * TRUE / FALSE QUESTIONS: 2 options (`<option text="True" ... />` and `<option text="False" ... />`) with exactly ONE correct answer (`correct="true"`). You may mix Multiple Choice and True/False questions in the same quiz!
  * CONCISE EXPLANATIONS: Keep every `<option>` explanation to exactly 1 crisp, factual sentence (max 15-20 words). Do NOT write multi-sentence or rambling explanations inside `<option>` tags.
  * RANDOMIZE CORRECT ANSWERS: Randomly distribute correct answers across all positions. NEVER make every answer the first option!
  * Structure Example:
    <quiz title="Algorithms & Systems Quiz" topic="Computer Science" difficulty="Medium">
      <question text="What is the worst-case time complexity of quicksort with standard pivot selection?" category="Algorithms">
        <option text="O(n log n)" explanation="O(n log n) is the average-case time complexity, not worst case." />
        <option text="O(n²)" correct="true" explanation="Unbalanced partitions cause quadratic degradation in worst-case scenarios." />
        <option text="O(log n)" explanation="O(log n) is the stack depth space complexity." />
        <option text="O(n)" explanation="Comparison-based sorting cannot run in linear time." />
      </question>
      <question text="TCP provides reliable, ordered, and error-checked delivery of a stream of bytes." category="Networking">
        <option text="True" correct="true" explanation="TCP guarantees in-order delivery and retransmits lost packets via sequence numbers." />
        <option text="False" explanation="UDP is connectionless and unordered, whereas TCP guarantees reliable delivery." />
      </question>
    </quiz>
  * Always generate original, dynamic questions tailored specifically to the user's requested topic and difficulty.
- STRICT RULE: ONLY emit `<quiz>` tags when the user explicitly requests a quiz, exam, test, knowledge check, or trivia. Do NOT emit quizzes for general informational questions.

Active Artifacts & Code Deliverables ("Canvas & Artifacts"):
1. LIVE ARTIFACT EXECUTION vs. EXPLAINING/TEACHING ARTIFACTS:
   - CREATING LIVE DELIVERABLES (Action Mode):
     When creating a functional file, script, multi-file project, or markdown document canvas for the user, emit `<artifact identifier="...">...</artifact>` directly outside of any markdown code blocks. The system will intercept this tag, compile it into an interactive UI card, and provide a live download/canvas playground.
   - EXPLAINING / TEACHING ARTIFACT SYNTAX (Documentation Mode):
     When the user asks you to explain, demonstrate, or teach how artifacts, XML tags, or PriestyAI's features work:
     * ALWAYS wrap any example `<artifact>` or `<followup>` tags inside markdown code blocks (e.g. ```xml or ```markdown) or inline backticks (`<artifact>`).
     * NEVER emit raw, unescaped `<artifact>` tags when you are only showing an example or giving documentation.

2. Contextual Awareness & Updating Existing Artifacts:
   - All active deliverables previously generated in this conversation are provided in the <active_artifacts> context block.
   - When modifying an existing deliverable, read the code from <active_artifacts> and re-emit `<artifact identifier="same_filename.ext" title="Artifact Title">` with complete updated code.

Suggested Follow-up Action Buttons (<followup> tags):
- When concluding a complex explanation or deliverable, you MAY provide 1 to 3 suggested follow-up actions as buttons:
  <followup label="Short Button Label">Detailed, self-contained prompt to execute when clicked</followup>
- Maximum 3 follow-ups per response at the very end of your message.

Autonomous Tools:
- github_repo: Deep GitHub repository analysis, file reading, code searching, commit logs, PR diffs, and project digests.
- search_web / read_link: Mandatory for real-time facts, current news, updates, or latest documentation. Never guess.
- search_image / search_gif: Visual asset search and GIF reactions.
- edit_image / generate_image: Local diffusion image editing and text-to-image.
- execute_code: Run code in Docker sandbox to test logic.
- calc: Instant high-precision math calculator.
- create_poll: Native Discord interactive voting poll.
- remember / forget: Autonomously save durable user habits/preferences or server lore.
- ask_expert: Escalate deep mathematical proofs or difficult algorithmic barriers.
- react: Add emoji reactions to user messages when fitting.

Never output raw XML context tags. Address users naturally by name or mention."""

def normalize_thinking_level(model_name: str, requested_level: str) -> str:
    level = requested_level.upper().strip()

    if "3.7-flash" in model_name:
        if level in ["MINIMAL", "OFF"]:
            return "LOW"
        return level if level in ["LOW", "MEDIUM", "HIGH"] else "MEDIUM"

    if "gemma" in model_name:
        if level in ["LOW", "MEDIUM"]:
            return "HIGH"
        return level if level in ["MINIMAL", "HIGH"] else "HIGH"

    return level if level in ["MINIMAL", "LOW", "MEDIUM", "HIGH"] else "MEDIUM"

class SyntheticFunctionCall:
    def __init__(self, name: str, args: dict[str, Any]):
        self.name = name
        self.args = args

def extract_and_strip_leaked_calls(text: str) -> tuple[str, list[SyntheticFunctionCall]]:
    call_pattern = r'<call:(?:default_api:)?([a-zA-Z0-9_]+)\s*\{([^}]*)\}\s*(?:\/>|>)'
    matches = list(re.finditer(call_pattern, text))
    if not matches:
        return text, []

    synthetic_calls = []
    for m in matches:
        func_name = m.group(1).strip()
        raw_body = m.group(2).strip()
        args: dict[str, Any] = {}

        if raw_body:
            try:
                args = json.loads("{" + raw_body + "}")
            except Exception:
                pairs = re.findall(r'([a-zA-Z0-9_]+)\s*:\s*([^,]+?)(?=(?:,[a-zA-Z0-9_]+\s*:|$))', raw_body)
                for k, v in pairs:
                    args[k.strip()] = v.strip().strip('"\'')

        synthetic_calls.append(SyntheticFunctionCall(name=func_name, args=args))
        logger.info(f"[Engine Recovery] Intercepted leaked raw tool call '{func_name}' with args: {args}")

    cleaned_text = re.sub(call_pattern, '', text).strip()
    return cleaned_text, synthetic_calls

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
    def _is_retryable_error(error_str: str) -> bool:
        err_lower = error_str.lower()
        retryable_keywords = [
            "429", "503", "500", "502", "504",
            "unavailable", "overloaded", "resource_exhausted",
            "timeout", "timed out", "internal server error", "connection reset"
        ]
        return any(kw in err_lower for kw in retryable_keywords)

    @staticmethod
    async def stream_fast_answer(
        conversation_contents: list[types.Content],
        formatted_system_prompt: str,
        tool_declarations: list[types.Tool],
        tool_context: ToolExecutionContext,
        custom_tools_map: dict[str, dict[str, Any]] | None = None
    ) -> AsyncGenerator[tuple[str, Any], None]:
        c_map = custom_tools_map or {}
        fast_models = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", WORKHORSE_MOE_MODEL, WORKHORSE_DENSE_MODEL]
        for model_name in fast_models:
            attempted_keys: set[int] = set()
            while True:
                client, key_idx, active_model = client_manager.get_client_for_model(
                    model_name,
                    exclude_keys=attempted_keys,
                    fallback=False
                )
                if client is None or key_idx in attempted_keys:
                    break

                attempted_keys.add(key_idx)
                eff_thinking = normalize_thinking_level(active_model, "MINIMAL")
                logger.info(f"[Answer Now Fast Stream] Dispatched '{active_model}' with {eff_thinking} thinking (Key #{key_idx})")
                yield ("ACTIVE_MODEL", active_model)

                try:
                    for tool_turn in range(5):
                        config = types.GenerateContentConfig(
                            system_instruction=formatted_system_prompt,
                            thinking_config=types.ThinkingConfig(
                                thinking_level=eff_thinking,
                                include_thoughts=False
                            ),
                            tools=tool_declarations,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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
                                        clean_txt, leaked_calls = extract_and_strip_leaked_calls(part.text)
                                        if clean_txt:
                                            yield ("CONTENT", clean_txt)
                                        if leaked_calls:
                                            tool_calls_to_execute.extend(leaked_calls)
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

                            if call_name in c_map:
                                tool_result = await custom_tool_manager.execute_custom_tool(c_map[call_name], call_args)
                            else:
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
                    err_desc = str(e)
                    client_manager.report_error(key_idx, active_model, e)
                    logger.warning(f"Fast stream fail on '{active_model}' (Key #{key_idx}): {err_desc}")
                    if ChatEngine._is_retryable_error(err_desc):
                        continue
                    break

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

        now_utc = datetime.now(timezone.utc)
        current_date_str = now_utc.strftime("%A, %B %d, %Y")
        current_year_str = str(now_utc.year)

        custom_instructions = resolved_cfg.get("combined_system_prompt", "")
        preferred_name_note = f"\nThe user's preferred name is '{resolved_cfg['preferred_name']}'. Address them by this name." if resolved_cfg.get("preferred_name") else ""
        
        formatted_system_prompt = (
            SYSTEM_INSTRUCTION_TEMPLATE
            .replace("{current_date}", current_date_str)
            .replace("{current_year}", current_year_str)
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
            candidate_models += ["gemini-3.6-flash", "gemini-3.5-flash", WORKHORSE_DENSE_MODEL, WORKHORSE_MOE_MODEL, "gemini-3.5-flash-lite"]
        elif requested_model == WORKHORSE_DENSE_MODEL:
            candidate_models += [WORKHORSE_MOE_MODEL, "gemini-3.5-flash", "gemini-3.5-flash-lite"]
        elif requested_model == WORKHORSE_MOE_MODEL:
            candidate_models += [WORKHORSE_DENSE_MODEL, "gemini-3.5-flash-lite", "gemini-3.5-flash"]
        elif requested_model in LITE_MODELS:
            candidate_models += ["gemini-3.1-flash-lite", WORKHORSE_MOE_MODEL, WORKHORSE_DENSE_MODEL, "gemini-3.5-flash"]
        else:
            candidate_models += [WORKHORSE_DENSE_MODEL, WORKHORSE_MOE_MODEL, "gemini-3.5-flash-lite"]

        seen_cands = set()
        clean_candidate_models = []
        for m in candidate_models:
            if m not in seen_cands:
                seen_cands.add(m)
                clean_candidate_models.append(m)

        disabled_set = set(resolved_cfg.get("disabled_tools", []))
        standard_declarations = [
            d for d in tool_registry._declarations
            if d.name not in disabled_set
        ]

        allow_user_tools = bool(resolved_cfg.get("allow_user_custom_tools", True))
        active_custom_tools = custom_tool_manager.get_active_custom_tools(
            guild_id=guild_id,
            user_id=author_id,
            allow_user_tools=allow_user_tools
        )
        custom_tools_map = {t["name"]: t for t in active_custom_tools}

        custom_declarations = [
            custom_tool_manager.build_tool_declaration(t)
            for t in active_custom_tools
            if t["name"] not in disabled_set
        ]

        all_function_declarations = standard_declarations + custom_declarations
        tool_declarations = [types.Tool(function_declarations=all_function_declarations)] if all_function_declarations else []

        for model_cand in clean_candidate_models:
            conversation_contents: list[types.Content] = [
                types.Content(
                    role="user",
                    parts=turn_parts
                )
            ]

            eff_thinking = normalize_thinking_level(model_cand, thinking_level)
            attempted_keys: set[int] = set()

            while True:
                if answer_now_event and answer_now_event.is_set():
                    logger.info("[Answer Now Intercept] Instantly switching to fast stream generator.")
                    async for event in ChatEngine.stream_fast_answer(
                        conversation_contents=conversation_contents,
                        formatted_system_prompt=formatted_system_prompt,
                        tool_declarations=tool_declarations,
                        tool_context=tool_context,
                        custom_tools_map=custom_tools_map
                    ):
                        yield event
                    return

                client, key_idx, active_model = client_manager.get_client_for_model(
                    model_cand, 
                    exclude_keys=attempted_keys,
                    fallback=False
                )
                
                if client is None or key_idx in attempted_keys:
                    logger.warning(f"Exhausted all available keys for model '{model_cand}'. Cascading to next candidate in chain...")
                    break

                attempted_keys.add(key_idx)
                logger.info(f"Stream generating on '{active_model}' (Key #{key_idx}, Thinking: {eff_thinking})")
                yield ("ACTIVE_MODEL", active_model)

                try:
                    for tool_turn in range(10):
                        if answer_now_event and answer_now_event.is_set():
                            logger.info("[Answer Now Intercept] Aborting active thinking and switching to fast stream.")
                            async for event in ChatEngine.stream_fast_answer(
                                conversation_contents=conversation_contents,
                                formatted_system_prompt=formatted_system_prompt,
                                tool_declarations=tool_declarations,
                                tool_context=tool_context,
                                custom_tools_map=custom_tools_map
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
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
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
                        emitted_early_tools: set[str] = set()

                        if first_chunk.candidates and first_chunk.candidates[0].content:
                            for part in first_chunk.candidates[0].content.parts:
                                model_parts.append(part)
                                if getattr(part, 'thought', False) and part.text:
                                    yield ("THOUGHT", part.text)
                                elif part.text:
                                    clean_txt, leaked_calls = extract_and_strip_leaked_calls(part.text)
                                    if clean_txt:
                                        yield ("CONTENT", clean_txt)
                                    if leaked_calls:
                                        tool_calls_to_execute.extend(leaked_calls)
                                        for sc in leaked_calls:
                                            if sc.name not in emitted_early_tools:
                                                emitted_early_tools.add(sc.name)
                                                yield ("TOOL_START", {"name": sc.name, "args": sc.args})
                                elif part.function_call:
                                    tool_calls_to_execute.append(part.function_call)
                                    fn_name = getattr(part.function_call, 'name', '')
                                    if fn_name and fn_name not in emitted_early_tools:
                                        emitted_early_tools.add(fn_name)
                                        fn_args = dict(part.function_call.args) if getattr(part.function_call, 'args', None) else {}
                                        yield ("TOOL_START", {"name": fn_name, "args": fn_args})

                        async for chunk in stream_iter:
                            if answer_now_event and answer_now_event.is_set():
                                logger.info("[Answer Now Intercept] Cutting stream for instant response.")
                                async for event in ChatEngine.stream_fast_answer(
                                    conversation_contents=conversation_contents,
                                    formatted_system_prompt=formatted_system_prompt,
                                    tool_declarations=tool_declarations,
                                    tool_context=tool_context,
                                    custom_tools_map=custom_tools_map
                                ):
                                    yield event
                                return

                            if chunk.candidates and chunk.candidates[0].content:
                                for part in chunk.candidates[0].content.parts:
                                    model_parts.append(part)
                                    if getattr(part, 'thought', False) and part.text:
                                        yield ("THOUGHT", part.text)
                                    elif part.text:
                                        clean_txt, leaked_calls = extract_and_strip_leaked_calls(part.text)
                                        if clean_txt:
                                            yield ("CONTENT", clean_txt)
                                        if leaked_calls:
                                            tool_calls_to_execute.extend(leaked_calls)
                                            for sc in leaked_calls:
                                                if sc.name not in emitted_early_tools:
                                                    emitted_early_tools.add(sc.name)
                                                    yield ("TOOL_START", {"name": sc.name, "args": sc.args})
                                    elif part.function_call:
                                        tool_calls_to_execute.append(part.function_call)
                                        fn_name = getattr(part.function_call, 'name', '')
                                        if fn_name and fn_name not in emitted_early_tools:
                                            emitted_early_tools.add(fn_name)
                                            fn_args = dict(part.function_call.args) if getattr(part.function_call, 'args', None) else {}
                                            yield ("TOOL_START", {"name": fn_name, "args": fn_args})

                        if model_parts:
                            conversation_contents.append(types.Content(role="model", parts=model_parts))

                        if not tool_calls_to_execute:
                            return

                        function_response_parts: list[types.Part] = []
                        for call in tool_calls_to_execute:
                            call_name = call.name
                            call_args = dict(call.args) if call.args else {}

                            if call_name not in emitted_early_tools:
                                yield ("TOOL_START", {"name": call_name, "args": call_args})

                            if call_name in resolved_cfg["disabled_tools"]:
                                tool_result = {"error": f"The tool '{call_name}' has been disabled by server/channel configuration."}
                            elif call_name in custom_tools_map:
                                tool_result = await custom_tool_manager.execute_custom_tool(custom_tools_map[call_name], call_args)
                                yield ("TOOL_END", {"name": call_name, "args": call_args, "result": tool_result})
                            else:
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

                    if ChatEngine._is_retryable_error(err_desc):
                        logger.warning(f"[Transient Error on Key #{key_idx}] '{active_model}': {err_desc}. Trying next available key...")
                        continue
                    else:
                        logger.warning(f"[Fatal / Unsupported Config] '{active_model}': {err_desc}. Cascading to next candidate...")
                        break

            tool_context.staged_components.clear()
            tool_context.staged_modals.clear()
            tool_context.staged_artifacts.clear()
            tool_context.staged_image_bytes = None

            yield ("CASCADE_RESET", active_model)

        yield ("ERROR", "All model cascades and API key rotations failed to complete execution.")