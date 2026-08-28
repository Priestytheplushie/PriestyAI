import time
import base64
import asyncio
import logging
import discord
from discord import app_commands
from core.engine import ChatEngine
from core.memory_manager import memory_manager
from core.config_manager import config_manager
from core.moderation import (
    check_moderation,
    generate_friendly_refusal,
    log_moderation_violation,
    is_user_banned,
    ban_user
)
from handlers.stream_handler import (
    DiscordStreamDispatcher,
    build_v2_message_layout
)
from parsers.artifact_parser import ArtifactStreamParser
from tools.registry import ToolExecutionContext
from ui.onboarding_views import (
    build_welcome_terms_modal,
    build_terms_review_modal,
    BannedUserNoticeView
)

logger = logging.getLogger("PriestyAI.Commands.Chat")

def setup_chat_commands(tree: app_commands.CommandTree):

    @tree.command(name="terms", description="Review PriestyAI's Terms of Service, Safety Guidelines, and Moderation Policies")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def terms_command(interaction: discord.Interaction):
        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_terms_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.send_message(
                    content="✅ **Terms Accepted:** Thank you for agreeing to the Terms of Service & Safety Guidelines! You can now use all PriestyAI features.",
                    ephemeral=True
                )

            modal = build_welcome_terms_modal(on_agree_callback=on_terms_agreed)
            await interaction.response.send_modal(modal)
            return

        modal = build_terms_review_modal()
        await interaction.response.send_modal(modal)

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

        thinking_start_time = time.time()
        stream_dispatcher = DiscordStreamDispatcher(interaction=interaction, is_ephemeral=is_ephemeral, guild=interaction.guild)
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
                active_version=1,
                total_versions=1
            )

        except Exception as e:
            logger.exception(f"Error in /ask command: {e}")
            try:
                err_view = build_v2_message_layout(raw_text=f"⚠️ Error: `{e}`", guild=interaction.guild)
                await interaction.edit_original_response(view=err_view)
            except discord.HTTPException:
                pass