import io
import re
import time
import uuid
import json
import base64
import logging
import asyncio
from typing import Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager
import discord
from discord import app_commands
from discord.ui import (
    ActionRow,
    Button,
    View
)
from google.genai import types

from config.settings import LOADING_EMOJI
from core.engine import ChatEngine
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.memory_manager import memory_manager
from handlers.stream_handler import (
    DiscordStreamDispatcher,
    build_v2_message_layout,
    apply_message_parsers,
    extract_text_from_v2_message,
    cleanup_sibling_messages,
    should_show_reply_button,
    ChatMessageLayoutView
)
from handlers.chat_handler import (
    format_placeholder_content,
    get_tool_subtext,
    update_placeholder_loop,
    extract_message_attachments_raw
)
from ui.thought_container import PlaceholderLayoutView
from parsers.artifact_parser import ArtifactStreamParser
from parsers.markdown_parser import apply_dfm
from tools.registry import ToolExecutionContext
from ui.modals import DynamicModalV2
from ui.onboarding_views import build_welcome_terms_modal, BannedUserNoticeView
from ui.schedule_views import is_user_server_admin
from agent.constants import OCTICONS_MAP
from ui.context_views import (
    BranchHeaderView,
    build_branch_version_picker_modal
)
from core.moderation import (
    check_moderation,
    log_moderation_violation,
    is_user_banned,
    ban_user,
    generate_friendly_refusal
)

logger = logging.getLogger("PriestyAI.Commands.ContextMenus")

DEFAULT_RETRY_STATUSES = [
    "Retrying with fresh neural synapses",
    "Actually reading the prompt this time",
    "Refactoring the previous approach",
    "Consulting deeper databanks",
    "Brewing a better response",
    "Polishing the output to perfection"
]

DEFAULT_SUMMARIZE_STATUSES = [
    "Distilling core insights",
    "Extracting key takeaways",
    "Condensing points of interest",
    "Polishing summary structure"
]

@asynccontextmanager
async def safe_typing(channel: Any):
    if channel and hasattr(channel, "typing"):
        try:
            async with channel.typing():
                yield
                return
        except Exception:
            pass
    yield

def is_foreign_context(interaction: discord.Interaction) -> bool:
    if not interaction.guild_id:
        return False
    bot_guild = interaction.client.get_guild(interaction.guild_id)
    return bot_guild is None or getattr(bot_guild, "me", None) is None

async def generate_retry_witty_statuses(prompt: str, version_idx: int) -> list[str]:
    client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
    if not client:
        return DEFAULT_RETRY_STATUSES

    gen_prompt = (
        f"Original user prompt: \"{prompt[:300]}\"\n"
        f"The user clicked Retry to generate Version {version_idx}.\n"
        f"Generate 5 funny, sarcastic, witty 3-5 word loading messages specifically about re-attempting or improving this answer.\n"
        f"Output ONLY a JSON array of strings, e.g. [\"Trying without hallucinating\", \"Actually checking documentation\", ...]"
    )

    try:
        res = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=active_model,
                contents=gen_prompt
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
        logger.debug(f"Failed to generate retry witty statuses: {e}")

    return DEFAULT_RETRY_STATUSES

def build_retry_placeholder_layout(
    status_text: str,
    target_version: int,
    total_versions: int,
    message_id: str | int
) -> ChatMessageLayoutView:
    view = ChatMessageLayoutView(timeout=900)
    view.add_item(discord.ui.TextDisplay(f"{LOADING_EMOJI} *{status_text}...*"))
    view.add_item(discord.ui.Separator(visible=True))

    thought_btn = discord.ui.Button(
        label="🧠 Thought for 1s",
        style=discord.ButtonStyle.secondary,
        custom_id=f"gen_thought_{message_id}_{target_version}"
    )
    view.add_item(discord.ui.ActionRow(thought_btn))

    prev_btn = discord.ui.Button(
        label="◀",
        style=discord.ButtonStyle.secondary,
        disabled=(target_version <= 1),
        custom_id=f"gen_prev_{message_id}"
    )
    ind_btn = discord.ui.Button(
        label=f"{target_version} / {total_versions}",
        style=discord.ButtonStyle.secondary,
        disabled=True,
        custom_id=f"gen_ind_{message_id}"
    )
    next_btn = discord.ui.Button(
        label="▶",
        style=discord.ButtonStyle.secondary,
        disabled=(target_version >= total_versions),
        custom_id=f"gen_next_{message_id}"
    )
    view.add_item(discord.ui.ActionRow(prev_btn, ind_btn, next_btn))
    return view

