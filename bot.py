import io
import re
import os
import time
import base64
import asyncio
import logging
from typing import Any
import discord
from discord import app_commands
from config.settings import DISCORD_TOKEN
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.poll_manager import poll_manager
from core.searxng_client import searxng_client
from core.playground_server import playground_server
from core.screenshot_service import screenshot_service
from agent.session_manager import session_manager
from agent.engine import AgentEngine
from agent.views import AgentStepInspectorView, AgentFinalDeliverableView, build_agent_new_task_modal
from agent.constants import OCTICONS_MAP
from handlers.chat_handler import ChatHandler
from commands import setup_commands, build_retry_placeholder_layout
from commands.generate import model_catalog
from handlers.stream_handler import (
    build_v2_message_layout,
    apply_message_parsers,
    chunk_timeline,
    cleanup_sibling_messages
)
from ui.thought_container import ThoughtContainerView
from ui.context_views import BranchTranscriptView
from ui.artifact_views import build_code_preview_modal, build_artifact_open_modal, prepare_artifact_download_payload

logger = logging.getLogger("PriestyAI.Main")

IDLE_TIMEOUT_SECONDS = 600

class PriestyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.last_activity_time = time.time()
        self.current_status = discord.Status.online
        self.presence_task: asyncio.Task | None = None
        self.poll_watchdog_task: asyncio.Task | None = None

    async def setup_hook(self):
        asyncio.create_task(searxng_client.ensure_running())
        asyncio.create_task(playground_server.start())
        asyncio.create_task(screenshot_service.start())
        asyncio.create_task(session_manager.prune_stale_workspaces())
        asyncio.create_task(model_catalog.ensure_initialized())

        setup_commands(self.tree)
        try:
            synced = await self.tree.sync()
            logger.info(f"[CommandTree] Successfully synced {len(synced)} application command(s) & context menus globally!")
        except Exception as e:
            logger.error(f"[CommandTree] Failed to sync application commands: {e}")

    async def close(self):
        await screenshot_service.stop()
        await playground_server.stop()
        await super().close()

    async def on_ready(self):
        logger.info("=" * 60)
        logger.info(f"PriestyAI logged in as: {self.user} (ID: {self.user.id}) | Connected to {len(self.guilds)} guild(s)")
        logger.info("=" * 60)

        await self.set_bot_presence(discord.Status.online)
        
        if not self.presence_task or self.presence_task.done():
            self.presence_task = asyncio.create_task(self.presence_watchdog_loop())

        if not self.poll_watchdog_task or self.poll_watchdog_task.done():
            self.poll_watchdog_task = asyncio.create_task(self.poll_watchdog_loop())

    async def set_bot_presence(self, status: discord.Status):
        self.current_status = status
        activity = discord.CustomActivity(name="Listening for @mentions")
        try:
            await self.change_presence(status=status, activity=activity)
            logger.debug(f"[Presence] Updated status to {status.name.upper()}")
        except Exception as e:
            logger.warning(f"Failed to update presence: {e}")

    def record_activity(self):
        self.last_activity_time = time.time()
        if self.current_status == discord.Status.idle:
            asyncio.create_task(self.set_bot_presence(discord.Status.online))

    async def poll_watchdog_loop(self):
        while not self.is_closed():
            try:
                await asyncio.sleep(30)
                await poll_manager.poll_watchdog_tick(self)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Poll watchdog loop error: {e}")

    async def presence_watchdog_loop(self):
        while not self.is_closed():
            try:
                await asyncio.sleep(30)
                
                if client_manager.is_completely_exhausted():
                    if self.current_status != discord.Status.dnd:
                        logger.warning("[Presence] All API keys exhausted. Setting status to DND.")
                        await self.set_bot_presence(discord.Status.dnd)
                    continue

                now = time.time()
                if (now - self.last_activity_time) >= IDLE_TIMEOUT_SECONDS:
                    if self.current_status == discord.Status.online:
                        logger.info("[Presence] Inactivity threshold reached (10m). Switching to Idle.")
                        await self.set_bot_presence(discord.Status.idle)
                else:
                    if self.current_status != discord.Status.online:
                        await self.set_bot_presence(discord.Status.online)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Presence watchdog loop error: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        self.record_activity()

        if isinstance(message.channel, discord.Thread):
            agent_session = session_manager.get_session_by_thread_id(message.channel.id)
            if agent_session:
                await AgentEngine.handle_thread_message(message, agent_session)
                return

            branch = branch_manager.get_branch_by_thread_id(message.channel.id)
            if branch:
                branch_manager.add_branch_message(
                    thread_id=message.channel.id,
                    role="user",
                    author_name=message.author.display_name,
                    author_id=message.author.id,
                    content=message.clean_content
                )
                await ChatHandler.handle_message(self, message, force_respond=True)
                return

        is_ai_channel = False
        if message.guild and message.channel:
            is_ai_channel = config_manager.is_ai_channel(message.guild.id, message.channel.id)

        await ChatHandler.handle_message(self, message, force_respond=is_ai_channel)

    async def on_interaction(self, interaction: discord.Interaction):
        self.record_activity()
        
        custom_id = None
        if hasattr(interaction, "data") and interaction.data:
            if isinstance(interaction.data, dict):
                custom_id = interaction.data.get("custom_id")
            else:
                custom_id = getattr(interaction.data, "custom_id", None)

        if not custom_id:
            return

        async def resolve_attachment_url(filename: str, message_id_val: str | int) -> str | None:
            target_msg = interaction.message
            if not target_msg or not target_msg.attachments:
                mid_str = str(message_id_val).strip() if message_id_val is not None else ""
                if interaction.channel and mid_str.isdigit() and mid_str not in ("0", "temp"):
                    try:
                        target_msg = await interaction.channel.fetch_message(int(mid_str))
                    except Exception:
                        target_msg = None

            if target_msg and target_msg.attachments:
                for att in target_msg.attachments:
                    if att.filename == filename:
                        return att.url
                return target_msg.attachments[0].url
            return None

        if custom_id.startswith("fup:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                msg_id = parts[1]
                fup_idx = int(parts[2]) if parts[2].isdigit() else 0

                gen = branch_manager.get_generation(msg_id)
                if gen and interaction.message:
                    root_id = gen["message_id"]
                    active_v = gen.get("active_version", 1)
                    versions = gen.get("versions", [])
                    if 1 <= active_v <= len(versions):
                        v_data = versions[active_v - 1]
                        fups = v_data.get("staged_followups", [])
                        if 0 <= fup_idx < len(fups):
                            fup_item = fups[fup_idx]
                            prompt_text = fup_item.get("prompt", "")

                            for f in fups:
                                f["disabled"] = True

                            v_data["staged_followups"] = fups
                            branch_manager.update_version_data(root_id, active_v, v_data)

                            mod_map = {m["modal_id"]: m for m in v_data.get("staged_modals", [])}
                            disabled_view = build_v2_message_layout(
                                raw_text=v_data.get("content", "") if not v_data.get("timeline_blocks") else None,
                                timeline_blocks=v_data.get("timeline_blocks"),
                                guild=interaction.guild,
                                staged_components=v_data.get("staged_components", []),
                                staged_artifacts=v_data.get("staged_artifacts", []),
                                staged_followups=fups,
                                modals_map=mod_map,
                                thought_duration=max(1, v_data.get("duration_seconds", 1)),
                                has_thoughts=v_data.get("has_thoughts", True),
                                active_version=active_v,
                                total_versions=len(versions),
                                message_id=root_id,
                                is_live_stream=False
                            )

                            try:
                                await interaction.response.edit_message(view=disabled_view)
                            except Exception:
                                try:
                                    await interaction.response.defer()
                                except Exception:
                                    pass

                            await ChatHandler.handle_followup_turn(
                                bot=self,
                                interaction=interaction,
                                prompt_text=prompt_text
                            )
                            return

            await interaction.response.send_message(content="❌ Follow-up action expired.", ephemeral=True)
            return

        if custom_id.startswith("artprev:"):
            parts = custom_id.split(":")
            if len(parts) >= 4:
                msg_id = parts[1]
                art_id = parts[2]
                target_v = int(parts[3]) if parts[3].isdigit() else 1
                chan_id = interaction.channel_id or 0

                art_db = branch_manager.get_artifact(art_id)
                if art_db:
                    versions = art_db.get("versions", [])
                    if 1 <= target_v <= len(versions):
                        v_entry = versions[target_v - 1]
                        filename = art_db.get("filename", "code.txt")
                        att_url = await resolve_attachment_url(filename, msg_id)

                        async def preview_submit(inter: discord.Interaction, data: dict[str, Any]):
                            msg_content, discord_files = prepare_artifact_download_payload(art_db, target_v)
                            if not inter.response.is_done():
                                await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                        adds = v_entry.get("additions", 0)
                        dels = v_entry.get("deletions", 0)
                        diff_tup = (adds, dels) if (adds > 0 or dels > 0) else None

                        modal = build_code_preview_modal(
                            filename=filename,
                            raw_code=v_entry.get("content", ""),
                            channel_id=chan_id,
                            message_id=msg_id,
                            attachment_url=att_url,
                            on_submit_callback=preview_submit,
                            version=target_v,
                            diff_stats=diff_tup,
                            artifact_id=art_id
                        )
                        await interaction.response.send_modal(modal)
                        return

                gen = branch_manager.get_generation(msg_id)
                if gen:
                    active_v = gen.get("active_version", 1)
                    versions = gen.get("versions", [])
                    if 1 <= active_v <= len(versions):
                        v_data = versions[active_v - 1]
                        for art in v_data.get("staged_artifacts", []):
                            if art.get("artifact_id") == art_id:
                                art_versions = art.get("versions", [])
                                filename = art.get("filename", "code.txt")
                                att_url = await resolve_attachment_url(filename, msg_id)

                                target_art_obj = art
                                async def gen_preview_submit(inter: discord.Interaction, data: dict[str, Any]):
                                    msg_content, discord_files = prepare_artifact_download_payload(target_art_obj, target_v)
                                    if not inter.response.is_done():
                                        await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                                if art_versions and 1 <= target_v <= len(art_versions):
                                    v_entry = art_versions[target_v - 1]
                                    adds = v_entry.get("additions", 0)
                                    dels = v_entry.get("deletions", 0)
                                    diff_tup = (adds, dels) if (adds > 0 or dels > 0) else None

                                    modal = build_code_preview_modal(
                                        filename=filename,
                                        raw_code=v_entry.get("content", ""),
                                        channel_id=chan_id,
                                        message_id=msg_id,
                                        attachment_url=att_url,
                                        on_submit_callback=gen_preview_submit,
                                        version=target_v,
                                        diff_stats=diff_tup,
                                        artifact_id=art_id
                                    )
                                    await interaction.response.send_modal(modal)
                                    return

            await interaction.response.send_message(content="❌ File preview record expired.", ephemeral=True)
            return

        if custom_id.startswith("artopen:"):
            parts = custom_id.split(":")
            if len(parts) >= 4:
                msg_id = parts[1]
                art_id = parts[2]
                target_v = int(parts[3]) if parts[3].isdigit() else 1
                chan_id = interaction.channel_id or 0

                target_art = branch_manager.get_artifact(art_id)
                if not target_art:
                    gen = branch_manager.get_generation(msg_id)
                    if gen:
                        active_v = gen.get("active_version", 1)
                        versions = gen.get("versions", [])
                        if 1 <= active_v <= len(versions):
                            v_data = versions[active_v - 1]
                            for art in v_data.get("staged_artifacts", []):
                                if art.get("artifact_id") == art_id:
                                    target_art = art
                                    break

                if target_art:
                    filename = target_art.get("filename", "project.zip")
                    att_url = await resolve_attachment_url(filename, msg_id)

                    async def open_submit(inter: discord.Interaction, data: dict[str, Any]):
                        msg_content, discord_files = prepare_artifact_download_payload(target_art, target_v)
                        if not inter.response.is_done():
                            await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                    modal = build_artifact_open_modal(
                        artifact=target_art,
                        target_version=target_v,
                        channel_id=chan_id,
                        message_id=msg_id,
                        attachment_url=att_url,
                        on_submit_callback=open_submit
                    )
                    await interaction.response.send_modal(modal)
                    return

            await interaction.response.send_message(content="❌ Project record expired.", ephemeral=True)
            return

        if custom_id.startswith("arthist:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                msg_id = parts[1]
                art_id = parts[2]
                selected_v_str = interaction.data.get("values", [None])[0] if interaction.data else None
                if selected_v_str is not None:
                    chosen_v = int(selected_v_str)
                    gen = branch_manager.get_generation(msg_id)
                    if gen and interaction.message:
                        root_id = gen["message_id"]
                        active_v = gen.get("active_version", 1)
                        versions = gen.get("versions", [])
                        if 1 <= active_v <= len(versions):
                            v_data = versions[active_v - 1]
                            v_content = v_data.get("content", "")
                            timeline_blocks = v_data.get("timeline_blocks")
                            staged_comps = v_data.get("staged_components", [])
                            staged_arts = v_data.get("staged_artifacts", [])
                            staged_fups = v_data.get("staged_followups", [])
                            staged_mods = v_data.get("staged_modals", [])
                            dur = max(1, v_data.get("duration_seconds", 1))
                            has_t = v_data.get("has_thoughts", True)
                            mod_map = {m["modal_id"]: m for m in staged_mods}

                            for art in staged_arts:
                                if art.get("artifact_id") == art_id:
                                    art["active_version"] = chosen_v

                            if timeline_blocks:
                                for block in timeline_blocks:
                                    if block.get("type") == "artifact" and block.get("artifact", {}).get("artifact_id") == art_id:
                                        block["artifact"]["active_version"] = chosen_v

                            updated_view = build_v2_message_layout(
                                raw_text=v_content if not timeline_blocks else None,
                                timeline_blocks=timeline_blocks,
                                guild=interaction.guild,
                                staged_components=staged_comps,
                                staged_artifacts=staged_arts,
                                staged_followups=staged_fups,
                                modals_map=mod_map,
                                thought_duration=dur,
                                has_thoughts=has_t,
                                active_version=active_v,
                                total_versions=len(versions),
                                message_id=root_id,
                                is_live_stream=False
                            )
                            await interaction.response.edit_message(view=updated_view)
                            return

            await interaction.response.send_message(content="❌ History selector expired.", ephemeral=True)
            return

        if custom_id.startswith("branch_view_"):
            branch_id = custom_id.replace("branch_view_", "")
            transcript_view = BranchTranscriptView(branch_id=branch_id, page=0)
            await interaction.response.send_message(view=transcript_view, ephemeral=True)
            return

        if custom_id.startswith("branch_del_"):
            branch_id = custom_id.replace("branch_del_", "")
            branch = branch_manager.get_branch_by_id(branch_id)
            if not branch:
                await interaction.response.send_message(content="❌ Branch record not found.", ephemeral=True)
                return

            is_creator = str(interaction.user.id) == branch.get("creator_id")
            is_mod = interaction.guild and interaction.user.guild_permissions.manage_threads
            if not (is_creator or is_mod or not interaction.guild):
                await interaction.response.send_message(content="❌ You lack permission to delete this branch.", ephemeral=True)
                return

            branch_manager.delete_branch(branch_id)
            await interaction.response.send_message(content="🗑️ **Branch deleted.** Deleting thread...", ephemeral=True)
            try:
                if isinstance(interaction.channel, discord.Thread):
                    agent_ses = session_manager.get_session_by_thread_id(interaction.channel.id)
                    if agent_ses:
                        asyncio.create_task(session_manager.cleanup_session(agent_ses["session_id"]))
                    await interaction.channel.delete()
                elif branch.get("thread_id"):
                    thread_obj = interaction.guild.get_thread(int(branch["thread_id"])) or await interaction.guild.fetch_channel(int(branch["thread_id"]))
                    if thread_obj:
                        agent_ses = session_manager.get_session_by_thread_id(thread_obj.id)
                        if agent_ses:
                            asyncio.create_task(session_manager.cleanup_session(agent_ses["session_id"]))
                        await thread_obj.delete()
            except Exception as ex:
                logger.warning(f"Failed to delete thread channel: {ex}")
            return

        if custom_id.startswith("branch_prune_"):
            parts = custom_id.replace("branch_prune_", "").split("_")
            if len(parts) >= 2:
                branch_id = parts[0]
                msg_idx = int(parts[1])
                success = branch_manager.prune_branch_message(branch_id, msg_idx)
                if success:
                    updated_view = BranchTranscriptView(branch_id=branch_id, page=0)
                    await interaction.response.edit_message(view=updated_view)
                else:
                    await interaction.response.send_message(content="❌ Failed to prune message.", ephemeral=True)
            return

        if custom_id.startswith("gen_prev_") or custom_id.startswith("gen_next_"):
            is_prev = custom_id.startswith("gen_prev_")
            msg_id = custom_id.replace("gen_prev_", "") if is_prev else custom_id.replace("gen_next_", "")

            gen_record = branch_manager.get_generation(msg_id)
            if not gen_record:
                await interaction.response.send_message(content="❌ Generation record expired.", ephemeral=True)
                return

            root_id = str(gen_record["message_id"])
            current_v = gen_record.get("active_version", 1)
            versions = gen_record.get("versions", [])
            total_v = len(versions)
            target_v = (current_v - 1) if is_prev else (current_v + 1)

            if not (1 <= target_v <= total_v):
                await interaction.response.send_message(content="❌ Target version out of range.", ephemeral=True)
                return

            target_version_data = branch_manager.set_active_version(root_id, target_v)
            if target_version_data and interaction.channel:
                curr_v_data = versions[current_v - 1] if 1 <= current_v <= len(versions) else {}
                old_message_ids = [str(x) for x in curr_v_data.get("message_ids", [root_id])]
                if root_id not in old_message_ids:
                    old_message_ids.insert(0, root_id)

                if target_version_data.get("status") == "generating":
                    generating_view = build_retry_placeholder_layout(
                        status_text=f"Generating version {target_v}",
                        target_version=target_v,
                        total_versions=total_v,
                        message_id=root_id
                    )
                    await interaction.response.edit_message(view=generating_view)
                    return

                dur = max(1, target_version_data.get("duration_seconds", 1))
                has_t = target_version_data.get("has_thoughts", True)
                v_content = target_version_data.get("content", "")
                raw_timeline = target_version_data.get("timeline_blocks") or ([{"type": "text", "content": v_content}] if v_content else [])
                staged_comps = target_version_data.get("staged_components", [])
                staged_arts = target_version_data.get("staged_artifacts", [])
                staged_fups = target_version_data.get("staged_followups", [])
                staged_mods = target_version_data.get("staged_modals", [])
                mod_map = {m["modal_id"]: m for m in staged_mods}

                files = []
                img_name = None
                for att in target_version_data.get("attachments", []):
                    b64 = att.get("data_b64", "")
                    if b64:
                        raw = base64.b64decode(b64)
                        fname = att.get("filename", "file.bin")
                        if fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                            img_name = fname
                        files.append(discord.File(io.BytesIO(raw), filename=fname))

                target_slices = chunk_timeline(raw_timeline)
                num_slices = max(1, len(target_slices))
                new_version_msg_ids = []

                first_slice = target_slices[0]
                is_first_last = (num_slices == 1)

                v2_view_primary = build_v2_message_layout(
                    timeline_blocks=first_slice,
                    guild=interaction.guild,
                    staged_components=staged_comps if is_first_last else None,
                    staged_artifacts=staged_arts,
                    staged_followups=staged_fups if is_first_last else None,
                    modals_map=mod_map if is_first_last else None,
                    image_filename=img_name,
                    has_image=bool(img_name),
                    thought_duration=dur if is_first_last else 0,
                    has_thoughts=has_t if is_first_last else False,
                    active_version=target_v,
                    total_versions=total_v,
                    message_id=root_id if is_first_last else None,
                    is_live_stream=False
                )

                try:
                    if str(interaction.message.id) == root_id:
                        if files:
                            await interaction.response.edit_message(view=v2_view_primary, attachments=files)
                        else:
                            await interaction.response.edit_message(view=v2_view_primary)
                    else:
                        root_msg = await interaction.channel.fetch_message(int(root_id))
                        if files:
                            await root_msg.edit(view=v2_view_primary, attachments=files)
                        else:
                            await root_msg.edit(view=v2_view_primary)
                        if not interaction.response.is_done():
                            await interaction.response.defer()
                except Exception as ex:
                    logger.warning(f"Root message update error during version swap: {ex}")

                new_version_msg_ids.append(root_id)

                for s_idx in range(1, num_slices):
                    slice_data = target_slices[s_idx]
                    is_slice_last = (s_idx == num_slices - 1)

                    slice_view = build_v2_message_layout(
                        timeline_blocks=slice_data,
                        guild=interaction.guild,
                        staged_components=staged_comps if is_slice_last else None,
                        staged_artifacts=staged_arts,
                        staged_followups=staged_fups if is_slice_last else None,
                        modals_map=mod_map if is_slice_last else None,
                        thought_duration=dur if is_slice_last else 0,
                        has_thoughts=has_t if is_slice_last else False,
                        active_version=target_v,
                        total_versions=total_v,
                        message_id=root_id if is_slice_last else None,
                        is_live_stream=False
                    )

                    existing_sibling_msg = None
                    if s_idx < len(old_message_ids):
                        existing_sibling_id = old_message_ids[s_idx]
                        if existing_sibling_id != root_id:
                            try:
                                existing_sibling_msg = await interaction.channel.fetch_message(int(existing_sibling_id))
                                await existing_sibling_msg.edit(view=slice_view)
                                new_version_msg_ids.append(str(existing_sibling_msg.id))
                            except Exception:
                                existing_sibling_msg = None

                    if not existing_sibling_msg:
                        new_msg = await interaction.channel.send(view=slice_view)
                        new_version_msg_ids.append(str(new_msg.id))

                if len(old_message_ids) > num_slices:
                    orphan_ids = [m for m in old_message_ids[num_slices:] if m != root_id]
                    if orphan_ids:
                        asyncio.create_task(cleanup_sibling_messages(interaction.channel, orphan_ids))

                target_version_data["message_ids"] = new_version_msg_ids
                branch_manager.update_version_data(root_id, target_v, target_version_data)
                
                if 1 <= current_v <= len(versions):
                    curr_v_data["message_ids"] = new_version_msg_ids
                    branch_manager.update_version_data(root_id, current_v, curr_v_data)

            return

        if custom_id.startswith("gen_thought_") and not custom_id.startswith("gen_thought_agent_"):
            parts = custom_id.replace("gen_thought_", "").split("_")
            if len(parts) >= 2:
                msg_id = parts[0]
                v_idx = int(parts[1]) if parts[1].isdigit() else 1
                gen = branch_manager.get_generation(msg_id)
                if gen:
                    root_id = gen["message_id"]
                    versions = gen.get("versions", [])
                    if 1 <= v_idx <= len(versions):
                        v_data = versions[v_idx - 1]

                        raw_thoughts = v_data.get("thoughts", "")
                        formatted_thoughts = v_data.get("formatted_thoughts")
                        model_name = v_data.get("model")
                        dur_sec = max(1, v_data.get("duration_seconds", 1))
                        is_generating = (v_data.get("status") == "generating")

                        files = []
                        for att in v_data.get("attachments", []):
                            b64 = att.get("data_b64", "")
                            if b64:
                                raw = base64.b64decode(b64)
                                fname = att.get("filename", "file.bin")
                                files.append(discord.File(io.BytesIO(raw), filename=fname))

                        container = ThoughtContainerView(
                            raw_thoughts=raw_thoughts,
                            formatted_thoughts=formatted_thoughts,
                            tool_calls=v_data.get("tool_calls", []),
                            duration_seconds=dur_sec,
                            is_thinking=is_generating,
                            show_toggle=not is_generating,
                            message_id=root_id,
                            version_idx=v_idx,
                            model_name=model_name
                        )
                        try:
                            if files:
                                await interaction.response.send_message(view=container, files=files, ephemeral=True)
                            else:
                                await interaction.response.send_message(view=container, ephemeral=True)
                            return
                        except Exception as ex:
                            logger.warning(f"Failed to open thought container: {ex}")
                            return

            await interaction.response.send_message(content="❌ Thoughts unavailable for this version.", ephemeral=True)
            return

        if custom_id.startswith("agent_step_view_"):
            parts = custom_id.replace("agent_step_view_", "").split("_")
            if len(parts) >= 2:
                session_id = parts[0]
                step_idx = parts[1]
                
                step_data = session_manager.get_step_log(session_id, step_idx)
                if step_data:
                    inspector = AgentStepInspectorView(step_data)
                    await interaction.response.send_message(view=inspector, ephemeral=True)
                    return

            await interaction.response.send_message(content="❌ Step inspection data expired.", ephemeral=True)
            return

        if custom_id.startswith("gen_thought_agent_"):
            session_id = custom_id.replace("gen_thought_agent_", "")
            t_data = session_manager.get_session_thoughts(session_id)
            if t_data:
                container = ThoughtContainerView(
                    raw_thoughts=t_data.get("thoughts", "") or "Researching workspace and analyzing repository architecture...",
                    tool_calls=t_data.get("tool_calls", []),
                    duration_seconds=max(1, t_data.get("duration_seconds", 1)),
                    is_thinking=False,
                    show_toggle=False,
                    model_name="gemma-4-31b-it"
                )
                await interaction.response.send_message(view=container, ephemeral=True)
                return

            await interaction.response.send_message(content="❌ Reasoning details unavailable for this session.", ephemeral=True)
            return

        if custom_id.startswith("agent_stop_"):
            session_id = custom_id.replace("agent_stop_", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can stop the agent.", ephemeral=True)
                return

            session_manager.trigger_abort(session_id)
            session_manager.update_session(session_id, state="stopped")

            last_completed_mid = session.get("last_completed_message_id")
            if last_completed_mid and interaction.channel:
                try:
                    c_msg = await interaction.channel.fetch_message(int(last_completed_mid))
                    if c_msg:
                        deliverable_art = branch_manager.get_artifact_by_channel_and_file(interaction.channel.id, "report.html")
                        re_enabled_view = AgentFinalDeliverableView(
                            summary_text="",
                            artifact=deliverable_art,
                            session=session,
                            citations=session.get("citations", []),
                            thought_duration=1,
                            guild=interaction.guild,
                            is_new_task_disabled=False
                        )
                        await c_msg.edit(view=re_enabled_view)
                except Exception:
                    pass

            await interaction.response.send_message(content=f"⏹️ **Agent Stopped:** Signal received from {interaction.user.mention}. Stopping after current step. You can chat naturally or start a new task.", ephemeral=False)
            return

        if custom_id.startswith("agent_new_task_"):
            session_id = custom_id.replace("agent_new_task_", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can start a new task in this workspace.", ephemeral=True)
                return

            if session.get("state") == "planning":
                await interaction.response.send_message(content="⚠️ A task planning turn is already in progress.", ephemeral=True)
                return

            origin_msg = interaction.message

            async def on_new_task_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                next_prompt = data.get("prompt", "").strip()
                if not next_prompt:
                    await sub_inter.response.send_message(content="❌ Task prompt cannot be empty.", ephemeral=True)
                    return

                await sub_inter.response.defer(ephemeral=True)

                if origin_msg:
                    try:
                        deliverable_art = branch_manager.get_artifact_by_channel_and_file(interaction.channel_id, "report.html")
                        disabled_view = AgentFinalDeliverableView(
                            summary_text="",
                            artifact=deliverable_art,
                            session=session,
                            citations=session.get("citations", []),
                            thought_duration=1,
                            guild=interaction.guild,
                            is_new_task_disabled=True
                        )
                        await origin_msg.edit(view=disabled_view)
                    except Exception as ex:
                        logger.debug(f"Failed to disable origin new task button: {ex}")

                raw_data = getattr(sub_inter, "data", {})
                resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
                if resolved_attachments:
                    import aiohttp
                    async with aiohttp.ClientSession() as http_session:
                        for att_id, att_obj in resolved_attachments.items():
                            att_url = att_obj.get("url")
                            att_fname = att_obj.get("filename", f"attachment_{att_id}")
                            if att_url:
                                try:
                                    async with http_session.get(att_url) as resp:
                                        if resp.status == 200:
                                            file_bytes = await resp.read()
                                            dest = os.path.join(session["workspace_path"], att_fname)
                                            with open(dest, "wb") as f_out:
                                                f_out.write(file_bytes)
                                            logger.info(f"[Agent] Saved New Task modal attachment '{att_fname}' to workspace.")
                                except Exception as dl_err:
                                    logger.warning(f"[Agent] Failed to download New Task attachment '{att_fname}': {dl_err}")

                thread = interaction.channel
                if isinstance(thread, discord.Thread):
                    session_manager.clear_abort_event(session_id)
                    session_manager.update_session(session_id, state="planning")
                    await thread.send(content=f"{OCTICONS_MAP['oct_checklist']} **New Task Started by {interaction.user.mention}:**\n> {next_prompt}")
                    asyncio.create_task(AgentEngine.start_planning_turn(thread, session, feedback=next_prompt))
                else:
                    await sub_inter.followup.send(content="❌ Cannot find the agent thread.", ephemeral=True)

            modal = build_agent_new_task_modal(session_id, on_submit=on_new_task_submit)
            await interaction.response.send_modal(modal)
            return

if __name__ == "__main__":
    bot = PriestyBot()
    bot.run(DISCORD_TOKEN)