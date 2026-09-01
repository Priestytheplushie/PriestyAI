import time
import json
import base64
import asyncio
import logging
import aiohttp
import re
from typing import Any, Callable
from datetime import datetime, timezone
import discord
from discord import app_commands
import pycountry
from google.genai import types

from config.settings import WORKHORSE_DENSE_MODEL
from core.engine import ChatEngine
from core.client_manager import client_manager
from core.memory_manager import memory_manager, get_user_chat_session_id
from core.config_manager import config_manager
from core.branch_manager import branch_manager
from core.moderation import (
    check_moderation,
    generate_friendly_refusal,
    log_moderation_violation,
    is_user_banned,
    ban_user
)
from handlers.stream_handler import (
    DiscordStreamDispatcher,
    apply_message_parsers,
    build_v2_message_layout,
    should_show_reply_button
)
from handlers.chat_handler import (
    format_placeholder_content,
    get_tool_subtext
)
from parsers.artifact_parser import ArtifactStreamParser
from parsers.markdown_parser import apply_dfm
from tools.registry import ToolExecutionContext
from ui.thought_container import PlaceholderLayoutView
from ui.modals import DynamicModalV2
from ui.onboarding_views import (
    build_welcome_terms_modal,
    LegalDocumentViewerLayoutView,
    BannedUserNoticeView
)

logger = logging.getLogger("PriestyAI.Commands.Chat")

POPULAR_SCRIBE_LANGUAGES = [
    "English",
    "Spanish",
    "Japanese",
    "French",
    "German",
    "Chinese (Mandarin)",
    "Korean",
    "Portuguese",
    "Russian",
    "Italian",
    "Arabic",
    "Hindi",
    "Dutch",
    "Polish",
    "Turkish",
    "Swedish",
    "Vietnamese",
    "Greek",
    "Hebrew",
    "Thai",
    "Indonesian",
    "Ukrainian",
    "Czech",
    "Danish",
    "Finnish"
]

DEFAULT_SCRIBE_STATUSES = [
    "Consulting linguistic archives",
    "Untangling grammatical nuances",
    "Channeling cultural idioms",
    "Polishing prose and punctuation",
    "Adapting tone and styling",
    "Brewing creative localization"
]

async def scribe_language_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    q = current.lower().strip()
    if not q:
        return [app_commands.Choice(name=lang, value=lang) for lang in POPULAR_SCRIBE_LANGUAGES[:25]]

    choices: list[app_commands.Choice[str]] = []
    seen: set[str] = set()

    for lang in POPULAR_SCRIBE_LANGUAGES:
        if q in lang.lower() and lang not in seen:
            choices.append(app_commands.Choice(name=lang, value=lang))
            seen.add(lang)

    for lang in pycountry.languages:
        name = getattr(lang, "name", None)
        if not name or name in seen:
            continue
        code_2 = getattr(lang, "alpha_2", "")
        code_3 = getattr(lang, "alpha_3", "")
        if q in name.lower() or q == code_2.lower() or q == code_3.lower():
            choices.append(app_commands.Choice(name=name, value=name))
            seen.add(name)
            if len(choices) >= 25:
                break

    return choices[:25]

async def generate_scribe_witty_statuses(content: str, target_lang: str, instructions: str) -> list[str]:
    client, _, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
    if not client:
        return DEFAULT_SCRIBE_STATUSES

    prompt = (
        f"Content sample: \"{content[:250]}\"\n"
        f"Target language: {target_lang}\n"
        f"Tone directives: \"{instructions[:150]}\"\n"
        "Generate 5 funny, witty, 3-5 word loading status messages about translating, localizing, or stylizing this text.\n"
        "Output ONLY a JSON array of strings, e.g. [\"Consulting Spanish dictionary\", \"Channeling Shakespearean slang\", ...]"
    )
    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=active_model,
                contents=prompt
            ),
            timeout=2.0
        )
        if res.text:
            cleaned = res.text.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```[a-zA-Z]*\n|\n```$", "", cleaned).strip()
            arr = json.loads(cleaned)
            if isinstance(arr, list) and arr:
                return [str(s).strip() for s in arr if str(s).strip()]
    except Exception as e:
        logger.debug(f"Failed to generate custom scribe witty statuses: {e}")

    return DEFAULT_SCRIBE_STATUSES

async def update_interaction_placeholder_loop(
    interaction: discord.Interaction,
    placeholder_view: PlaceholderLayoutView,
    statuses: list[str],
    get_active_subtext_func: Callable[[], str | None],
    start_time: float,
    stop_event: asyncio.Event,
    interval: float = 1.0,
    target_message: discord.Message | None = None
):
    idx = 0
    last_status_change = time.time()

    while not stop_event.is_set():
        try:
            await asyncio.sleep(interval)
            if stop_event.is_set():
                break

            now = time.time()
            if (now - last_status_change) >= 3.0 and statuses:
                idx = (idx + 1) % len(statuses)
                last_status_change = now

            current_status = statuses[idx % len(statuses)] if statuses else "Thinking"
            elapsed = max(0, int(now - start_time))
            subtext = get_active_subtext_func()
            full_text = format_placeholder_content(current_status, subtext)

            placeholder_view.update_state(full_text, elapsed)
            try:
                if target_message:
                    await target_message.edit(view=placeholder_view)
                else:
                    await interaction.edit_original_response(view=placeholder_view)
            except (discord.HTTPException, discord.NotFound):
                break
            await placeholder_view.push_live_update()
        except Exception:
            break