async def update_interaction_placeholder_loop(
    interaction: discord.Interaction,
    placeholder_view: PlaceholderLayoutView,
    statuses: list[str],
    get_active_subtext_func: Any,
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
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                break
            await placeholder_view.push_live_update()
        except Exception:
            break

async def _execute_summarize(interaction: discord.Interaction, message: discord.Message):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    extracted_text = extract_text_from_v2_message(message)
    attachment_parts, raw_image_bytes = await extract_message_attachments_raw(message)

    if not extracted_text.strip() and not attachment_parts:
        await interaction.followup.send(content="The selected message has no text or attachments to summarize.", ephemeral=True)
        return

    is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(extracted_text, raw_image_bytes)
    if is_flagged:
        log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
        if is_zero_tolerance:
            ban_user(interaction.user.id, reason=f"Zero-tolerance violation in summary: {', '.join(flagged_cats)}")
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.followup.send(view=ban_view, ephemeral=True)
            return

        refusal_text = await generate_friendly_refusal(flagged_cats)
        await interaction.followup.send(content=refusal_text, ephemeral=True)
        return

    client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
    if not client:
        await interaction.followup.send(content="Summarization service is currently busy. Please try again.", ephemeral=True)
        return

    placeholder_view = PlaceholderLayoutView(
        loading_text=format_placeholder_content(DEFAULT_SUMMARIZE_STATUSES[0], None),
        duration_seconds=0,
        is_enabled=False,
        thought_data={"thoughts": "", "tool_calls": [], "model": active_model, "is_quiz": False},
        model_name=active_model
    )
    placeholder_msg = await interaction.followup.send(view=placeholder_view, ephemeral=True)

    prompt_contents: list[Any] = []
    if attachment_parts:
        prompt_contents.extend(attachment_parts)

    instruction = (
        "You are PriestyAI. Summarize the following message clearly, naturally, and concisely.\n"
        "Extract key takeaways, core points, and any action items.\n"
        "Format using clean Discord-flavored markdown (bold headers, bullet points). "
        "Do NOT wrap the entire text in code fences, and do NOT emit any <artifact> tags."
    )
    prompt_contents.append(f"{instruction}\n\nContent to summarize:\n{extracted_text[:4000]}")

    try:
        res = await client.aio.models.generate_content(
            model=active_model,
            contents=prompt_contents
        )
        raw_summary = res.text.strip() if (res and res.text) else "Unable to generate summary."
        cleaned_summary = re.sub(r'<artifact[^>]*>.*?</artifact>', '', raw_summary, flags=re.DOTALL).strip()
        parsed_summary = apply_message_parsers(cleaned_summary, interaction.guild)
        
        final_view = build_v2_message_layout(
            raw_text=f"### Summary\n{parsed_summary}",
            guild=interaction.guild,
            show_reply_button=False
        )
        if placeholder_msg:
            try:
                await placeholder_msg.edit(view=final_view)
            except Exception:
                await interaction.edit_original_response(view=final_view)
        else:
            await interaction.followup.send(view=final_view, ephemeral=True)

    except Exception as e:
        logger.exception(f"Summarize execution error: {e}")
        try:
            err_view = build_v2_message_layout(raw_text=f"Failed to generate summary: `{e}`", guild=interaction.guild)
            if placeholder_msg:
                await placeholder_msg.edit(view=err_view)
            else:
                await interaction.followup.send(view=err_view, ephemeral=True)
        except Exception:
            pass

async def _execute_run_as_prompt(interaction: discord.Interaction, message: discord.Message):
    extracted_text = extract_text_from_v2_message(message)
    clean_prompt = re.sub(rf'<@!?{interaction.client.user.id}>', '', extracted_text).strip()
    
    attachment_parts, raw_image_bytes = await extract_message_attachments_raw(message)
    if not clean_prompt and not attachment_parts:
        if not interaction.response.is_done():
            await interaction.response.send_message(content="The selected message contains no readable text or media prompt.", ephemeral=True)
        else:
            await interaction.followup.send(content="The selected message contains no readable text or media prompt.", ephemeral=True)
        return

    if not clean_prompt:
        clean_prompt = "Analyze the attached content."

    is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(clean_prompt, raw_image_bytes)
    if is_flagged:
        log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
        if is_zero_tolerance:
            ban_user(interaction.user.id, reason=f"Zero-tolerance violation: {', '.join(flagged_cats)}")
            ban_view = BannedUserNoticeView(author=interaction.user)
            if not interaction.response.is_done():
                await interaction.response.send_message(view=ban_view, ephemeral=True)
            else:
                await interaction.followup.send(view=ban_view, ephemeral=True)
            return

        refusal_text = await generate_friendly_refusal(flagged_cats)
        if not interaction.response.is_done():
            await interaction.response.send_message(content=refusal_text, ephemeral=True)
        else:
            await interaction.followup.send(content=refusal_text, ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False)

    multimodal_prompt: list[Any] = []
    if attachment_parts:
        multimodal_prompt.extend(attachment_parts)
    multimodal_prompt.append(clean_prompt)

    tool_context = ToolExecutionContext(
        channel=interaction.channel,
        guild=interaction.guild,
        author=interaction.user,
        bot=interaction.client,
        input_image_bytes=raw_image_bytes[0] if raw_image_bytes else None
    )
    tool_context.message = message

    show_reply = should_show_reply_button(
        bot=interaction.client,
        guild=interaction.guild,
        channel=interaction.channel,
        interaction=interaction
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

    await interaction.edit_original_response(view=placeholder_view)
    placeholder_msg = None
    try:
        placeholder_msg = await interaction.original_response()
    except Exception:
        pass

    stream_dispatcher = DiscordStreamDispatcher(
        interaction=interaction,
        is_ephemeral=False,
        guild=interaction.guild,
        show_reply_button=show_reply,
        existing_response_msg=placeholder_msg
    )
    artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(interaction.channel, "id", "global"))

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
            context_xml="<context></context>",
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
                        else:
                            await interaction.edit_original_response(view=placeholder_view)
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
                tool_name = payload.get("name", "Tool")
                args = payload.get("args", {})
                active_tool_start_times[tool_name] = time.perf_counter()
                active_tool_subtext = get_tool_subtext(tool_name, args)
                placeholder_view.enable_thinking()
                if tool_name in ["create_artifact", "update_artifact"]:
                    stream_dispatcher.add_artifact_placeholder(tool_name, args)

            elif event_type == "TOOL_END":
                tool_name = payload.get("name", "Tool")
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

        final_duration = max(1, int(time.time() - thinking_start_time))
        active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
        has_reasoning = bool(accumulated_thoughts or active_tools)

        modals_map = {m["modal_id"]: m for m in tool_context.staged_modals}

        for art in tool_context.staged_artifacts:
            art_bytes = art.get("data_bytes", b"")
            art_fname = art.get("filename", "artifact.zip")
            if art_bytes:
                stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

        await stream_dispatcher.finalize(
            staged_artifacts=tool_context.staged_artifacts,
            staged_components=tool_context.staged_components,
            staged_followups=stream_dispatcher.staged_followups,
            modals_map=modals_map,
            thought_duration=final_duration,
            has_thoughts=has_reasoning,
            show_reply_button=show_reply,
            active_version=1,
            total_versions=1
        )

    except Exception as e:
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()
        logger.exception(f"Run as Prompt error: {e}")

async def _execute_branch_creation(
    interaction: discord.Interaction,
    message: discord.Message,
    target_version_idx: int | None = None
):
    if not interaction.guild:
        if not interaction.response.is_done():
            await interaction.response.send_message(content="Branches can only be created inside Discord servers.", ephemeral=True)
        else:
            await interaction.followup.send(content="Branches can only be created inside Discord servers.", ephemeral=True)
        return

    is_inside_thread = isinstance(interaction.channel, discord.Thread)
    parent_channel = interaction.channel.parent if is_inside_thread else interaction.channel

    if not isinstance(parent_channel, discord.TextChannel):
        if not interaction.response.is_done():
            await interaction.response.send_message(content="Branches must be anchored to a server text channel.", ephemeral=True)
        else:
            await interaction.followup.send(content="Branches must be anchored to a server text channel.", ephemeral=True)
        return

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)

    parent_branch_id = ""
    parent_thread_id = ""
    if is_inside_thread:
        existing_parent_branch = branch_manager.get_branch_by_thread_id(interaction.channel.id)
        if existing_parent_branch:
            parent_branch_id = existing_parent_branch.get("branch_id", "")
            parent_thread_id = str(interaction.channel.id)

    history_msgs = []
    try:
        raw_history = [m async for m in interaction.channel.history(limit=20, before=message.created_at)]
        raw_history.reverse()
        raw_history.append(message)

        for m in raw_history:
            extracted_content = extract_text_from_v2_message(m)
            att_records = []
            if m.attachments:
                for a in m.attachments:
                    att_records.append({"filename": a.filename, "url": a.url})

            history_msgs.append({
                "id": str(m.id),
                "role": "assistant" if m.author.id == interaction.client.user.id else "user",
                "author": m.author.display_name,
                "author_id": str(m.author.id),
                "content": extracted_content,
                "attachments": att_records,
                "timestamp": m.created_at.isoformat()
            })
    except Exception as ex:
        logger.warning(f"Branch history fetch exception: {ex}")

    if target_version_idx and target_version_idx > 0:
        gen = branch_manager.get_generation(message.id)
        if gen:
            versions = gen.get("versions", [])
            if 1 <= target_version_idx <= len(versions):
                v_entry = versions[target_version_idx - 1]
                if history_msgs and history_msgs[-1]["role"] == "assistant":
                    history_msgs[-1]["content"] = v_entry.get("content", history_msgs[-1]["content"])

    base_title = "Exploration Branch"
    try:
        sample_text = extract_text_from_v2_message(message)[:300]
        title_prompt = f"Generate a clean 3 to 5 word topic title for this discussion. Output ONLY the title:\n{sample_text}"
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
        if client:
            res = await client.aio.models.generate_content(model=active_model, contents=title_prompt)
            if res.text:
                base_title = res.text.strip().replace('"', '').replace("'", "")[:50]
    except Exception:
        pass

    clean_title = f"Fork: {base_title}" if is_inside_thread else base_title

    try:
        if is_inside_thread:
            thread = await parent_channel.create_thread(name=clean_title, type=discord.ChannelType.public_thread)
        else:
            try:
                thread = await message.create_thread(name=clean_title)
            except Exception:
                thread = await parent_channel.create_thread(name=clean_title, type=discord.ChannelType.public_thread)
    except Exception as e:
        await interaction.followup.send(content=f"❌ Failed to create branch thread: {e}", ephemeral=True)
        return

    try:
        await thread.add_user(interaction.user)
    except Exception as ex:
        logger.debug(f"Failed to add user to thread: {ex}")

    try:
        async for sys_m in parent_channel.history(limit=5):
            if sys_m.type == discord.MessageType.thread_created:
                await sys_m.delete()
                break
    except Exception:
        pass

    branch_id = str(uuid.uuid4())[:8]
    branch_manager.create_branch(
        branch_id=branch_id,
        thread_id=thread.id,
        channel_id=parent_channel.id,
        guild_id=interaction.guild_id,
        creator_id=interaction.user.id,
        title=clean_title,
        root_message_id=message.id,
        messages=history_msgs,
        parent_branch_id=parent_branch_id,
        parent_thread_id=parent_thread_id,
        collaborators=[interaction.user.id],
        auto_reply=1
    )

    starter_view = BranchHeaderView(branch_id=branch_id)
    await thread.send(view=starter_view)

    fork_note = f" (Forked from <#{parent_thread_id}>)" if parent_thread_id else ""
    await interaction.followup.send(content=f"{OCTICONS_MAP['oct_branch']} **Branch Created:** Joined thread <#{thread.id}>{fork_note}.", ephemeral=True)

