import io
import time
import base64
import asyncio
import logging
import discord
from discord import app_commands
from config.settings import DISCORD_TOKEN
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.poll_manager import poll_manager
from core.searxng_client import searxng_client
from handlers.chat_handler import ChatHandler
from handlers.slash_handler import setup_slash_commands
from handlers.stream_handler import build_v2_message_layout, apply_message_parsers
from ui.thought_container import ThoughtContainerView
from ui.context_views import BranchTranscriptView
from ui.artifact_views import build_code_preview_modal

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

        setup_slash_commands(self.tree)
        try:
            synced = await self.tree.sync()
            logger.info(f"[CommandTree] Successfully synced {len(synced)} application command(s) & context menus globally!")
        except Exception as e:
            logger.error(f"[CommandTree] Failed to sync application commands: {e}")

    async def close(self):
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

        is_in_branch = False
        if isinstance(message.channel, discord.Thread):
            branch = branch_manager.get_branch_by_thread_id(message.channel.id)
            if branch:
                is_in_branch = True
                branch_manager.add_branch_message(
                    thread_id=message.channel.id,
                    role="user",
                    author_name=message.author.display_name,
                    author_id=message.author.id,
                    content=message.clean_content
                )

        is_ai_channel = False
        if message.guild and message.channel:
            is_ai_channel = config_manager.is_ai_channel(message.guild.id, message.channel.id)

        await ChatHandler.handle_message(self, message, force_respond=(is_in_branch or is_ai_channel))

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

        if custom_id.startswith("artprev:"):
            parts = custom_id.split(":")
            if len(parts) >= 4:
                msg_id = parts[1]
                art_id = parts[2]
                target_v = int(parts[3]) if parts[3].isdigit() else 1

                art_db = branch_manager.get_artifact(art_id)
                if art_db:
                    versions = art_db.get("versions", [])
                    if 1 <= target_v <= len(versions):
                        v_entry = versions[target_v - 1]
                        modal = build_code_preview_modal(art_db.get("filename", "code.txt"), v_entry.get("content", ""))
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
                                if art_versions and 1 <= target_v <= len(art_versions):
                                    v_entry = art_versions[target_v - 1]
                                    modal = build_code_preview_modal(art.get("filename", "code.txt"), v_entry.get("content", ""))
                                    await interaction.response.send_modal(modal)
                                    return
                                files = art.get("files", [])
                                if files:
                                    modal = build_code_preview_modal(files[0].get("filename", "code.txt"), files[0].get("content", ""))
                                    await interaction.response.send_modal(modal)
                                    return

            await interaction.response.send_message(content="❌ File preview record expired.", ephemeral=True)
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
                        active_v = gen.get("active_version", 1)
                        versions = gen.get("versions", [])
                        if 1 <= active_v <= len(versions):
                            v_data = versions[active_v - 1]
                            v_content = v_data.get("content", "")
                            timeline_blocks = v_data.get("timeline_blocks")
                            staged_comps = v_data.get("staged_components", [])
                            staged_arts = v_data.get("staged_artifacts", [])
                            staged_mods = v_data.get("staged_modals", [])
                            dur = v_data.get("duration_seconds", 0)
                            has_t = v_data.get("has_thoughts", False)
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
                                modals_map=mod_map,
                                thought_duration=dur,
                                has_thoughts=has_t,
                                active_version=active_v,
                                total_versions=len(versions),
                                message_id=msg_id,
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
                    await interaction.channel.delete()
                elif branch.get("thread_id"):
                    thread_obj = interaction.guild.get_thread(int(branch["thread_id"])) or await interaction.guild.fetch_channel(int(branch["thread_id"]))
                    if thread_obj:
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

            current_v = gen_record.get("active_version", 1)
            total_v = len(gen_record.get("versions", []))
            target_v = (current_v - 1) if is_prev else (current_v + 1)

            target_version_data = branch_manager.set_active_version(msg_id, target_v)
            if target_version_data and interaction.message:
                dur = target_version_data.get("duration_seconds", 0)
                has_t = target_version_data.get("has_thoughts", False)
                v_content = target_version_data.get("content", "")
                timeline_blocks = target_version_data.get("timeline_blocks")
                staged_comps = target_version_data.get("staged_components", [])
                staged_arts = target_version_data.get("staged_artifacts", [])
                staged_mods = target_version_data.get("staged_modals", [])
                mod_map = {m["modal_id"]: m for m in staged_mods}

                files = []
                img_name = None
                for att in target_version_data.get("attachments", []):
                    b64 = att.get("data_b64", "")
                    if b64:
                        raw = base64.b64decode(b64)
                        fname = att.get("filename", "file.bin")
                        if fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
                            img_name = fname
                        files.append(discord.File(io.BytesIO(raw), filename=fname))

                v2_view = build_v2_message_layout(
                    raw_text=v_content if not timeline_blocks else None,
                    timeline_blocks=timeline_blocks,
                    guild=interaction.guild,
                    staged_components=staged_comps,
                    staged_artifacts=staged_arts,
                    modals_map=mod_map,
                    image_filename=img_name,
                    has_image=bool(img_name),
                    thought_duration=dur,
                    has_thoughts=has_t,
                    active_version=target_v,
                    total_versions=total_v,
                    message_id=msg_id,
                    is_live_stream=False
                )

                if files:
                    await interaction.response.edit_message(view=v2_view, attachments=files)
                else:
                    await interaction.response.edit_message(view=v2_view)
            return

        if custom_id.startswith("gen_thought_"):
            parts = custom_id.replace("gen_thought_", "").split("_")
            if len(parts) >= 2:
                msg_id = parts[0]
                v_idx = int(parts[1])
                gen = branch_manager.get_generation(msg_id)
                if gen:
                    versions = gen.get("versions", [])
                    if 1 <= v_idx <= len(versions):
                        v_data = versions[v_idx - 1]

                        files = []
                        for att in v_data.get("attachments", []):
                            b64 = att.get("data_b64", "")
                            if b64:
                                raw = base64.b64decode(b64)
                                fname = att.get("filename", "file.bin")
                                files.append(discord.File(io.BytesIO(raw), filename=fname))

                        container = ThoughtContainerView(
                            raw_thoughts=v_data.get("thoughts", ""),
                            tool_calls=v_data.get("tool_calls", []),
                            duration_seconds=v_data.get("duration_seconds", 0),
                            is_thinking=False
                        )
                        if files:
                            await interaction.response.send_message(view=container, files=files, ephemeral=True)
                        else:
                            await interaction.response.send_message(view=container, ephemeral=True)
                        return

            await interaction.response.send_message(content="❌ Thoughts unavailable for this version.", ephemeral=True)
            return

if __name__ == "__main__":
    bot = PriestyBot()
    bot.run(DISCORD_TOKEN)