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
from handlers.chat_handler import ChatHandler
from handlers.slash_handler import setup_slash_commands
from handlers.stream_handler import apply_message_parsers
from ui.thought_container import ThoughtContainerView
from ui.context_views import build_version_switcher_view, BranchTranscriptView

logger = logging.getLogger("PriestyAI.Main")

IDLE_TIMEOUT_SECONDS = 600

class PriestyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.last_activity_time = time.time()
        self.current_status = discord.Status.online
        self.presence_task: asyncio.Task | None = None

    async def setup_hook(self):
        setup_slash_commands(self.tree)
        try:
            synced = await self.tree.sync()
            logger.info(f"[CommandTree] Successfully synced {len(synced)} application command(s) & context menus globally!")
        except Exception as e:
            logger.error(f"[CommandTree] Failed to sync application commands: {e}")

    async def on_ready(self):
        logger.info("=" * 60)
        logger.info(f"PriestyAI logged in as: {self.user} (ID: {self.user.id}) | Connected to {len(self.guilds)} guild(s)")
        logger.info("=" * 60)

        await self.set_bot_presence(discord.Status.online)
        
        if not self.presence_task or self.presence_task.done():
            self.presence_task = asyncio.create_task(self.presence_watchdog_loop())

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
                
                switcher = build_version_switcher_view(
                    message_id=msg_id,
                    active_idx=target_v,
                    total_versions=total_v,
                    thought_duration=dur,
                    has_thoughts=has_t
                )

                version_text = apply_message_parsers(target_version_data.get("content", ""), interaction.guild)

                files = []
                for att in target_version_data.get("attachments", []):
                    b64 = att.get("data_b64", "")
                    if b64:
                        raw = base64.b64decode(b64)
                        files.append(discord.File(io.BytesIO(raw), filename=att.get("filename", "image.png")))

                if files:
                    await interaction.response.edit_message(content=version_text, view=switcher, attachments=files)
                else:
                    await interaction.response.edit_message(content=version_text, view=switcher)
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
                        container = ThoughtContainerView(
                            raw_thoughts=v_data.get("thoughts", ""),
                            tool_calls=v_data.get("tool_calls", []),
                            duration_seconds=v_data.get("duration_seconds", 0),
                            is_thinking=False
                        )
                        await interaction.response.send_message(view=container, ephemeral=True)
                        return
            await interaction.response.send_message(content="❌ Thoughts unavailable for this version.", ephemeral=True)
            return

if __name__ == "__main__":
    bot = PriestyBot()
    bot.run(DISCORD_TOKEN)