async def _execute_branch(interaction: discord.Interaction, message: discord.Message):
    if not interaction.guild:
        await interaction.response.send_message(content="Branches can only be created inside Discord servers.", ephemeral=True)
        return

    gen_record = branch_manager.get_generation(message.id)
    if gen_record:
        versions = gen_record.get("versions", [])
        if len(versions) >= 2:
            async def on_vpick_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                chosen_val = data.get("chosen_version", "1")
                if isinstance(chosen_val, list) and chosen_val:
                    chosen_val = chosen_val[0]
                target_v = int(chosen_val) if str(chosen_val).isdigit() else 1
                await _execute_branch_creation(sub_inter, message, target_version_idx=target_v)

            v_modal = build_branch_version_picker_modal(message.id, versions, on_vpick_submit)
            await interaction.response.send_modal(v_modal)
            return

    await _execute_branch_creation(interaction, message, target_version_idx=None)

async def _execute_view_prompt(interaction: discord.Interaction, message: discord.Message):
    if message.author.id != interaction.client.user.id:
        if not interaction.response.is_done():
            await interaction.response.send_message(content="You can only inspect prompts on PriestyAI's messages.", ephemeral=True)
        else:
            await interaction.followup.send(content="You can only inspect prompts on PriestyAI's messages.", ephemeral=True)
        return

    gen_record = branch_manager.get_generation(message.id)
    if not gen_record:
        if not interaction.response.is_done():
            await interaction.response.send_message(content="No prompt record found for this response.", ephemeral=True)
        else:
            await interaction.followup.send(content="No prompt record found for this response.", ephemeral=True)
        return

    active_idx = gen_record.get("active_version", 1)
    versions = gen_record.get("versions", [])
    v_data = versions[active_idx - 1] if 1 <= active_idx <= len(versions) else {}

    prompt_txt = gen_record.get("prompt_text", "*Empty prompt*")
    author_id = gen_record.get("author_id", "0")
    dur = max(1, v_data.get("duration_seconds", 1))

    card_text = (
        f"### Prompt Inspector (Version {active_idx}/{len(versions)})\n"
        f"**Invoking User:** <@{author_id}>\n"
        f"**Input Prompt:**\n> {prompt_txt}\n\n"
        f"-# Duration: `{dur}s`"
    )
    if not interaction.response.is_done():
        await interaction.response.send_message(content=card_text, ephemeral=True)
    else:
        await interaction.followup.send(content=card_text, ephemeral=True)

