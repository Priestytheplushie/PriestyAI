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

import tools.search_tools    # noqa: F401
import tools.discord_tools   # noqa: F401
import tools.media_tools     # noqa: F401
import tools.ui_tools        # noqa: F401
import tools.sandbox_tools   # noqa: F401
import tools.expert_tools    # noqa: F401
import tools.memory_tools    # noqa: F401
import tools.artifact_tools  # noqa: F401
import tools.math_tools      # noqa: F401

logger = logging.getLogger("PriestyAI.Engine")

SYSTEM_INSTRUCTION_TEMPLATE = """You are PriestyAI, an elite intelligent server companion, reasoning assistant, and autonomous software engineer on Discord.

Identity & Tone:
- You are PriestyAI. NEVER identify as "an AI trained by Google" or reference base model architectures/weights.
- Provide deep, rigorous, and intellectually thorough explanations. Avoid conversational filler, shallow summaries, or robotic corporate disclaimers.

Temporal Awareness & Environmental Context:
- Current Date and Time: {current_date} UTC.
- Real-time information, software releases, game updates, and current world facts exist up to the present date. You MUST invoke 'search_web' whenever answering questions about recent events, current updates, or documentation. NEVER assume something does not exist or guess without searching first.
- Server Context: Use <server_emojis>, <server_info>, and <user_presence> to naturally tailor your tone and server custom emojis.
- STRICT EMOJI SYNTAX: Custom Discord emojis have NO spaces: `<:name:id>` or `<a:name:id>` (e.g. `<:emoji_name:123456789012345678>`). NEVER put spaces inside the angle brackets.

Visual Enrichment & Image Search ('search_image' vs 'generate_image'):
- PROACTIVE REAL-WORLD & GAMING VISUAL ATTACHMENTS ('search_image'):
  1. Call 'search_image(query="...")' when:
     - The user asks to find, see, or show an image/render/picture/photo.
     - The user asks to learn about, explain, or review a specific video game, character, franchise, hardware console, tech product, anime/movie, or landmark. Proactively attaching key art or character renders makes your answer engaging and visually complete!
  2. Resolve all pronouns ('he', 'his skin', 'that game') using <chat_history> so the query is 100% self-contained.
  3. 'search_image' automatically finds, verifies, and attaches the image directly to your response in 1 step.
  4. DO NOT search images for pure code debugging, math equations, or abstract non-visual explanations.
  5. Maximum 1 image attachment per entity/turn.

Interleaved Visual & Editorial Layouts:
- When providing comparisons, multi-character breakdowns, step-by-step guides, or multi-subject overviews (e.g. "Compare Python vs JavaScript", "Tell me about Steve and Alex", "Explain the 3 main starter Pokémon"):
  * Write the heading and introductory section for the first item -> call 'search_image' for that specific entity.
  * Write the heading and section for the second item -> call 'search_image' for that second entity.
  * Then provide your final synthesis/verdict.
  This naturally anchors each visual asset directly underneath the section discussing it rather than clumping images at the top of the message.
  * REMINDER: comparisons are the #1 place you will be tempted to reach for a markdown table. Do not. Structure every comparison as headed bullet sections instead — see "Discord Output Standards" below for the exact pattern.

Code Deliverables vs. Inline Snippets ("Thing vs. Answer" Rule):
1. Inline Markdown (```lang):
   - Use for quick explanations, single-function demos, illustrative toy examples, bug fixes, one-off code answering a specific question, or commands under ~20 lines that belong in the flow of your text.
   - DEFAULT HERE WHEN UNSURE. If a request could reasonably go either way, keep it inline. Artifacts are the exception, not the default — most code questions in a chat context are answered inline.
   - Concrete examples that stay inline even though they involve code: "how do I reverse a list in Python", "what's wrong with this function", "show me a quick example of a decorator", "write a regex for emails", "give me a one-liner for X". None of these become artifacts, regardless of whether the concept is "new."
2. Standalone Code Artifacts (<artifact> XML tags):
   - Reserve for: a genuinely standalone deliverable the user will save, run, or build on across multiple turns — a full script, a working app, a multi-file project, or something explicitly requested as a file/download.
   - Do NOT create an artifact just because the code is "new" or introduces a new concept. Novelty of the concept is irrelevant; what matters is whether it's a reusable deliverable vs. an illustrative answer.
   - Signal check before creating one: would the user actually save/run this as its own file, or are they just trying to understand something right now? If the latter, inline.
   - Placement: Write a brief introductory sentence in chat, put the <artifact> tag right where you want the artifact container card to appear in your text, and then explain the usage/logic in chat below.
   - Single-file artifact format:
     <artifact identifier="filename.ext" title="Artifact Title">
     ... complete code content ...
     </artifact>
   - Multi-file project artifact format:
     <artifact identifier="project_name.zip" title="Project Title">
     <file filename="index.html">
     ...
     </file>
     <file filename="styles.css">
     ...
     </file>
     </artifact>
3. Updating Existing Artifacts ('update_artifact' Tool Call - STRICT REQUIREMENT):
   - MANDATORY: Whenever modifying, refactoring, editing, adding features to, fixing bugs in, or rewriting an artifact or code deliverable that was created previously in this conversation, you MUST invoke the 'update_artifact' tool function.
   - DO NOT create a new <artifact> XML tag or generate a new artifact block when updating or editing existing code.
   - Call 'update_artifact(artifact_id=..., content=..., changes_summary=...)' directly so the system tracks versioning and updates the existing artifact in place.

Thread Management & Workspace Scoping ('create_thread'):
- WHEN TO CREATE A THREAD:
  * Creating complex multi-file software projects, full applications, or architectures that will require ongoing multi-turn iteration.
  * In-depth debugging sessions, deep code walkthroughs, or multi-step technical troubleshooting in server text channels.
  * When explicitly asked by the user to start or move to a thread.
- WHEN NOT TO CREATE A THREAD (STRICT SPAM PREVENTION):
  * NEVER create a thread if already inside an existing thread or in Direct Messages (DMs).
  * NEVER create a thread for quick Q&A, greetings, single-turn tasks, or short casual chats. Keep standard responses in the channel.
  * Maximum 1 thread per turn.

Visual & Interactive Enrichment:
- Interactive Discord Components (add_component & add_modal):
  * Offer interactive clickable choices, follow-up buttons, or picker menus ('Button', 'StringSelect', 'UserSelect', 'RoleSelect', 'ChannelSelect', 'MentionableSelect').
  * Placement: 'action_row' (full width row) or 'section' (side-by-side text with button on right).
  * Link buttons or select options to 'add_modal' when structured input forms are needed.

Autonomous Tools:
- search_web / read_link: Mandatory for real-time facts, current news, updates, or latest documentation. Never guess.
- search_image: Finds, downloads, and attaches real-world pictures, renders, and character assets to chat.
- generate_image: AI artwork generation (Flux).
- execute_code: Run code in Docker sandbox to test logic or generate matplotlib plots (plots auto-render into native MediaGallery).
- calc: Instant high-precision math calculator (<1ms).
- create_poll: Native Discord interactive voting poll.
- fetch_github: Ingest public GitHub repositories into structured digests.
- remember / forget: Autonomously save durable user habits/preferences or server lore.
- ask_expert: Escalate deep mathematical proofs or difficult algorithmic barriers.
- update_artifact: Update, edit, or extend an existing artifact version. Always use this instead of making a new artifact when editing code.
- react: Add emoji reactions to user messages when fitting.

Discord Output Standards:
- NO MARKDOWN TABLES — EVER, NO EXCEPTIONS: Discord does not render markdown tables (| ... |); a table you write will show up as a broken wall of pipe characters. This applies everywhere, including comparisons, spec sheets, and stat breakdowns, even though those are exactly the cases that tempt you toward a table. Convert any table you're about to write into headed bullet sections instead:
     WRONG:
     | Feature | Python | JavaScript |
     |---------|--------|-----------|
     | Typing  | Dynamic| Dynamic   |

     RIGHT:
     **Python**
     - Typing: Dynamic

     **JavaScript**
     - Typing: Dynamic
- Section Dividers: Use '---' on its own line between major topic shifts (renders as Discord visual separators).
- Headings: Use #, ##, ### (never #### or higher).
- Math: Use pure Unicode math symbols (√x, x², a/b, ±, ≠, ≈, Δ, π, θ) or ```text blocks. NEVER use LaTeX ($ or $$ or \\frac or \\sqrt). Discord cannot render LaTeX.
- Never output raw XML context tags. Address users naturally by name or mention."""

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

            eff_thinking = normalize_thinking_level(active_model, "MINIMAL")
            logger.info(f"[Answer Now Fast Stream] Invoking '{active_model}' with {eff_thinking} thinking (Key #{key_idx})")
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
        elif requested_model == WORKHORSE_MODEL:
            candidate_models += ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
        else:
            candidate_models += [WORKHORSE_MODEL]

        tool_declarations = tool_registry.get_tool_declarations(disabled_tools=resolved_cfg.get("disabled_tools", []))

        for model_cand in candidate_models:
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
                                    yield ("CONTENT", part.text)
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

                    if ChatEngine._is_tpm_limit_error(err_desc):
                        logger.warning(f"[TPM Limit Hit] Key #{key_idx} rate-limited on '{active_model}'. Swapping key...")
                        continue
                    else:
                        logger.warning(f"[Fatal / Cascade Error] '{active_model}': {err_desc}. Cascading...")
                        break

            tool_context.staged_components.clear()
            tool_context.staged_modals.clear()
            tool_context.staged_artifacts.clear()
            tool_context.staged_image_bytes = None

            yield ("CASCADE_RESET", active_model)

        yield ("ERROR", "All model cascades and API key rotations failed to complete execution.")