def build_user_chat_modal(on_submit: Callable[[discord.Interaction, dict[str, Any]], Any]) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": (
                "# Chat with PriestyAI\n"
                "Type your message or follow-up. Stored context is preserved across replies in this channel."
            )
        },
        {
            "type": "text_input",
            "custom_id": "prompt",
            "label": "Message / Reply",
            "description": "Your question, follow-up, or instructions",
            "placeholder": "Type your message or reply here...",
            "style": "paragraph",
            "required": True,
            "max_length": 3500
        },
        {
            "type": "file_upload",
            "custom_id": "attachments",
            "label": "Attachments",
            "description": "Upload code, screenshots, PDFs, or datasets (Optional)",
            "required": False,
            "max_values": 5
        },
        {
            "type": "mentionable_select",
            "custom_id": "channel_context",
            "label": "Channel Context / Participants",
            "description": "Select users or roles in this channel to feed context to PriestyAI (Optional)",
            "placeholder": "Tag participants in this conversation...",
            "required": False,
            "min_values": 0,
            "max_values": 10
        }
    ]

    return DynamicModalV2(
        title="Chat with PriestyAI",
        custom_id="modal_user_app_chat",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

def build_scribe_modal(on_submit: Callable[[discord.Interaction, dict[str, Any]], Any]) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": (
                "Translate, localize, and adapt text with custom creative tone and style instructions."
            )
        },
        {
            "type": "text_input",
            "custom_id": "content",
            "label": "Content to Scribe",
            "description": "The text you want to translate, adapt, or rewrite",
            "placeholder": "Paste or type the text here...",
            "style": "paragraph",
            "required": True,
            "max_length": 3500
        },
        {
            "type": "text_input",
            "custom_id": "instructions",
            "label": "Tone & Style Instructions",
            "description": "Specify tone, persona, dialect, or formatting constraints (Optional)",
            "placeholder": "e.g. Formal business email, casual gaming slang, 17th century pirate, preserve rhymes...",
            "style": "paragraph",
            "required": False,
            "max_length": 1500
        },
        {
            "type": "text_input",
            "custom_id": "target_language",
            "label": "Language",
            "description": "Target language to translate into",
            "placeholder": "e.g. English, Spanish, Japanese, German...",
            "style": "short",
            "required": False,
            "max_length": 60
        },
        {
            "type": "radio_group",
            "custom_id": "visibility",
            "label": "Visibility",
            "description": "Choose who can see the scribed output",
            "value": "public",
            "options": [
                {
                    "label": "Public",
                    "value": "public",
                    "description": "Visible to all members",
                    "default": True
                },
                {
                    "label": "Ephemeral",
                    "value": "private",
                    "description": "Only visible to you"
                }
            ],
            "required": True
        }
    ]

    return DynamicModalV2(
        title="Scribe with PriestyAI",
        custom_id="modal_scribe_generator",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

async def _execute_scribe(
    interaction: discord.Interaction,
    content: str,
    target_language: str = "English",
    instructions: str = "",
    is_ephemeral: bool = False
):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=is_ephemeral)

    clean_content = content.strip()
    clean_lang = (target_language or "English").strip()
    clean_inst = instructions.strip()

    if not clean_content:
        await interaction.followup.send(content="Content cannot be empty.", ephemeral=True)
        return

    is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(clean_content)
    if is_flagged:
        log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
        if is_zero_tolerance:
            ban_user(interaction.user.id, reason=f"Zero-tolerance violation in scribe: {', '.join(flagged_cats)}")
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.followup.send(view=ban_view, ephemeral=True)
            return

        refusal_text = await generate_friendly_refusal(flagged_cats)
        await interaction.followup.send(content=refusal_text, ephemeral=True)
        return

    target_model_name = WORKHORSE_DENSE_MODEL if clean_inst else "gemini-3.5-flash-lite"
    client, key_idx, active_model = client_manager.get_client_for_model(target_model_name, fallback=True)
    if not client:
        await interaction.followup.send(content="Scribe engine is currently busy. Please try again.", ephemeral=True)
        return

    active_statuses = list(DEFAULT_SCRIBE_STATUSES)

    async def fetch_dynamic_statuses():
        dynamic_statuses = await generate_scribe_witty_statuses(clean_content, clean_lang, clean_inst)
        if dynamic_statuses:
            active_statuses[:] = dynamic_statuses

    status_fetch_task = asyncio.create_task(fetch_dynamic_statuses())

    start_time = time.time()
    stop_placeholder_loop = asyncio.Event()

    placeholder_view = PlaceholderLayoutView(
        loading_text=format_placeholder_content(active_statuses[0], None),
        duration_seconds=0,
        is_enabled=False,
        thought_data={"thoughts": "", "tool_calls": [], "model": active_model, "is_quiz": False},
        model_name=active_model
    )

    placeholder_msg = await interaction.followup.send(view=placeholder_view, ephemeral=is_ephemeral)

    placeholder_task = asyncio.create_task(
        update_interaction_placeholder_loop(
            interaction=interaction,
            placeholder_view=placeholder_view,
            statuses=active_statuses,
            get_active_subtext_func=lambda: None,
            start_time=start_time,
            stop_event=stop_placeholder_loop,
            target_message=placeholder_msg
        )
    )

    sys_instruction = (
        f"You are PriestyAI's creative localization specialist and master linguistic scribe.\n"
        f"Your task is to translate, localize, and adapt the provided text into {clean_lang}.\n\n"
        "STRICT STYLISTIC & DFM DIRECTIVES:\n"
        "1. TONE & INSTRUCTIONS: Faithfully follow any custom tone, persona, dialect, or style rules specified by the user.\n"
        "2. ACCURACY & NATURAL PHRASING: Preserve the core message while ensuring natural phrasing and cultural localization.\n"
        "3. DISCORD-FLAVORED MARKDOWN (DFM) & CALLOUT PRESERVATION:\n"
        "   - Standard callouts MUST use canonical GitHub/Discord alert syntax:\n"
        "     > [!NOTE]\n"
        "     > Content...\n\n"
        "     > [!TIP]\n"
        "     > Content...\n\n"
        "     > [!IMPORTANT]\n"
        "     > Content...\n\n"
        "     > [!WARNING]\n"
        "     > Content...\n\n"
        "     > [!CAUTION]\n"
        "     > Content...\n\n"
        "   - CRITICAL: Keep the bracketed tag keyword ([!NOTE], [!TIP], [!IMPORTANT], [!WARNING], [!CAUTION]) in standard English "
        "so the DFM parser can recognize and render it, but translate the body text inside the blockquote (>).\n"
        "   - If the input contains pre-rendered callout emojis or headers (e.g. `<:gfm_tip:...> **Tip**`, `<:gfm_note:...> **Note**`, or similar), "
        "RECONSTRUCT them into standard clean markdown callouts (e.g. `> [!TIP]\\n> Translated body...`).\n"
        "   - Ensure all lines within a callout box start with the blockquote character `>`.\n"
        "4. PRESERVE TECHNICAL ELEMENTS:\n"
        "   - Keep code blocks (```lang ... ```), inline code (`...`), file paths, URLs, and commands intact.\n"
        "   - Keep Discord subtext (`-# ...`), timestamps (`<t:...>`), custom emojis (`<:...:...>`), and user/channel mentions (`<@...>`, `<#...>`).\n"
        "5. NO XML/ARTIFACT TAGS: Do NOT emit any <artifact>, <quiz>, or <followup> tags.\n"
        "6. Output ONLY the scribed/localized response with zero meta-commentary."
    )

    prompt_payload = f"Target Language: {clean_lang}\n"
    if clean_inst:
        prompt_payload += f"Creative Tone & Directives:\n{clean_inst}\n\n"
    prompt_payload += f"Content to Scribe:\n{clean_content}"

    try:
        res = await client.aio.models.generate_content(
            model=active_model,
            contents=prompt_payload,
            config=types.GenerateContentConfig(
                system_instruction=sys_instruction
            )
        )
        raw_result = res.text.strip() if (res and res.text) else "Scribing failed."
        cleaned_result = re.sub(r'<artifact[^>]*>.*?</artifact>', '', raw_result, flags=re.DOTALL).strip()
        parsed_result = apply_message_parsers(cleaned_result, interaction.guild)

        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()
        if not status_fetch_task.done():
            status_fetch_task.cancel()

        final_view = build_v2_message_layout(
            raw_text=parsed_result,
            guild=interaction.guild,
            show_reply_button=False,
            message_id=placeholder_msg.id if placeholder_msg else None
        )

        if placeholder_msg:
            await placeholder_msg.edit(view=final_view)
        else:
            await interaction.followup.send(view=final_view, ephemeral=is_ephemeral)

    except Exception as e:
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()
        if not status_fetch_task.done():
            status_fetch_task.cancel()
        logger.exception(f"Scribe error: {e}")
        try:
            err_view = build_v2_message_layout(raw_text=f"Scribing error: `{e}`", guild=interaction.guild, show_reply_button=False)
            if placeholder_msg:
                await placeholder_msg.edit(view=err_view)
            else:
                await interaction.followup.send(view=err_view, ephemeral=True)
        except Exception:
            pass