async def _execute_retry(interaction: discord.Interaction, message: discord.Message):
    if message.author.id != interaction.client.user.id:
        if not interaction.response.is_done():
            await interaction.response.send_message(content="❌ You can only retry PriestyAI's responses.", ephemeral=True)
        else:
            await interaction.followup.send(content="❌ You can only retry PriestyAI's responses.", ephemeral=True)
        return

    is_foreign = is_foreign_context(interaction)

    if not interaction.response.is_done():
        try:
            await interaction.response.defer(ephemeral=is_foreign)
        except Exception:
            pass

    gen_record = branch_manager.get_generation(message.id)
    if not gen_record:
        ref_msg = None
        if not is_foreign:
            if message.reference and message.reference.message_id and message.channel:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    ref_msg = None

            if not ref_msg and message.channel:
                try:
                    async for prev_m in message.channel.history(limit=6, before=message.created_at):
                        if not prev_m.author.bot:
                            ref_msg = prev_m
                            break
                except Exception:
                    ref_msg = None

        clean_p = ""
        if ref_msg:
            clean_p = re.sub(rf'<@!?{interaction.client.user.id}>', '', ref_msg.content).strip()
            if not clean_p:
                clean_p = "Analyze attached content" if ref_msg.attachments else "Hello!"

        extracted_bot_text = extract_text_from_v2_message(message)
        if not clean_p:
            clean_p = f"Regenerate response: {extracted_bot_text[:100]}"

        initial_v_data = {
            "version_idx": 1,
            "content": apply_message_parsers(extracted_bot_text, message.guild),
            "timeline_blocks": [{"type": "text", "content": extracted_bot_text}],
            "duration_seconds": 1,
            "has_thoughts": True,
            "thoughts": "",
            "formatted_thoughts": None,
            "model": "gemma-4-31b-it",
            "tool_calls": [],
            "attachments": [],
            "staged_components": [],
            "staged_artifacts": [],
            "staged_modals": [],
            "message_ids": [str(message.id)],
            "created_at": message.created_at.isoformat(),
            "status": "ready"
        }

        branch_manager.save_generation(
            message_id=message.id,
            channel_id=getattr(message.channel, "id", 0),
            guild_id=message.guild.id if message.guild else None,
            author_id=ref_msg.author.id if ref_msg else interaction.user.id,
            prompt_text=clean_p,
            attachments=[],
            context_xml="<context></context>",
            initial_version_data=initial_v_data
        )
        gen_record = branch_manager.get_generation(message.id)

    if not gen_record:
        await interaction.followup.send(content="No prompt history or reference found for this message.", ephemeral=True)
        return

    root_msg_id = gen_record["message_id"]
    root_msg = message
    can_edit_directly = not is_foreign

    if can_edit_directly and str(message.id) != str(root_msg_id) and message.channel:
        try:
            root_msg = await message.channel.fetch_message(int(root_msg_id))
        except (discord.Forbidden, discord.HTTPException):
            can_edit_directly = False
            root_msg = message
        except Exception:
            root_msg = message

    existing_versions = gen_record.get("versions", [])
    current_active_idx = gen_record.get("active_version", len(existing_versions))
    if can_edit_directly and 1 <= current_active_idx <= len(existing_versions):
        curr_v_data = existing_versions[current_active_idx - 1]
        sibling_ids = [m for m in curr_v_data.get("message_ids", []) if str(m) != str(root_msg_id)]
        if sibling_ids and message.channel:
            asyncio.create_task(cleanup_sibling_messages(message.channel, sibling_ids))

    new_version_idx = len(existing_versions) + 1
    prompt = gen_record.get("prompt_text", "")
    context_xml = gen_record.get("context_xml", "<context></context>")

    retry_statuses = await generate_retry_witty_statuses(prompt, new_version_idx)

    in_progress_v_data = {
        "version_idx": new_version_idx,
        "content": f"{LOADING_EMOJI} *{retry_statuses[0]}...*",
        "timeline_blocks": [],
        "duration_seconds": 1,
        "has_thoughts": True,
        "thoughts": "",
        "formatted_thoughts": None,
        "tool_calls": [],
        "attachments": [],
        "staged_components": [],
        "staged_artifacts": [],
        "staged_modals": [],
        "message_ids": [str(root_msg_id)],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "generating"
    }
    branch_manager.add_retry_version(root_msg_id, in_progress_v_data)
    branch_manager.set_active_version(root_msg_id, new_version_idx)

    tool_context = ToolExecutionContext(
        channel=message.channel,
        guild=message.guild,
        author=interaction.user,
        bot=interaction.client
    )

    accumulated_thoughts = []
    tool_call_history = []
    active_tool_start_times = {}
    active_model_used = "gemma-4-31b-it"

    show_reply = should_show_reply_button(
        bot=interaction.client,
        guild=interaction.guild,
        channel=message.channel,
        interaction=interaction
    )

    start_t = time.time()
    stop_placeholder_loop = asyncio.Event()
    answer_now_event = asyncio.Event()
    first_content_received = False
    active_tool_subtext = None

    placeholder_view = PlaceholderLayoutView(
        loading_text=format_placeholder_content(retry_statuses[0], active_tool_subtext),
        duration_seconds=1,
        is_enabled=True,
        thought_data={"thoughts": "", "tool_calls": [], "model": active_model_used},
        model_name=active_model_used
    )

    placeholder_msg = None
    if can_edit_directly:
        try:
            await root_msg.edit(view=placeholder_view)
        except (discord.Forbidden, discord.HTTPException):
            can_edit_directly = False

    if not can_edit_directly:
        placeholder_msg = await interaction.followup.send(view=placeholder_view, ephemeral=is_foreign)

    stream_dispatcher = DiscordStreamDispatcher(
        existing_response_msg=root_msg if can_edit_directly else placeholder_msg,
        interaction=interaction if not can_edit_directly else None,
        guild=interaction.guild,
        show_reply_button=show_reply,
        active_version=new_version_idx,
        total_versions=new_version_idx,
        is_ephemeral=is_foreign
    )
    artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(interaction.channel, "id", "global"))

    def get_current_msg():
        return root_msg if can_edit_directly else placeholder_msg

    def get_active_subtext():
        return active_tool_subtext

    placeholder_task = asyncio.create_task(
        update_placeholder_loop(
            get_current_msg, placeholder_view, retry_statuses, get_active_subtext, start_t, stop_placeholder_loop
        )
    )

    try:
        async for event_type, payload in ChatEngine.stream_chat(
            prompt=prompt,
            context_xml=context_xml,
            bot_user_id=interaction.client.user.id,
            tool_context=tool_context,
            answer_now_event=answer_now_event
        ):
            if event_type == "ACTIVE_MODEL":
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
                tool_name = payload.get("name", "Tool")
                args = payload.get("args", {})
                active_tool_start_times[tool_name] = time.perf_counter()
                active_tool_subtext = get_tool_subtext(tool_name, args)
                if tool_name in ["create_artifact", "update_artifact"]:
                    stream_dispatcher.add_artifact_placeholder(tool_name, args)

            elif event_type == "TOOL_END":
                tool_name = payload.get("name", "Tool")
                st = active_tool_start_times.pop(tool_name, time.perf_counter())
                dur_ms = int((time.perf_counter() - st) * 1000)
                tool_call_history.append({"name": tool_name, "args": payload.get("args", {}), "result": payload.get("result", {}), "duration_ms": dur_ms})
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

        dur_sec = max(1, int(time.time() - start_t))
        active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
        has_thoughts = bool(accumulated_thoughts or active_tools)
        final_content = stream_dispatcher.get_accumulated_text()
        parsed_final_content = apply_message_parsers(final_content, interaction.guild)

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

        sanitized_timeline = []
        for b in stream_dispatcher.timeline:
            b_copy = dict(b)
            if b_copy.get("type") == "artifact" and "artifact" in b_copy:
                art_copy = dict(b_copy["artifact"])
                art_copy.pop("data_bytes", None)
                b_copy["artifact"] = art_copy
            sanitized_timeline.append(b_copy)

        raw_collected_thoughts = "".join(accumulated_thoughts)
        target_saved_id = root_msg_id if can_edit_directly else (str(placeholder_msg.id) if placeholder_msg else str(root_msg_id))
        sent_msg_ids = [str(m.id) for m in stream_dispatcher.sent_messages if m] or [target_saved_id]

        new_v_data = {
            "version_idx": new_version_idx,
            "content": parsed_final_content,
            "timeline_blocks": sanitized_timeline,
            "duration_seconds": dur_sec,
            "has_thoughts": has_thoughts,
            "thoughts": raw_collected_thoughts,
            "formatted_thoughts": None,
            "model": active_model_used,
            "tool_calls": tool_call_history,
            "attachments": stored_attachments,
            "staged_components": tool_context.staged_components,
            "staged_artifacts": sanitized_artifacts,
            "staged_followups": stream_dispatcher.staged_followups,
            "staged_modals": tool_context.staged_modals,
            "message_ids": sent_msg_ids,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "ready"
        }

        branch_manager.update_version_data(root_msg_id, new_version_idx, new_v_data)
        mod_map = {m["modal_id"]: m for m in tool_context.staged_modals}

        await stream_dispatcher.finalize(
            staged_artifacts=tool_context.staged_artifacts,
            staged_components=tool_context.staged_components,
            staged_followups=stream_dispatcher.staged_followups,
            modals_map=mod_map,
            thought_duration=dur_sec,
            has_thoughts=has_thoughts,
            show_reply_button=show_reply,
            active_version=new_version_idx,
            total_versions=new_version_idx,
            message_id=root_msg_id if can_edit_directly else (placeholder_msg.id if placeholder_msg else root_msg_id)
        )

    except Exception as e:
        stop_placeholder_loop.set()
        if placeholder_task and not placeholder_task.done():
            placeholder_task.cancel()
        logger.exception(f"Retry error: {e}")
        await interaction.followup.send(content=f"Retry generation failed: `{e}`", ephemeral=True)

