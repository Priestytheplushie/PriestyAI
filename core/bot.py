from PIL.Image import Image
import discord
import io
import os
import aiohttp
import re
import logging
import asyncio
import random
import time
import json
import datetime
from pydantic import BaseModel, Field
import urllib.parse
from typing import Optional, Literal, Tuple, List
from datetime import datetime as dt_class, timezone, timedelta
from discord import app_commands
from core.memory import ChatHistoryTracker
import core.memory as memory
from core.chat_handler import ChatHandler
from core.image_gen import ImageGenerator
import core.image_gen as image_gen
from core.link_reader import LinkReader
from core.ui_components import (
    DynamicView,
    ThoughtsButton,
    EphemeralThoughtsView,
    sanitize_thoughts,
    CustomButton,
    CustomUserSelect,
    CustomRoleSelect,
    CustomChannelSelect,
    CustomStringSelect,
    CustomModalButton,
    DynamicModal,
    ConfigModal,
    CustomMentionableSelect,
    SaveContextModal,
    AgentPreStartView,
    ProfileEditModal,
)
from agents.discord_react.agent import AgentSession
from tools.message_builder.mb_tool import (
    build_layout_generator_prompt,
    build_message_layout,
)
from google.genai import types


from tools.message_builder import (
    build_message_layout,
    inject_message_builder_hook,
    DSLRuntimeView,
    DSL_STATE_STORAGE,
)


class PollOptionSchema(BaseModel):
    text: str = Field(description="Option text. Keep strictly under 55 characters.")
    emoji: str = Field(
        description="A single standard Unicode emoji symbol contextually relevant to the option."
    )


class QOTDPollSchema(BaseModel):
    question: str = Field(
        description="Witty Question of the Day or 'Would You Rather' text. Keep strictly under 80 characters."
    )
    answers: List[PollOptionSchema] = Field(
        description="List of exactly 2 to 4 distinct options."
    )


logger = logging.getLogger("DiscordFriend")

RATIO_MAP = {
    "1:1": (1024, 1024),
    "4:5": (800, 1000),
    "9:16": (720, 1280),
    "3:2": (1200, 800),
    "16:9": (1280, 720),
}


def generate_slug_from_prompt(prompt: str) -> str:
    """Extracts a readable 2-word identifier from raw task text to prevent random timestamps."""
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", prompt)
    words = [w.lower().strip() for w in cleaned.split() if w.strip()]
    stop_words = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "but",
        "is",
        "are",
        "was",
        "were",
        "to",
        "for",
        "with",
        "of",
        "on",
        "at",
        "in",
        "by",
        "from",
        "who",
        "whom",
        "how",
        "what",
        "which",
        "compare",
        "determine",
        "check",
    }
    filtered_words = [w for w in words if w not in stop_words]
    if not filtered_words:
        filtered_words = words if words else ["session"]
    slug = "-".join(filtered_words[:2])
    return slug if slug else "session"


def sanitize_channel_name(name: str) -> str:
    """Sanitizes names into Discord-compatible channel identifiers."""
    return re.sub(r"[^a-z0-9\-]", "", name.lower().replace(" ", "-"))


def split_outside_parentheses(
    text: str, char: str = ":", maxsplit: int = -1
) -> list[str]:
    """Splits a string by a character only when that character is outside any parentheses."""
    parts = []
    current = []
    depth = 0
    splits = 0
    for c in text:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1

        if c == char and depth == 0 and (maxsplit == -1 or splits < maxsplit):
            parts.append("".join(current))
            current = []
            splits += 1
        else:
            current.append(c)
    parts.append("".join(current))
    return parts


def extract_build_message(content: str) -> tuple[str, Optional[str]]:
    """
    Finds '[BUILD_MESSAGE:' and extracts the balanced string matching up to the closing ']'.
    Uses a highly robust, string-aware balancing scanner to ignore inner list brackets.
    """
    start_tag = "[BUILD_MESSAGE:"
    idx = content.find(start_tag)
    if idx == -1:
        return content, None

    bracket_depth = 0
    end_pos = -1
    in_single_quote = False
    in_double_quote = False
    escape = False

    for i in range(idx, len(content)):
        char = content[i]
        if escape:
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
            continue
        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            continue

        if not in_single_quote and not in_double_quote:
            if char == "[":
                bracket_depth += 1
            elif char == "]":
                bracket_depth -= 1

            if bracket_depth == 0:
                end_pos = i
                break

    if end_pos == -1:
        dsl_code = content[idx + len(start_tag) :]
        cleaned = content[:idx].strip()
        return cleaned, dsl_code

    dsl_code = content[idx + len(start_tag) : end_pos].strip()
    cleaned = (content[:idx] + content[end_pos + 1 :]).strip()
    return cleaned, dsl_code


class SubmissionCollector:
    """State management class tracking and gathering multi-response interaction sessions."""

    def __init__(
        self,
        message_id: int,
        channel_id: int,
        anonymous: bool,
        timeout: int,
        prompt_title: str,
    ):
        self.message_id = message_id
        self.channel_id = channel_id
        self.anonymous = anonymous
        self.timeout = timeout
        self.prompt_title = prompt_title
        self.submissions = []
        self.participants = set()


class FriendBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.brain_server_id = int(os.getenv("BRAIN_SERVER_ID", 0))

        prompt_path = os.path.join("config", "system_prompt.md")

        self.history_tracker = ChatHistoryTracker(limit=25)
        self.chat_handler = ChatHandler(
            api_key=self.gemini_key, system_prompt_path=prompt_path
        )
        self.image_generator = ImageGenerator()
        self.link_reader = LinkReader()

        self.active_channels = set()
        self.watched_channels_decay = {}
        self.active_collectors = {}
        self.active_agent_sessions = {}

        self.configs = {}
        self.user_context_cache = {}

        self.active_thought_listeners = {}
        self.rerun_cache = {}
        self.rerun_indexes = {}
        self.image_versions = {}
        self.image_version_indexes = {}

        self.last_activity_time = dt_class.now(timezone.utc)
        self.dnd_until = None
        self._active_news_runs = set()

        inject_message_builder_hook(self)

    async def get_config(self, target_id: int, is_dm: bool) -> dict:
        """Retrieves active tool and system prompt configurations, applying auto-migrations on load."""
        if target_id in self.configs:
            return self.configs[target_id]

        loaded = await memory.load_config(self, self.brain_server_id, target_id, is_dm)
        if loaded:
            if "discord_tools" in loaded:
                dt = loaded["discord_tools"]
                legacy_selectors = [
                    "Member Selector",
                    "Channel Selector",
                    "Role Selector",
                ]

                if any(x in dt for x in legacy_selectors):
                    if "Entity Dropdowns" not in dt:
                        dt.append("Entity Dropdowns")

                for x in legacy_selectors:
                    if x in dt:
                        dt.remove(x)

                if "Unicode Emojis" not in dt:
                    dt.append("Unicode Emojis")
                if "Server Emojis" not in dt:
                    dt.append("Server Emojis")

            if "news_enabled" not in loaded:
                loaded["news_enabled"] = False
            if "news_channel_id" not in loaded:
                loaded["news_channel_id"] = None
            if "news_timezone" not in loaded:
                loaded["news_timezone"] = "America/New_York"
            if "excluded_roles" not in loaded:
                loaded["excluded_roles"] = []
            if "morning_length" not in loaded:
                loaded["morning_length"] = "Standard"
            if "night_length" not in loaded:
                loaded["night_length"] = "Standard"

            self.configs[target_id] = loaded
            return loaded

        default_cfg = {
            "system_prompt": "",
            "tool_mode": "Auto",
            "thinking_level": "Auto",
            "system_tools": [
                "Google Search",
                "Code Execution",
                "URL Content",
                "Generate Images",
                "Memory Journals",
                "Message Builder",
            ],
            "discord_tools": [
                "Buttons",
                "Modals",
                "Threads",
                "Entity Dropdowns",
                "Custom Dropdowns",
                "Double-Texting",
                "Reactions",
                "Native Polls",
                "Unicode Emojis",
                "Server Emojis",
            ],
            "server_profile": {
                "nickname": "",
                "bio": "",
                "avatar_url": None,
                "banner_url": None,
                "clear_avatar": True,
                "clear_banner": True,
            },
            "news_enabled": False,
            "news_channel_id": None,
            "news_timezone": "America/New_York",
            "excluded_roles": [],
            "morning_length": "Standard",
            "night_length": "Standard",
        }
        self.configs[target_id] = default_cfg
        return default_cfg

    async def setup_hook(self):
        self.loop.create_task(self._presence_monitor_loop())
        self.loop.create_task(self._spontaneous_checkin_loop())
        self.loop.create_task(self._watched_channels_decay_loop())
        self.loop.create_task(self._automated_news_loop())

        self.tree.add_command(
            app_commands.ContextMenu(
                name="Re-run", callback=self.context_rerun_pagination
            )
        )
        self.tree.add_command(
            app_commands.ContextMenu(name="Branch", callback=self.context_branch)
        )
        self.tree.add_command(
            app_commands.ContextMenu(
                name="Delete Bot Message", callback=self.context_delete
            )
        )
        self.tree.add_command(
            app_commands.ContextMenu(
                name="Save Message as Context", callback=self.context_save_message
            )
        )

        self.tree.add_command(
            app_commands.ContextMenu(
                name="Reset AI Memory", callback=self.context_reset_memory
            )
        )
        self.tree.add_command(
            app_commands.ContextMenu(
                name="Save User as Context", callback=self.context_save_user
            )
        )

        @app_commands.command(
            name="config",
            description="Configure AI tools, prompt behavior, custom profiles, or Server News",
        )
        @app_commands.describe(
            target="Select whether to configure channel settings, customize the bot's server profile, or configure Server News"
        )
        async def config_cmd(
            interaction: discord.Interaction,
            target: Literal[
                "Channel Settings", "Server Identity", "Server News"
            ] = "Channel Settings",
        ):
            is_dm = isinstance(interaction.channel, discord.DMChannel)

            if target == "Server Identity":
                if is_dm:
                    await interaction.response.send_message(
                        "❌ Bot identity customization can only be performed inside server channels (guilds), not inside Direct Messages.",
                        ephemeral=True,
                    )
                    return

                if not interaction.user.guild_permissions.manage_guild:
                    await interaction.response.send_message(
                        "❌ You do not have permission to configure Server Identity. This requires the `Manage Server` permission.",
                        ephemeral=True,
                    )
                    return

                guild_id = interaction.guild.id
                config_state = await self.get_config(guild_id, is_dm=False)

                bot_member = interaction.guild.me
                saved_profile = config_state.get("server_profile", {})
                current_name = (
                    saved_profile.get("nickname")
                    or bot_member.nick
                    or bot_member.global_name
                    or bot_member.name
                )
                current_bio = saved_profile.get("bio") or ""

                modal = ProfileEditModal(
                    current_name=current_name,
                    current_bio=current_bio,
                    target_id=guild_id,
                    bot_instance=self,
                    saved_profile=saved_profile,
                )
                await interaction.response.send_modal(modal)

            elif target == "Server News":
                if is_dm:
                    await interaction.response.send_message(
                        "❌ Server News configuration cannot be performed inside Direct Messages.",
                        ephemeral=True,
                    )
                    return

                if not interaction.user.guild_permissions.administrator:
                    await interaction.response.send_message(
                        "❌ You do not have permission to configure Server News. This requires the `Administrator` permission.",
                        ephemeral=True,
                    )
                    return

                news_enabled_env = os.getenv("NEWS_ENABLED", "")
                enabled_guilds = [
                    g.strip() for g in news_enabled_env.split(",") if g.strip()
                ]

                if str(interaction.guild.id) not in enabled_guilds:
                    await interaction.response.send_message(
                        "Server News is not available in this guild! This feature is experimental and may be enabled at a future date....",
                        ephemeral=True,
                    )
                    return

                guild_id = interaction.guild.id
                config_state = await self.get_config(guild_id, is_dm=False)

                if not config_state.get("news_enabled", False):
                    from core.ui_components import NewsConfigModalStage1

                    modal = NewsConfigModalStage1(config_state, guild_id, self)
                    await interaction.response.send_modal(modal)
                else:

                    from core.ui_components import NewsControlRoomView

                    view = NewsControlRoomView(config_state, guild_id, self)
                    await interaction.response.send_message(
                        content="📺 **Welcome to your Server News Control Room!**\n"
                        "Your automated news service is currently **Active**. Use the controls below to manage your settings or deactivate the service.",
                        view=view,
                        ephemeral=True,
                    )
            else:
                target_id = interaction.user.id if is_dm else interaction.channel.id
                config_state = await self.get_config(target_id, is_dm)
                modal = ConfigModal(config_state, target_id, is_dm, self)
                await interaction.response.send_modal(modal)

        self.tree.add_command(config_cmd)

        context_group = app_commands.Group(
            name="context", description="Manage context snapshots for agent sessions"
        )

        @context_group.command(
            name="delete", description="Permanently delete a saved context snapshot"
        )
        @app_commands.describe(
            alias="The lowercase name of the context alias to remove"
        )
        async def context_delete_cmd(interaction: discord.Interaction, alias: str):
            await interaction.response.defer(ephemeral=True)
            clean_alias = re.sub(r"[^a-z0-9_]", "", alias.strip().lower())

            if not clean_alias:
                await interaction.followup.send(
                    "❌ Error: Invalid context alias format.", ephemeral=True
                )
                return

            guild = self.get_guild(self.brain_server_id)
            if not guild:
                await interaction.followup.send(
                    "❌ Error: Brain server is unreachable.", ephemeral=True
                )
                return

            forum = discord.utils.get(
                guild.channels, name="context-snippets", type=discord.ChannelType.forum
            )
            if not forum:
                await interaction.followup.send(
                    "❌ Error: No saved contexts exist.", ephemeral=True
                )
                return

            thread = discord.utils.get(forum.threads, name=str(interaction.user.id))
            if not thread:
                async for arch_thread in forum.archived_threads(limit=100):
                    if arch_thread.name == str(interaction.user.id):
                        thread = arch_thread
                        await thread.edit(archived=False)
                        break

            if not thread:
                await interaction.followup.send(
                    "❌ Error: You have no saved context records.", ephemeral=True
                )
                return

            deleted = False
            async for msg in thread.history(limit=100):
                if f'"alias": "{clean_alias}"' in msg.content:
                    await msg.delete()
                    deleted = True
                    break

            if deleted:
                await interaction.followup.send(
                    f"✅ Context snapshot `{clean_alias}` has been permanently deleted.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    f"⚠️ Context snapshot `{clean_alias}` was not found in your records.",
                    ephemeral=True,
                )

        self.tree.add_command(context_group)

        @app_commands.command(
            name="agent",
            description="Start an autonomous diagnostic or research agent (Private Thread)",
        )
        @app_commands.describe(
            prompt="The main investigation task, analytical request, research query, or logs query for the agent"
        )
        async def agent_cmd(interaction: discord.Interaction, prompt: str):
            if not interaction.guild:
                await interaction.response.send_message(
                    "❌ Error: Thread creation requires server features. `/agent` cannot be executed inside Direct Messages.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)

            from agents.router import classify_agent_intent

            routing_data = await classify_agent_intent(self, prompt)

            from agents.router_views import AgentRouterView

            view = AgentRouterView(
                bot_instance=self,
                user=interaction.user,
                prompt=prompt,
                initial_agent=routing_data["agent"],
                initial_plan=routing_data["plan"],
            )

            await interaction.followup.send(view=view, ephemeral=True)

        self.tree.add_command(agent_cmd)

        @app_commands.command(
            name="start",
            description="[Owner-Only] Manually trigger news pre-generation and broadcast pipeline",
        )
        @app_commands.describe(
            type="Choose the edition (morning / night). If blank, detected automatically based on local time.",
            length="Choose the length format. If blank, fetched from configuration settings.",
            dev_mode="Enable dev mode: overrides episode/show name, sends to DMs, and deletes from Streamable in 30 mins.",
        )
        @app_commands.default_permissions(administrator=True)
        async def start_cmd(
            interaction: discord.Interaction,
            type: Optional[Literal["morning", "night"]] = None,
            length: Optional[Literal["Brief", "Standard", "Extended"]] = None,
            dev_mode: bool = False,
        ):
            owner_id_env = os.getenv("OWNER_ID")
            if not owner_id_env or interaction.user.id != int(owner_id_env.strip()):
                await interaction.response.send_message(
                    "❌ This diagnostic command is restricted to the bot owner.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            status_msg = await interaction.followup.send(
                "🚀 *Initializing diagnostic news pre-generation...*", wait=True
            )

            async def edit_status(text: str):
                try:
                    await interaction.edit_original_response(content=text)
                except Exception:
                    pass

            guild_id = interaction.guild.id
            config = await self.get_config(guild_id, is_dm=False)

            if not config.get("news_enabled", False) and not dev_mode:
                await edit_status(
                    "❌ [Failure] Server News must be enabled via `/config target:Server News` first."
                )
                return

            import zoneinfo

            tz_str = config.get("news_timezone", "America/New_York")
            try:
                tz = zoneinfo.ZoneInfo(tz_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("America/New_York")

            local_now = dt_class.now(tz)
            formatted_date = local_now.strftime("%A, %B %d, %Y")

            if type is None:
                detected_type = "morning" if local_now.hour < 12 else "night"
                edition = detected_type
            else:
                edition = type

            formatted_time = "9:00 AM" if edition == "morning" else "8:00 PM"
            length_override = (
                length
                if length is not None
                else config.get(f"{edition}_length", "Standard")
            )

            if dev_mode:
                show_name = "PriestyAI Dev Diary"
                episode = 1
                state = {"last_episode_number": 1, "show_name": show_name}
            else:
                import core.memory as memory

                state = await memory.load_news_state(
                    self, self.brain_server_id, guild_id
                )
                if not state:
                    state = {
                        "last_episode_number": 0,
                        "show_name": "",
                        "last_morning_pregen_date": "",
                        "last_morning_broadcast_date": "",
                        "last_night_pregen_date": "",
                        "last_night_broadcast_date": "",
                    }

                show_name = state.get("show_name", "").strip()
                if not show_name:
                    await edit_status(
                        "🎨 *Show branding missing. Generating permanent news station name...*"
                    )
                    show_name = await self._generate_show_branding(
                        interaction.guild.name,
                        self.gemini_key,
                        os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash"),
                    )
                    state["show_name"] = show_name
                    await memory.save_news_state(
                        self, self.brain_server_id, guild_id, state
                    )

                episode = state.get("last_episode_number", 0) + 1
                state["last_episode_number"] = episode
                await memory.save_news_state(
                    self, self.brain_server_id, guild_id, state
                )

            logger.info(
                f"[Diagnostic News] Manual run started. Edition: {edition} | Format: {length_override} | Episode: {episode} | Dev Mode: {dev_mode}"
            )

            try:
                await edit_status(
                    "⏳ **[Step 1/5]** Scraping server logs, announcements, and schedules..."
                )
                from tools.news.data_gatherer import NewsScraper

                scraper = NewsScraper(self, guild_id, edition=edition)
                raw_data_path = await scraper.gather_all_data(config)

                await edit_status(
                    "⏳ **[Step 2/5]** Composing layouts and script dialogue via Gemini API..."
                )
                from tools.news.script_writer import write_news_script

                segments = await write_news_script(
                    edition=edition,
                    episode_number=episode,
                    date_str=formatted_date,
                    time_str=formatted_time,
                    show_name=show_name,
                    length=length_override,
                    guild_id=guild_id,
                )

                await edit_status(
                    "⏳ **[Step 3/5]** Rendering video frames & mixing audio (Rendering thread active)..."
                )
                music_path = (
                    "assets/late_night_jazz.mp3"
                    if edition == "night"
                    else "assets/morning_acoustic.mp3"
                )
                local_output_filename = (
                    f"temp_{edition}_edition_broadcast_{guild_id}.mp4"
                )

                from tools.news.video_generator import generate_full_news_video

                await asyncio.to_thread(
                    generate_full_news_video,
                    segments=segments,
                    output_filepath=local_output_filename,
                    music_path=music_path,
                    edition=edition,
                    guild_id=guild_id,
                )

                await edit_status(
                    "⏳ **[Step 4/5]** Uploading final render output to Streamable hosting..."
                )
                from tools.news.news_orchestrator import upload_to_streamable

                title = f"{show_name} - Ep. {episode} ({edition.capitalize()})"
                streamable_url = await asyncio.to_thread(
                    upload_to_streamable, local_output_filename, title
                )

                if not streamable_url:
                    await edit_status(
                        "❌ **[Failure]** Pre-generation failed: Streamable upload returned an empty URL."
                    )
                    return

                state[f"staged_{edition}_url"] = streamable_url
                state[f"staged_{edition}_episode"] = episode
                if not dev_mode:
                    await memory.save_news_state(
                        self, self.brain_server_id, guild_id, state
                    )

                await edit_status(
                    "⏳ **[Step 5/5]** Writing daily highlight summary and broadcasting news..."
                )
                await self._run_broadcast_pipeline(
                    guild_id, edition, config, state, dev_mode=dev_mode
                )

                await edit_status(
                    f"🎉 **Diagnostic News Run Succeeded!**\n📺 Watch output here: {streamable_url}"
                )

            except Exception as err:
                logger.error(
                    f"[Diagnostic News] Manual pre-generation crash: {err}",
                    exc_info=True,
                )
                await edit_status(
                    f"❌ **Diagnostic Run Failed**: `{err}`\nCheck the console log for complete trace details."
                )

        self.tree.add_command(start_cmd)

        @app_commands.command(
            name="generate",
            description="Force the AI to programmatically generate structured outputs",
        )
        @app_commands.describe(
            type="Select the output media pipeline to execute",
            prompt="The description or design instructions for the generator",
        )
        @app_commands.choices(
            type=[
                app_commands.Choice(name="Image", value="image"),
                app_commands.Choice(name="Message Builder (Components v2)", value="mb"),
                app_commands.Choice(name="Standard Legacy UI", value="legacy"),
            ]
        )
        async def generate_cmd(
            interaction: discord.Interaction,
            type: app_commands.Choice[str],
            prompt: str,
        ):
            await interaction.response.defer()
            is_dm = isinstance(interaction.channel, discord.DMChannel)
            target_id = interaction.user.id if is_dm else interaction.channel.id
            active_config = await self.get_config(target_id, is_dm)

            if type.value == "image":
                import copy

                config_override = copy.deepcopy(active_config)
                if "Generate Images" not in config_override["system_tools"]:
                    config_override["system_tools"].append("Generate Images")

                placeholder = await interaction.followup.send(
                    "🎨 *Generating Image...*", wait=True
                )
                history = self.history_tracker.get_formatted_history(
                    interaction.channel.id
                )

                self.loop.create_task(
                    self._generate_decoupled_image(
                        channel=interaction.channel,
                        author=interaction.user,
                        raw_image_prompt=prompt,
                        placeholder_msg=placeholder,
                        context_history=history,
                        is_edit_flow=False,
                        original_message=None,
                        banter="",
                        disabled_triggers={},
                        config=config_override,
                    )
                )

            elif type.value == "mb":
                import copy

                config_override = copy.deepcopy(active_config)
                if "Message Builder" not in config_override["system_tools"]:
                    config_override["system_tools"].append("Message Builder")

                placeholder = await interaction.followup.send(
                    "🎨 *Generating Message Builder Layout...*", wait=True
                )

                override_prompt = (
                    f"[System Directive: You are running in force-generator mode. Compile a highly detailed, "
                    f"beautifully styled Discord V2 message builder layout conforming to mb_stubs.py matching these instructions: "
                    f"'{prompt}'. You must output the code inside a [BUILD_MESSAGE: python_dsl_code] block. "
                    f"Write only a single, brief, lowercase banter sentence at the very end of your response.]"
                )

                history = self.history_tracker.get_formatted_history(
                    interaction.channel.id
                )
                display_name = interaction.user.display_name
                context = (
                    self._compile_server_context(interaction.guild, interaction.user)
                    if interaction.guild
                    else ""
                )
                memories = {
                    "user_memories": "",
                    "server_lore": "",
                    "global_database": "",
                }

                await self._execute_ai_with_retries(
                    override_prompt,
                    history,
                    [],
                    display_name,
                    memories,
                    context,
                    interaction.channel,
                    interaction.user,
                    is_dm,
                    None,
                    edit_target=placeholder,
                    config=config_override,
                )

            elif type.value == "legacy":
                placeholder = await interaction.followup.send(
                    "⚡ *Generating Legacy Interface...*", wait=True
                )

                override_prompt = (
                    f"[System Directive: You are running in force-generator mode. Compose an interactive legacy Discord message "
                    f"using standard legacy tags like [BUTTON: label | color] or [SELECT_STRING: placeholder | Opt1:desc, Opt2:desc] "
                    f"matching these instructions: '{prompt}'. Only output standard legacy component tags, and keep "
                    f"conversational banter natural and brief.]"
                )

                history = self.history_tracker.get_formatted_history(
                    interaction.channel.id
                )
                display_name = interaction.user.display_name
                context = (
                    self._compile_server_context(interaction.guild, interaction.user)
                    if interaction.guild
                    else ""
                )
                memories = {
                    "user_memories": "",
                    "server_lore": "",
                    "global_database": "",
                }

                await self._execute_ai_with_retries(
                    override_prompt,
                    history,
                    [],
                    display_name,
                    memories,
                    context,
                    interaction.channel,
                    interaction.user,
                    is_dm,
                    None,
                    edit_target=placeholder,
                    config=active_config,
                )

        self.tree.add_command(generate_cmd)

        from core.user_app_handler import register_user_app_commands

        register_user_app_commands(self)

        await self.tree.sync()
        logger.info("Discord application slash commands & context menus synced.")

    async def _compile_selected_context_payloads(
        self, user_id: int, contexts_str: str
    ) -> str:
        """Parses a comma-separated string of aliases, loads their JSON payloads, and formats the block."""
        aliases = [a.strip().lower() for a in contexts_str.split(",") if a.strip()]
        if not aliases:
            return ""

        all_contexts = await memory.fetch_all_contexts_for_user(
            self, self.brain_server_id, user_id
        )
        if not all_contexts:
            return ""

        matched_payloads = []
        for alias in aliases:
            match = next((c for c in all_contexts if c.get("alias") == alias), None)
            if match:
                matched_payloads.append(match)

        if not matched_payloads:
            return ""

        composed_lines = [
            "=== ACTIVE USER CONTEXTS ===",
            "The following raw data profiles have been attached to this session by the user.",
            "Refer to these IDs, files, and attributes as primary sources during your steps.\n",
        ]

        for payload in matched_payloads:
            composed_lines.append(f"[CONTEXT ALIAS: {payload.get('alias')}]")
            composed_lines.append(f"- Type: {payload.get('type', 'Unknown')}")
            if payload.get("additional_notes"):
                composed_lines.append(
                    f"- Additional Notes: \"{payload.get('additional_notes')}\""
                )
            composed_lines.append("- Data Payload:")
            composed_lines.append(json.dumps(payload.get("data", {}), indent=2))
            composed_lines.append("")

        composed_lines.append("============================\n")
        return "\n".join(composed_lines)

    async def _process_context_autocomplete_choices(
        self, interaction: discord.Interaction, current_input: str
    ) -> list[tuple[str, str]]:
        """Resolves saved context records and mutual servers to suggest chained options."""
        user_id = interaction.user.id
        now = time.time()

        cached = self.user_context_cache.get(user_id)
        if cached:
            all_contexts = cached[1]
            if now - cached[0] > 45.0:

                async def refresh_task():
                    try:
                        latest = await memory.fetch_all_contexts_for_user(
                            self, self.brain_server_id, user_id
                        )
                        self.user_context_cache[user_id] = (time.time(), latest)
                    except Exception as err:
                        logger.warning(
                            f"Silently failed to background-refresh contexts: {err}"
                        )

                self.loop.create_task(refresh_task())
        else:
            all_contexts = []

            async def first_load_task():
                try:
                    latest = await memory.fetch_all_contexts_for_user(
                        self, self.brain_server_id, user_id
                    )
                    self.user_context_cache[user_id] = (time.time(), latest)
                except Exception as err:
                    logger.warning(f"Silently failed to first-load contexts: {err}")

            self.loop.create_task(first_load_task())

        try:
            context_choices = [
                (f"context: {c['alias']}", c["alias"])
                for c in all_contexts
                if c.get("alias")
            ]

            mutual_guilds = []
            if interaction.guild:
                mutual_guilds.append(interaction.guild)

            for guild in self.guilds:
                if interaction.guild and guild.id == interaction.guild.id:
                    continue
                member = guild.get_member(user_id)
                if member is not None:
                    mutual_guilds.append(guild)

            server_choices = [
                (f"server: {g.name}", f"server_{g.id}") for g in mutual_guilds
            ]

            all_choices = context_choices + server_choices

            if not all_choices:
                return [("No contexts found (type raw server ID)", "none")]

            all_values = [val for _, val in all_choices]
            parts = [p.strip() for p in current_input.split(",")]

            if not current_input.strip():
                return all_choices[:25]

            if current_input.endswith(","):
                completed_choices = [p for p in parts if p]
                active_typing = ""
            else:
                completed_choices = [p for p in parts[:-1] if p]
                active_typing = parts[-1] if parts else ""

            if active_typing in all_values:
                return []

            available_choices = [
                (lbl, val) for lbl, val in all_choices if val not in completed_choices
            ]

            matches = []
            for lbl, val in available_choices:
                if val.lower().startswith(
                    active_typing.lower()
                ) or lbl.lower().startswith(active_typing.lower()):
                    matches.append((lbl, val))

            prefix = ", ".join(completed_choices) + ", " if completed_choices else ""

            suggestions = []
            for lbl, val in matches:
                suggestions.append((f"{lbl}", f"{prefix}{val}"))

            return suggestions[:25]
        except Exception as err:
            logger.error(f"AUTOCOMPLETE EXCEPTION ENCOUNTERED: {err}", exc_info=True)
            return [("Error loading options (see bot console)", "error")]

    async def context_save_user(
        self, interaction: discord.Interaction, member: discord.Member
    ):
        """Right-click app callback to serialize user snapshot data and display the Save Context Modal."""
        user_data = {
            "user_id": member.id,
            "username": member.name,
            "display_name": member.display_name,
            "joined_at": (
                member.joined_at.isoformat() if member.joined_at else "Unknown"
            ),
            "roles": [role.name for role in member.roles if not role.is_default()],
            "status_activity": self._compile_user_activity(member),
        }

        suggested_alias = re.sub(
            r"[^a-z0-9_]", "", member.display_name.lower().replace(" ", "_")
        )
        if not suggested_alias:
            suggested_alias = f"user_{member.id}"

        modal = SaveContextModal(
            target_alias=suggested_alias,
            payload_type="User Profile Snapshot",
            prefilled_data=json.dumps(user_data, indent=2),
            bot_instance=self,
        )
        await interaction.response.send_modal(modal)

    async def context_save_message(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """Right-click app callback to serialize message metadata and display the Save Context Modal."""
        message_data = {
            "channel_id": message.channel.id,
            "message_id": message.id,
            "author": f"{message.author.display_name} (@{message.author.name}) [ID: {message.author.id}]",
            "content": message.clean_content,
            "timestamp": (
                message.created_at.isoformat() if message.created_at else "Unknown"
            ),
            "attachments": [att.url for att in message.attachments],
        }

        author_alias = re.sub(
            r"[^a-z0-9_]", "", message.author.display_name.lower().replace(" ", "_")
        )
        suggested_alias = f"msg_{author_alias[:15]}_{str(message.id)[-4:]}"

        modal = SaveContextModal(
            target_alias=suggested_alias,
            payload_type="Message Transcript Snippet",
            prefilled_data=json.dumps(message_data, indent=2),
            bot_instance=self,
        )
        await interaction.response.send_modal(modal)

    def _compile_user_activity(self, member) -> str:
        if not isinstance(member, discord.Member):
            return "No active status (Direct Messages)."
        activities = member.activities
        if not activities:
            return "No active status (Offline / Idle / No current activity)."

        status_lines = []
        for act in activities:
            if isinstance(act, discord.CustomActivity):
                status_lines.append(
                    f'Custom Status: "{act.name if act.name else act.state}"'
                )
            elif isinstance(act, discord.Spotify):
                status_lines.append(
                    f'Listening to Spotify: "{act.title}" by {act.artist}'
                )
            elif isinstance(act, discord.Game):
                status_lines.append(f"Playing Game: {act.name}")
            elif isinstance(act, discord.Streaming):
                status_lines.append(
                    f"Streaming on {act.platform if act.platform else 'stream'}: \"{act.name}\""
                )
            else:
                status_lines.append(f"Activity: {act.name}")

        return " | ".join(status_lines)

    async def _resolve_mentions(self, content: str, channel) -> str:
        """Post-processes final response content to resolve malformed or plaintext user mentions."""
        if not content:
            return content

        members = []
        if hasattr(channel, "members") and channel.members:
            members = channel.members
        elif hasattr(channel, "guild") and channel.guild and channel.guild.members:
            members = channel.guild.members

        if not members:
            members = list(self.users) if hasattr(self, "users") else []

        if not members:
            return content

        name_to_id = {}
        for m in members:
            actual_member = getattr(m, "member", m)
            if not actual_member or getattr(actual_member, "bot", True):
                continue
            display_name_lower = actual_member.display_name.lower().strip()
            username_lower = actual_member.name.lower().strip()
            name_to_id[display_name_lower] = actual_member.id
            name_to_id[username_lower] = actual_member.id
            if hasattr(actual_member, "nick") and actual_member.nick:
                name_to_id[actual_member.nick.lower().strip()] = actual_member.id

        sorted_names = sorted(name_to_id.keys(), key=len, reverse=True)

        def malformed_replacer(match):
            val = match.group(1).strip().lower()
            if val.isdigit():
                return f"<@{val}>"
            if val in name_to_id:
                return f"<@{name_to_id[val]}>"
            val_strip = val.lstrip("@")
            if val_strip in name_to_id:
                return f"<@{name_to_id[val_strip]}>"
            return match.group(0)

        content = re.sub(r"<@([^>]+)>", malformed_replacer, content)

        if sorted_names:
            escaped_names = [re.escape(name) for name in sorted_names if name]
            names_pattern = "|".join(escaped_names)
            pattern = re.compile(rf"@({names_pattern})\b", re.IGNORECASE)

            def name_replacer(match):
                name_matched = match.group(1).lower().strip()
                if name_matched in name_to_id:
                    return f"<@{name_to_id[name_matched]}>"
                return match.group(0)

            content = pattern.sub(name_replacer, content)

        return content

    async def _choose_semantic_emoji(self, content: str) -> str:
        """Invokes a lightweight single-pass fallback request to choose a contextually fitting reaction."""
        prompt = (
            "You are a casual Discord friend. Read this server announcement/message:\n"
            f'"{content}"\n\n'
            "Select the single most natural, contextually relevant emoji to react with. "
            "Only output the single emoji character itself, nothing else (no punctuation, no words, no explanations)."
        )
        try:
            response = await self.chat_handler.client.aio.models.generate_content(
                model=self.chat_handler.fallback_model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=5),
            )
            emoji = ""
            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                emoji = "".join(
                    p.text
                    for p in response.candidates[0].content.parts
                    if getattr(p, "text", None)
                ).strip()

            emoji = emoji.strip()
            if emoji:
                return emoji[:5]
        except Exception as e:
            logger.warning(f"Failed to choose semantic emoji: {e}")
        return random.choice(["👀", "👋", "🤔", "😮", "🔥"])

    async def _monitor_poll_end(self, poll_msg: discord.Message, delay_seconds: int):
        await asyncio.sleep(delay_seconds)
        try:
            channel = poll_msg.channel
            refetched_msg = await channel.fetch_message(poll_msg.id)
            poll = refetched_msg.poll
            if not poll:
                return

            results_summary = [
                f"- {answer.text}: {answer.vote_count} votes" for answer in poll.answers
            ]
            results_text = "\n".join(results_summary)

            prompt = (
                f'[System Prompt: The native Discord poll you posted "{poll.question.text}" has just closed. '
                f"Here are the final compiled results:\n{results_text}\n\nGenerate a natural, casual reaction to this outcome.]"
            )

            async with channel.typing():
                history = self.history_tracker.get_formatted_history(channel.id)
                members = (
                    [m for m in channel.members if not m.bot]
                    if hasattr(channel, "members")
                    else []
                )
                target_user = random.choice(members) if members else self.user
                memories = await self._compile_memories_for_ai(target_user, channel)
                server_context = (
                    self._compile_server_context(channel.guild, target_user)
                    if hasattr(channel, "guild")
                    else "Environment: Direct Messages."
                )

                await self._execute_ai_with_retries(
                    prompt=prompt,
                    history=history,
                    attachments=[],
                    display_name=target_user.display_name,
                    memory_dict=memories,
                    context=server_context,
                    channel=channel,
                    author=target_user,
                    is_dm=isinstance(channel, discord.DMChannel),
                    original_message=refetched_msg,
                )
        except Exception as e:
            logger.error(f"Error handling poll results callback: {e}")

    async def context_generate_image(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        await interaction.response.defer(ephemeral=True)
        prompt = message.clean_content.strip()
        if not prompt:
            await interaction.followup.send(
                "Cannot generate an image from empty message text.", ephemeral=True
            )
            return

        placeholder_msg = await message.reply(content="🎨 *Generating Image...*")
        await interaction.followup.send(
            "🎨 Sparking generation from target message prompt...", ephemeral=True
        )

        try:
            history = self.history_tracker.get_formatted_history(message.channel.id)
            self.loop.create_task(
                self._generate_decoupled_image(
                    channel=message.channel,
                    author=message.author,
                    raw_image_prompt=prompt,
                    placeholder_msg=placeholder_msg,
                    context_history=history,
                    is_edit_flow=False,
                    original_message=message,
                    banter="",
                    disabled_triggers=None,
                )
            )
        except Exception as e:
            await placeholder_msg.edit(content=f"❌ *Image generation failed:* {e}")

    async def context_rerun_pagination(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        await interaction.response.defer(ephemeral=True)
        if message.author.id != self.user.id:
            await interaction.followup.send(
                "I can only generate alternate versions for my own messages.",
                ephemeral=True,
            )
            return

        original_user_msg = None
        if message.reference and message.reference.resolved:
            original_user_msg = message.reference.resolved
        elif message.reference and message.reference.message_id:
            try:
                original_user_msg = await message.channel.fetch_message(
                    message.reference.message_id
                )
            except Exception:
                pass

        if not original_user_msg:
            try:
                history = [
                    msg
                    async for msg in message.channel.history(limit=5, before=message)
                ]
                for hist_msg in history:
                    if not hist_msg.author.bot:
                        original_user_msg = hist_msg
                        break
            except Exception:
                pass

        if not original_user_msg:
            await interaction.followup.send(
                "Could not identify the user prompt associated with this message context.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "🔄 Generating an alternative response option...", ephemeral=True
        )

        channel = message.channel
        async with channel.typing():
            self.last_activity_time = dt_class.now(timezone.utc)
            is_dm = isinstance(channel, discord.DMChannel)
            target_id = interaction.user.id if is_dm else channel.id
            config_state = await self.get_config(target_id, is_dm)

            history = self.history_tracker.get_formatted_history(channel.id)
            display_name = (
                original_user_msg.author.nick
                if isinstance(original_user_msg.author, discord.Member)
                and original_user_msg.author.nick
                else original_user_msg.author.display_name
            )
            server_context = self._compile_server_context(
                channel.guild, original_user_msg.author
            )

            if "Memory Journals" in config_state.get("system_tools", []):
                memories_task = self._compile_memories_for_ai(
                    original_user_msg.author, channel
                )
            else:

                async def get_empty():
                    return {
                        "user_memories": "",
                        "server_lore": "",
                        "global_database": "",
                    }

                memories_task = get_empty()

            if "URL Content" in config_state.get("system_tools", []):
                urls = self.link_reader.extract_urls(original_user_msg.clean_content)
                url_tasks = [self.link_reader.fetch_and_clean(url) for url in urls[:2]]
            else:
                url_tasks = []

            if url_tasks:
                results = await asyncio.gather(memories_task, *url_tasks)
                memories = results[0]
                scraped_pages = []
                for res in results[1:]:
                    scraped_pages.append(res)
            else:
                memories = await memories_task
                scraped_pages = []

            user_status = (
                self._compile_user_activity(original_user_msg.author)
                if isinstance(original_user_msg.author, discord.Member)
                else None
            )

            if message.id not in self.rerun_cache:
                self.rerun_cache[message.id] = [
                    {
                        "content": message.content,
                        "attachments": list(message.attachments),
                        "ui_state": {},
                    }
                ]
                self.rerun_indexes[message.id] = 0

            await self._execute_ai_with_retries(
                prompt=original_user_msg.clean_content,
                history=history,
                attachments=list(original_user_msg.attachments),
                display_name=display_name,
                memory_dict=memories,
                context=server_context,
                channel=channel,
                author=original_user_msg.author,
                is_dm=is_dm,
                original_message=original_user_msg,
                scraped_pages=scraped_pages,
                user_status=user_status,
                edit_target=message,
                config=config_state,
            )
            await interaction.followup.send(
                "Alternative version applied successfully!", ephemeral=True
            )

    async def context_branch(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        if interaction.guild is None or isinstance(
            interaction.channel, discord.DMChannel
        ):
            await interaction.response.send_message(
                "The 'Branch' action cannot be used inside Direct Messages.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            thread_name = f"branch-{message.author.display_name[:15].lower()}-{dt_class.now(timezone.utc).strftime('%H%M')}"
            thread = await message.create_thread(
                name=thread_name, auto_archive_duration=1440
            )
            self.active_channels.add(thread.id)
            self.watched_channels_decay[thread.id] = time.time() + 600.0
            await interaction.followup.send(
                f"🌱 Conversational branch created successfully: {thread.mention}",
                ephemeral=True,
            )

            preceding_history = [
                hist_msg
                async for hist_msg in message.channel.history(limit=10, before=message)
            ]
            preceding_history.reverse()
            preceding_history.append(message)

            history_str = "\n".join(
                [
                    f"[{h.created_at.strftime('%H:%M:%S')}] {h.author.display_name}: {h.clean_content}"
                    for h in preceding_history
                ]
            )

            async with thread.typing():
                self.last_activity_time = dt_class.now(timezone.utc)
                display_name = (
                    message.author.nick
                    if isinstance(message.author, discord.Member)
                    and message.author.nick
                    else message.author.display_name
                )
                server_context = self._compile_server_context(
                    interaction.guild, message.author
                )

                config_state = await self.get_config(thread.id, False)
                if "Memory Journals" in config_state.get("system_tools", []):
                    memories = await self._compile_memories_for_ai(
                        message.author, thread
                    )
                else:
                    memories = {
                        "user_memories": "",
                        "server_lore": "",
                        "global_database": "",
                    }

                branch_prompt = "[System Prompt: The user has created an isolated conversation branch from this message. Analyze the transcript provided and reply naturally.]"

                await self._execute_ai_with_retries(
                    prompt=branch_prompt,
                    history=history_str,
                    attachments=[],
                    display_name=display_name,
                    memory_dict=memories,
                    context=server_context,
                    channel=thread,
                    author=message.author,
                    is_dm=False,
                    original_message=None,
                    config=config_state,
                )
        except Exception as e:
            await interaction.followup.send(
                f"Could not construct a branch: {e}", ephemeral=True
            )

    async def context_delete(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        if message.author.id != self.user.id:
            await interaction.response.send_message(
                "I can only delete messages sent by me!", ephemeral=True
            )
            return
        try:
            await message.delete()
            await interaction.response.send_message(
                "Message removed successfully.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"Could not delete message: {e}", ephemeral=True
            )

    async def context_extract_dsl(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """Extracts the DSL block from a message builder layout and sends it as a code block."""
        await interaction.response.defer(ephemeral=True)

        content, dsl_code = extract_build_message(message.clean_content)

        if not dsl_code:
            await interaction.followup.send(
                "❌ No DSL block found in this message. "
                "This context menu only works on messages with `[BUILD_MESSAGE: ...]` layouts.",
                ephemeral=True,
            )
            return

        if len(dsl_code) > 1950:
            chunks = [dsl_code[i : i + 1950] for i in range(0, len(dsl_code), 1950)]
            await interaction.followup.send(
                f"📋 **DSL Block Extracted** (Message Building Layout Code):\n```python\n{chunks[0]}\n```",
                ephemeral=True,
            )
            for chunk in chunks[1:]:
                await interaction.followup.send(
                    f"```python\n{chunk}\n```", ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"📋 **DSL Block Extracted** (Message Building Layout Code):\n```python\n{dsl_code}\n```",
                ephemeral=True,
            )

    async def context_reset_memory(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        await interaction.response.defer(ephemeral=True)
        guild = self.get_guild(self.brain_server_id)
        if not guild:
            await interaction.followup.send(
                "Failed to reach brain storage. Verify BRAIN_SERVER_ID is correct.",
                ephemeral=True,
            )
            return

        channel_name = f"{user.name}-memory".lower().replace(" ", "-")
        category = discord.utils.get(guild.categories, name="🧠 User Memories")
        if category:
            channel = discord.utils.get(category.text_channels, name=channel_name)
            if channel:
                try:
                    await channel.delete(
                        reason=f"Memory reset initiated by {interaction.user.display_name}"
                    )
                    await interaction.followup.send(
                        f"Success! All saved memory metrics for **{user.display_name}** have been wiped.",
                        ephemeral=True,
                    )
                    return
                except Exception as e:
                    await interaction.followup.send(
                        f"Failed to delete channel record: {e}", ephemeral=True
                    )
                    return
        await interaction.followup.send(
            f"No existing long-term memory records found for **{user.display_name}**.",
            ephemeral=True,
        )

    async def _presence_monitor_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            now = dt_class.now(timezone.utc)
            if self.dnd_until and now >= self.dnd_until:
                self.dnd_until = None
                await self.change_presence(status=discord.Status.online)

            if not self.dnd_until:
                elapsed_seconds = (now - self.last_activity_time).total_seconds()
                if elapsed_seconds >= 300:
                    await self.change_presence(status=discord.Status.idle)
                else:
                    await self.change_presence(status=discord.Status.online)

            await asyncio.sleep(10)

    async def _spontaneous_checkin_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(1800)

            pt_tz = getattr(
                getattr(self, "chat_handler", None), "pt_zone", timezone.utc
            )
            now_pt = dt_class.now(pt_tz)
            if now_pt.hour >= 23 or now_pt.hour < 8:

                logger.info(
                    f"Spontaneous check-in suppressed during quiet hours: {now_pt.strftime('%I:%M %p %Z')}"
                )
                continue

            now = dt_class.now(timezone.utc)
            elapsed_seconds = (now - self.last_activity_time).total_seconds()

            if elapsed_seconds >= 3600 and self.active_channels:
                if random.random() < 0.30:
                    target_channel_id = list(self.active_channels)[0]
                    channel = self.get_channel(
                        target_channel_id
                    ) or await self.fetch_channel(target_channel_id)
                    if channel:
                        raw_members = (
                            channel.members if hasattr(channel, "members") else []
                        )

                        active_members = []
                        for rm in raw_members:
                            m = getattr(rm, "member", rm)
                            if m and not getattr(m, "bot", True):

                                if hasattr(m, "status") and m.status in (
                                    discord.Status.online,
                                    discord.Status.idle,
                                ):
                                    active_members.append(m)

                        if not active_members:
                            logger.info(
                                f"Spontaneous check-in aborted in channel {target_channel_id}: No active members online."
                            )
                            continue

                        self.last_activity_time = dt_class.now(timezone.utc)

                        target_user = random.choice(active_members)

                        async with channel.typing():
                            history = self.history_tracker.get_formatted_history(
                                channel.id
                            )
                            server_context = self._compile_server_context(
                                channel.guild, target_user
                            )

                            is_dm = isinstance(channel, discord.DMChannel)
                            config_state = await self.get_config(channel.id, is_dm)

                            user_status = self._compile_user_activity(target_user)
                            current_time_str = now_pt.strftime("%I:%M %p")

                            spontaneous_prompt = (
                                f"[System Prompt: The local time is {current_time_str}. You haven't spoken to your friends in an hour. "
                                f"You noticed {target_user.display_name} is active in the server with status/activity: '{user_status}'. "
                                f"Spontaneously initiate a casual, highly engaging, and context-aware conversation topic. "
                                f"Avoid sarcasm, rude remarks, or generic template opens. Be a supportive, chill friend.]"
                            )

                            if "Memory Journals" in config_state.get(
                                "system_tools", []
                            ):
                                memories = await self._compile_memories_for_ai(
                                    target_user, channel, query_text=spontaneous_prompt
                                )
                            else:
                                memories = {
                                    "user_memories": "",
                                    "server_lore": "",
                                    "global_database": "",
                                }

                            await self._execute_ai_with_retries(
                                prompt=spontaneous_prompt,
                                history=history,
                                attachments=[],
                                display_name=target_user.display_name,
                                memory_dict=memories,
                                context=server_context,
                                channel=channel,
                                author=target_user,
                                is_dm=is_dm,
                                original_message=None,
                                user_status=user_status,
                                config=config_state,
                            )

    async def _watched_channels_decay_loop(self):
        """Asynchronous daemon that un-watches quiet channels after their rolling 10-minute timer decays."""
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(15)
            now = time.time()
            expired_channels = []

            for channel_id, exp_time in list(self.watched_channels_decay.items()):
                if now >= exp_time:
                    expired_channels.append(channel_id)

            for cid in expired_channels:
                if cid in self.active_channels:
                    self.active_channels.remove(cid)
                self.watched_channels_decay.pop(cid, None)

                channel = self.get_channel(cid) or await self.fetch_channel(cid)
                if channel:
                    try:
                        async with channel.typing():
                            await asyncio.sleep(1.5)
                    except Exception as decay_err:
                        logger.warning(
                            f"Failed to post decay sign-off to channel {cid}: {decay_err}"
                        )

    async def _run_async_memory_evaluation(
        self, user_msg: str, bot_reply: str, author, channel
    ):
        """
        Asynchronous, single-pass background memory evaluator.
        Utilizes a strict factual durability schema with score thresholds to prevent slang/banter leakage.
        """
        is_dm = isinstance(channel, discord.DMChannel)
        target_id = author.id if is_dm else channel.id
        config = await self.get_config(target_id, is_dm)

        if "Memory Journals" not in config.get("system_tools", []):
            return

        logger.info(
            f"Triggering asynchronous ChatGPT-style memory evaluation for {author.display_name}"
        )

        eval_prompt = (
            "You are a factual database memory gatekeeper. Your sole objective is to audit conversations "
            "and extract durable, high-value, long-term personal facts. You must strictly ignore slang, "
            "temporary emotions, generic comments, conversational updates, or simple jokes.\n\n"
            "--- DISCIPLINED FILTERING SPECIFICATION ---\n"
            "• BANNED FROM SAVING (Will be scored under 8): Conversational slang (like 'lmao' or 'bruh'), "
            "short-term opinions ('I hate this song'), generic mood states ('user is tired/sad'), pizza preferences, "
            "repetitive jokes, reactions, or immediate activities ('eating/playing game right now').\n"
            "• ALLOWED FOR SAVING (Will be scored 8+): Core professional backgrounds, real names, timezones, "
            "programming languages, customized system preferences, ongoing large software projects, life milestones "
            "(marriage, job changes), and explicit user instructions on how to behave.\n\n"
            "Evaluate this interaction:\n"
            f'User ({author.display_name}): "{user_msg}"\n'
            f'AI Reply: "{bot_reply}"\n\n'
            "Identify if there is any new, durable factual detail, relationship vibe, favorite things, "
            "programming languages, or personal traits worth remembering about this user, server, or global space. "
            "Determine if any action is needed: LEARN (new fact), FORGET (remove outdated fact), or IGNORE (no change needed).\n\n"
            "Output your decision in a strict, parsable JSON array of objects "
            "matching this schema:\n"
            "[\n"
            "  {\n"
            '    "action": "LEARN" or "FORGET",\n'
            '    "target": "USER" or "SERVER" or "GLOBAL",\n'
            '    "category": "PROFILE & IDENTITY" or "TECHNICAL ENVIRONMENT" or "RELATIONSHIP & VIBE",\n'
            '    "score": Integer between 1 and 10 representing factual durability,\n'
            '    "fact": "A single, concise, factual contextual statement describing this detail."\n'
            "  }\n"
            "]\n\n"
            "Write only the parsable JSON array inside a ```json ``` block, nothing else."
        )

        try:
            response = await self.chat_handler.client.aio.models.generate_content(
                model=self.chat_handler.fallback_model,
                contents=eval_prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2, max_output_tokens=1000
                ),
            )

            res_text = ""
            if (
                response.candidates
                and response.candidates[0].content
                and response.candidates[0].content.parts
            ):
                res_text = "".join(
                    p.text
                    for p in response.candidates[0].content.parts
                    if getattr(p, "text", None)
                ).strip()

            json_match = re.search(
                r"```json\s*(.*?)\s*```", res_text, flags=re.DOTALL | re.IGNORECASE
            )
            if json_match:
                payload = json.loads(json_match.group(1))
                for entry in payload:
                    action = entry.get("action")
                    target = entry.get("target")
                    category = entry.get("category", "PROFILE & IDENTITY")
                    score = int(entry.get("score", 10))
                    fact_text = entry.get("fact", "").strip()

                    if not fact_text:
                        continue

                    if score < 8:
                        logger.info(
                            f"Memory Gatekeeper ignored noisy fact (Score {score}): '{fact_text}'"
                        )
                        continue

                    if action == "LEARN":
                        if target == "USER":
                            await memory.save_fact(
                                self,
                                self.brain_server_id,
                                author,
                                fact_text,
                                category,
                                score,
                            )
                            logger.info(
                                f"Async Evaluator: Learned User Fact: '{fact_text}' (Score: {score})"
                            )
                        elif (
                            target == "SERVER"
                            and hasattr(channel, "guild")
                            and channel.guild
                        ):
                            await memory.save_server_fact(
                                self,
                                self.brain_server_id,
                                channel.guild,
                                fact_text,
                                category,
                                score,
                            )
                            logger.info(
                                f"Async Evaluator: Learned Server Fact: '{fact_text}' (Score: {score})"
                            )
                        elif target == "GLOBAL":
                            await memory.save_global_fact(
                                self, self.brain_server_id, fact_text, category, score
                            )
                            logger.info(
                                f"Async Evaluator: Learned Global Fact: '{fact_text}' (Score: {score})"
                            )

                    elif action == "FORGET":
                        if target == "USER":
                            u_channel = f"{author.name}-memory".lower().replace(
                                " ", "-"
                            )
                            await memory.forget_fact(
                                self,
                                self.brain_server_id,
                                "🧠 User Memories",
                                u_channel,
                                fact_text,
                            )
                        elif (
                            target == "SERVER"
                            and hasattr(channel, "guild")
                            and channel.guild
                        ):
                            s_channel = f"{channel.guild.name}-lore".lower().replace(
                                " ", "-"
                            )
                            await memory.forget_fact(
                                self,
                                self.brain_server_id,
                                "🌍 Server Lore",
                                s_channel,
                                fact_text,
                            )
                        elif target == "GLOBAL":
                            await memory.forget_fact(
                                self,
                                self.brain_server_id,
                                "🌐 Global Database",
                                "global-memory",
                                fact_text,
                            )

        except Exception as eval_err:
            logger.warning(f"Memory evaluation task failed: {eval_err}")

    async def _compile_memories_for_ai(
        self, author, channel, query_text: str = ""
    ) -> dict:
        """Helper that groups long-term memories using semantic lookup loops."""
        user_channel = f"{author.name}-memory".lower().replace(" ", "-")
        user_task = memory.fetch_memory_block(
            self, self.brain_server_id, "🧠 User Memories", user_channel, query_text
        )
        if channel.guild:
            server_channel = f"{channel.guild.name}-lore".lower().replace(" ", "-")
            server_task = memory.fetch_memory_block(
                self, self.brain_server_id, "🌍 Server Lore", server_channel, query_text
            )
        else:

            async def get_empty():
                return ""

            server_task = get_empty()

        global_task = memory.fetch_memory_block(
            self,
            self.brain_server_id,
            "🌐 Global Database",
            "global-memory",
            query_text,
        )
        user_mem, server_mem, global_mem = await asyncio.gather(
            user_task, server_task, global_task
        )

        return {
            "user_memories": user_mem if user_mem else "No memories saved yet.",
            "server_lore": server_mem if server_mem else "No server lore saved yet.",
            "global_database": (
                global_mem if global_mem else "No global knowledge saved yet."
            ),
        }

    async def trigger_ai_reply(self, channel, user: discord.Member):
        async with channel.typing():
            self.last_activity_time = dt_class.now(timezone.utc)
            history = self.history_tracker.get_formatted_history(channel.id)
            server_context = self._compile_server_context(channel.guild, user)
            is_dm = isinstance(channel, discord.DMChannel)
            target_id = user.id if is_dm else channel.id
            config_state = await self.get_config(target_id, is_dm)

            prompt = "[System Prompt: The user just interacted with a UI component. Read the transcript action and respond naturally to their choice.]"

            if "Memory Journals" in config_state.get("system_tools", []):
                memories = await self._compile_memories_for_ai(
                    user, channel, query_text=prompt
                )
            else:
                memories = {
                    "user_memories": "",
                    "server_lore": "",
                    "global_database": "",
                }

            await self._execute_ai_with_retries(
                prompt,
                history,
                [],
                user.display_name,
                memories,
                server_context,
                channel,
                user,
                is_dm,
                None,
                config=config_state,
            )

    async def trigger_ephemeral_ai_reply(
        self,
        interaction: discord.Interaction,
        collector: SubmissionCollector,
        user_choice_str: str,
    ):
        """Generates an instant, private, natural-sounding AI acknowledgment of a collection submission."""
        now_pt = dt_class.now(timezone.utc)
        self.last_activity_time = now_pt

        history = self.history_tracker.get_formatted_history(interaction.channel.id)
        display_name = (
            interaction.user.nick
            if isinstance(interaction.user, discord.Member) and interaction.user.nick
            else interaction.user.display_name
        )
        server_context = (
            self._compile_server_context(interaction.guild, interaction.user)
            if interaction.guild
            else ""
        )

        is_dm = isinstance(interaction.channel, discord.DMChannel)
        target_id = interaction.user.id if is_dm else interaction.channel.id
        config_state = await self.get_config(target_id, is_dm)

        ephemeral_prompt = (
            f"[System Prompt: The user {display_name} just submitted interaction data to your active "
            f"collection session '{collector.prompt_title}': \"{user_choice_str}\".\n"
            f"Generate an instant, short, natural and lighthearted reply in your personal casual voice to acknowledge "
            f"their specific choice. Keep it between 1-2 casual sentences. Do not use robotic layout or formal words. "
            f"This message will be sent to them privately (ephemerally).]"
        )

        if "Memory Journals" in config_state.get("system_tools", []):
            memories = await self._compile_memories_for_ai(
                interaction.user, interaction.channel, query_text=ephemeral_prompt
            )
        else:
            memories = {"user_memories": "", "server_lore": "", "global_database": ""}

        await self._execute_ai_with_retries(
            prompt=ephemeral_prompt,
            history=history,
            attachments=[],
            display_name=display_name,
            memory_dict=memories,
            context=server_context,
            channel=interaction.channel,
            author=interaction.user,
            is_dm=is_dm,
            original_message=None,
            ephemeral_interaction=interaction,
            config=config_state,
        )

    async def _run_collection_timeout(self, message_id: int, duration_seconds: int):
        """Asynchronous task ticking down until a SubmissionCollector's timeout expires."""
        await asyncio.sleep(duration_seconds)
        collector = self.active_collectors.pop(message_id, None)
        if not collector:
            return

        channel = self.get_channel(collector.channel_id) or await self.fetch_channel(
            collector.channel_id
        )
        if not channel:
            return

        try:
            msg = await channel.fetch_message(message_id)
            if msg:
                view = msg.view
                if view:
                    for child in view.children:
                        if hasattr(child, "disabled"):
                            child.disabled = True
                    await msg.edit(view=view)
        except Exception as err:
            logger.warning(f"Could not disable view on timed-out message: {err}")

        submission_lines = []
        for i, sub in enumerate(collector.submissions):
            user_label = (
                "Anonymous Friend"
                if collector.anonymous
                else f"{sub['display_name']} (@{sub['username']})"
            )
            submission_lines.append(f"Submission {i+1} by {user_label}: {sub['data']}")

        compiled_data = (
            "\n".join(submission_lines)
            if submission_lines
            else "No submissions were recorded."
        )
        participants_str = (
            ", ".join([f"<@{uid}>" for uid in collector.participants])
            if collector.participants
            else "No one"
        )

        anonymity_guideline = ""
        if collector.anonymous:
            anonymity_guideline = (
                "CRITICAL: This collection session was strictly anonymous. Under no circumstances "
                "should you reveal who submitted what or mention the usernames of the participants. "
                "Keep the final tally completely anonymous."
            )
        else:
            anonymity_guideline = (
                f"Weave the following participant mentions naturally into your final post: {participants_str}. "
                f"Do not write them as a plain bulleted or formatted vertical list—integrate them naturally into your text."
            )

        compilation_prompt = (
            f"[System Prompt: The collection session for your interactive poll/form '{collector.prompt_title}' "
            f"has just closed. Here are the compiled results:\n\n{compiled_data}\n\n"
            f"Generate a single, natural, casual and highly consolidated public response in your natural conversational "
            f"voice discussing the compiled results and wrapping up the activity.\n"
            f"{anonymity_guideline}]"
        )

        async with channel.typing():
            target_user = self.user
            history = self.history_tracker.get_formatted_history(channel.id)
            server_context = (
                self._compile_server_context(channel.guild, None)
                if hasattr(channel, "guild")
                else ""
            )

            is_dm = isinstance(channel, discord.DMChannel)
            target_id = channel.id
            config_state = await self.get_config(target_id, is_dm)

            if "Memory Journals" in config_state.get("system_tools", []):
                memories = await self._compile_memories_for_ai(
                    target_user, channel, query_text=compilation_prompt
                )
            else:
                memories = {
                    "user_memories": "",
                    "server_lore": "",
                    "global_database": "",
                }

            await self._execute_ai_with_retries(
                prompt=compilation_prompt,
                history=history,
                attachments=[],
                display_name=self.user.display_name,
                memory_dict=memories,
                context=server_context,
                channel=channel,
                author=target_user,
                is_dm=is_dm,
                original_message=None,
                config=config_state,
            )

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.user.id:
            return
        self.last_activity_time = dt_class.now(timezone.utc)
        channel = self.get_channel(payload.channel_id) or await self.fetch_channel(
            payload.channel_id
        )
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == self.user.id:
                async with channel.typing():
                    user = self.get_user(payload.user_id) or await self.fetch_user(
                        payload.user_id
                    )
                    display_name = (
                        payload.member.display_name
                        if payload.member
                        else user.display_name
                    )

                    context_history = ""
                    try:
                        surrounding = [
                            m async for m in channel.history(limit=5, before=message)
                        ]
                        surrounding.reverse()
                        context_lines = []
                        for m in surrounding:
                            author_lbl = (
                                m.author.nick
                                if isinstance(m.author, discord.Member)
                                and m.author.nick
                                else m.author.display_name
                            )
                            context_lines.append(
                                f"[{m.created_at.strftime('%H:%M:%S')}] {author_lbl}: {m.clean_content}"
                            )
                        context_history = "\n".join(context_lines)
                    except Exception as context_err:
                        logger.warning(
                            f"Could not fetch surrounding context history for reaction: {context_err}"
                        )

                    self.history_tracker.add_system_action(
                        channel.id,
                        f"{display_name} reacted to my message with {payload.emoji.name}",
                    )

                    prompt = (
                        f"[System Prompt: The user {display_name} just reacted to your message with the emoji '{payload.emoji.name}'.\n"
                        f'Reacted-To Message Content: "{message.clean_content}"\n'
                        f"Here is the local conversational history leading up to that message:\n{context_history}\n\n"
                        "Respond naturally in your casual, personal voice to their reaction, referencing what they reacted to if relevant.]"
                    )

                    history = self.history_tracker.get_formatted_history(channel.id)
                    server_context = (
                        self._compile_server_context(channel.guild, user)
                        if channel.guild
                        else "Environment: Direct Messages."
                    )

                    is_dm = isinstance(channel, discord.DMChannel)
                    target_id = user.id if is_dm else channel.id
                    config_state = await self.get_config(target_id, is_dm)

                    if "Memory Journals" in config_state.get("system_tools", []):
                        memories = await self._compile_memories_for_ai(user, channel)
                    else:
                        memories = {
                            "user_memories": "",
                            "server_lore": "",
                            "global_database": "",
                        }

                    await self._execute_ai_with_retries(
                        prompt=prompt,
                        history=history,
                        attachments=[],
                        display_name=display_name,
                        memory_dict=memories,
                        context=server_context,
                        channel=channel,
                        author=user,
                        is_dm=is_dm,
                        original_message=message,
                        config=config_state,
                    )
        except Exception as e:
            logger.error(f"Error handling raw reaction add context loop: {e}")

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        self.last_activity_time = dt_class.now(timezone.utc)
        channel = self.get_channel(payload.channel_id) or await self.fetch_channel(
            payload.channel_id
        )
        if not channel:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == self.user.id:
                async with channel.typing():
                    user = self.get_user(payload.user_id) or await self.fetch_user(
                        payload.user_id
                    )
                    display_name = user.display_name

                    context_history = ""
                    try:
                        surrounding = [
                            m async for m in channel.history(limit=5, before=message)
                        ]
                        surrounding.reverse()
                        context_lines = []
                        for m in surrounding:
                            author_lbl = (
                                m.author.nick
                                if isinstance(m.author, discord.Member)
                                and m.author.nick
                                else m.author.display_name
                            )
                            context_lines.append(
                                f"[{m.created_at.strftime('%H:%M:%S')}] {author_lbl}: {m.clean_content}"
                            )
                        context_history = "\n".join(context_lines)
                    except Exception as context_err:
                        logger.warning(
                            f"Could not fetch surrounding context history for reaction remove: {context_err}"
                        )

                    self.history_tracker.add_system_action(
                        channel.id,
                        f"{display_name} removed their reaction {payload.emoji.name} from my message",
                    )

                    prompt = (
                        f"[System Prompt: The user {display_name} just removed their reaction '{payload.emoji.name}' from your message.\n"
                        f'Reacted-To Message Content: "{message.clean_content}"\n'
                        f"Here is the local conversational history leading up to that message:\n{context_history}\n\n"
                        "Respond naturally, casually, or jokingly to them withdrawing their reaction.]"
                    )

                    history = self.history_tracker.get_formatted_history(channel.id)
                    server_context = (
                        self._compile_server_context(channel.guild, user)
                        if channel.guild
                        else "Environment: Direct Messages."
                    )

                    is_dm = isinstance(channel, discord.DMChannel)
                    target_id = user.id if is_dm else channel.id
                    config_state = await self.get_config(target_id, is_dm)

                    if "Memory Journals" in config_state.get("system_tools", []):
                        memories = await self._compile_memories_for_ai(user, channel)
                    else:
                        memories = {
                            "user_memories": "",
                            "server_lore": "",
                            "global_database": "",
                        }

                    await self._execute_ai_with_retries(
                        prompt=prompt,
                        history=history,
                        attachments=[],
                        display_name=display_name,
                        memory_dict=memories,
                        context=server_context,
                        channel=channel,
                        author=user,
                        is_dm=is_dm,
                        original_message=message,
                        config=config_state,
                    )
        except Exception as e:
            logger.error(f"Error handling raw reaction remove context loop: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id:
            self.history_tracker.add_message(message)
            return

        self.history_tracker.add_message(message)

        is_qa_thread = (
            isinstance(message.channel, discord.Thread)
            and message.channel.name.startswith("Ep. ")
            and message.channel.name.endswith(" Q&A")
        )
        if is_qa_thread:
            if message.author.bot:
                return

            if not hasattr(self, "qa_spam_tracker"):
                self.qa_spam_tracker = {}

            user_id = message.author.id
            thread_id = message.channel.id
            if thread_id not in self.qa_spam_tracker:
                self.qa_spam_tracker[thread_id] = {}

            now = time.time()
            if user_id not in self.qa_spam_tracker[thread_id]:
                self.qa_spam_tracker[thread_id][user_id] = []

            self.qa_spam_tracker[thread_id][user_id] = [
                t for t in self.qa_spam_tracker[thread_id][user_id] if now - t < 60
            ]

            self.qa_spam_tracker[thread_id][user_id].append(now)
            spam_count = len(self.qa_spam_tracker[thread_id][user_id])

            async with message.channel.typing():
                if spam_count > 2:
                    prompt = (
                        f"You are a late-night show announcer named PriestyAI. "
                        f"The user {message.author.display_name} is spamming the morning Q&A thread with too many questions. "
                        f"They just sent this additional question: '{message.clean_content}'. "
                        f"Generate a highly satirical, funny, and direct late-night host roast telling them to touch grass, "
                        f"leave the host alone, or stop spamming the cue. Keep it between 1-2 casual sentences, lowercase, and extremely witty."
                    )
                else:
                    prompt = (
                        f"You are a helpful and chill digital news announcer named PriestyAI. "
                        f"The user {message.author.display_name} just submitted this question to the morning Q&A thread: '{message.clean_content}'. "
                        f"Write a very short, friendly, and natural one-sentence confirmation acknowledging their question "
                        f"and telling them it's been queued for tonight's Late-Night Show. Keep it casual, lowercase, and engaging."
                    )

                try:
                    response = (
                        await self.chat_handler.client.aio.models.generate_content(
                            model=self.chat_handler.fallback_model,
                            contents=prompt,
                            config=types.GenerateContentConfig(
                                max_output_tokens=100, temperature=0.8
                            ),
                        )
                    )
                    reply_text = ""
                    if response and response.text:
                        reply_text = response.text.strip()
                    if not reply_text:
                        reply_text = f"got it, <@{message.author.id}>! queued that up."
                except Exception:
                    reply_text = f"got it, <@{message.author.id}>! queued that up."

                await message.reply(reply_text)
            return

        if message.channel.id in self.active_channels:
            self.watched_channels_decay[message.channel.id] = time.time() + 600.0

        if message.channel.id in self.active_agent_sessions:
            session = self.active_agent_sessions[message.channel.id]
            if session.status == "paused_user_question":
                session.status = "running"
                session.react_history[-1][
                    "observation"
                ] = f"User replied via text: '{message.clean_content}'"
                self.loop.create_task(session.execute_tick(self))
                return

        is_thread = isinstance(message.channel, discord.Thread)
        is_run_trigger = "run:" in message.content.lower() or (
            self.user in message.mentions and "run" in message.content.lower()
        )

        if (
            is_thread
            and is_run_trigger
            and message.channel.id not in self.active_agent_sessions
        ):
            prompt_content = ""
            if "run:" in message.content.lower():
                parts = message.content.split("run:", 1)
                prompt_content = parts[1].strip()
            elif self.user in message.mentions:
                clean_text = (
                    message.content.replace(f"<@{self.user.id}>", "")
                    .replace(f"<@!{self.user.id}>", "")
                    .strip()
                )
                clean_text = re.sub(r"(?i)^run\s*[:\-]?\s*", "", clean_text)
                prompt_content = clean_text

            if prompt_content:
                session = AgentSession(
                    thread_id=message.channel.id,
                    user_id=message.author.id,
                    prompt=prompt_content,
                    loaded_contexts="",
                    channel=message.channel,
                )
                self.active_agent_sessions[message.channel.id] = session

                from core.ui_components import AgentPreStartView

                view = AgentPreStartView(message.channel.id)
                checklist_content = (
                    f"📋 **Agent Pre-Start Checklist (Workspace Continuation)**\n"
                    f"----------------------------------------\n"
                    f'🎯 Primary Task: "{prompt_content}"\n\n'
                    f"Review the configuration above. You can add extra directions or start execution below."
                )
                checklist_msg = await message.channel.send(
                    content=checklist_content, view=view
                )
                view.checklist_msg = checklist_msg
                return

        ref_msg = None
        is_reply_to_bot = False
        if message.reference:
            ref_msg = message.reference.resolved
            if not ref_msg and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(
                        message.reference.message_id
                    )
                except Exception:
                    pass
            if (
                isinstance(ref_msg, discord.Message)
                and ref_msg.author.id == self.user.id
            ):
                is_reply_to_bot = True

        is_mentioned = self.user in message.mentions
        is_everyone_mentioned = message.mention_everyone
        is_role_mentioned = False
        if message.guild and message.guild.me:
            is_role_mentioned = any(
                role in message.role_mentions for role in message.guild.me.roles
            )

        is_watched = message.channel.id in self.active_channels
        is_dm = isinstance(message.channel, discord.DMChannel)

        if (
            is_mentioned
            or is_reply_to_bot
            or is_watched
            or is_dm
            or is_role_mentioned
            or is_everyone_mentioned
        ):
            if is_everyone_mentioned and not (
                is_mentioned or is_reply_to_bot or is_role_mentioned
            ):
                if random.random() < 0.90:
                    try:
                        chosen_emoji = await self._choose_semantic_emoji(
                            message.clean_content
                        )
                        await message.add_reaction(chosen_emoji)
                    except Exception:
                        try:
                            await message.add_reaction("👀")
                        except Exception:
                            pass
                    return

            self.last_activity_time = dt_class.now(timezone.utc)
            async with message.channel.typing():
                history = self.history_tracker.get_formatted_history(message.channel.id)
                display_name = (
                    message.author.nick
                    if isinstance(message.author, discord.Member)
                    and message.author.nick
                    else message.author.display_name
                )
                server_context = self._compile_server_context(
                    message.guild, message.author
                )

                target_id = message.author.id if is_dm else message.channel.id
                config_state = await self.get_config(target_id, is_dm)

                raw_text_to_scan = message.clean_content
                ref_msg_context = ""
                all_attachments = list(message.attachments)

                if isinstance(ref_msg, discord.Message):
                    all_attachments.extend(ref_msg.attachments)
                    ref_author = (
                        ref_msg.author.nick
                        if isinstance(ref_msg.author, discord.Member)
                        and ref_msg.author.nick
                        else ref_msg.author.display_name
                    )
                    ref_msg_context = (
                        f'[Replying to {ref_author}: "{ref_msg.clean_content}"]\n'
                    )
                    raw_text_to_scan += " " + ref_msg.clean_content

                has_disabled_url = False
                if "URL Content" not in config_state.get("system_tools", []):
                    urls = self.link_reader.extract_urls(raw_text_to_scan)
                    if urls:
                        has_disabled_url = True

                has_disabled_search = False
                if "Google Search" not in config_state.get("system_tools", []):
                    if self.chat_handler._should_use_search(message.clean_content):
                        has_disabled_search = True

                has_disabled_images = False
                if "Generate Images" not in config_state.get("system_tools", []):
                    if (
                        "[IMAGE_PENDING:" in raw_text_to_scan
                        or "[IMAGE:" in raw_text_to_scan
                        or any(
                            kw in raw_text_to_scan.lower()
                            for kw in ["draw me", "generate an image of", "paint me"]
                        )
                    ):
                        has_disabled_images = True

                disabled_triggers = {
                    "URL Content": has_disabled_url,
                    "Google Search": has_disabled_search,
                    "Generate Images": has_disabled_images,
                }

                prompt = f"{ref_msg_context}{message.clean_content}"

                if "Memory Journals" in config_state.get("system_tools", []):
                    memories_task = self._compile_memories_for_ai(
                        message.author, message.channel, query_text=prompt
                    )
                else:

                    async def get_empty_mem():
                        return {
                            "user_memories": "",
                            "server_lore": "",
                            "global_database": "",
                        }

                    memories_task = get_empty_mem()

                if "URL Content" in config_state.get("system_tools", []):
                    urls = self.link_reader.extract_urls(raw_text_to_scan)
                    url_tasks = [
                        self.link_reader.fetch_and_clean(url) for url in urls[:2]
                    ]
                else:
                    url_tasks = []

                if url_tasks:
                    results = await asyncio.gather(memories_task, *url_tasks)
                    memories = results[0]
                    scraped_pages = results[1:]
                else:
                    memories = await memories_task
                    scraped_pages = []

                user_status = (
                    self._compile_user_activity(message.author)
                    if isinstance(message.author, discord.Member)
                    else None
                )

                await self._execute_ai_with_retries(
                    prompt=prompt,
                    history=history,
                    attachments=all_attachments,
                    display_name=display_name,
                    memory_dict=memories,
                    context=server_context,
                    channel=message.channel,
                    author=message.author,
                    is_dm=is_dm,
                    original_message=message,
                    scraped_pages=scraped_pages,
                    user_status=user_status,
                    config=config_state,
                    disabled_triggers=disabled_triggers,
                )

    def format_thoughts_block(
        self, thoughts: str, thinking_active: bool = False, thinking_level: str = "HIGH"
    ) -> str:
        status_prefix = "*Thinking in progress...*\n\n" if thinking_active else ""
        lines = []
        for p in thoughts.split("\n"):
            p_strip = p.strip()
            if p_strip:
                lines.append(f"> *{p_strip}*")
            else:
                lines.append("")
        cleaned = "\n".join(lines)
        return (
            f"**🧠 Inner Reasoning & Deduction (Thinking Level: {thinking_level})**\n"
            f"----------------------------------------\n"
            f"{status_prefix}"
            f"{cleaned}\n"
            f"----------------------------------------"
        )

    def _split_message_chunks(self, content: str, max_length: int = 1900) -> list[str]:
        if not content:
            return [""]
        if len(content) <= max_length:
            return [content]

        pattern = re.compile(r"(```[\s\S]*?```|\[[^\]]+?\]|\n|[^\[`\n]+|.)", re.DOTALL)
        pieces = [m.group(1) for m in pattern.finditer(content) if m.group(1)]

        chunks = []
        current = []
        current_len = 0

        for piece in pieces:
            piece_len = len(piece)

            if piece_len > max_length:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_len = 0

                if piece.startswith("```") and piece.endswith("```"):
                    first_line = piece[3:].split("\n", 1)[0].strip()
                    lang = first_line if "\n" in piece[3:] else ""

                    inner = piece[3 + len(lang) : -3].strip("\n")
                    lines = inner.split("\n")

                    buffer = f"```{lang}\n"
                    for line in lines:
                        if len(buffer) + len(line) + 4 > max_length:
                            buffer += "```"
                            chunks.append(buffer)
                            buffer = f"```{lang}\n{line}\n"
                        else:
                            buffer += line + "\n"

                    if buffer != f"```{lang}\n":
                        buffer += "```"
                        chunks.append(buffer)
                elif piece.startswith("[") and piece.endswith("]"):
                    remainder = piece
                    while remainder:
                        split_pos = max_length
                        chunks.append(remainder[:split_pos])
                        remainder = remainder[split_pos:]
                else:
                    remainder = piece
                    while remainder:
                        split_pos = remainder.rfind("\n", 0, max_length)
                        if split_pos <= 0:
                            split_pos = remainder.rfind(" ", 0, max_length)
                        if split_pos <= 0:
                            split_pos = max_length
                        chunks.append(remainder[:split_pos])
                        remainder = remainder[split_pos:]
                continue

            if current_len + piece_len > max_length:
                if current:
                    chunks.append("".join(current))
                    current = []
                    current_len = 0

            current.append(piece)
            current_len += piece_len

        if current:
            chunks.append("".join(current))

        return chunks

    async def _send_split_content(
        self, channel, content: str, view=None, edit_target=None, original_message=None
    ):
        math_file, content = await self._render_math_from_text(content)
        file_arg = [math_file] if math_file else None

        content = await self._resolve_mentions(content, channel)
        chunks = self._split_message_chunks(content)
        sent_msg = None

        if edit_target:
            if file_arg:
                sent_msg = await edit_target.edit(
                    content=chunks[0], view=view, attachments=file_arg
                )
            else:
                sent_msg = await edit_target.edit(content=chunks[0], view=view)

            for chunk in chunks[1:]:
                await channel.send(content=chunk)
        elif original_message and (
            self.user in original_message.mentions or original_message.reference
        ):
            if file_arg:
                sent_msg = await original_message.reply(
                    content=chunks[0], view=view, files=file_arg
                )
            else:
                sent_msg = await original_message.reply(content=chunks[0], view=view)

            for chunk in chunks[1:]:
                await channel.send(content=chunk)
        else:
            if file_arg:
                sent_msg = await channel.send(
                    content=chunks[0], view=view, files=file_arg
                )
            else:
                sent_msg = await channel.send(content=chunks[0], view=view)

            for chunk in chunks[1:]:
                await channel.send(content=chunk)

        return sent_msg

    async def _render_math_from_text(
        self, text: str
    ) -> Tuple[Optional[discord.File], str]:
        """
        Scans response text for math formulas or LaTeX tags, retrieves transparent 150 DPI
        renderings from the CodeCogs API, and compiles them as custom transparent attachments.
        """
        math_patterns = [r"\$\$(.*?)\$\$", r"(?<!\\)\$(?!\s|\d)(.*?)(?<!\s)(?<!\\)\$"]

        math_string = None
        matched_block = None

        for pattern in math_patterns:
            match = re.search(pattern, text, flags=re.DOTALL)
            if match:
                math_string = match.group(1).strip()
                matched_block = match.group(0)
                break

        if not math_string:
            plain_patterns = [
                r"\b(?:y|f\(x\))\s*=\s*[0-9a-zA-Z+\-*/^().\s]+?(?=[.,;]|\s{2,}|\n|\Z)",
                r"\b[a-zA-Z]\s*=\s*(?:-?\d*(?:\.\d+)?\s*\*?\s*(?:cos|sin|tan|sec|csc|cot)\b[0-9a-zA-Z+\-*/^().\s]+?)(?=[.,;]|\s{2,}|\n|\Z)",
            ]
            for pattern in plain_patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    candidate = match.group(0).strip()
                    if any(
                        char in candidate
                        for char in ["\\", "^", "_", "(", ")", "/", "*", "=", "π"]
                    ):
                        math_string = candidate
                        matched_block = candidate
                        break

        if not math_string:
            return None, text

        math_string_clean = math_string.replace("π", r"\pi")
        math_string_clean = math_string_clean.replace("·", r"\cdot")
        math_string_clean = math_string_clean.replace("×", r"\times")
        math_string_clean = math_string_clean.replace("→", r"\rightarrow")
        math_string_clean = math_string_clean.strip("`$* ")

        encoded_formula = urllib.parse.quote(math_string_clean)
        url = f"https://latex.codecogs.com/png.image?\\dpi{{150}}\\bg{{transparent}}\\color{{white}}{encoded_formula}"

        try:
            import aiohttp

            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, timeout=10) as response:
                    if response.status == 200:
                        img_bytes = await response.read()
                        if img_bytes and len(img_bytes) > 100:
                            file = discord.File(
                                fp=io.BytesIO(img_bytes), filename="equation.png"
                            )
                            clean_text = text.replace(
                                matched_block, f"**{math_string_clean}**"
                            )
                            return file, clean_text
        except Exception as e:
            logger.warning(f"Failed to fetch rendered LaTeX math image: {e}")

        return None, text

    async def _execute_ai_with_retries(
        self,
        prompt,
        history,
        attachments,
        display_name,
        memory_dict,
        context,
        channel,
        author,
        is_dm,
        original_message,
        scraped_pages=None,
        user_status=None,
        edit_target=None,
        ephemeral_interaction: discord.Interaction = None,
        config: dict = None,
        disabled_triggers: dict = None,
        self_correction_count: int = 0,
    ):
        max_retries = 6

        if config is None:
            target_id = author.id if is_dm else channel.id
            config = await self.get_config(target_id, is_dm)

        config_thinking = config.get("thinking_level", "Auto")
        if config_thinking == "Auto":
            current_thinking_level = self.chat_handler._select_thinking_level(
                prompt, history
            )
        elif config_thinking == "High":
            current_thinking_level = "HIGH"
        else:
            current_thinking_level = "NONE"

        now_pt = dt_class.now(self.chat_handler.pt_zone)
        should_think = (current_thinking_level in ("HIGH", "MINIMAL")) and (
            ephemeral_interaction is None
        )
        user_app_session_id = config.get("user_app_session_id")

        if should_think:
            bot_name = (
                channel.guild.me.display_name
                if channel.guild and channel.guild.me
                else self.user.display_name
            )
            initial_text = f"💭 *{bot_name} is thinking...*"
            initial_view = DynamicView(
                self,
                channel,
                bot_text=initial_text,
                search_queries=None,
                code_blocks=None,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            initial_btn = ThoughtsButton(
                "Thinking has just started...",
                elapsed=0,
                thinking_active=True,
                message_id=0,
                bot_instance=self,
                thinking_level=current_thinking_level,
            )
            initial_view.add_item(initial_btn)
            initial_view.finalize_layout()

            if not edit_target:
                if original_message and (
                    self.user in original_message.mentions or original_message.reference
                ):
                    edit_target = await original_message.reply(
                        content=initial_text, view=initial_view
                    )
                else:
                    edit_target = await channel.send(
                        content=initial_text, view=initial_view
                    )
            else:
                try:
                    await edit_target.edit(
                        content=initial_text, view=initial_view, attachments=[]
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to edit target message with placeholder: {e}"
                    )

            for item in initial_view.walk_children():
                if isinstance(item, ThoughtsButton):
                    item.message_id = edit_target.id

        for attempt in range(max_retries):
            try:
                response_stream = await self.chat_handler.generate_reply_stream(
                    message_content=prompt,
                    channel_history=history,
                    attachments=attachments,
                    user_display_name=display_name,
                    user_memory=memory_dict,
                    server_context=context,
                    scraped_pages=scraped_pages,
                    user_status=user_status,
                    thinking_level=current_thinking_level,
                    is_dm=is_dm,
                    active_config=config,
                )
                await self._process_stream_and_send(
                    response_stream=response_stream,
                    channel=channel,
                    author=author,
                    is_dm=is_dm,
                    original_message=original_message,
                    edit_target=edit_target,
                    should_think=should_think,
                    thinking_level=current_thinking_level,
                    ephemeral_interaction=ephemeral_interaction,
                    disabled_triggers=disabled_triggers,
                    config=config,
                    self_correction_count=self_correction_count,
                    prompt=prompt,
                    history=history,
                    attachments=attachments,
                    display_name=display_name,
                    memory_dict=memory_dict,
                    context=context,
                    scraped_pages=scraped_pages,
                    user_status=user_status,
                )
                if self.dnd_until and dt_class.now(timezone.utc) < self.dnd_until:
                    self.dnd_until = None
                    await self.change_presence(status=discord.Status.online)
                return

            except Exception as e:
                error_str = str(e).lower()
                logger.warning(f"Generation Attempt {attempt + 1} Failed: {error_str}")

                if (
                    "429" in error_str
                    or "exhausted" in error_str
                    or "quota" in error_str
                ):
                    if not self.chat_handler.premium_cooldown_until:
                        logger.warning(
                            "Premium model quota exhausted! Placing premium model on PT midnight cooldown and immediately retrying with Fallback."
                        )
                        tomorrow_pt = now_pt + timedelta(days=1)
                        self.chat_handler.premium_cooldown_until = dt_class.combine(
                            tomorrow_pt.date(),
                            datetime.time(0, 0, 0),
                            tzinfo=self.chat_handler.pt_zone,
                        )
                        await asyncio.sleep(1.0)
                        continue

                if "429" in error_str and not (
                    "exhausted" in error_str or "quota" in error_str
                ):
                    logger.warning("Global Rate limit hit! Changing status to DND.")
                    self.dnd_until = dt_class.now(timezone.utc) + timedelta(minutes=5)
                    await self.change_presence(status=discord.Status.dnd)

                if (
                    "500" in error_str
                    or "503" in error_str
                    or "internal" in error_str
                    or "unavailable" in error_str
                ):
                    logger.warning(
                        "Server error detected! Changing status to DND while retrying..."
                    )
                    self.dnd_until = dt_class.now(timezone.utc) + timedelta(minutes=2)
                    await self.change_presence(status=discord.Status.dnd)

                if attempt == max_retries - 1:
                    excuse_msg = random.choice(
                        [
                            "wait my discord is lagging so bad rn, hold on",
                            "bruh my internet is literally dying, brb",
                            "sorry my connection is acting up lol, what did u say again?",
                            "hold on my brain is literally lagging rn, brb",
                        ]
                    )
                    excuse_view = DynamicView(
                        self,
                        channel,
                        disabled_triggers=disabled_triggers,
                        original_message=original_message,
                        user_app_session_id=user_app_session_id,
                    )
                    excuse_view.finalize_layout()

                    if ephemeral_interaction:
                        try:
                            if ephemeral_interaction.is_expired():
                                await ephemeral_interaction.followup.send(
                                    content=excuse_msg, ephemeral=True
                                )
                            else:
                                await ephemeral_interaction.response.send_message(
                                    content=excuse_msg, ephemeral=True
                                )
                        except Exception:
                            pass
                    elif edit_target:
                        await edit_target.edit(content=excuse_msg, view=excuse_view)
                    elif original_message:
                        await original_message.reply(
                            content=excuse_msg, view=excuse_view
                        )
                    else:
                        await channel.send(content=excuse_msg, view=excuse_view)
                    self.dnd_until = None
                    await self.change_presence(status=discord.Status.online)
                    return

                backoff_delay = 5 * (2**attempt)
                logger.info(f"Retrying in {backoff_delay}s after generation failure...")
                await asyncio.sleep(backoff_delay)

    async def _process_stream_and_send(
        self,
        response_stream,
        channel,
        author,
        is_dm,
        original_message,
        edit_target=None,
        should_think=False,
        thinking_level="HIGH",
        ephemeral_interaction: discord.Interaction = None,
        disabled_triggers: dict = None,
        config: dict = None,
        self_correction_count: int = 0,
        prompt=None,
        history=None,
        attachments=None,
        display_name=None,
        memory_dict=None,
        context=None,
        scraped_pages=None,
        user_status=None,
    ):
        start_time = time.time()
        accumulated_thoughts = []
        accumulated_text = []
        bot_name = (
            channel.guild.me.display_name
            if channel.guild and channel.guild.me
            else self.user.display_name
        )
        sent_msg = edit_target
        search_queries, code_blocks = [], []

        generation_complete = False
        user_app_session_id = config.get("user_app_session_id") if config else None

        async def ticker_loop():
            nonlocal sent_msg
            while not generation_complete:
                await asyncio.sleep(1.8)
                if generation_complete or not sent_msg:
                    break

                now = time.time()
                current_elapsed = int(now - start_time)

                current_thoughts_str = "".join(accumulated_thoughts).strip()
                combined_raw = "".join(accumulated_text)
                extracted_live_thoughts = []
                for thought_match in re.finditer(
                    r"(?i)<\s*thought\b[^>]*>?\s*(.*?)(?:<\s*/\s*thought\s*>|$)",
                    combined_raw,
                    flags=re.DOTALL,
                ):
                    extracted_live_thoughts.append(thought_match.group(1).strip())
                if extracted_live_thoughts:
                    current_thoughts_str += "\n\n" + "\n\n".join(
                        extracted_live_thoughts
                    )
                current_thoughts_str = current_thoughts_str.strip()

                view = DynamicView(
                    self,
                    channel,
                    search_queries=search_queries,
                    code_blocks=code_blocks,
                    disabled_triggers=disabled_triggers,
                    original_message=original_message,
                    user_app_session_id=user_app_session_id,
                )
                thoughts_payload = (
                    current_thoughts_str
                    if current_thoughts_str
                    else "Thinking has just started..."
                )
                sanitized_payload = sanitize_thoughts(thoughts_payload)

                btn = ThoughtsButton(
                    sanitized_payload,
                    elapsed=current_elapsed,
                    thinking_active=True,
                    message_id=sent_msg.id,
                    bot_instance=self,
                    thinking_level=thinking_level,
                )
                view.add_item(btn)
                view.finalize_layout()

                text_content = f"💭 *{bot_name} is thinking...*"
                try:
                    await sent_msg.edit(content=text_content, view=view)
                except Exception:
                    pass

                for listener_data in list(
                    self.active_thought_listeners.get(sent_msg.id, [])
                ):
                    try:
                        ephemeral_view = EphemeralThoughtsView(
                            sanitized_payload,
                            thinking_active=True,
                            current_level=thinking_level,
                            page_index=listener_data["page_index"],
                            bot_instance=self,
                        )
                        await listener_data["message"].edit(view=ephemeral_view)
                    except Exception:
                        if listener_data in self.active_thought_listeners[sent_msg.id]:
                            self.active_thought_listeners[sent_msg.id].remove(
                                listener_data
                            )

        if should_think:
            initial_text = f"💭 *{bot_name} is thinking...*"
            view = DynamicView(
                self,
                channel,
                search_queries=None,
                code_blocks=None,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            btn = ThoughtsButton(
                "Thinking has just started...",
                elapsed=0,
                thinking_active=True,
                message_id=0,
                bot_instance=self,
                thinking_level=thinking_level,
            )
            view.add_item(btn)
            view.finalize_layout()

            if not sent_msg:
                if original_message and (
                    self.user in original_message.mentions or original_message.reference
                ):
                    sent_msg = await original_message.reply(
                        content=initial_text, view=view
                    )
                else:
                    sent_msg = await channel.send(content=initial_text, view=view)
            else:
                try:
                    await sent_msg.edit(content=initial_text, view=view, attachments=[])
                except Exception:
                    pass

            for item in view.walk_children():
                if isinstance(item, ThoughtsButton):
                    item.message_id = sent_msg.id

        ticker_task = None
        if should_think and sent_msg:
            ticker_task = asyncio.create_task(ticker_loop())

        response_aiter = response_stream.__aiter__()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        response_aiter.__anext__(), timeout=30.0
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    logger.warning("Stream response timed out after 30 seconds.")
                    break

                if chunk:
                    if (
                        chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                    ):
                        for part in chunk.candidates[0].content.parts:
                            if getattr(part, "executable_code", None):
                                code_blocks.append(
                                    {
                                        "code": part.executable_code.code,
                                        "output": "No execution output returned.",
                                    }
                                )
                            elif getattr(part, "code_execution_result", None):
                                if code_blocks:
                                    code_blocks[-1][
                                        "output"
                                    ] = part.code_execution_result.output

                    if chunk.candidates and chunk.candidates[0].grounding_metadata:
                        meta = chunk.candidates[0].grounding_metadata
                        if getattr(meta, "web_search_queries", None):
                            search_queries.extend(meta.web_search_queries)

                    if (
                        chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                    ):
                        for part in chunk.candidates[0].content.parts:
                            is_thought = getattr(part, "thought", False)
                            if is_thought and getattr(part, "text", None):
                                accumulated_thoughts.append(part.text)
                            elif getattr(part, "text", None):
                                accumulated_text.append(part.text)
        except Exception as e:
            logger.error(f"Error reading stream chunk: {e}")
            raise e
        finally:
            generation_complete = True
            if ticker_task:
                ticker_task.cancel()

        final_thoughts_str = "".join(accumulated_thoughts).strip()
        final_text_str = "".join(accumulated_text).strip()
        elapsed_total = int(time.time() - start_time)

        regex_thoughts = []
        for match in re.finditer(
            r"(?i)<\s*thought\b[^>]*>?\s*(.*?)(?:<\s*/\s*thought\s*>|$)",
            final_text_str,
            flags=re.DOTALL,
        ):
            regex_thoughts.append(match.group(1).strip())
        final_text_str = re.sub(
            r"(?i)<\s*thought\b[^>]*>?\s*.*?(?:<\s*/\s*thought\s*>|$)",
            "",
            final_text_str,
            flags=re.DOTALL,
        )

        for match in re.finditer(
            r"(?i)\[\s*thought\b[^\]]*\]?\s*(.*?)(?:\[\s*/\s*thought\s*\]|$)",
            final_text_str,
            flags=re.DOTALL,
        ):
            regex_thoughts.append(match.group(1).strip())
        final_text_str = re.sub(
            r"(?i)\[\s*thought\b[^\]]*\]?\s*.*?(?:\[\s*/\s*thought\s*\]|$)",
            "",
            final_text_str,
            flags=re.DOTALL,
        )

        for match in re.finditer(
            r"(?i)THOUGHT:(.*?)(?=\n\n|\Z)",
            final_text_str,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            regex_thoughts.append(match.group(1).strip())
        final_text_str = re.sub(
            r"(?i)THOUGHT:.*?(?=\n\n|\Z)",
            "",
            final_text_str,
            flags=re.DOTALL | re.IGNORECASE,
        )

        if regex_thoughts:
            final_thoughts_str += "\n\n" + "\n\n".join(regex_thoughts)
        final_thoughts_str = final_thoughts_str.strip()
        final_text_str = final_text_str.strip()

        if sent_msg and sent_msg.id in self.active_thought_listeners:
            for listener_data in self.active_thought_listeners[sent_msg.id]:
                try:
                    completed_view = EphemeralThoughtsView(
                        final_thoughts_str,
                        thinking_active=False,
                        current_level=thinking_level,
                        page_index=listener_data["page_index"],
                        bot_instance=self,
                    )
                    await listener_data["message"].edit(view=completed_view)
                except Exception:
                    pass
            del self.active_thought_listeners[sent_msg.id]

        self.loop.create_task(
            self._run_async_memory_evaluation(prompt, final_text_str, author, channel)
        )

        dsl_instructions_check = (
            "[BUILD_MESSAGE:" in final_text_str or "[BUTTON:" in final_text_str
        )
        if (
            not final_text_str.strip()
            and not dsl_instructions_check
            and self_correction_count < 2
        ):
            logger.info(
                "AI returned empty text content on interaction. Triggering fallback conversational correction..."
            )
            fallback_prompt = (
                f"[System Prompt: Your previous generation was empty. "
                f"The user just clicked/selected a component, and you must respond with a "
                f"natural, casual conversational message acknowledging their action. Speak in lowercase.]"
            )
            import copy

            fallback_config = copy.deepcopy(config)
            fallback_config["thinking_level"] = "None"

            await self._execute_ai_with_retries(
                prompt=fallback_prompt,
                history=history,
                attachments=attachments,
                display_name=display_name,
                memory_dict=memory_dict,
                context=context,
                channel=channel,
                author=author,
                is_dm=is_dm,
                original_message=original_message,
                scraped_pages=scraped_pages,
                user_status=user_status,
                edit_target=sent_msg,
                config=fallback_config,
                self_correction_count=self_correction_count + 1,
            )
            return

        await self._process_stream_and_send_final_hub(
            final_text_str=final_text_str,
            search_queries=search_queries,
            code_blocks=code_blocks,
            final_thoughts_str=final_thoughts_str,
            channel=channel,
            author=author,
            is_dm=is_dm,
            original_message=original_message,
            sent_msg=sent_msg,
            elapsed_total=elapsed_total,
            should_think=should_think,
            thinking_level=thinking_level,
            ephemeral_interaction=ephemeral_interaction,
            disabled_triggers=disabled_triggers,
            config=config,
            self_correction_count=self_correction_count,
            prompt=prompt,
            history=history,
            attachments=attachments,
            display_name=display_name,
            memory_dict=memory_dict,
            context=context,
            scraped_pages=scraped_pages,
            user_status=user_status,
        )

    async def _process_stream_and_send_final_hub(
        self,
        final_text_str,
        search_queries,
        code_blocks,
        final_thoughts_str,
        channel,
        author,
        is_dm,
        original_message,
        sent_msg,
        elapsed_total,
        should_think,
        thinking_level,
        ephemeral_interaction=None,
        disabled_triggers=None,
        config=None,
        self_correction_count=0,
        prompt=None,
        history=None,
        attachments=None,
        display_name=None,
        memory_dict=None,
        context=None,
        scraped_pages=None,
        user_status=None,
    ):
        await self._process_and_send_final(
            response_text=final_text_str,
            search_queries=search_queries,
            code_blocks=code_blocks,
            thoughts_text=final_thoughts_str,
            channel=channel,
            author=author,
            is_dm=is_dm,
            original_message=original_message,
            edit_target=sent_msg,
            elapsed=elapsed_total,
            should_think=should_think,
            thinking_level=thinking_level,
            ephemeral_interaction=ephemeral_interaction,
            disabled_triggers=disabled_triggers,
            config=config,
            self_correction_count=self_correction_count,
            prompt=prompt,
            history=history,
            attachments=attachments,
            display_name=display_name,
            memory_dict=memory_dict,
            context=context,
            scraped_pages=scraped_pages,
            user_status=user_status,
        )

    def _populate_dynamic_components(
        self,
        view,
        target_channel,
        buttons_parsed,
        user_selects_parsed,
        role_selects_parsed,
        channel_selects_parsed,
        mentionable_selects_parsed,
        string_selects_parsed,
        modal_buttons_parsed,
    ):
        """Iteratively adds elements to the view, avoiding nested closure lookup errors."""
        btn_row = 0
        btn_count = 0

        for label, color, emoji_char in buttons_parsed:
            if btn_count >= 5:
                btn_row += 1
                btn_count = 0
            btn = CustomButton(
                label=label,
                style_name=color,
                emoji=emoji_char,
                bot_instance=self,
                channel=target_channel,
                row=btn_row,
            )
            view.add_item(btn)
            btn_count += 1

        select_row = btn_row + 1

        for pr in user_selects_parsed:
            view.add_item(
                CustomUserSelect(
                    placeholder=pr,
                    bot_instance=self,
                    channel=target_channel,
                    row=select_row,
                )
            )
            select_row += 1

        for pr in role_selects_parsed:
            view.add_item(
                CustomRoleSelect(
                    placeholder=pr,
                    bot_instance=self,
                    channel=target_channel,
                    row=select_row,
                )
            )
            select_row += 1

        for pr in channel_selects_parsed:
            view.add_item(
                CustomChannelSelect(
                    placeholder=pr,
                    bot_instance=self,
                    channel=target_channel,
                    row=select_row,
                )
            )
            select_row += 1

        for pr in mentionable_selects_parsed:
            view.add_item(
                CustomMentionableSelect(
                    placeholder=pr,
                    bot_instance=self,
                    channel=target_channel,
                    row=select_row,
                )
            )
            select_row += 1

        for placeholder, options in string_selects_parsed:
            select_options = []
            for o_label, o_desc, o_emoji in options:
                select_options.append(
                    discord.SelectOption(
                        label=o_label, description=o_desc, emoji=o_emoji
                    )
                )
            view.add_item(
                CustomStringSelect(
                    placeholder=placeholder,
                    options_list=select_options,
                    bot_instance=self,
                    channel=target_channel,
                    row=select_row,
                )
            )
            select_row += 1

        modal_row = select_row
        modal_count = 0
        for btn_label, fields in modal_buttons_parsed:
            if modal_count >= 5:
                modal_row += 1
                modal_count = 0
            btn = CustomModalButton(
                label=btn_label,
                title=btn_label,
                fields=fields,
                bot_instance=self,
                channel=target_channel,
                row=modal_row,
            )
            view.add_item(btn)
            modal_count += 1

    async def _process_and_send_final(
        self,
        response_text: str,
        search_queries: list,
        code_blocks: list,
        thoughts_text: str,
        channel,
        author,
        is_dm: bool,
        original_message: discord.Message = None,
        edit_target: discord.Message = None,
        elapsed: int = None,
        should_think: bool = False,
        thinking_level: str = "HIGH",
        ephemeral_interaction: discord.Interaction = None,
        disabled_triggers: dict = None,
        config: dict = None,
        self_correction_count: int = 0,
        prompt=None,
        history=None,
        attachments=None,
        display_name=None,
        memory_dict=None,
        context=None,
        scraped_pages=None,
        user_status=None,
    ):
        if (
            not response_text
            and not search_queries
            and not code_blocks
            and not thoughts_text
        ):
            return
        target_channel = channel
        sent_msg = edit_target

        thread_match = re.search(r"(?i)\[THREAD(?::\s*(.+?))?\]", response_text)
        thread_title = (
            thread_match.group(1).strip()
            if (thread_match and thread_match.group(1))
            else None
        )

        is_already_thread = isinstance(channel, discord.Thread)
        is_too_short = len(response_text) < 800 and not thread_title
        should_create_thread = (
            bool(thread_title)
            and not is_dm
            and not is_already_thread
            and not is_too_short
        )

        main_text_payload = response_text
        thread_text_payload = ""

        if thread_match:
            if should_create_thread:
                parts = response_text.split(thread_match.group(0))
                main_text_payload = parts[0].strip()
                thread_text_payload = parts[1].strip() if len(parts) > 1 else ""
            else:
                response_text = response_text.replace(thread_match.group(0), "").strip()
                main_text_payload = response_text

        thread = None
        if should_create_thread:
            try:
                thread = await channel.create_thread(
                    name=thread_title,
                    auto_archive_duration=1440,
                    type=discord.ChannelType.public_thread,
                )
                self.active_channels.add(thread.id)
                self.watched_channels_decay[thread.id] = time.time() + 600.0
                logger.info(
                    f"Thread '{thread_title}' successfully created (ID: {thread.id})"
                )
            except Exception as e:
                logger.error(f"Failed to create thread: {e}")

        active_text_payload = thread_text_payload if thread else main_text_payload

        text_mentions = re.findall(r"<#(\d+)>", active_text_payload)
        for channel_str in text_mentions:
            cid = int(channel_str)
            if cid not in self.active_channels:
                self.active_channels.add(cid)
                logger.info(f"Auto-watching channel ID {cid} from bot mention.")
            self.watched_channels_decay[cid] = time.time() + 600.0

        if config is None:
            config = await self.get_config(author.id if is_dm else channel.id, is_dm)

        if "Message Builder" in config.get("system_tools", []):
            active_text_payload, dsl_instructions = extract_build_message(
                active_text_payload
            )
            if thread:
                thread_text_payload = active_text_payload
            else:
                main_text_payload = active_text_payload
        else:
            dsl_instructions = None

        if "[RESET_CHAT]" in active_text_payload:
            if channel.id in self.history_tracker.histories:
                self.history_tracker.histories[channel.id].clear()
            active_text_payload = active_text_payload.replace(
                "[RESET_CHAT]", ""
            ).strip()

        if "[CLEAR_WEBPAGE_CACHE]" in active_text_payload:
            active_text_payload = active_text_payload.replace(
                "[CLEAR_WEBPAGE_CACHE]", ""
            ).strip()

        for match in re.finditer(
            r"\[WATCH_CHANNEL:\s*<#?(\d+)>?\s*\]", active_text_payload
        ):
            cid = int(match.group(1))
            self.active_channels.add(cid)
            self.watched_channels_decay[cid] = time.time() + 600.0
            active_text_payload = active_text_payload.replace(
                match.group(0), ""
            ).strip()

        if "[UNWATCH_CHANNEL]" in active_text_payload:
            if channel.id in self.active_channels:
                self.active_channels.remove(channel.id)
                self.watched_channels_decay.pop(channel.id, None)
            active_text_payload = active_text_payload.replace(
                "[UNWATCH_CHANNEL]", ""
            ).strip()

        image_prompts = []
        is_edit_flow = False
        for match in re.finditer(r"\[IMAGE_EDIT:\s*(.+?)\]", active_text_payload):
            image_prompts.append(match.group(1).strip())
            is_edit_flow = True
            active_text_payload = active_text_payload.replace(match.group(0), "")

        if not is_edit_flow:
            for match in re.finditer(
                r"\[IMAGE_PENDING:\s*(.+?)\]", active_text_payload
            ):
                image_prompts.append(match.group(1).strip())
                active_text_payload = active_text_payload.replace(match.group(0), "")
            for match in re.finditer(r"\[IMAGE:\s*(.+?)\]", active_text_payload):
                image_prompts.append(match.group(1).strip())
                active_text_payload = active_text_payload.replace(match.group(0), "")

        emoji = None
        for match in re.finditer(r"\[REACT:\s*(.+?)\]", active_text_payload):
            emoji = match.group(1).strip()
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[REACT_USER:\s*(.+?)\]", active_text_payload):
            user_emoji = match.group(1).strip()
            if original_message:
                try:
                    await original_message.add_reaction(user_emoji)
                except Exception:
                    pass
            active_text_payload = active_text_payload.replace(match.group(0), "")

        typo_edit_data = None
        for match in re.finditer(
            r"\[TYPO_EDIT:\s*(.+?)\s*\|\s*(.+?)\]", active_text_payload
        ):
            typo_word, corrected_word = match.groups()
            if typo_word.strip().lower() != corrected_word.strip().lower():
                typo_edit_data = (typo_word.strip(), corrected_word.strip())
            active_text_payload = active_text_payload.replace(match.group(0), "")

        followups = []
        if "[FOLLOW_UP]" in active_text_payload:
            parts = active_text_payload.split("[FOLLOW_UP]")
            active_text_payload = parts[0].strip()
            for p in parts[1:]:
                if p.strip():
                    followups.append(p.strip())

        for match in re.finditer(r"\[LEARN:\s*(.+?)\]", active_text_payload):
            await memory.save_fact(
                self, self.brain_server_id, author, match.group(1).strip()
            )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[FORGET:\s*(.+?)\]", active_text_payload):
            channel_name = f"{author.name}-memory".lower().replace(" ", "-")
            await memory.forget_fact(
                self,
                self.brain_server_id,
                "🧠 User Memories",
                channel_name,
                match.group(1).strip(),
            )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[LEARN_SERVER:\s*(.+?)\]", active_text_payload):
            if channel.guild:
                await memory.save_server_fact(
                    self, self.brain_server_id, channel.guild, match.group(1).strip()
                )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[FORGET_SERVER:\s*(.+?)\]", active_text_payload):
            if channel.guild:
                channel_name = f"{channel.guild.name}-lore".lower().replace(" ", "-")
                await memory.forget_fact(
                    self,
                    self.brain_server_id,
                    "🌍 Server Lore",
                    channel_name,
                    match.group(1).strip(),
                )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[LEARN_GLOBAL:\s*(.+?)\]", active_text_payload):
            await memory.save_global_fact(
                self, self.brain_server_id, match.group(1).strip()
            )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        for match in re.finditer(r"\[FORGET_GLOBAL:\s*(.+?)\]", active_text_payload):
            await memory.forget_fact(
                self,
                self.brain_server_id,
                "🌐 Global Database",
                "global-memory",
                match.group(1).strip(),
            )
            active_text_payload = active_text_payload.replace(match.group(0), "")

        collect_session = None
        for match in re.finditer(
            r"\[COLLECT:\s*([^|\]]+?)\s*\|\s*([^|\]]+?)\s*(?:\|\s*(anonymous|public))?\s*\]",
            active_text_payload,
        ):
            title = match.group(1).strip()
            duration_raw = match.group(2).strip()
            is_anon = (
                match.group(3).strip().lower() != "public" if match.group(3) else True
            )

            duration_sec = 60
            dur_match = re.match(r"^(\d+)(s|m|h)?$", duration_raw, re.IGNORECASE)
            if dur_match:
                val = int(dur_match.group(1))
                unit = dur_match.group(2).lower() if dur_match.group(2) else "s"
                if unit == "s":
                    duration_sec = val
                elif unit == "m":
                    duration_sec = val * 60
                elif unit == "h":
                    duration_sec = val * 3600

            collect_session = (title, duration_sec, is_anon)
            active_text_payload = active_text_payload.replace(match.group(0), "")

        buttons_parsed = []
        for match in re.finditer(
            r"\[BUTTON:\s*([^|\]]+?)\s*\|\s*([^|\]]+?)\s*(?:\|\s*([^\]]+?))?\s*\]",
            active_text_payload,
        ):
            label = match.group(1).strip()
            color = match.group(2).strip()
            emoji_char = match.group(3).strip() if match.group(3) else None
            buttons_parsed.append((label, color, emoji_char))
            active_text_payload = active_text_payload.replace(match.group(0), "")

        user_selects_parsed = []
        role_selects_parsed = []
        channel_selects_parsed = []
        mentionable_selects_parsed = []

        config_state = config
        disc_tools = config_state.get("discord_tools", [])
        user_app_session_id = config_state.get("user_app_session_id")

        if "Entity Dropdowns" in disc_tools:
            for match in re.finditer(
                r"\[USER_SELECT:\s*([^\]]+?)\s*\]", active_text_payload
            ):
                prompt = match.group(1).strip()
                user_selects_parsed.append(prompt)
                active_text_payload = active_text_payload.replace(match.group(0), "")

            for match in re.finditer(
                r"\[ROLE_SELECT:\s*([^\]]+?)\s*\]", active_text_payload
            ):
                prompt = match.group(1).strip()
                role_selects_parsed.append(prompt)
                active_text_payload = active_text_payload.replace(match.group(0), "")

            for match in re.finditer(
                r"\[CHANNEL_SELECT:\s*([^\]]+?)\s*\]", active_text_payload
            ):
                prompt = match.group(1).strip()
                channel_selects_parsed.append(prompt)
                active_text_payload = active_text_payload.replace(match.group(0), "")

            for match in re.finditer(
                r"\[MENTIONABLE_SELECT:\s*([^\]]+?)\s*\]", active_text_payload
            ):
                prompt = match.group(1).strip()
                mentionable_selects_parsed.append(prompt)
                active_text_payload = active_text_payload.replace(match.group(0), "")

        string_selects_parsed = []
        for match in re.finditer(
            r"\[SELECT_STRING:\s*([^|\]]+?)\s*\|\s*([^\]]+?)\s*\]", active_text_payload
        ):
            placeholder = match.group(1).strip()
            raw_options = match.group(2).strip().split(",")
            options = []
            for raw_opt in raw_options:
                parts = raw_opt.split(":")
                opt_label = parts[0].strip()
                opt_desc = (
                    parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
                )
                opt_emoji = (
                    parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                )
                options.append((opt_label, opt_desc, opt_emoji))
            string_selects_parsed.append((placeholder, options))
            active_text_payload = active_text_payload.replace(match.group(0), "")

        modal_buttons_parsed = []
        for match in re.finditer(
            r"\[MODAL_BUTTON:\s*([^|\]]+?)\s*\|\s*([^\]]+?)\s*\]", active_text_payload
        ):
            btn_label = match.group(1).strip()
            raw_fields = re.split(r",(?![^(]*\))", match.group(2).strip())
            fields = []
            for raw_field in raw_fields:
                if not raw_field.strip():
                    continue
                parts = split_outside_parentheses(raw_field, ":")
                field_name = parts[0].strip()
                field_style = (
                    parts[1].strip() if len(parts) > 1 and parts[1].strip() else "short"
                )
                field_desc = (
                    parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                )
                fields.append((field_name, field_style, field_desc))
            modal_buttons_parsed.append((btn_label, fields))
            active_text_payload = active_text_payload.replace(match.group(0), "")

        if thread:
            thread_text_payload = active_text_payload
        else:
            main_text_payload = active_text_payload

        has_ui = (
            should_think
            or bool(search_queries)
            or bool(code_blocks)
            or bool(thoughts_text)
            or bool(buttons_parsed)
            or bool(user_selects_parsed)
            or bool(role_selects_parsed)
            or bool(channel_selects_parsed)
            or bool(mentionable_selects_parsed)
            or bool(string_selects_parsed)
            or bool(modal_buttons_parsed)
            or bool(user_app_session_id)
        )
        history = self.history_tracker.get_formatted_history(channel.id)

        if ephemeral_interaction:
            view = DynamicView(
                self,
                channel,
                search_queries=search_queries,
                code_blocks=code_blocks,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            self._populate_dynamic_components(
                view,
                channel,
                buttons_parsed,
                user_selects_parsed,
                role_selects_parsed,
                channel_selects_parsed,
                mentionable_selects_parsed,
                string_selects_parsed,
                modal_buttons_parsed,
            )
            view.finalize_layout()

            main_text_payload = await self._resolve_mentions(main_text_payload, channel)
            try:
                if ephemeral_interaction.is_expired():
                    await ephemeral_interaction.followup.send(
                        content=main_text_payload, view=view, ephemeral=True
                    )
                else:
                    await ephemeral_interaction.response.send_message(
                        content=main_text_payload, view=view, ephemeral=True
                    )
            except Exception as e:
                logger.error(f"Failed to transmit ephemeral interaction response: {e}")
            return

        if thread:
            redirect_view = DynamicView(
                self,
                channel,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            redirect_view.finalize_layout()

            main_text_payload = await self._resolve_mentions(main_text_payload, channel)
            if edit_target:
                await edit_target.edit(
                    content=main_text_payload, embed=None, view=redirect_view
                )
            elif original_message and (
                self.user in original_message.mentions or original_message.reference
            ):
                await original_message.reply(
                    content=main_text_payload, view=redirect_view
                )
            else:
                await channel.send(content=main_text_payload, view=redirect_view)

            thread_text = (
                f"hey <@{author.id}>, let's look at this here!\n\n{thread_text_payload}"
            )
            if image_prompts:
                thread_text += "\n\n*(🎨 Generating Image...)*"

            thread_view = DynamicView(
                self,
                thread,
                search_queries=search_queries,
                code_blocks=code_blocks,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            if thoughts_text and should_think:
                thread_view.add_item(
                    ThoughtsButton(
                        sanitize_thoughts(thoughts_text),
                        elapsed=elapsed,
                        thinking_active=False,
                        message_id=None,
                        bot_instance=self,
                        thinking_level=thinking_level,
                    )
                )
            self._populate_dynamic_components(
                thread_view,
                thread,
                buttons_parsed,
                user_selects_parsed,
                role_selects_parsed,
                channel_selects_parsed,
                mentionable_selects_parsed,
                string_selects_parsed,
                modal_buttons_parsed,
            )
            thread_view.finalize_layout()
            sent_msg = await self._send_split_content(
                thread, thread_text, view=thread_view
            )
            target_channel = thread

            if collect_session and sent_msg:
                title, dur, anon = collect_session
                self.active_collectors[sent_msg.id] = SubmissionCollector(
                    sent_msg.id, target_channel.id, anon, dur, title
                )
                self.loop.create_task(self._run_collection_timeout(sent_msg.id, dur))

            if image_prompts:
                self.loop.create_task(
                    self._generate_decoupled_image(
                        channel=target_channel,
                        author=author,
                        raw_image_prompt=image_prompts[0],
                        placeholder_msg=sent_msg,
                        context_history=history,
                        is_edit_flow=is_edit_flow,
                        original_message=original_message,
                        banter=thread_text_payload,
                        disabled_triggers=disabled_triggers,
                        config=config_state,
                    )
                )
        else:
            initial_text = main_text_payload
            if image_prompts:
                initial_text += "\n\n*(🎨 Generating Image...)*"

            if dsl_instructions:
                placeholder_text = "🎨 *Priesty is drafting composition...*"
                placeholder_view = DynamicView(
                    self, target_channel, user_app_session_id=user_app_session_id
                )
                placeholder_btn = ThoughtsButton(
                    "Visualizing layout...",
                    elapsed=0,
                    thinking_active=True,
                    message_id=0,
                    bot_instance=self,
                    thinking_level="HIGH",
                )
                placeholder_view.add_item(placeholder_btn)
                placeholder_view.finalize_layout()

                if edit_target:
                    placeholder_msg = await edit_target.edit(
                        content=placeholder_text, view=placeholder_view
                    )
                elif original_message and (
                    self.user in original_message.mentions or original_message.reference
                ):
                    placeholder_msg = await original_message.reply(
                        content=placeholder_text, view=placeholder_view
                    )
                else:
                    placeholder_msg = await target_channel.send(
                        content=placeholder_text, view=placeholder_view
                    )

                for item in placeholder_view.walk_children():
                    if isinstance(item, ThoughtsButton):
                        item.message_id = placeholder_msg.id

                self.loop.create_task(
                    self._generate_decoupled_layout(
                        channel=target_channel,
                        author=author,
                        dsl_instructions=dsl_instructions,
                        placeholder_msg=placeholder_msg,
                        context_history=history,
                        initial_prompt=prompt,
                        config_state=config_state,
                        user_app_session_id=user_app_session_id,
                        banter_text=initial_text,
                    )
                )

                sent_msg = placeholder_msg
            else:
                if not has_ui and not image_prompts:
                    sent_msg = await self._send_split_content(
                        target_channel, initial_text, original_message=original_message
                    )

                    if collect_session and sent_msg:
                        title, dur, anon = collect_session
                        self.active_collectors[sent_msg.id] = SubmissionCollector(
                            sent_msg.id, target_channel.id, anon, dur, title
                        )
                        self.loop.create_task(
                            self._run_collection_timeout(sent_msg.id, dur)
                        )
                else:
                    view = DynamicView(
                        self,
                        channel,
                        search_queries=search_queries,
                        code_blocks=code_blocks,
                        disabled_triggers=disabled_triggers,
                        original_message=original_message,
                        user_app_session_id=user_app_session_id,
                    )
                    if thoughts_text and should_think:
                        view.add_item(
                            ThoughtsButton(
                                sanitize_thoughts(thoughts_text),
                                elapsed=elapsed,
                                thinking_active=False,
                                message_id=edit_target.id if edit_target else None,
                                bot_instance=self,
                                thinking_level=thinking_level,
                            )
                        )

                    self._populate_dynamic_components(
                        view,
                        channel,
                        buttons_parsed,
                        user_selects_parsed,
                        role_selects_parsed,
                        channel_selects_parsed,
                        mentionable_selects_parsed,
                        string_selects_parsed,
                        modal_buttons_parsed,
                    )

                    if edit_target and edit_target.id in self.rerun_cache:
                        view.add_rerun_pagination(self, edit_target.id)
                    view.finalize_layout()

                    sent_msg = await self._send_split_content(
                        target_channel,
                        initial_text,
                        view=view,
                        edit_target=edit_target,
                        original_message=original_message,
                    )

                    if collect_session and sent_msg:
                        title, dur, anon = collect_session
                        self.active_collectors[sent_msg.id] = SubmissionCollector(
                            sent_msg.id, target_channel.id, anon, dur, title
                        )
                        self.loop.create_task(
                            self._run_collection_timeout(sent_msg.id, dur)
                        )

                    if image_prompts and sent_msg:
                        self.loop.create_task(
                            self._generate_decoupled_image(
                                channel=target_channel,
                                author=author,
                                raw_image_prompt=image_prompts[0],
                                placeholder_msg=sent_msg,
                                context_history=history,
                                is_edit_flow=is_edit_flow,
                                original_message=original_message,
                                banter=main_text_payload,
                                disabled_triggers=disabled_triggers,
                                config=config_state,
                            )
                        )

        if sent_msg and emoji:
            try:
                await sent_msg.add_reaction(emoji)
            except Exception:
                pass

        if typo_edit_data and sent_msg:
            self.loop.create_task(self._apply_typo_edit(sent_msg, typo_edit_data))

        if followups:
            for text in followups:
                await asyncio.sleep(2.0)
                async with target_channel.typing():
                    await asyncio.sleep(1.5)
                    followup_view = DynamicView(
                        self,
                        target_channel,
                        bot_text=text,
                        disabled_triggers=disabled_triggers,
                        original_message=original_message,
                        user_app_session_id=user_app_session_id,
                    )
                    followup_view.finalize_layout()
                    await self._send_split_content(
                        target_channel, text, view=followup_view
                    )

    async def _stream_layout_and_get_params(
        self, raw_prompt, context_history, channel, placeholder_msg
    ):
        """Streams the layout Python code block from Gemini Live Premium with HIGH reasoning metrics."""
        parts = [
            f"--- SERVER ENVIRONMENT CONTEXT ---\n{context_history}\n\n",
            f"--- TARGET LAYOUT SPECIFICATION ---\n{raw_prompt}",
        ]

        config = types.GenerateContentConfig(
            system_instruction="Generate only a valid python dsl block representing layout constructors conforming to mb_stubs.py.",
            temperature=0.7,
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH", include_thoughts=True
            ),
        )

        stream = await self.chat_handler.client.aio.models.generate_content_stream(
            model=self.chat_handler.premium_model, contents=parts, config=config
        )

        start_time = time.time()
        last_edit = 0
        accumulated_thoughts = []
        accumulated_text = []
        sanitized_payload = "Visualizing layout..."

        async for chunk in stream:
            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    is_thought = getattr(part, "thought", False)
                    if is_thought and getattr(part, "text", None):
                        accumulated_thoughts.append(part.text)
                    elif getattr(part, "text", None):
                        accumulated_text.append(part.text)

            now = time.time()
            if now - last_edit >= 1.8:
                last_edit = now
                current_elapsed = int(now - start_time)

                current_thoughts_str = "".join(accumulated_thoughts).strip()
                sanitized_payload = (
                    sanitize_thoughts(current_thoughts_str)
                    if current_thoughts_str
                    else "Visualizing layout..."
                )

                view = DynamicView(self, channel)
                btn = ThoughtsButton(
                    sanitized_payload,
                    elapsed=current_elapsed,
                    thinking_active=True,
                    message_id=placeholder_msg.id,
                    bot_instance=self,
                    thinking_level="HIGH",
                )
                view.add_item(btn)
                view.finalize_layout()

                listeners = self.active_thought_listeners.get(placeholder_msg.id, [])
                for listener_data in list(listeners):
                    try:
                        ephemeral_view = EphemeralThoughtsView(
                            sanitized_payload,
                            thinking_active=True,
                            current_level="HIGH",
                            page_index=listener_data["page_index"],
                            bot_instance=self,
                        )
                        await listener_data["message"].edit(view=ephemeral_view)
                    except Exception:
                        if listener_data in self.active_thought_listeners.get(
                            placeholder_msg.id, []
                        ):
                            self.active_thought_listeners[placeholder_msg.id].remove(
                                listener_data
                            )

                await placeholder_msg.edit(view=view)

        elapsed_total = int(time.time() - start_time)
        final_text_str = "".join(accumulated_text)
        final_thoughts_str = "".join(accumulated_thoughts).strip()

        final_thoughts_sanitized = (
            sanitize_thoughts(final_thoughts_str)
            if final_thoughts_str
            else "Visualizing layout..."
        )
        if placeholder_msg.id in self.active_thought_listeners:
            for listener_data in self.active_thought_listeners[placeholder_msg.id]:
                try:
                    completed_view = EphemeralThoughtsView(
                        final_thoughts_sanitized,
                        thinking_active=False,
                        current_level="HIGH",
                        page_index=listener_data["page_index"],
                        bot_instance=self,
                    )
                    await listener_data["message"].edit(view=completed_view)
                except Exception:
                    pass
            del self.active_thought_listeners[placeholder_msg.id]

        return final_text_str, final_thoughts_str, elapsed_total

    async def _generate_decoupled_layout(
        self,
        channel,
        author,
        dsl_instructions: str,
        placeholder_msg: discord.Message,
        context_history: str,
        initial_prompt: str,
        config_state: dict,
        user_app_session_id: Optional[int] = None,
        banter_text: str = "",
    ):
        """Asynchronous decoupled task coordinating the background compilation turn, retries, and handover."""
        logger.info(
            f"Decoupled Layout compile background job triggered. Instructions: '{dsl_instructions[:30]}...'"
        )

        compiler_prompt = build_layout_generator_prompt(
            initial_prompt=initial_prompt,
            ai_banter_reply=banter_text,
            extracted_instructions=dsl_instructions,
        )

        max_attempts = 6
        for attempt in range(1, max_attempts + 1):
            try:
                ai_code, final_thoughts, elapsed_total = (
                    await self._stream_layout_and_get_params(
                        raw_prompt=compiler_prompt,
                        context_history=context_history,
                        channel=channel,
                        placeholder_msg=placeholder_msg,
                    )
                )

                rendered_layout_view, compile_error = await build_message_layout(
                    self, channel, ai_code, initial_prompt, user_app_session_id
                )

                if compile_error:
                    logger.warning(
                        f"Decoupled layout compile pass {attempt} failed: {compile_error}. Retrying..."
                    )
                    if attempt < max_attempts:
                        compiler_prompt = (
                            f"[System Correction Pass: Your generated python DSL code failed compilation with error:\n"
                            f"{compile_error}\n\n"
                            f"Correct the syntax and generate a revised, functional Python layout block. Only output python code!]"
                        )
                        await asyncio.sleep(2.0)
                        continue
                    else:
                        raise ValueError(
                            f"Max compilation retries exceeded. Final error: {compile_error}"
                        )

                banter_view = DynamicView(
                    self, channel, user_app_session_id=user_app_session_id
                )
                if final_thoughts:
                    banter_view.add_item(
                        ThoughtsButton(
                            final_thoughts,
                            elapsed=elapsed_total,
                            thinking_active=False,
                            message_id=placeholder_msg.id,
                            bot_instance=self,
                            thinking_level="HIGH",
                        )
                    )
                banter_view.finalize_layout()

                resolved_banter = await self._resolve_mentions(banter_text, channel)
                if not resolved_banter.strip():
                    resolved_banter = "​"

                await placeholder_msg.edit(content=resolved_banter, view=banter_view)

                if rendered_layout_view:
                    await channel.send(content="", view=rendered_layout_view)

                return

            except Exception as exc:
                error_str = str(exc).lower()
                logger.error(
                    f"Decoupled Layout background compile attempt {attempt} failed: {exc}"
                )

                if attempt == max_attempts:
                    logger.error(
                        "Layout compilation failed after all retries. Silently removing broken placeholder."
                    )
                    try:
                        await placeholder_msg.delete()
                    except Exception:
                        pass
                    return

                backoff_delay = 3 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff_delay)

    async def _stream_artist_and_get_params(
        self,
        raw_prompt,
        context_history,
        channel,
        placeholder_msg,
        base_image_bytes=None,
        is_edit_flow=False,
    ):
        """Streams the layout Python code block from Gemini, automatically falling back on 503 errors."""
        start_time = time.time()
        last_edit = 0
        accumulated_thoughts = []
        accumulated_text = []

        current_banter = (
            placeholder_msg.content.replace("*(🎨 Generating Image...)*", "")
            .replace("*(🎨 Generating next version...)*", "")
            .strip()
            if placeholder_msg.content
            else ""
        )
        text_content = current_banter + "\n\n*(🎨 Priesty is drafting composition...)*"

        view = DynamicView(self, channel)
        btn = ThoughtsButton(
            "Drafting canvas...",
            elapsed=0,
            thinking_active=True,
            message_id=placeholder_msg.id,
            bot_instance=self,
            thinking_level="HIGH",
        )
        view.add_item(btn)
        view.finalize_layout()
        await placeholder_msg.edit(content=text_content, view=view)

        try:
            stream = await self.chat_handler.generate_artist_stream(
                raw_prompt, context_history, base_image_bytes
            )
        except Exception as e:
            logger.warning(
                f"Premium artist planning engine failed ({e}). Re-routing immediately to fallback model..."
            )

            contents_fallback = [
                f"--- CONVERSATIONAL REFERENCE HISTORY ---\n{context_history}\n\n",
                f"--- TARGET USER REQUEST TO EXPAND/EDIT ---\n{raw_prompt}",
            ]
            if base_image_bytes:
                try:
                    img = Image.open(io.BytesIO(base_image_bytes))
                    contents_fallback.insert(0, img)
                except Exception:
                    pass

            stream = await self.chat_handler.client.aio.models.generate_content_stream(
                model=self.chat_handler.fallback_model,
                contents=contents_fallback,
                config=types.GenerateContentConfig(
                    system_instruction=self.chat_handler.artist_system_instruction,
                    temperature=0.7,
                ),
            )

        async for chunk in stream:
            if (
                chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                for part in chunk.candidates[0].content.parts:
                    is_thought = getattr(part, "thought", False)
                    if is_thought and getattr(part, "text", None):
                        accumulated_thoughts.append(part.text)
                    elif getattr(part, "text", None):
                        accumulated_text.append(part.text)

            now = time.time()
            if now - last_edit >= 1.8:
                last_edit = now
                current_elapsed = int(now - start_time)

                combined_raw = "".join(accumulated_text)
                extracted_live_thoughts = []
                for thought_match in re.finditer(
                    r"(?i)<\s*thought\b[^>]*>?\s*(.*?)(?:<\s*/\s*thought\s*>|$)",
                    combined_raw,
                    flags=re.DOTALL,
                ):
                    extracted_live_thoughts.append(thought_match.group(1).strip())

                current_thoughts_str = "".join(accumulated_thoughts).strip()
                if extracted_live_thoughts:
                    current_thoughts_str += "\n\n" + "\n\n".join(
                        extracted_live_thoughts
                    )
                current_thoughts_str = current_thoughts_str.strip()

                sanitized_payload = (
                    sanitize_thoughts(current_thoughts_str)
                    if current_thoughts_str
                    else "Visualizing..."
                )

                view = DynamicView(self, channel)
                btn = ThoughtsButton(
                    sanitized_payload,
                    elapsed=current_elapsed,
                    thinking_active=True,
                    message_id=placeholder_msg.id,
                    bot_instance=self,
                    thinking_level="HIGH",
                )
                view.add_item(btn)
                view.finalize_layout()

                listeners = self.active_thought_listeners.get(placeholder_msg.id, [])
                for listener_data in list(listeners):
                    try:
                        ephemeral_view = EphemeralThoughtsView(
                            sanitized_payload,
                            thinking_active=True,
                            current_level="HIGH",
                            page_index=listener_data["page_index"],
                            bot_instance=self,
                        )
                        await listener_data["message"].edit(view=ephemeral_view)
                    except Exception:
                        if listener_data in self.active_thought_listeners.get(
                            placeholder_msg.id, []
                        ):
                            self.active_thought_listeners[placeholder_msg.id].remove(
                                listener_data
                            )

                await placeholder_msg.edit(content=text_content, view=view)

        elapsed_total = int(time.time() - start_time)
        final_text_str = "".join(accumulated_text)
        final_thoughts_str = "".join(accumulated_thoughts).strip()

        regex_thoughts = []
        for match in re.finditer(
            r"(?i)<\s*thought\b[^>]*>?\s*(.*?)(?:<\s*/\s*thought\s*>|$)",
            final_text_str,
            flags=re.DOTALL,
        ):
            regex_thoughts.append(match.group(1).strip())

        if regex_thoughts:
            final_thoughts_str += "\n\n" + "\n\n".join(regex_thoughts)
            final_thoughts_str = final_thoughts_str.strip()

        if placeholder_msg.id in self.active_thought_listeners:
            for listener_data in self.active_thought_listeners[placeholder_msg.id]:
                try:
                    completed_view = EphemeralThoughtsView(
                        final_thoughts_str,
                        thinking_active=False,
                        current_level="HIGH",
                        page_index=listener_data["page_index"],
                        bot_instance=self,
                    )
                    await listener_data["message"].edit(view=completed_view)
                except Exception:
                    pass
            del self.active_thought_listeners[placeholder_msg.id]

        json_match = re.search(
            r"```json\s*(.*?)\s*```", final_text_str, flags=re.DOTALL | re.IGNORECASE
        )
        data = {}
        if json_match:
            try:
                data = json.loads(json_match.group(1))
            except Exception:
                pass

        clean_summary = re.sub(
            r"(?i)<\s*thought\b[^>]*>?\s*.*?(?:<\s*/\s*thought\s*>|$)",
            "",
            final_text_str,
            flags=re.DOTALL,
        )
        clean_summary = re.sub(
            r"```json.*?```", "", clean_summary, flags=re.DOTALL | re.IGNORECASE
        ).strip()

        return data, clean_summary, final_thoughts_str, elapsed_total

    async def _generate_decoupled_image(
        self,
        channel,
        author,
        raw_image_prompt: str,
        placeholder_msg: discord.Message,
        context_history: str,
        is_edit_flow: bool = False,
        original_message: discord.Message = None,
        banter: str = "",
        disabled_triggers: dict = None,
        config: dict = None,
    ):
        logger.info(
            f"Artist decoupled background generation triggered. Edit Mode: {is_edit_flow} | Input: '{raw_image_prompt}'"
        )

        user_app_session_id = config.get("user_app_session_id") if config else None
        base_image_bytes = None

        if is_edit_flow:
            if original_message and original_message.attachments:
                for att in original_message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        try:
                            base_image_bytes = await att.read()
                            break
                        except Exception as read_err:
                            logger.error(
                                f"Failed to parse source attachments from message: {read_err}"
                            )

            if (
                not base_image_bytes
                and original_message
                and original_message.reference
                and original_message.reference.message_id
            ):
                ref_id = original_message.reference.message_id
                if ref_id in self.image_versions and self.image_versions[ref_id]:
                    base_image_bytes = self.image_versions[ref_id][-1]["image_bytes"]

        try:
            past_creations = await memory.fetch_recent_visual_memories(
                self, self.brain_server_id, author, limit=3
            )
            if past_creations:
                ledger_notes = ["\n\n=== PAST VISUAL STYLING REFERENCE ==="]
                for p_idx, item in enumerate(past_creations):
                    ledger_notes.append(
                        f"- Project {p_idx+1}: Prompt=\"{item['prompt']}\" | Preset=\"{item['style']}\" | Ratio=\"{item['ratio']}\" | Seed={item['seed']}"
                    )
                context_history += (
                    "\n".join(ledger_notes) + "\n==================================\n"
                )
        except Exception as fetch_err:
            logger.warning(f"Failed to fetch user's visual history ledger: {fetch_err}")

        try:
            data, clean_summary, final_thoughts, elapsed_total = (
                await self._stream_artist_and_get_params(
                    raw_image_prompt,
                    context_history,
                    channel,
                    placeholder_msg,
                    base_image_bytes,
                    is_edit_flow,
                )
            )
        except Exception as planning_exc:
            logger.error(
                f"Single-pass LLM prompt expansion pass failed: {planning_exc}"
            )
            data = {
                "expanded_prompt": raw_image_prompt,
                "selected_style": "photorealistic",
                "selected_ratio": "1:1",
                "selected_strength": "0.6",
            }
            clean_summary = ""
            final_thoughts = "Direct prompt fallback. Planning engine was bypassed."
            elapsed_total = 0

        expanded_prompt = data.get("expanded_prompt", raw_image_prompt)
        selected_style = data.get("selected_style", "photorealistic").strip().lower()
        selected_ratio = data.get("selected_ratio", "1:1").strip()
        selected_strength = data.get("selected_strength", "0.6").strip()

        width, height = RATIO_MAP.get(selected_ratio, (1024, 1024))
        seed = random.randint(1, 10000000)

        try:
            img_bytes = await self.image_generator.generate(
                prompt=expanded_prompt,
                width=width,
                height=height,
                seed=seed,
                style_key=selected_style,
                base_image_bytes=base_image_bytes,
                strength=selected_strength,
            )
        except image_gen.SafetyBlockError as safe_err:
            logger.info("Safety block triggered. Generating in-character complaint.")
            complain_prompt = f'[System Prompt: The image generation tool blocked the user\'s prompt "{raw_image_prompt}" due to a safety filter. Explain this casually to the user. Keep it relaxed and in-character.]'
            try:
                stream = await self.chat_handler.generate_reply_stream(
                    message_content=complain_prompt,
                    channel_history=context_history,
                    attachments=[],
                    user_display_name=author.display_name,
                    user_memory={},
                    server_context="",
                    thinking_level="MINIMAL",
                )
                complaint_text = ""
                async for chunk in stream:
                    if (
                        chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                    ):
                        chunk_text = "".join(
                            p.text
                            for p in chunk.candidates[0].content.parts
                            if getattr(p, "text", None)
                            and not getattr(p, "thought", False)
                        )
                        complaint_text += chunk_text
                complaint_text = re.sub(r"\[.*?\]", "", complaint_text).strip()
            except Exception as ai_err:
                logger.error(f"Failed to generate complaint text: {ai_err}")
                complaint_text = "bruh the rendering engine's safety filter absolutely lost its mind at that prompt and blocked it lol. try tweaking the wording a bit and i'll try again"

            view = DynamicView(
                self,
                channel,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            view.add_image_controls(
                self,
                placeholder_msg.id,
                current_prompt=raw_image_prompt,
                current_style=selected_style,
                current_ratio=selected_ratio,
                current_strength=selected_strength,
                current_is_edit_flow=is_edit_flow,
                show_actions=True,
            )
            view.add_item(
                ThoughtsButton(
                    final_thoughts,
                    elapsed=elapsed_total,
                    thinking_active=False,
                    message_id=placeholder_msg.id,
                    bot_instance=self,
                    thinking_level="HIGH",
                )
            )
            view.finalize_layout()
            complaint_text = await self._resolve_mentions(complaint_text, channel)
            await placeholder_msg.edit(content=complaint_text, embed=None, view=view)
            return

        except Exception as final_fail_exc:
            logger.error(
                f"Unified fallback image pipeline was completely exhausted: {final_fail_exc}"
            )
            raw_content = placeholder_msg.content or ""
            clean_content = banter if banter else raw_content
            clean_content = (
                clean_content.replace("*(🎨 Generating Image...)*", "")
                .replace("*(🎨 The Artist is drafting composition...)*", "")
                .strip()
            )
            if not clean_content or clean_content == "🎨 *Generating Image...*":
                clean_content = f'🎨 Attempting to generate "{raw_image_prompt[:50]}"'

            error_text = (
                clean_content + f"\n\n*(❌ Artist rendering failed: {final_fail_exc})*"
            )
            view = DynamicView(
                self,
                channel,
                disabled_triggers=disabled_triggers,
                original_message=original_message,
                user_app_session_id=user_app_session_id,
            )
            view.finalize_layout()
            error_text = await self._resolve_mentions(error_text, channel)
            try:
                await placeholder_msg.edit(
                    content=error_text, embed=None, view=view, attachments=[]
                )
            except Exception as edit_err:
                logger.error(f"Failed to post crash log notice: {edit_err}")
            return

        original_banter = banter
        if not original_banter:
            original_banter = getattr(placeholder_msg, "content", "") or ""

        clean_banter = (
            original_banter.replace("*(🎨 Generating Image...)*", "")
            .replace("*(🎨 The Artist is drafting composition...)*", "")
            .replace("*(🎨 Image Generated)*", "")
            .strip()
        )
        if clean_banter == "🎨 *Generating Image...*":
            clean_banter = ""

        if clean_summary:
            clean_banter = (
                clean_banter + ("\n\n" if clean_banter else "") + clean_summary
            )

        if placeholder_msg.id not in self.image_versions:
            self.image_versions[placeholder_msg.id] = []
            self.image_version_indexes[placeholder_msg.id] = 0

        version_payload = {
            "prompt": raw_image_prompt,
            "expanded": expanded_prompt,
            "style": selected_style,
            "ratio": selected_ratio,
            "strength": selected_strength,
            "seed": seed,
            "image_bytes": img_bytes,
            "is_completed": True,
            "banter": clean_banter,
            "is_edit_flow": is_edit_flow,
            "thoughts": final_thoughts,
            "thoughts_elapsed": elapsed_total,
            "user_app_session_id": user_app_session_id,
        }
        self.image_versions[placeholder_msg.id].append(version_payload)
        self.image_version_indexes[placeholder_msg.id] = (
            len(self.image_versions[placeholder_msg.id]) - 1
        )

        try:
            cdn_url = ""
            await self._update_image_message_view(
                placeholder_msg,
                channel,
                version_payload,
                user_app_session_id=user_app_session_id,
            )
            if placeholder_msg.attachments:
                cdn_url = placeholder_msg.attachments[0].url

            await memory.save_visual_memory(
                self,
                self.brain_server_id,
                author,
                raw_image_prompt,
                selected_style,
                selected_ratio,
                seed,
                cdn_url,
            )
        except Exception as save_err:
            logger.warning(
                f"Failed to write visual metadata to user's database: {save_err}"
            )

    async def process_image_generation_update(
        self,
        interaction: discord.Interaction,
        message_id: int,
        prompt: str,
        style: str,
        ratio: str,
        strength: str = "0.6",
        generation_mode: str = "t2i",
        seed: int = None,
        regenerate: bool = False,
    ):
        msg = (
            interaction.message
            if interaction.message
            else await interaction.channel.fetch_message(message_id)
        )

        prev_banter = ""
        versions = self.image_versions.get(message_id, [])
        user_app_session_id = (
            versions[-1].get("user_app_session_id") if versions else None
        )

        if versions:
            prev_banter = versions[-1].get("banter", "")

        current_text = prev_banter if prev_banter else (msg.content or "")

        loading_indicator = "\n\n*(🎨 Generating next version...)*"
        if loading_indicator not in current_text:
            current_text = (
                current_text.replace("*(🎨 Image Generated)*", "").strip()
                + loading_indicator
            )

        base_image_bytes = None
        is_edit_flow = False
        if generation_mode == "i2i" and versions:
            base_image_bytes = versions[-1]["image_bytes"]
            is_edit_flow = True

        temp_view = DynamicView(
            self, interaction.channel, user_app_session_id=user_app_session_id
        )
        temp_view.add_image_controls(
            self,
            message_id,
            prompt,
            style,
            ratio,
            strength,
            current_is_edit_flow=is_edit_flow,
            show_actions=False,
        )
        temp_view.finalize_layout()

        current_text = await self._resolve_mentions(current_text, interaction.channel)
        await msg.edit(content=current_text, embed=None, view=temp_view)

        self.loop.create_task(
            self._execute_image_generation_update_task(
                message_id=message_id,
                prompt=prompt,
                style=style,
                ratio=ratio,
                strength=strength,
                seed=seed,
                base_image_bytes=base_image_bytes,
                is_edit_flow=is_edit_flow,
                prev_banter=prev_banter,
                msg_target=msg,
                channel=interaction.channel,
                user_app_session_id=user_app_session_id,
                author=interaction.user,
            )
        )

    async def _execute_image_generation_update_task(
        self,
        message_id: int,
        prompt: str,
        style: str,
        ratio: str,
        strength: str,
        seed: int,
        base_image_bytes: bytes,
        is_edit_flow: bool,
        prev_banter: str,
        msg_target: discord.Message,
        channel,
        user_app_session_id=None,
        author=None,
    ):
        logger.info(
            f"Artist parameters update background generation task triggered. Style: {style} | Ratio: {ratio}"
        )
        width, height = RATIO_MAP.get(ratio, (1024, 1024))
        if seed is None:
            seed = random.randint(1, 10000000)

        try:
            history = self.history_tracker.get_formatted_history(channel.id)

            try:
                past_creations = await memory.fetch_recent_visual_memories(
                    self, self.brain_server_id, author, limit=3
                )
                if past_creations:
                    ledger_notes = ["\n\n=== PAST VISUAL STYLING REFERENCE ==="]
                    for p_idx, item in enumerate(past_creations):
                        ledger_notes.append(
                            f"- Project {p_idx+1}: Prompt=\"{item['prompt']}\" | Preset=\"{item['style']}\" | Ratio=\"{item['ratio']}\" | Seed={item['seed']}"
                        )
                    history += (
                        "\n".join(ledger_notes)
                        + "\n==================================\n"
                    )
            except Exception as fetch_err:
                logger.warning(
                    f"Failed to fetch user's visual history ledger: {fetch_err}"
                )

            data, clean_summary, final_thoughts, elapsed_total = (
                await self._stream_artist_and_get_params(
                    prompt, history, msg_target, base_image_bytes, is_edit_flow
                )
            )
        except Exception as planning_exc:
            logger.error(f"Update parameter planning pass failed: {planning_exc}")
            data = {
                "expanded_prompt": prompt,
                "selected_style": style,
                "selected_ratio": ratio,
                "selected_strength": strength,
            }
            clean_summary = ""
            final_thoughts = (
                "Direct prompt update fallback. Planning engine was bypassed."
            )
            elapsed_total = 0

        expanded_prompt = data.get("expanded_prompt", prompt)

        try:
            img_bytes = await self.image_generator.generate(
                prompt=expanded_prompt,
                width=width,
                height=height,
                seed=seed,
                style_key=style,
                base_image_bytes=base_image_bytes,
                strength=strength,
            )
        except image_gen.SafetyBlockError as safe_err:
            logger.info(
                "Safety block triggered during edit update. Generating complaint."
            )
            complain_prompt = f'[System Prompt: The image generation tool blocked the user\'s edit prompt "{prompt}" due to a safety filter. Explain this casually to the user. Keep it relaxed and in-character.]'
            try:
                stream = await self.chat_handler.generate_reply_stream(
                    message_content=complain_prompt,
                    channel_history=self.history_tracker.get_formatted_history(
                        channel.id
                    ),
                    attachments=[],
                    user_display_name="User",
                    user_memory={},
                    server_context="",
                    thinking_level="MINIMAL",
                )
                complaint_text = ""
                async for chunk in stream:
                    if (
                        chunk.candidates
                        and chunk.candidates[0].content
                        and chunk.candidates[0].content.parts
                    ):
                        chunk_text = "".join(
                            p.text
                            for p in chunk.candidates[0].content.parts
                            if getattr(p, "text", None)
                            and not getattr(p, "thought", False)
                        )
                        complaint_text += chunk_text
                    complaint_text = re.sub(r"\[.*?\]", "", complaint_text).strip()
            except Exception:
                complaint_text = "bruh the safety filter completely blocked that edit lol. try tweaking the wording a bit and i'll try again"

            view = DynamicView(self, channel, user_app_session_id=user_app_session_id)
            view.add_image_controls(
                self,
                msg_target.id,
                current_prompt=prompt,
                current_style=style,
                current_ratio=ratio,
                current_strength=strength,
                current_is_edit_flow=is_edit_flow,
                show_actions=True,
            )
            view.finalize_layout()
            complaint_text = await self._resolve_mentions(complaint_text, channel)
            await msg_target.edit(content=complaint_text, embed=None, view=view)
            return

        except Exception as final_fail_exc:
            logger.error(
                f"Unified fallback update task was completely exhausted: {final_fail_exc}"
            )
            try:
                versions = self.image_versions.get(message_id, [])
                if versions:
                    last_version = versions[-1]
                    await self._update_image_message_view(
                        msg_target,
                        channel,
                        last_version,
                        user_app_session_id=user_app_session_id,
                    )
                    content_note = (
                        (prev_banter or "")
                        .replace("*(🎨 Generating next version...)*", "")
                        .strip()
                    )
                    note = f"\n\n*(❌ Parameter update failed: {final_fail_exc})*"
                    error_text = content_note + note
                    view = DynamicView(
                        self, channel, user_app_session_id=user_app_session_id
                    )
                    view.finalize_layout()
                    error_text = await self._resolve_mentions(error_text, channel)
                    await msg_target.edit(content=error_text, embed=None, view=view)
                else:
                    raw_content = msg_target.content or ""
                    clean_content = prev_banter or raw_content
                    clean_content = clean_content.replace(
                        "*(🎨 Generating next version...)*", ""
                    ).strip()
                    error_text = (
                        clean_content
                        + f"\n\n*(❌ Parameter update failed: {final_fail_exc})*"
                    )
                    view = DynamicView(
                        self, channel, user_app_session_id=user_app_session_id
                    )
                    view.finalize_layout()
                    error_text = await self._resolve_mentions(error_text, channel)
                    await msg_target.edit(content=error_text, embed=None, view=view)
            except Exception as recover_err:
                logger.error(
                    f"Failed to cleanly recover and render error fallback: {recover_err}"
                )
            return

        final_banter = clean_summary if clean_summary else prev_banter

        if message_id not in self.image_versions:
            self.image_versions[message_id] = []
            self.image_version_indexes[message_id] = 0

        version_payload = {
            "prompt": prompt,
            "expanded": expanded_prompt,
            "style": style,
            "ratio": ratio,
            "strength": strength,
            "seed": seed,
            "image_bytes": img_bytes,
            "is_completed": True,
            "banter": final_banter,
            "is_edit_flow": is_edit_flow,
            "thoughts": final_thoughts,
            "thoughts_elapsed": elapsed_total,
            "user_app_session_id": user_app_session_id,
        }
        self.image_versions[message_id].append(version_payload)
        self.image_version_indexes[message_id] = (
            len(self.image_versions[message_id]) - 1
        )

        try:
            cdn_url = ""
            await self._update_image_message_view(
                msg_target,
                channel,
                version_payload,
                user_app_session_id=user_app_session_id,
            )
            if msg_target.attachments:
                cdn_url = msg_target.attachments[0].url

            await memory.save_visual_memory(
                self, self.brain_server_id, author, prompt, style, ratio, seed, cdn_url
            )
        except Exception as save_err:
            logger.warning(
                f"Failed to write visual metadata to user's database: {save_err}"
            )

    async def _update_image_message_view(
        self,
        message: discord.Message,
        channel,
        version_payload: dict,
        user_app_session_id=None,
    ):
        prompt = version_payload["prompt"]
        style = version_payload["style"]
        ratio = version_payload["ratio"]
        strength = version_payload.get("strength", "0.6")
        is_edit_flow = version_payload.get("is_edit_flow", False)
        img_bytes = version_payload["image_bytes"]
        is_completed = version_payload.get("is_completed", False)
        banter = version_payload.get("banter", "").strip()
        final_thoughts = version_payload.get("thoughts", "")
        thoughts_elapsed = version_payload.get("thoughts_elapsed", None)

        file = discord.File(fp=io.BytesIO(img_bytes), filename="generated.png")

        clean_content = banter if banter else message.content or ""
        clean_content = (
            clean_content.replace("*(🎨 Generating Image...)*", "")
            .replace("*(🎨 Generating next version...)*", "")
            .replace("*(🎨 The Artist is drafting composition...)*", "")
            .replace("*(🎨 Image Generated)*", "")
            .strip()
        )

        if is_completed:
            clean_content += "\n\n*(🎨 Image Generated)*"
        if not clean_content or clean_content == "🎨 *Generating Image...*":
            clean_content = (
                f'🎨 Here is your drawing of "{prompt[:80]}"!\n\n*(🎨 Image Generated)*'
            )

        view = DynamicView(self, channel, user_app_session_id=user_app_session_id)
        if final_thoughts:
            view.add_item(
                ThoughtsButton(
                    final_thoughts,
                    elapsed=thoughts_elapsed,
                    thinking_active=False,
                    message_id=message.id,
                    bot_instance=self,
                    thinking_level="HIGH",
                )
            )
        view.add_image_controls(
            self,
            message.id,
            prompt,
            style,
            ratio,
            strength,
            current_is_edit_flow=is_edit_flow,
        )
        view.finalize_layout()

        clean_content = await self._resolve_mentions(clean_content, channel)
        await message.edit(
            content=clean_content, embed=None, view=view, attachments=[file]
        )

    async def _apply_typo_edit(
        self, message: discord.Message, typo_edit_data: tuple[str, str]
    ):
        await asyncio.sleep(2.5)
        try:
            corrected_text = message.content.replace(
                typo_edit_data[0], typo_edit_data[1]
            )
            await message.edit(content=corrected_text)
        except Exception as e:
            logger.warning(f"Failed to apply typo self-edit correction: {e}")

    def _compile_server_context(
        self, guild: discord.Guild, member: discord.Member = None
    ) -> str:
        if not guild:
            return "Environment: Direct Messages."

        context = f"Current Server: {guild.name}\nAvailable Text Channels to Mention:\n"
        for channel in guild.text_channels:
            context += f"- #{channel.name}: Use mention tag <#{channel.id}>\n"

        if (
            member
            and isinstance(member, discord.Member)
            and member.voice
            and member.voice.channel
        ):
            context += f"\nActive User Voice Status:\n- User is currently inside Voice Channel: #{member.voice.channel.name}\n"
        else:
            context += f"\nActive User Voice Status:\n- User is not currently connected to any Voice Channel in this server.\n"

        context += "\nAvailable Custom Emojis in this Server (You MUST use this EXACT syntax to display them):\n"
        if guild.emojis:
            for emoji in guild.emojis:
                syntax = (
                    f"<a:{emoji.name}:{emoji.id}>"
                    if emoji.animated
                    else f"<:{emoji.name}:{emoji.id}>"
                )
                context += f"- Name: :{emoji.name}: | Syntax: {syntax}\n"
        else:
            context += "- No custom emojis available in this server.\n"

        return context

    def _get_clean_user_id(self, user: discord.User) -> str:
        clean_name = re.sub(r"[^a-zA-Z0-9]", "", user.name).lower()
        return f"{clean_name}-{user.id}"

    def run_bot(self):
        """Standard boot handler reading credentials and starting client connection gateway."""
        token = os.getenv("DISCORD_TOKEN")
        self.run(token)

    async def _automated_news_loop(self):
        """Asynchronous background scheduler that evaluates configured timezones and processes news runs."""
        await self.wait_until_ready()
        logger.info("Automated Server News background loop task successfully started.")
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                for guild in self.guilds:
                    config = await self.get_config(guild.id, is_dm=False)
                    if not config.get("news_enabled", False):
                        continue

                    import zoneinfo

                    tz_str = config.get("news_timezone", "America/New_York")
                    try:
                        tz = zoneinfo.ZoneInfo(tz_str)
                    except Exception:
                        tz = zoneinfo.ZoneInfo("America/New_York")

                    local_time = dt_class.now(tz)
                    date_str = local_time.strftime("%Y-%m-%d")
                    hour = local_time.hour
                    minute = local_time.minute

                    import core.memory as memory

                    state = await memory.load_news_state(
                        self, self.brain_server_id, guild.id
                    )
                    if not state:
                        state = {
                            "last_episode_number": 0,
                            "show_name": "",
                            "last_morning_pregen_date": "",
                            "last_morning_broadcast_date": "",
                            "last_night_pregen_date": "",
                            "last_night_broadcast_date": "",
                        }

                    if (hour == 8 and minute >= 30) and state.get(
                        "last_morning_pregen_date"
                    ) != date_str:
                        self.loop.create_task(
                            self._run_pregen_pipeline(
                                guild.id, "morning", config, state
                            )
                        )

                    elif (hour >= 9 and hour < 12) and state.get(
                        "last_morning_broadcast_date"
                    ) != date_str:
                        if state.get("last_morning_pregen_date") == date_str:
                            state["last_morning_broadcast_date"] = date_str
                            await memory.save_news_state(
                                self, self.brain_server_id, guild.id, state
                            )
                            self.loop.create_task(
                                self._run_broadcast_pipeline(
                                    guild.id, "morning", config, state
                                )
                            )

                    elif (hour == 19 and minute >= 30) and state.get(
                        "last_night_pregen_date"
                    ) != date_str:
                        self.loop.create_task(
                            self._run_pregen_pipeline(guild.id, "night", config, state)
                        )

                    elif (hour >= 20 or hour < 4) and state.get(
                        "last_night_broadcast_date"
                    ) != date_str:
                        if state.get("last_night_pregen_date") == date_str:
                            state["last_night_broadcast_date"] = date_str
                            await memory.save_news_state(
                                self, self.brain_server_id, guild.id, state
                            )
                            self.loop.create_task(
                                self._run_broadcast_pipeline(
                                    guild.id, "night", config, state
                                )
                            )

            except Exception as e:
                logger.error(
                    f"Error inside automated news cron loop: {e}", exc_info=True
                )

    async def _generate_show_branding(
        self, server_name: str, gemini_key: str, news_model: str
    ) -> str:
        """Helper generates a permanent, highly creative news station show name via Gemini."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=gemini_key)
        prompt = (
            f"You are a professional television branding producer. The Discord server is named '{server_name}'. "
            f"Generate a single, highly creative, memorable, and thematic daily broadcast news show name "
            f"representing this server (e.g. 'Chaos Conquest Daily Chronicle', 'Chaos Conquest News Network'). "
            f"Do not write any introductory or explanatory text. Output ONLY the clean, final show name string itself."
        )
        try:
            response = await client.aio.models.generate_content(
                model=news_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=40, temperature=0.7
                ),
            )
            if response and response.text:
                show_name = response.text.strip().replace('"', "").replace("'", "")
                return (
                    show_name
                    if len(show_name) >= 4
                    else f"{server_name} Daily Chronicle"
                )
        except Exception as e:
            logger.warning(f"Branding generation failed, falling back to default: {e}")
        return f"{server_name} Daily Chronicle"

    async def _automated_news_loop(self):
        """Asynchronous background scheduler that evaluates configured timezones and processes news runs."""
        await self.wait_until_ready()
        logger.info("Automated Server News background loop task successfully started.")
        while not self.is_closed():
            await asyncio.sleep(60)
            try:
                for guild in self.guilds:
                    config = await self.get_config(guild.id, is_dm=False)
                    if not config.get("news_enabled", False):
                        continue

                    if guild.id in self._active_news_runs:
                        logger.info(
                            f"Skipping cron tick for Guild ID {guild.id}: news render already in progress."
                        )
                        continue

                    import zoneinfo

                    tz_str = config.get("news_timezone", "America/New_York")
                    try:
                        tz = zoneinfo.ZoneInfo(tz_str)
                    except Exception:
                        tz = zoneinfo.ZoneInfo("America/New_York")

                    local_time = dt_class.now(tz)
                    date_str = local_time.strftime("%Y-%m-%d")
                    hour = local_time.hour
                    minute = local_time.minute

                    import core.memory as memory

                    state = await memory.load_news_state(
                        self, self.brain_server_id, guild.id
                    )
                    if not state:
                        state = {
                            "last_episode_number": 0,
                            "show_name": "",
                            "last_morning_pregen_date": "",
                            "last_morning_broadcast_date": "",
                            "last_night_pregen_date": "",
                            "last_night_broadcast_date": "",
                        }

                    if (hour == 8 and minute >= 30) and state.get(
                        "last_morning_pregen_date"
                    ) != date_str:
                        self.loop.create_task(
                            self._run_pregen_pipeline(
                                guild.id, "morning", config, state
                            )
                        )

                    elif (hour >= 9 and hour < 12) and state.get(
                        "last_morning_broadcast_date"
                    ) != date_str:
                        if state.get("last_morning_pregen_date") == date_str:
                            state["last_morning_broadcast_date"] = date_str
                            await memory.save_news_state(
                                self, self.brain_server_id, guild.id, state
                            )
                            self.loop.create_task(
                                self._run_broadcast_pipeline(
                                    guild.id, "morning", config, state
                                )
                            )

                    elif (hour == 19 and minute >= 30) and state.get(
                        "last_night_pregen_date"
                    ) != date_str:
                        self.loop.create_task(
                            self._run_pregen_pipeline(guild.id, "night", config, state)
                        )

                    elif (hour >= 20 or hour < 4) and state.get(
                        "last_night_broadcast_date"
                    ) != date_str:
                        if state.get("last_night_pregen_date") == date_str:
                            state["last_night_broadcast_date"] = date_str
                            await memory.save_news_state(
                                self, self.brain_server_id, guild.id, state
                            )
                            self.loop.create_task(
                                self._run_broadcast_pipeline(
                                    guild.id, "night", config, state
                                )
                            )

            except Exception as e:
                logger.error(
                    f"Error inside automated news cron loop: {e}", exc_info=True
                )

    async def _run_pregen_pipeline(
        self, guild_id: int, edition: str, config: dict, state: dict
    ):
        """Asynchronous, non-blocking scheduler controls resources and offloads rendering processes."""
        if guild_id in self._active_news_runs:
            return

        self._active_news_runs.add(guild_id)
        logger.info(
            f"[Server News: {edition.upper()}] Initiating pre-generation pipeline for Guild ID {guild_id}..."
        )

        try:
            gemini_key = self.gemini_key
            news_model = os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash")

            show_name = state.get("show_name", "").strip()
            guild = self.get_guild(guild_id)
            if not guild:
                guild = await self.fetch_guild(guild_id)
            server_name = guild.name if guild else "Server News"

            if not show_name:
                show_name = await self._generate_show_branding(
                    server_name, gemini_key, news_model
                )
                state["show_name"] = show_name
                import core.memory as memory

                await memory.save_news_state(
                    self, self.brain_server_id, guild_id, state
                )

            episode = state.get("last_episode_number", 0) + 1

            import zoneinfo

            tz_str = config.get("news_timezone", "America/New_York")
            try:
                tz = zoneinfo.ZoneInfo(tz_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("America/New_York")
            local_now = dt_class.now(tz)
            formatted_date = local_now.strftime("%A, %B %d, %Y")
            formatted_time = "9:00 AM" if edition == "morning" else "8:00 PM"

            from tools.news.data_gatherer import NewsScraper

            scraper = NewsScraper(self, guild_id, edition=edition)
            raw_data_path = await scraper.gather_all_data(config)

            from tools.news.script_writer import write_news_script

            try:
                segments = await write_news_script(
                    edition=edition,
                    episode_number=episode,
                    date_str=formatted_date,
                    time_str=formatted_time,
                    show_name=show_name,
                    guild_id=guild_id,
                )
            except Exception as script_err:
                logger.error(
                    f"[Server News: {edition.upper()}] Script writing failed: {script_err}",
                    exc_info=True,
                )
                self._active_news_runs.discard(guild_id)
                return

            self.loop.create_task(
                self._execute_threaded_rendering(
                    guild_id, edition, episode, show_name, segments, state
                )
            )
        except Exception as pregen_err:
            logger.error(f"Error executing pre-generation pipeline: {pregen_err}")
            self._active_news_runs.discard(guild_id)

    async def purge_streamable_later(self, shortcode: str, delay_seconds: int = 1800):
        """Asynchronously issue an HTTP DELETE to Streamable API after the test period expires."""
        await asyncio.sleep(delay_seconds)
        email = os.getenv("STREAMABLE_EMAIL")
        password = os.getenv("STREAMABLE_PASSWORD")
        if not email or not password:
            logger.warning(
                "Streamable credentials missing during auto-purge execution."
            )
            return

        url = f"https://api.streamable.com/videos/{shortcode}"
        auth = aiohttp.BasicAuth(email, password)
        try:
            async with aiohttp.ClientSession(auth=auth) as session:
                async with session.delete(url, timeout=15) as resp:
                    if resp.status == 200:
                        logger.info(
                            f"Successfully auto-purged dev broadcast video {shortcode} from Streamable."
                        )
                    else:
                        logger.warning(
                            f"Streamable delete returned status {resp.status}: {await resp.text()}"
                        )
        except Exception as e:
            logger.warning(f"Error during Streamable auto-purge: {e}")

    async def _execute_threaded_rendering(
        self,
        guild_id: int,
        edition: str,
        episode: int,
        show_name: str,
        segments: list,
        state: dict,
        dev_mode: bool = False,
    ):
        """Offloads rendering processes and uploads outputs to Streamable on worker threads."""
        logger.info(
            f"[Server News: {edition.upper()}] Starting non-blocking render thread pool..."
        )

        config = await self.get_config(guild_id, is_dm=False)
        music_path = (
            "assets/late_night_jazz.mp3"
            if edition == "night"
            else "assets/morning_acoustic.mp3"
        )
        local_output_filename = f"temp_{edition}_edition_broadcast_{guild_id}.mp4"

        from tools.news.video_generator import generate_full_news_video

        try:
            await asyncio.to_thread(
                generate_full_news_video,
                segments=segments,
                output_filepath=local_output_filename,
                music_path=music_path,
                edition=edition,
                guild_id=guild_id,
            )
        except Exception as render_err:
            logger.error(
                f"[Server News: {edition.upper()}] Video compiling thread crashed: {render_err}",
                exc_info=True,
            )
            self._active_news_runs.discard(guild_id)
            return

        from tools.news.news_orchestrator import upload_to_streamable

        title = f"{show_name} - Ep. {episode} ({edition.capitalize()})"
        try:
            streamable_url = await asyncio.to_thread(
                upload_to_streamable, local_output_filename, title
            )
        except Exception as upload_err:
            logger.error(
                f"[Server News: {edition.upper()}] Video uploading thread crashed: {upload_err}",
                exc_info=True,
            )
            self._active_news_runs.discard(guild_id)
            return

        if not streamable_url:
            logger.warning(
                f"[Server News: {edition.upper()}] Pre-generation uploaded null url."
            )
            self._active_news_runs.discard(guild_id)
            return

        if dev_mode:
            shortcode = streamable_url.split("/")[-1].strip()
            self.loop.create_task(self.purge_streamable_later(shortcode))

        state[f"staged_{edition}_url"] = streamable_url
        state[f"staged_{edition}_episode"] = episode
        if not dev_mode:
            state["last_episode_number"] = episode
            import zoneinfo

            tz_str = config.get("news_timezone", "America/New_York")
            try:
                tz = zoneinfo.ZoneInfo(tz_str)
            except Exception:
                tz = zoneinfo.ZoneInfo("America/New_York")
            local_now = dt_class.now(tz)
            date_str = local_now.strftime("%Y-%m-%d")
            state[f"last_{edition}_pregen_date"] = date_str

            import core.memory as memory

            await memory.save_news_state(self, self.brain_server_id, guild_id, state)

        logger.info(
            f"[Server News: {edition.upper()}] Success! Staged video link cached: {streamable_url}"
        )

        try:
            await self._run_broadcast_pipeline(
                guild_id, edition, config, state, dev_mode=dev_mode
            )
        finally:

            self._active_news_runs.discard(guild_id)

    async def _run_broadcast_pipeline(
        self,
        guild_id: int,
        edition: str,
        config: dict,
        state: dict,
        dev_mode: bool = False,
    ):
        """Generates visual descriptors, selects dispatch channels, and broadcasts news packages."""
        staged_url = state.get(f"staged_{edition}_url", "")
        episode = state.get(f"staged_{edition}_episode", 1)
        show_name = state.get("show_name", "Server News")

        if not staged_url:
            logger.warning(
                f"No staged broadcast payload found to publish for {edition}."
            )
            return

        if dev_mode:
            owner_id = int(os.getenv("OWNER_ID", 0))
            user = self.get_user(owner_id) or await self.fetch_user(owner_id)
            if not user:
                logger.warning("Could not locate developer user object to dispatch DM.")
                return
            try:
                target_channel = await user.create_dm()
            except Exception as dm_err:
                logger.warning(
                    f"Could not open direct messages with developer: {dm_err}"
                )
                return
        else:
            news_channel_id = config.get("news_channel_id")
            if not news_channel_id:
                logger.warning("No broadcast news channel set in configuration.")
                return
            guild = self.get_guild(guild_id)
            if not guild:
                guild = await self.bot.fetch_guild(guild_id)
            target_channel = guild.get_channel(news_channel_id)
            if not target_channel:
                logger.warning(
                    f"Broadcast channel ID {news_channel_id} is unreachable."
                )
                return

        gemini_key = self.gemini_key
        fallback_model = self.chat_handler.fallback_model
        news_model = os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash")

        raw_data_path = f"temp/raw_news_data_{guild_id}.json"
        raw_context_str = ""
        if os.path.exists(raw_data_path):
            with open(raw_data_path, "r", encoding="utf-8") as f:
                raw_context_str = f.read()

        import zoneinfo

        tz_str = config.get("news_timezone", "America/New_York")
        try:
            tz = zoneinfo.ZoneInfo(tz_str)
        except Exception:
            tz = zoneinfo.ZoneInfo("America/New_York")
        local_now = dt_class.now(tz)
        formatted_date = local_now.strftime("%A, %B %d, %Y")

        summary_prompt = (
            f"You are a professional social media manager for the daily broadcast show '{show_name}'.\n"
            f"Analyze the following server activity JSON logs:\n\n{raw_context_str[:4000]}\n\n"
            f"Your task is to write a highly exciting, casual, and engaging broadcast summary for Episode {episode} ({edition.capitalize()} Show).\n\n"
            f"=== STRICT FORMATTING INSTRUCTIONS (MANDATORY) ===\n"
            f"1. You must write exactly 3 distinct highlights (news, funny conversations, active users, or gaming alerts) discovered in the logs.\n"
            f"2. Format each highlight as a single short paragraph starting with an emoji bullet point.\n"
            f"3. Do NOT write section headers like 'Heading 2', 'Highlights', 'Segment', or 'Today's Top Headlines'. Start directly with the bullet points.\n"
            f"4. Do NOT use placeholders, template markers, or generic descriptions. Mention actual usernames, text channel names, or event titles.\n"
            f"5. If the server logs are completely empty or contain fewer than 5 chat messages, write a humorous, self-aware update about how the server is peacefully quiet, residents are touching grass, and state that we are standing by for breaking updates.\n\n"
            f"Output ONLY the three clean, highly engaging highlight bullet points. Do not include introductory notes or closing banter."
        )

        from google import genai
        from google.genai import types

        ai_client = genai.Client(api_key=gemini_key)
        summary_text = ""
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                summary_response = await ai_client.aio.models.generate_content(
                    model=fallback_model,
                    contents=summary_prompt,
                    config=types.GenerateContentConfig(
                        max_output_tokens=700, temperature=0.7
                    ),
                )
                if summary_response and summary_response.text:
                    summary_text = summary_response.text.strip()
                    break
            except Exception as e:
                logger.warning(f"Summary generation attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2.0)

        if not summary_text:
            summary_text = (
                "📝 **Tonight's Highlights:**\n"
                "• Core log sweeps compiled across public text channels.\n"
                "• Dynamic global news and scheduled events overview.\n"
                "• Special commendation plaque awards ceremony live."
            )

        bold_title = show_name.upper().replace(" ", "  •  ")
        final_announcement = (
            f"🎬 **{bold_title}** — *Episode {episode}*\n"
            f"🕒 **Edition**: {edition.capitalize()} Show | {formatted_date}\n"
            f"📺 **Watch Live**: {staged_url}\n\n"
            f"----------------------------------------\n"
            f"{summary_text}\n"
            f"----------------------------------------\n"
            f"💬 *React below to let us know your thoughts on tonight's episode!*"
        )

        if len(final_announcement) > 1900:
            lines = final_announcement.split("\n")
            truncated_announcement = []
            current_len = 0
            for line in lines:
                if current_len + len(line) + 50 > 1900:
                    truncated_announcement.append(
                        "... [Summary truncated due to length limits]"
                    )
                    break
                truncated_announcement.append(line)
                current_len += len(line) + 1
            final_announcement = "\n".join(truncated_announcement)

        final_announcement = await self._resolve_mentions(
            final_announcement, target_channel
        )

        try:
            announcement_msg = await target_channel.send(content=final_announcement)
            logger.info(
                f"[Server News: {edition.upper()}] Success! Daily Broadcast Announcement dispatched."
            )

            if (
                edition == "morning"
                and not dev_mode
                and hasattr(announcement_msg, "create_thread")
            ):
                try:
                    thread = await announcement_msg.create_thread(
                        name=f"Ep. {episode} Q&A", auto_archive_duration=1440
                    )
                    await thread.send(
                        "📬 **Welcome to the morning broadcast Q&A Thread!**\n"
                        "Ask our digital anchor any questions or discuss today's morning broadcast right here. "
                        "A few questions will be selected and answered on tonight's Late-Night Show!"
                    )
                    logger.info(f"Created morning Q&A thread: {thread.name}")
                except Exception as thread_err:
                    logger.warning(f"Could not create morning Q&A thread: {thread_err}")

            if edition == "morning" and not dev_mode:
                try:
                    poll_config = types.GenerateContentConfig(
                        system_instruction=(
                            "Generate a highly entertaining, creative, and server-thematic 'Question of the Day' "
                            "or 'Would You Rather' poll for a gaming/social community named Chaos Conquest."
                        ),
                        temperature=0.8,
                        response_mime_type="application/json",
                        response_schema=QOTDPollSchema,
                    )

                    poll_response = await ai_client.aio.models.generate_content(
                        model=news_model,
                        contents="Generate today's community interaction poll.",
                        config=poll_config,
                    )

                    if poll_response and poll_response.text:
                        poll_data = json.loads(poll_response.text.strip())

                        dp_poll = discord.Poll(
                            question=poll_data["question"][:80],
                            duration=timedelta(hours=10),
                        )
                        for opt in poll_data["answers"][:4]:
                            dp_poll.add_answer(
                                text=opt["text"][:55], emoji=opt["emoji"]
                            )

                        await target_channel.send(poll=dp_poll)
                        logger.info(
                            "Successfully posted daily interactive morning poll."
                        )
                except Exception as poll_err:
                    logger.warning(f"Could not deploy morning QOTD poll: {poll_err}")

        except Exception as post_err:
            logger.error(
                f"[Server News: {edition.upper()}] Failed to post broadcast message: {post_err}",
                exc_info=True,
            )
            return

        if not dev_mode:
            state[f"staged_{edition}_url"] = ""
            state[f"staged_{edition}_episode"] = ""
            import core.memory as memory

            await memory.save_news_state(self, self.brain_server_id, guild_id, state)

        local_output_filename = f"temp_{edition}_edition_broadcast_{guild_id}.mp4"
        if os.path.exists(local_output_filename):
            try:
                os.remove(local_output_filename)
                logger.info(
                    f"[Server News: {edition.upper()}] Cleared local staging cache file: {local_output_filename}"
                )
            except Exception as rm_err:
                logger.warning(f"Could not clear local staging cache file: {rm_err}")