def build_chat_session_context_xml(
    session_id: str,
    channel: discord.abc.Messageable | None,
    guild: discord.Guild | None,
    user: discord.User | discord.Member,
    participant_entities: list[str | int] | None = None
) -> str:
    envelope = ['<context>']

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    now_formatted = now_utc.strftime("%A, %B %d, %Y %H:%M:%S UTC")
    envelope.append(f'  <temporal_context current_utc="{now_iso}" current_date="{now_formatted}" current_year="{now_utc.year}" />')

    if guild:
        ch_name = getattr(channel, "name", "channel")
        envelope.append(
            f'  <server_info name="{guild.name}" id="{guild.id}">\n'
            f'    <current_channel name="#{ch_name}" id="{getattr(channel, "id", 0)}" />\n'
            f'  </server_info>'
        )

    if participant_entities and guild:
        p_lines = ['  <channel_participants>']
        for ent_id in participant_entities:
            ent_str = str(ent_id).strip()
            if not ent_str:
                continue
            try:
                num_id = int(ent_str)
                member = guild.get_member(num_id)
                role = guild.get_role(num_id)
                if member:
                    p_lines.append(f'    <participant type="user" id="{member.id}" name="{member.display_name}" username="{member.name}" />')
                elif role:
                    p_lines.append(f'    <participant type="role" id="{role.id}" name="{role.name}" />')
                else:
                    p_lines.append(f'    <participant type="entity" id="{ent_str}" />')
            except Exception:
                p_lines.append(f'    <participant type="entity" id="{ent_str}" />')
        p_lines.append('  </channel_participants>')
        envelope.append("\n".join(p_lines))

    history = memory_manager.get_chat_session(session_id)
    if history:
        envelope.append("  <chat_history>")
        for turn in history[-14:]:
            role = turn.get("role", "user")
            content = turn.get("content", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            author_name = turn.get("author", "User" if role == "user" else "PriestyAI")
            envelope.append(f'    <message role="{role}" author="{author_name}">\n      {content}\n    </message>')
        envelope.append("  </chat_history>")

    envelope.append('</context>')
    return "\n".join(envelope)

async def execute_chat_turn(
    interaction: discord.Interaction,
    prompt_text: str,
    raw_attachment_parts: list[types.Part] | None = None,
    raw_image_bytes: list[bytes] | None = None,
    participant_entities: list[str | int] | None = None,
    is_ephemeral: bool = False
):
    user = interaction.user
    guild = interaction.guild
    channel = interaction.channel
    session_id = get_user_chat_session_id(interaction.channel_id, user.id)

    show_reply = should_show_reply_button(
        bot=interaction.client,
        guild=guild,
        channel=channel,
        interaction=interaction
    )

    is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(prompt_text, raw_image_bytes)
    if is_flagged:
        log_moderation_violation(user.id, interaction.guild_id, flagged_cats, score)
        if is_zero_tolerance:
            ban_user(user.id, reason=f"Zero-tolerance violation: {', '.join(flagged_cats)}")
            ban_view = BannedUserNoticeView(author=user)
            await interaction.followup.send(view=ban_view, ephemeral=True)
            return

        refusal_text = await generate_friendly_refusal(flagged_cats)
        await interaction.followup.send(content=refusal_text, ephemeral=is_ephemeral)
        return

    cfg = config_manager.resolve_effective_config(interaction.guild_id, getattr(channel, "id", None), user.id)
    asyncio.create_task(
        memory_manager.auto_extract_and_store_async(
            user_id=user.id,
            guild_id=interaction.guild_id,
            prompt_text=prompt_text,
            user_memory_policy=cfg.get("user_memory_policy", "read_write"),
            server_lore_policy=cfg.get("server_lore_policy", "read_write")
        )
    )

    multimodal_prompt: list[Any] = []
    if raw_attachment_parts:
        multimodal_prompt.extend(raw_attachment_parts)
    multimodal_prompt.append(prompt_text)

    tool_context = ToolExecutionContext(
        channel=channel,
        guild=guild,
        author=user,
        bot=interaction.client,
        input_image_bytes=raw_image_bytes[0] if raw_image_bytes else None
    )

    context_xml = build_chat_session_context_xml(
        session_id=session_id,
        channel=channel,
        guild=guild,
        user=user,
        participant_entities=participant_entities
    )

    thinking_start_time = time.time()
    answer_now_event = asyncio.Event()
    stop_placeholder_loop = asyncio.Event()

    active_witty_statuses = ["Thinking", "Consulting neural cores", "Formulating response"]
    active_tool_subtext: str | None = None
    first_content_received = False
    is_quiz_turn = False
    active_model_used = "gemma-4-31b-it"

    def get_active_subtext():
        return active_tool_subtext

    async def on_answer_now_clicked(inter: discord.Interaction):
        try:
            if not inter.response.is_done():
                await inter.response.defer(ephemeral=True)
        except Exception:
            pass
        answer_now_event.set()
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()

    placeholder_view = PlaceholderLayoutView(
        loading_text=format_placeholder_content(active_witty_statuses[0], active_tool_subtext),
        duration_seconds=0,
        is_enabled=False,
        on_answer_now_callback=on_answer_now_clicked,
        thought_data={"thoughts": "", "tool_calls": [], "model": active_model_used, "is_quiz": is_quiz_turn},
        model_name=active_model_used,
        is_quiz=is_quiz_turn
    )

    placeholder_msg = await interaction.followup.send(view=placeholder_view, ephemeral=is_ephemeral)

    stream_dispatcher = DiscordStreamDispatcher(
        interaction=interaction,
        is_ephemeral=is_ephemeral,
        guild=guild,
        show_reply_button=show_reply,
        existing_response_msg=placeholder_msg
    )
    artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(channel, "id", "global"))

    accumulated_thoughts = []
    tool_call_history = []
    active_tool_start_times = {}

    placeholder_task = asyncio.create_task(
        update_interaction_placeholder_loop(
            interaction=interaction,
            placeholder_view=placeholder_view,
            statuses=active_witty_statuses,
            get_active_subtext_func=get_active_subtext,
            start_time=thinking_start_time,
            stop_event=stop_placeholder_loop,
            target_message=placeholder_msg
        )
    )

    try:
        async for event_type, payload in ChatEngine.stream_chat(
            prompt=multimodal_prompt,
            context_xml=context_xml,
            bot_user_id=interaction.client.user.id,
            tool_context=tool_context,
            answer_now_event=answer_now_event
        ):
            if event_type == "ROUTED":
                if payload.witty_statuses:
                    active_witty_statuses[:] = payload.witty_statuses
                    curr_txt = format_placeholder_content(active_witty_statuses[0], active_tool_subtext)
                    placeholder_view.update_state(curr_txt, max(0, int(time.time() - thinking_start_time)))
                    try:
                        if placeholder_msg:
                            await placeholder_msg.edit(view=placeholder_view)
                    except Exception:
                        pass

                if getattr(payload, "is_quiz", False):
                    is_quiz_turn = True
                    placeholder_view.is_quiz = True
                    placeholder_view.thought_data["is_quiz"] = True

            elif event_type == "ACTIVE_MODEL":
                active_model_used = str(payload)
                placeholder_view.model_name = active_model_used

            elif event_type == "RECALLED_MEMORIES":
                count = payload.get("count", 0)
                tool_call_history.insert(0, {
                    "name": "recall_memories",
                    "args": {"count": count},
                    "result": payload,
                    "duration_ms": 0,
                    "order": -1.0
                })
                placeholder_view.thought_data["tool_calls"] = tool_call_history

            elif event_type == "THOUGHT":
                accumulated_thoughts.append(payload)
                placeholder_view.enable_thinking()
                placeholder_view.thought_data["thoughts"] = "".join(accumulated_thoughts)

            elif event_type == "TOOL_START":
                tool_name = payload.get("name", "")
                args = payload.get("args", {})
                active_tool_start_times[tool_name] = time.perf_counter()
                active_tool_subtext = get_tool_subtext(tool_name, args)
                placeholder_view.enable_thinking()
                if tool_name in ["create_artifact", "update_artifact"]:
                    stream_dispatcher.add_artifact_placeholder(tool_name, args)

            elif event_type == "TOOL_END":
                tool_name = payload.get("name", "")
                st = active_tool_start_times.pop(tool_name, time.perf_counter())
                dur_ms = int((time.perf_counter() - st) * 1000)
                tool_call_history.append({
                    "name": tool_name,
                    "args": payload.get("args", {}),
                    "result": payload.get("result", {}),
                    "duration_ms": dur_ms
                })
                active_tool_subtext = None
                placeholder_view.thought_data["tool_calls"] = tool_call_history

                if tool_name in ["create_artifact", "update_artifact"] and tool_context.staged_artifacts:
                    last_art = tool_context.staged_artifacts[-1]
                    stream_dispatcher.update_artifact_ready(last_art)
                    art_bytes = last_art.get("data_bytes", b"")
                    art_fname = last_art.get("filename", "artifact.zip")
                    if art_bytes:
                        stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

                elif tool_name in ["search_image", "search_gif", "generate_image", "edit_image", "execute_code"] and tool_context.staged_image_bytes:
                    img_fname = tool_context.staged_image_filename
                    img_bytes = tool_context.staged_image_bytes
                    stream_dispatcher.add_media_block(img_fname, img_bytes)
                    tool_context.staged_image_bytes = None

                elif tool_name in ["github_repo", "fetch_github"] and hasattr(tool_context, "staged_github_files"):
                    for g_file in tool_context.staged_github_files:
                        stream_dispatcher.add_raw_attachment(g_file["filename"], g_file["bytes"])

            elif event_type == "CONTENT":
                if not first_content_received:
                    first_content_received = True
                    stop_placeholder_loop.set()
                    if placeholder_task and not placeholder_task.done():
                        placeholder_task.cancel()

                await artifact_parser.feed(payload)

        await artifact_parser.finish()
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()

        final_dur = max(1, int(time.time() - thinking_start_time))
        active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
        has_reasoning = bool(accumulated_thoughts or active_tools)

        stored_attachments = []
        for raw_att in stream_dispatcher.raw_attachment_buffers:
            b64 = base64.b64encode(raw_att["bytes"]).decode("utf-8")
            stored_attachments.append({"filename": raw_att["filename"], "data_b64": b64})

        sanitized_artifacts = []
        for art in tool_context.staged_artifacts:
            art_bytes = art.get("data_bytes", b"")
            art_fname = art.get("filename", "artifact.zip")
            b64_art = base64.b64encode(art_bytes).decode("utf-8") if art_bytes else ""
            clean_art = {k: v for k, v in art.items() if k != "data_bytes"}
            clean_art["data_b64"] = b64_art
            sanitized_artifacts.append(clean_art)

        root_msg = stream_dispatcher.primary_message or placeholder_msg
        target_id = root_msg.id if root_msg else "temp"

        await stream_dispatcher.finalize(
            staged_artifacts=tool_context.staged_artifacts,
            staged_components=tool_context.staged_components,
            staged_followups=stream_dispatcher.staged_followups,
            thought_duration=final_dur,
            has_thoughts=has_reasoning,
            show_reply_button=show_reply,
            active_version=1,
            total_versions=1,
            message_id=target_id
        )

        final_text = stream_dispatcher.get_accumulated_text()

        if root_msg:
            parsed_initial_content = apply_message_parsers(final_text, guild)
            raw_collected_thoughts = "".join(accumulated_thoughts)
            sent_msg_ids = [str(m.id) for m in stream_dispatcher.sent_messages if m] or [str(root_msg.id)]

            sanitized_timeline: list[dict[str, Any]] = []
            for b in stream_dispatcher.timeline:
                b_copy = dict(b)
                if b_copy.get("type") == "artifact" and "artifact" in b_copy:
                    art_copy = dict(b_copy["artifact"])
                    art_copy.pop("data_bytes", None)
                    b_copy["artifact"] = art_copy
                sanitized_timeline.append(b_copy)

            has_quiz_in_blocks = any(b.get("type") == "quiz" for b in sanitized_timeline)

            initial_v_data = {
                "version_idx": 1,
                "content": parsed_initial_content,
                "timeline_blocks": sanitized_timeline,
                "duration_seconds": final_dur,
                "has_thoughts": has_reasoning,
                "thoughts": raw_collected_thoughts,
                "formatted_thoughts": None,
                "model": active_model_used,
                "is_quiz": has_quiz_in_blocks,
                "tool_calls": tool_call_history,
                "attachments": stored_attachments,
                "staged_components": tool_context.staged_components,
                "staged_artifacts": sanitized_artifacts,
                "staged_followups": stream_dispatcher.staged_followups,
                "staged_modals": tool_context.staged_modals,
                "message_ids": sent_msg_ids,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "status": "ready"
            }

            branch_manager.save_generation(
                message_id=root_msg.id,
                channel_id=getattr(channel, "id", 0),
                guild_id=guild.id if guild else None,
                author_id=user.id,
                prompt_text=prompt_text,
                attachments=[],
                context_xml=context_xml,
                initial_version_data=initial_v_data
            )

        if final_text.strip():
            existing_history = memory_manager.get_chat_session(session_id)
            existing_history.append({
                "role": "user",
                "author": user.display_name,
                "content": prompt_text.strip()
            })
            existing_history.append({
                "role": "assistant",
                "author": "PriestyAI",
                "content": final_text.strip()
            })
            if len(existing_history) > 20:
                existing_history = existing_history[-20:]

            memory_manager.save_chat_session(
                session_id=session_id,
                channel_id=interaction.channel_id or 0,
                guild_id=interaction.guild_id,
                user_id=user.id,
                history=existing_history
            )

    except Exception as e:
        logger.exception(f"Error in /chat turn: {e}")
        try:
            err_view = build_v2_message_layout(raw_text=f"Error: `{e}`", guild=guild)
            if placeholder_msg:
                await placeholder_msg.edit(view=err_view)
            else:
                await interaction.followup.send(view=err_view, ephemeral=True)
        except Exception:
            pass
    finally:
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()