def setup_context_menus(tree: app_commands.CommandTree):

    @tree.context_menu(name="Run as Prompt")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def run_as_prompt_context_menu(interaction: discord.Interaction, message: discord.Message):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await _execute_run_as_prompt(sub_inter, message)

            welcome_modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(welcome_modal)
            return

        await _execute_run_as_prompt(interaction, message)

    @tree.context_menu(name="Branch")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def branch_context_menu(interaction: discord.Interaction, message: discord.Message):
        await _execute_branch(interaction, message)

    @tree.context_menu(name="Edit")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def edit_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="You can only edit PriestyAI's responses.", ephemeral=True)
            return

        gen_record = branch_manager.get_generation(message.id)
        is_creator = gen_record and gen_record.get("author_id") == str(interaction.user.id)
        is_mod = interaction.guild and interaction.user.guild_permissions.manage_messages

        if not (is_creator or is_mod or not interaction.guild):
            await interaction.response.send_message(content="You lack permission to edit this response.", ephemeral=True)
            return

        active_idx = gen_record.get("active_version", 1) if gen_record else 1
        versions = gen_record.get("versions", []) if gen_record else []
        current_text = versions[active_idx - 1].get("content", "") if 1 <= active_idx <= len(versions) else extract_text_from_v2_message(message)

        fields = [
            {
                "type": "text_display",
                "content": "Edit this response directly. Modifications will update the message in-place."
            },
            {
                "type": "text_input",
                "custom_id": "edited_content",
                "label": "Response Content",
                "description": "The updated text content for this message",
                "style": "paragraph",
                "value": current_text,
                "required": True,
                "max_length": 4000
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_text = data.get("edited_content", "").strip()
            parsed_text = apply_message_parsers(new_text, interaction.guild)

            branch_manager.update_active_version_content(message.id, parsed_text)

            show_reply = should_show_reply_button(
                bot=interaction.client,
                guild=interaction.guild,
                channel=message.channel,
                interaction=interaction
            )

            v2_view = build_v2_message_layout(
                raw_text=parsed_text,
                guild=interaction.guild,
                show_reply_button=show_reply,
                message_id=message.id,
                is_live_stream=False
            )
            
            try:
                await message.edit(view=v2_view)
            except discord.Forbidden:
                await sub_inter.response.send_message(view=v2_view, ephemeral=True)
                return
            await sub_inter.response.send_message(content="Response edited in-place successfully.", ephemeral=True)

        modal = DynamicModalV2(
            title="Edit PriestyAI Response",
            custom_id="modal_edit_response",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    @tree.context_menu(name="Translate")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def translate_context_menu(interaction: discord.Interaction, message: discord.Message):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        extracted_text = extract_text_from_v2_message(message)
        fields = [
            {
                "type": "text_display",
                "content": "Translate this message into any language."
            },
            {
                "type": "text_input",
                "custom_id": "target_language",
                "label": "Language",
                "description": "The language you want to translate the text into",
                "placeholder": "e.g. Spanish, Japanese, German, French...",
                "style": "short",
                "required": True,
                "max_length": 60
            },
            {
                "type": "text_input",
                "custom_id": "content",
                "label": "Content",
                "description": "The text content to be translated",
                "value": extracted_text[:3500],
                "style": "paragraph",
                "required": True,
                "max_length": 3500
            },
            {
                "type": "radio_group",
                "custom_id": "visibility",
                "label": "Visibility",
                "description": "Choose who can see the translated output",
                "value": "private",
                "options": [
                    {
                        "label": "Ephemeral",
                        "value": "private",
                        "description": "Only visible to you",
                        "default": True
                    },
                    {
                        "label": "Public",
                        "value": "public",
                        "description": "Visible to all members"
                    }
                ],
                "required": True
            }
        ]

        async def on_translate_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            target_lang = data.get("target_language", "English").strip()
            content_to_translate = data.get("content", "").strip()
            
            raw_vis = data.get("visibility", "private")
            if isinstance(raw_vis, list):
                raw_vis = raw_vis[0] if raw_vis else "private"
            is_ephemeral = (str(raw_vis).strip().lower() == "private")

            if not sub_inter.response.is_done():
                await sub_inter.response.defer(ephemeral=is_ephemeral)

            client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
            if not client:
                await sub_inter.followup.send(content="Translation service is currently busy.", ephemeral=True)
                return

            instruction = (
                f"You are a professional linguistic translator. Translate the following text into {target_lang}.\n"
                "Maintain exact meaning, tone, Markdown structures, lists, and code blocks.\n"
                "Do NOT wrap the output in extra blockquotes or conversational preambles. Do NOT emit <artifact> tags.\n"
                "Return ONLY the direct translation."
            )

            try:
                res = await client.aio.models.generate_content(
                    model=active_model,
                    contents=f"{instruction}\n\nText:\n{content_to_translate}"
                )
                raw_translation = res.text.strip() if (res and res.text) else "Translation failed."
                cleaned_translation = re.sub(r'<artifact[^>]*>.*?</artifact>', '', raw_translation, flags=re.DOTALL).strip()
                parsed_translation = apply_message_parsers(cleaned_translation, sub_inter.guild)
                
                formatted_resp = f"### Translated to {target_lang.title()}\n{parsed_translation}"
                final_view = build_v2_message_layout(raw_text=formatted_resp, guild=sub_inter.guild, show_reply_button=False)
                
                await sub_inter.followup.send(view=final_view, ephemeral=is_ephemeral)

            except Exception as e:
                logger.exception(f"Translation error: {e}")
                err_view = build_v2_message_layout(raw_text=f"Translation error: `{e}`", guild=sub_inter.guild, show_reply_button=False)
                await sub_inter.followup.send(view=err_view, ephemeral=True)

        modal = DynamicModalV2(
            title="Translate Message",
            custom_id=f"modal_translate_{message.id}",
            fields_schema=fields,
            on_submit_callback=on_translate_submit
        )
        await interaction.response.send_modal(modal)

    @tree.context_menu(name="All Commands")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def all_commands_context_menu(interaction: discord.Interaction, message: discord.Message):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        is_foreign = is_foreign_context(interaction)
        is_guild_text = (
            not is_foreign
            and interaction.guild is not None
            and (isinstance(interaction.channel, discord.TextChannel) or isinstance(interaction.channel, discord.Thread))
        )
        is_bot_message = (message.author.id == interaction.client.user.id)

        select_options = [
            {
                "label": "Summarize",
                "value": "summarize",
                "description": "Generate key takeaways with quick reply turn",
                "default": True
            },
            {
                "label": "Run as Prompt",
                "value": "run_as_prompt",
                "description": "Execute this message content as a prompt"
            }
        ]

        if is_bot_message:
            select_options.append({
                "label": "View Prompt",
                "value": "view_prompt",
                "description": "Inspect prompt telemetry and model duration"
            })
            select_options.append({
                "label": "Retry",
                "value": "retry",
                "description": "Regenerate this response with a new version"
            })
            if not is_foreign:
                select_options.append({
                    "label": "Delete Bot Message",
                    "value": "delete_message",
                    "description": "Delete this response sent by PriestyAI"
                })

        if is_guild_text:
            select_options.append({
                "label": "Branch",
                "value": "branch",
                "description": "Fork discussion into an isolated thread"
            })

        if is_foreign:
            select_options.append({
                "label": "Add to Chat Context",
                "value": "add_to_context",
                "description": "Queues this message into your next /chat query"
            })

        fields = [
            {
                "type": "text_display",
                "content": "Select an action to execute on this message:"
            },
            {
                "type": "string_select",
                "custom_id": "selected_action",
                "label": "Action",
                "description": "Choose an action to run on this message",
                "placeholder": "Choose an action...",
                "options": select_options,
                "required": True
            },
            {
                "type": "text_display",
                "content": "-# Note: Translate and Edit are on the main context menu to open dedicated input forms."
            }
        ]

        async def on_action_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            selected = data.get("selected_action", "summarize")
            if isinstance(selected, list) and selected:
                selected = selected[0]

            if selected == "delete_message":
                if message.author.id != interaction.client.user.id:
                    await sub_inter.response.send_message(content="❌ You can only delete messages sent by PriestyAI.", ephemeral=True)
                    return

                is_dm = not interaction.guild
                is_admin = is_user_server_admin(interaction.user, interaction.guild) if interaction.guild else False
                
                gen_record = branch_manager.get_generation(message.id)
                is_author = gen_record and str(gen_record.get("author_id")) == str(interaction.user.id)

                if not (is_dm or is_admin or is_author):
                    await sub_inter.response.send_message(
                        content="❌ You lack permission to delete this message. Only the prompt author or server moderators can delete it.",
                        ephemeral=True
                    )
                    return

                if gen_record and interaction.channel:
                    versions = gen_record.get("versions", [])
                    curr_v = gen_record.get("active_version", len(versions))
                    if 1 <= curr_v <= len(versions):
                        sibling_ids = [m for m in versions[curr_v - 1].get("message_ids", []) if str(m) != str(message.id)]
                        if sibling_ids:
                            asyncio.create_task(cleanup_sibling_messages(interaction.channel, sibling_ids))

                try:
                    await message.delete()
                    await sub_inter.response.send_message(content=f"{OCTICONS_MAP['oct_trash']} **Message deleted.**", ephemeral=True)
                except Exception as del_err:
                    await sub_inter.response.send_message(content=f"❌ Failed to delete message: `{del_err}`", ephemeral=True)

            elif selected == "add_to_context":
                chan_id = str(message.channel.id if message.channel else "dm")
                uid = str(sub_inter.user.id)
                extracted_content = extract_text_from_v2_message(message)

                staged_entry = {
                    "id": str(message.id),
                    "author": message.author.display_name,
                    "author_id": str(message.author.id),
                    "content": extracted_content[:3000]
                }

                count = memory_manager.add_staged_chat_context(chan_id, uid, staged_entry)

                clear_view = View()
                clear_btn = Button(
                    label="Clear Queued Context",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"clear_staged_ctx_{chan_id}"
                )
                clear_view.add_item(clear_btn)

                await sub_inter.response.send_message(
                    content=(
                        f"📎 **Added message from {message.author.mention} to your `/chat` context queue.**\n"
                        f"-# Total queued in this channel: `{count}` message(s). Will be included in your next `/chat` turn."
                    ),
                    view=clear_view,
                    ephemeral=True
                )

            elif selected == "summarize":
                await _execute_summarize(sub_inter, message)
            elif selected == "run_as_prompt":
                await _execute_run_as_prompt(sub_inter, message)
            elif selected == "view_prompt":
                await _execute_view_prompt(sub_inter, message)
            elif selected == "retry":
                await _execute_retry(sub_inter, message)
            elif selected == "branch":
                await _execute_branch(sub_inter, message)
            else:
                await sub_inter.response.send_message(content="Unrecognized action.", ephemeral=True)

        modal = DynamicModalV2(
            title="All Commands",
            custom_id=f"modal_all_commands_{message.id}",
            fields_schema=fields,
            on_submit_callback=on_action_submit
        )
        await interaction.response.send_modal(modal)