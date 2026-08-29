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
import discord
from discord import app_commands
from config.settings import LOADING_EMOJI
from core.engine import ChatEngine
from core.client_manager import client_manager
from core.branch_manager import branch_manager
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
from tools.registry import ToolExecutionContext
from ui.modals import DynamicModalV2
from ui.onboarding_views import BannedUserNoticeView
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

def setup_context_menus(tree: app_commands.CommandTree):

    @tree.context_menu(name="Run as Prompt")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def run_as_prompt_context_menu(interaction: discord.Interaction, message: discord.Message):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        extracted_text = extract_text_from_v2_message(message)
        clean_prompt = re.sub(rf'<@!?{interaction.client.user.id}>', '', extracted_text).strip()
        
        attachment_parts, raw_image_bytes = await extract_message_attachments_raw(message)
        if not clean_prompt and not attachment_parts:
            await interaction.response.send_message(content="❌ The selected message contains no readable text or media prompt.", ephemeral=True)
            return

        if not clean_prompt:
            clean_prompt = "Analyze the attached content."

        is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(clean_prompt, raw_image_bytes)
        if is_flagged:
            log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
            if is_zero_tolerance:
                ban_user(interaction.user.id, reason=f"Zero-tolerance violation: {', '.join(flagged_cats)}")
                ban_view = BannedUserNoticeView(author=interaction.user)
                await interaction.response.send_message(view=ban_view, ephemeral=True)
                return

            refusal_text = await generate_friendly_refusal(flagged_cats)
            await interaction.response.send_message(content=refusal_text, ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=False)
            await interaction.delete_original_response()
        except Exception:
            pass

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

        stream_dispatcher = DiscordStreamDispatcher(
            origin_message=message,
            guild=interaction.guild,
            show_reply_button=show_reply
        )
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(interaction.channel, "id", "global"))

        accumulated_thoughts = []
        tool_call_history = []
        active_tool_start_times = {}
        active_model_used = "gemma-4-31b-it"

        start_time = time.time()

        try:
            async with message.channel.typing():
                async for event_type, payload in ChatEngine.stream_chat(
                    prompt=multimodal_prompt,
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
                        tool_name = payload.get("name", "Tool")
                        args = payload.get("args", {})
                        active_tool_start_times[tool_name] = time.perf_counter()
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
                        await artifact_parser.feed(payload)

            await artifact_parser.finish()

            final_duration = max(1, int(time.time() - start_time))
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
            logger.exception(f"Run as Prompt error: {e}")

    @tree.context_menu(name="Branch")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def branch_context_menu(interaction: discord.Interaction, message: discord.Message):
        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(content="❌ Branches can only be created inside standard server text channels.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        history_msgs = []
        try:
            raw_history = [m async for m in interaction.channel.history(limit=20, before=message.created_at)]
            raw_history.reverse()
            raw_history.append(message)

            for m in raw_history:
                extracted_content = extract_text_from_v2_message(m)
                history_msgs.append({
                    "id": str(m.id),
                    "role": "assistant" if m.author.id == interaction.client.user.id else "user",
                    "author": m.author.display_name,
                    "author_id": str(m.author.id),
                    "content": extracted_content,
                    "timestamp": m.created_at.isoformat()
                })
        except Exception as ex:
            logger.warning(f"Branch history fetch exception: {ex}")

        title = "Exploration Branch"
        try:
            sample_text = extract_text_from_v2_message(message)[:300]
            title_prompt = f"Generate a clean 3 to 5 word topic title for this discussion. Output ONLY the title:\n{sample_text}"
            client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
            if client:
                res = await client.aio.models.generate_content(model=active_model, contents=title_prompt)
                if res.text:
                    title = res.text.strip().replace('"', '').replace("'", "")[:60]
        except Exception:
            pass

        try:
            thread = await message.create_thread(name=title)
        except Exception:
            thread = await interaction.channel.create_thread(name=title, type=discord.ChannelType.public_thread)

        try:
            await thread.add_user(interaction.user)
        except Exception as ex:
            logger.debug(f"Failed to add user to thread: {ex}")

        try:
            async for sys_m in interaction.channel.history(limit=5):
                if sys_m.type == discord.MessageType.thread_created:
                    await sys_m.delete()
                    break
        except Exception:
            pass

        branch_id = str(uuid.uuid4())[:8]
        branch_manager.create_branch(
            branch_id=branch_id,
            thread_id=thread.id,
            channel_id=interaction.channel_id,
            guild_id=interaction.guild_id,
            creator_id=interaction.user.id,
            title=title,
            root_message_id=message.id,
            messages=history_msgs
        )

        await interaction.followup.send(content=f"🧵 **Branch Created:** Joined thread <#{thread.id}>.", ephemeral=True)

    @tree.context_menu(name="Retry")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def retry_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="❌ You can only retry PriestyAI's responses.", ephemeral=True)
            return

        try:
            await interaction.response.defer(ephemeral=True)
            await interaction.delete_original_response()
        except Exception:
            pass

        gen_record = branch_manager.get_generation(message.id)
        if not gen_record:
            ref_msg = None
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

            if ref_msg:
                clean_p = re.sub(rf'<@!?{interaction.client.user.id}>', '', ref_msg.content).strip()
                if not clean_p:
                    clean_p = "Analyze attached content" if ref_msg.attachments else "Hello!"

                extracted_bot_text = extract_text_from_v2_message(message)

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
                    channel_id=message.channel.id,
                    guild_id=message.guild.id if message.guild else None,
                    author_id=ref_msg.author.id,
                    prompt_text=clean_p,
                    attachments=[],
                    context_xml="<context></context>",
                    initial_version_data=initial_v_data
                )
                gen_record = branch_manager.get_generation(message.id)

        if not gen_record:
            await interaction.followup.send(content="❌ No prompt history or reference found for this message.", ephemeral=True)
            return

        root_msg_id = gen_record["message_id"]
        root_msg = message
        if str(message.id) != str(root_msg_id) and message.channel:
            try:
                root_msg = await message.channel.fetch_message(int(root_msg_id))
            except Exception:
                root_msg = message

        existing_versions = gen_record.get("versions", [])
        current_active_idx = gen_record.get("active_version", len(existing_versions))
        if 1 <= current_active_idx <= len(existing_versions):
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

        stream_dispatcher = DiscordStreamDispatcher(
            existing_response_msg=root_msg,
            guild=interaction.guild,
            show_reply_button=show_reply,
            active_version=new_version_idx,
            total_versions=new_version_idx
        )
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(interaction.channel, "id", "global"))

        start_t = time.time()
        stop_placeholder_loop = asyncio.Event()
        answer_now_event = asyncio.Event()
        first_content_received = False
        active_tool_subtext = None

        def get_current_msg():
            return root_msg

        def get_active_subtext():
            return active_tool_subtext

        placeholder_view = PlaceholderLayoutView(
            loading_text=format_placeholder_content(retry_statuses[0], active_tool_subtext),
            duration_seconds=1,
            is_enabled=True,
            thought_data={"thoughts": "", "tool_calls": [], "model": active_model_used},
            model_name=active_model_used
        )

        try:
            await root_msg.edit(view=placeholder_view)
        except Exception as ex:
            logger.warning(f"Failed to set initial retry placeholder view: {ex}")

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
            sent_msg_ids = [str(m.id) for m in stream_dispatcher.sent_messages if m] or [str(root_msg_id)]

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
                message_id=root_msg_id
            )

        except Exception as e:
            stop_placeholder_loop.set()
            if placeholder_task and not placeholder_task.done():
                placeholder_task.cancel()
            logger.exception(f"Retry error: {e}")
            await interaction.followup.send(content=f"⚠️ Retry generation failed: `{e}`", ephemeral=True)

    @tree.context_menu(name="View Prompt")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def view_prompt_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="❌ You can only inspect prompts on PriestyAI's messages.", ephemeral=True)
            return

        gen_record = branch_manager.get_generation(message.id)
        if not gen_record:
            await interaction.response.send_message(content="❌ No prompt record found for this response.", ephemeral=True)
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
        await interaction.response.send_message(content=card_text, ephemeral=True)

    @tree.context_menu(name="Edit")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def edit_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="❌ You can only edit PriestyAI's responses.", ephemeral=True)
            return

        gen_record = branch_manager.get_generation(message.id)
        is_creator = gen_record and gen_record.get("author_id") == str(interaction.user.id)
        is_mod = interaction.guild and interaction.user.guild_permissions.manage_messages

        if not (is_creator or is_mod or not interaction.guild):
            await interaction.response.send_message(content="❌ You lack permission to edit this response.", ephemeral=True)
            return

        active_idx = gen_record.get("active_version", 1) if gen_record else 1
        versions = gen_record.get("versions", []) if gen_record else []
        current_text = versions[active_idx - 1].get("content", "") if 1 <= active_idx <= len(versions) else extract_text_from_v2_message(message)

        fields = [
            {
                "type": "text_display",
                "content": "Edit this response directly. Modifications will update the message in-place in Components V2 layout."
            },
            {
                "type": "text_input",
                "custom_id": "edited_content",
                "label": "Response Content",
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
            await message.edit(view=v2_view)
            await sub_inter.response.send_message(content="✅ **Response edited in-place successfully.**", ephemeral=True)

        modal = DynamicModalV2(
            title="Edit PriestyAI Response",
            custom_id="modal_edit_response",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)