def setup_chat_commands(tree: app_commands.CommandTree):

    @tree.command(name="terms", description="Review PriestyAI's Terms of Service, Safety Guidelines, and Moderation Policies")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def terms_command(interaction: discord.Interaction):
        viewer = LegalDocumentViewerLayoutView(doc_type="terms", user=interaction.user, page=0)
        await interaction.response.send_message(view=viewer, ephemeral=True)

    @tree.command(name="privacy", description="Review PriestyAI's Privacy Policy, data retention rules, and third-party disclosures")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def privacy_command(interaction: discord.Interaction):
        viewer = LegalDocumentViewerLayoutView(doc_type="privacy", user=interaction.user, page=0)
        await interaction.response.send_message(view=viewer, ephemeral=True)

    @tree.command(name="ask", description="Ask PriestyAI a quick question anywhere on Discord")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(query="The prompt or question to ask", visibility="Public or Ephemeral response")
    @app_commands.choices(visibility=[
        app_commands.Choice(name="Public (Visible to all members)", value="public"),
        app_commands.Choice(name="Ephemeral (Only visible to you)", value="private")
    ])
    async def ask_command(interaction: discord.Interaction, query: str, visibility: str = "public"):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.defer(ephemeral=(visibility == "private"))
                await _execute_ask(sub_inter, query, is_ephemeral=(visibility == "private"))

            modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(modal)
            return

        is_ephemeral = (visibility == "private")
        await interaction.response.defer(ephemeral=is_ephemeral)
        await _execute_ask(interaction, query, is_ephemeral=is_ephemeral)

    async def _execute_ask(interaction: discord.Interaction, query: str, is_ephemeral: bool):
        is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(query)
        if is_flagged:
            log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)

            if is_zero_tolerance:
                ban_user(interaction.user.id, reason=f"Zero-tolerance violation: {', '.join(flagged_cats)}")
                ban_view = BannedUserNoticeView(author=interaction.user)
                await interaction.followup.send(view=ban_view, ephemeral=True)
                return

            refusal_text = await generate_friendly_refusal(flagged_cats)
            await interaction.followup.send(content=refusal_text, ephemeral=is_ephemeral)
            return

        cfg = config_manager.resolve_effective_config(interaction.guild_id, getattr(interaction.channel, "id", None), interaction.user.id)
        asyncio.create_task(
            memory_manager.auto_extract_and_store_async(
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                prompt_text=query,
                user_memory_policy=cfg.get("user_memory_policy", "read_write"),
                server_lore_policy=cfg.get("server_lore_policy", "read_write")
            )
        )

        show_reply = should_show_reply_button(
            bot=interaction.client,
            guild=interaction.guild,
            channel=interaction.channel,
            interaction=interaction
        )

        thinking_start_time = time.time()
        tool_context = ToolExecutionContext(
            channel=interaction.channel,
            guild=interaction.guild,
            author=interaction.user,
            bot=interaction.client
        )
        
        accumulated_thoughts = []
        tool_call_history = []
        active_tool_start_times = {}
        active_model_used = "gemma-4-31b-it"

        stream_dispatcher = DiscordStreamDispatcher(
            interaction=interaction,
            is_ephemeral=is_ephemeral,
            guild=interaction.guild,
            show_reply_button=show_reply
        )
        artifact_parser = ArtifactStreamParser(
            stream_dispatcher,
            tool_context,
            channel_id=getattr(interaction.channel, "id", "global")
        )

        try:
            async for event_type, payload in ChatEngine.stream_chat(
                prompt=query,
                context_xml="<context></context>",
                bot_user_id=interaction.client.user.id,
                tool_context=tool_context
            ):
                if event_type == "ACTIVE_MODEL":
                    active_model_used = str(payload)

                elif event_type == "RECALLED_MEMORIES":
                    count = payload.get("count", 0)
                    tool_call_history.insert(0, {
                        "name": "recall_memories",
                        "args": {"count": count},
                        "result": payload,
                        "duration_ms": 0,
                        "order": -1.0
                    })

                elif event_type == "THOUGHT":
                    accumulated_thoughts.append(payload)

                elif event_type == "TOOL_START":
                    tool_name = payload.get("name", "")
                    args = payload.get("args", {})
                    active_tool_start_times[tool_name] = time.perf_counter()
                    if tool_name in ["create_artifact", "update_artifact"]:
                        stream_dispatcher.add_artifact_placeholder(tool_name, args)

                elif event_type == "TOOL_END":
                    tool_name = payload.get("name", "")
                    st = active_tool_start_times.pop(tool_name, time.perf_counter())
                    dur_ms = int((time.perf_counter() - st) * 1000)
                    tool_call_history.append({
                        "name": tool_name,
                        "args": payload.get("args", {}),
                        "result": payload.get("result", {}),
                        "duration_ms": dur_ms
                    })

                    if tool_name in ["create_artifact", "update_artifact"] and tool_context.staged_artifacts:
                        last_art = tool_context.staged_artifacts[-1]
                        stream_dispatcher.update_artifact_ready(last_art)
                        art_bytes = last_art.get("data_bytes", b"")
                        art_fname = last_art.get("filename", "artifact.zip")
                        if art_bytes:
                            stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

                    elif tool_name in ["search_image", "search_gif", "generate_image", "execute_code"] and tool_context.staged_image_bytes:
                        img_fname = tool_context.staged_image_filename
                        img_bytes = tool_context.staged_image_bytes
                        stream_dispatcher.add_media_block(img_fname, img_bytes)
                        tool_context.staged_image_bytes = None

                elif event_type == "CONTENT":
                    await artifact_parser.feed(payload)

            await artifact_parser.finish()

            final_dur = max(1, int(time.time() - thinking_start_time))
            active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
            has_reasoning = bool(accumulated_thoughts or active_tools)

            stored_attachments = []
            for raw_att in stream_dispatcher.raw_attachment_buffers:
                b64 = base64.b64encode(raw_att["bytes"]).decode("utf-8")
                stored_attachments.append({"filename": raw_att["filename"], "data_b64": b64})

            sanitized_artifacts = []
            for art in tool_context.staged_artifacts:
                art_bytes = art.get("data_bytes", b"")
                art_fname = art.get("filename", "artifact.zip")
                b64_art = base64.b64encode(art_bytes).decode("utf-8") if art_bytes else ""
                clean_art = {k: v for k, v in art.items() if k != "data_bytes"}
                clean_art["data_b64"] = b64_art
                sanitized_artifacts.append(clean_art)

            root_msg = stream_dispatcher.primary_message
            target_id = root_msg.id if root_msg else "temp"

            await stream_dispatcher.finalize(
                staged_artifacts=tool_context.staged_artifacts,
                staged_components=tool_context.staged_components,
                staged_followups=stream_dispatcher.staged_followups,
                thought_duration=final_dur,
                has_thoughts=has_reasoning,
                show_reply_button=show_reply,
                active_version=1,
                total_versions=1,
                message_id=target_id
            )

            if root_msg:
                final_text = stream_dispatcher.get_accumulated_text()
                parsed_initial_content = apply_message_parsers(final_text, interaction.guild)
                raw_collected_thoughts = "".join(accumulated_thoughts)
                sent_msg_ids = [str(m.id) for m in stream_dispatcher.sent_messages if m] or [str(root_msg.id)]

                sanitized_timeline: list[dict[str, Any]] = []
                for b in stream_dispatcher.timeline:
                    b_copy = dict(b)
                    if b_copy.get("type") == "artifact" and "artifact" in b_copy:
                        art_copy = dict(b_copy["artifact"])
                        art_copy.pop("data_bytes", None)
                        b_copy["artifact"] = art_copy
                    sanitized_timeline.append(b_copy)

                has_quiz_in_blocks = any(b.get("type") == "quiz" for b in sanitized_timeline)

                initial_v_data = {
                    "version_idx": 1,
                    "content": parsed_initial_content,
                    "timeline_blocks": sanitized_timeline,
                    "duration_seconds": final_dur,
                    "has_thoughts": has_reasoning,
                    "thoughts": raw_collected_thoughts,
                    "formatted_thoughts": None,
                    "model": active_model_used,
                    "is_quiz": has_quiz_in_blocks,
                    "tool_calls": tool_call_history,
                    "attachments": stored_attachments,
                    "staged_components": tool_context.staged_components,
                    "staged_artifacts": sanitized_artifacts,
                    "staged_followups": stream_dispatcher.staged_followups,
                    "staged_modals": tool_context.staged_modals,
                    "message_ids": sent_msg_ids,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "ready"
                }

                branch_manager.save_generation(
                    message_id=root_msg.id,
                    channel_id=getattr(interaction.channel, "id", 0),
                    guild_id=interaction.guild_id,
                    author_id=interaction.user.id,
                    prompt_text=query,
                    attachments=[],
                    context_xml="<context></context>",
                    initial_version_data=initial_v_data
                )

        except Exception as e:
            logger.exception(f"Error in /ask command: {e}")
            try:
                err_view = build_v2_message_layout(raw_text=f"Error: `{e}`", guild=interaction.guild)
                await interaction.followup.send(view=err_view, ephemeral=True)
            except Exception:
                pass

    @tree.command(name="chat", description="Start or continue a multi-turn conversation anywhere on Discord")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(input="Type a prompt or 'reset' to clear session, or leave blank to open the rich chat modal")
    async def chat_command(interaction: discord.Interaction, input: str | None = None):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        session_id = get_user_chat_session_id(interaction.channel_id, interaction.user.id)

        if input and input.strip().lower() in ["reset", "clear", "--reset", "-r", "clean"]:
            memory_manager.delete_chat_session(session_id)
            await interaction.response.send_message(
                content="Chat Session Reset: Your conversation history for this channel has been cleared. Your next message will start fresh.",
                ephemeral=True
            )
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.send_message("Terms accepted. You can now use /chat.", ephemeral=True)

            modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(modal)
            return

        if input and input.strip():
            await interaction.response.defer(ephemeral=False)
            await execute_chat_turn(interaction, prompt_text=input.strip(), is_ephemeral=False)
            return

        async def on_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            prompt_text = data.get("prompt", "").strip()
            if not prompt_text:
                await sub_inter.response.send_message(content="Message cannot be empty.", ephemeral=True)
                return

            await sub_inter.response.defer(ephemeral=False)

            raw_data = getattr(sub_inter, "data", {})
            resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
            attachment_parts: list[types.Part] = []
            raw_image_bytes: list[bytes] = []

            if resolved_attachments:
                async with aiohttp.ClientSession() as http_session:
                    for att_id, att_obj in resolved_attachments.items():
                        att_url = att_obj.get("url")
                        att_fname = att_obj.get("filename", "file.bin")
                        content_type = att_obj.get("content_type", "application/octet-stream")
                        if att_url:
                            try:
                                async with http_session.get(att_url) as resp:
                                    if resp.status == 200:
                                        file_bytes = await resp.read()
                                        part = types.Part.from_bytes(data=file_bytes, mime_type=content_type)
                                        attachment_parts.append(part)
                                        if content_type.startswith("image/"):
                                            raw_image_bytes.append(file_bytes)
                            except Exception as dl_err:
                                logger.warning(f"Failed to download modal attachment '{att_fname}': {dl_err}")

            raw_participants = data.get("channel_context", [])
            if isinstance(raw_participants, str):
                raw_participants = [raw_participants] if raw_participants else []
            elif not isinstance(raw_participants, list):
                raw_participants = []

            await execute_chat_turn(
                interaction=sub_inter,
                prompt_text=prompt_text,
                raw_attachment_parts=attachment_parts,
                raw_image_bytes=raw_image_bytes,
                participant_entities=raw_participants,
                is_ephemeral=False
            )

        modal = build_user_chat_modal(on_submit=on_modal_submit)
        await interaction.response.send_modal(modal)

    @tree.command(name="scribe", description="Translate, localize, and adapt text with custom creative tone and style instructions")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        text="The text to scribe or translate (leave blank to open interactive modal)",
        language="Target language to translate into",
        instructions="Tone or creative style directives (e.g. formal, slang, poetic)",
        visibility="Choose between Public or Ephemeral response"
    )
    @app_commands.choices(visibility=[
        app_commands.Choice(name="Public (Visible to all members)", value="public"),
        app_commands.Choice(name="Ephemeral (Only visible to you)", value="private")
    ])
    @app_commands.autocomplete(language=scribe_language_autocomplete)
    async def scribe_command(
        interaction: discord.Interaction,
        text: str | None = None,
        language: str = "English",
        instructions: str = "",
        visibility: str = "public"
    ):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.send_message("Terms accepted. You can now use /scribe.", ephemeral=True)

            modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(modal)
            return

        if text and text.strip():
            is_ephemeral = (str(visibility).strip().lower() == "private")
            await _execute_scribe(
                interaction=interaction,
                content=text.strip(),
                target_language=language or "English",
                instructions=instructions or "",
                is_ephemeral=is_ephemeral
            )
            return

        async def on_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            m_content = data.get("content", "").strip()
            m_inst = data.get("instructions", "").strip()
            m_lang = data.get("target_language", "English").strip() or "English"
            
            raw_vis = data.get("visibility", "public")
            if isinstance(raw_vis, list):
                raw_vis = raw_vis[0] if raw_vis else "public"
            m_ephemeral = (str(raw_vis).strip().lower() == "private")

            await _execute_scribe(
                interaction=sub_inter,
                content=m_content,
                target_language=m_lang,
                instructions=m_inst,
                is_ephemeral=m_ephemeral
            )

        modal = build_scribe_modal(on_submit=on_modal_submit)
        await interaction.response.send_modal(modal)