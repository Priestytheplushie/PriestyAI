import time
import json
import base64
import asyncio
import logging
import aiohttp
from typing import Any, Callable
from datetime import datetime, timezone
import discord
from discord import app_commands
from google.genai import types

from core.engine import ChatEngine
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
from parsers.artifact_parser import ArtifactStreamParser
from tools.registry import ToolExecutionContext
from ui.modals import DynamicModalV2
from ui.onboarding_views import (
    build_welcome_terms_modal,
    LegalDocumentViewerLayoutView,
    BannedUserNoticeView
)

logger = logging.getLogger("PriestyAI.Commands.Chat")

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
    stream_dispatcher = DiscordStreamDispatcher(
        interaction=interaction,
        is_ephemeral=is_ephemeral,
        guild=guild,
        show_reply_button=show_reply
    )
    artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(channel, "id", "global"))

    accumulated_thoughts = []
    tool_call_history = []
    active_model_used = "gemma-4-31b-it"

    try:
        async for event_type, payload in ChatEngine.stream_chat(
            prompt=multimodal_prompt,
            context_xml=context_xml,
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
                t_name = payload.get("name", "")
                t_args = payload.get("args", {})
                if t_name in ["create_artifact", "update_artifact"]:
                    stream_dispatcher.add_artifact_placeholder(t_name, t_args)

            elif event_type == "TOOL_END":
                t_name = payload.get("name", "")
                tool_call_history.append(payload)
                if t_name in ["create_artifact", "update_artifact"] and tool_context.staged_artifacts:
                    last_art = tool_context.staged_artifacts[-1]
                    stream_dispatcher.update_artifact_ready(last_art)
                    art_bytes = last_art.get("data_bytes", b"")
                    art_fname = last_art.get("filename", "artifact.zip")
                    if art_bytes:
                        stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

                elif t_name in ["search_image", "search_gif", "generate_image", "edit_image", "execute_code"] and tool_context.staged_image_bytes:
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

        for art in tool_context.staged_artifacts:
            art_bytes = art.get("data_bytes", b"")
            art_fname = art.get("filename", "artifact.zip")
            if art_bytes:
                stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

        await stream_dispatcher.finalize(
            staged_artifacts=tool_context.staged_artifacts,
            staged_components=tool_context.staged_components,
            staged_followups=stream_dispatcher.staged_followups,
            thought_duration=final_dur,
            has_thoughts=has_reasoning,
            show_reply_button=show_reply,
            active_version=1,
            total_versions=1
        )

        final_text = stream_dispatcher.get_accumulated_text()
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
            await interaction.edit_original_response(view=err_view)
        except discord.HTTPException:
            pass

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
        app_commands.Choice(name="Public", value="public"),
        app_commands.Choice(name="Ephemeral", value="private")
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
        stream_dispatcher = DiscordStreamDispatcher(
            interaction=interaction,
            is_ephemeral=is_ephemeral,
            guild=interaction.guild,
            show_reply_button=show_reply
        )
        tool_context = ToolExecutionContext(channel=interaction.channel, guild=interaction.guild, author=interaction.user, bot=interaction.client)
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=getattr(interaction.channel, "id", "global"))

        accumulated_thoughts = []
        tool_call_history = []

        try:
            async for event_type, payload in ChatEngine.stream_chat(
                prompt=query,
                context_xml="<context></context>",
                bot_user_id=interaction.client.user.id,
                tool_context=tool_context
            ):
                if event_type == "RECALLED_MEMORIES":
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
                    t_name = payload.get("name", "")
                    t_args = payload.get("args", {})
                    if t_name in ["create_artifact", "update_artifact"]:
                        stream_dispatcher.add_artifact_placeholder(t_name, t_args)

                elif event_type == "TOOL_END":
                    t_name = payload.get("name", "")
                    tool_call_history.append(payload)
                    if t_name in ["create_artifact", "update_artifact"] and tool_context.staged_artifacts:
                        last_art = tool_context.staged_artifacts[-1]
                        stream_dispatcher.update_artifact_ready(last_art)
                        art_bytes = last_art.get("data_bytes", b"")
                        art_fname = last_art.get("filename", "artifact.zip")
                        if art_bytes:
                            stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

                    elif t_name in ["search_image", "search_gif", "generate_image", "execute_code"] and tool_context.staged_image_bytes:
                        img_fname = tool_context.staged_image_filename
                        img_bytes = tool_context.staged_image_bytes
                        stream_dispatcher.add_media_block(img_fname, img_bytes)
                        tool_context.staged_image_bytes = None

                elif event_type == "CONTENT":
                    await artifact_parser.feed(payload)

            await artifact_parser.finish()

            final_dur = max(1, int(time.time() - thinking_start_time))
            has_reasoning = bool(accumulated_thoughts or tool_call_history)

            stored_attachments = []
            for raw_att in stream_dispatcher.raw_attachment_buffers:
                b64 = base64.b64encode(raw_att["bytes"]).decode("utf-8")
                stored_attachments.append({"filename": raw_att["filename"], "data_b64": b64})

            for art in tool_context.staged_artifacts:
                art_bytes = art.get("data_bytes", b"")
                art_fname = art.get("filename", "artifact.zip")
                if art_bytes:
                    stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

            await stream_dispatcher.finalize(
                staged_artifacts=tool_context.staged_artifacts,
                staged_components=tool_context.staged_components,
                staged_followups=stream_dispatcher.staged_followups,
                thought_duration=final_dur,
                has_thoughts=has_reasoning,
                show_reply_button=show_reply,
                active_version=1,
                total_versions=1
            )

        except Exception as e:
            logger.exception(f"Error in /ask command: {e}")
            try:
                err_view = build_v2_message_layout(raw_text=f"Error: `{e}`", guild=interaction.guild)
                await interaction.edit_original_response(view=err_view)
            except discord.HTTPException:
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