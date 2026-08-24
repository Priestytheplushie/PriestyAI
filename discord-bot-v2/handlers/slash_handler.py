import io
import re
import time
import uuid
import json
import base64
import asyncio
import logging
from typing import Any
from datetime import datetime, timezone
import aiohttp
import discord
from discord import app_commands, ui
from config.settings import LOADING_EMOJI
from core.engine import ChatEngine
from core.client_manager import client_manager
from core.memory_manager import memory_manager
from core.config_manager import config_manager
from core.branch_manager import branch_manager
from handlers.stream_handler import DiscordStreamDispatcher, merge_views, apply_message_parsers
from tools.registry import ToolExecutionContext
from ui.thought_container import ThinkingButtonView, ThoughtContainerView, PlaceholderLayoutView
from ui.context_views import build_version_switcher_view, BranchHeaderView, BranchTranscriptView
from ui.config_views import (
    ServerIdentityDashboardView,
    ConfigHelpView,
    build_ai_channels_modal,
    build_system_prompt_modal,
    build_memory_modal,
    build_permissions_modal,
    build_user_persona_modal,
    build_reasoning_modal,
    build_tool_permissions_modal
)
from ui.data_views import DatabaseDashboardView, DataDeletionView
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.SlashHandler")

SETTINGS_SCOPE_MAP = {
    "Help": ["User", "Channel", "Server", "Bot DM", "User App"],
    "Server Identity": ["Server"],
    "System Prompt": ["Channel", "Server", "User", "Bot DM", "User App"],
    "AI Channels": ["Channel", "Server"],
    "Memory": ["User", "Server", "Channel", "Bot DM", "User App"],
    "Permissions": ["Server"],
    "User Persona": ["User", "Bot DM", "User App"],
    "Reasoning": ["Server", "Channel", "User", "Bot DM", "User App"],
    "Tool Permissions": ["Server", "Channel"],
    "Reset": ["User", "Channel", "Server", "Bot DM", "User App"]
}

def get_tool_subtext(tool_name: str, args: dict[str, Any]) -> str | None:
    if tool_name == "execute_code":
        lang = args.get("language", "Python").capitalize()
        pkgs = args.get("packages", "")
        pkg_str = f" ({pkgs})" if pkgs else ""
        return f"-# 💻 Running {lang} sandbox{pkg_str}..."
    elif tool_name == "search_web":
        q = args.get("query", "")[:35]
        return f'-# 🔍 Searching: "{q}"...'
    elif tool_name == "read_link":
        url = args.get("url", "")
        domain = url.split("//")[-1].split("/")[0] if "//" in url else url[:30]
        return f"-# 📄 Reading article from `{domain}`..."
    elif tool_name == "generate_image":
        return "-# 🎨 Rendering artwork..."
    elif tool_name == "ask_expert":
        return "-# 🧠 Consulting deep reasoning expert..."
    return None

def format_placeholder_content(witty_text: str, subtext: str | None = None) -> str:
    content = f"{LOADING_EMOJI} *{witty_text}...*"
    if subtext:
        content += f"\n{subtext}"
    return content

async def update_ephemeral_retry_placeholder(
    interaction: discord.Interaction,
    placeholder_view: PlaceholderLayoutView,
    statuses: list[str],
    get_active_subtext_func,
    start_time: float,
    stop_event: asyncio.Event,
    interval: float = 1.0
):
    idx = 0
    last_status_change = time.time()
    current_status = statuses[0] if statuses else "Thinking"

    while not stop_event.is_set():
        try:
            await asyncio.sleep(interval)
            if stop_event.is_set():
                break

            now = time.time()
            if (now - last_status_change) >= 4.0 and statuses:
                idx = (idx + 1) % len(statuses)
                current_status = statuses[idx]
                last_status_change = now

            elapsed = int(now - start_time)
            subtext = get_active_subtext_func()
            full_text = format_placeholder_content(current_status, subtext)

            placeholder_view.update_state(full_text, elapsed)
            await interaction.edit_original_response(view=placeholder_view)
            await placeholder_view.push_live_update()
        except discord.HTTPException:
            break
        except Exception:
            break

def is_user_app_available(interaction: discord.Interaction) -> bool:
    owners = getattr(interaction, "authorizing_integration_owners", {})
    if hasattr(discord, "IntegrationType") and hasattr(discord.IntegrationType, "user_install"):
        return discord.IntegrationType.user_install in owners
    if hasattr(discord, "enums") and hasattr(discord.enums, "ApplicationIntegrationType"):
        return discord.enums.ApplicationIntegrationType.user_install in owners
    return False

async def setting_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    is_in_guild = interaction.guild is not None
    member = interaction.guild.get_member(interaction.user.id) if is_in_guild else None
    is_admin = (member.guild_permissions.administrator or (interaction.guild.owner_id == interaction.user.id)) if member else False

    available_settings = []
    for setting in SETTINGS_SCOPE_MAP.keys():
        if setting in ["Server Identity", "Permissions", "Tool Permissions", "AI Channels"] and not is_in_guild:
            continue
        if setting in ["Server Identity", "Permissions"] and not is_admin:
            continue
        if current.lower() in setting.lower():
            available_settings.append(app_commands.Choice(name=setting, value=setting))
            
    return available_settings[:25]

async def scope_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    chosen_setting = interaction.namespace.setting or ""
    valid_scopes = SETTINGS_SCOPE_MAP.get(chosen_setting, ["User", "Channel", "Server", "Bot DM", "User App"])
    has_user_app = is_user_app_available(interaction)
    is_in_guild = interaction.guild is not None

    filtered_scopes = []
    for s in valid_scopes:
        if s == "User App" and not has_user_app:
            continue
        if s in ["Server", "Channel"] and not is_in_guild:
            continue
        if current.lower() in s.lower():
            filtered_scopes.append(app_commands.Choice(name=s, value=s))

    return filtered_scopes[:25]



def setup_slash_commands(tree: app_commands.CommandTree):

    @tree.command(name="ask", description="Ask PriestyAI a quick question anywhere on Discord")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(query="The prompt or question to ask", visibility="Public or Ephemeral response")
    @app_commands.choices(visibility=[
        app_commands.Choice(name="Public", value="public"),
        app_commands.Choice(name="Ephemeral", value="private")
    ])
    async def ask_command(interaction: discord.Interaction, query: str, visibility: str = "public"):
        is_ephemeral = (visibility == "private")
        await interaction.response.defer(ephemeral=is_ephemeral)

        thinking_start_time = time.time()
        active_witty = "Thinking"
        await interaction.edit_original_response(content=f"{LOADING_EMOJI} *{active_witty}...*")

        stream_dispatcher = DiscordStreamDispatcher(interaction=interaction, is_ephemeral=is_ephemeral, guild=interaction.guild)
        tool_context = ToolExecutionContext(channel=interaction.channel, guild=interaction.guild, author=interaction.user, bot=interaction.client)

        accumulated_thoughts = []
        tool_call_history = []
        first_content = False
        full_content_accumulator = ""

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

                elif event_type == "TOOL_END":
                    tool_call_history.append(payload)

                elif event_type == "CONTENT":
                    full_content_accumulator += payload
                    if not first_content:
                        first_content = True
                        stream_dispatcher.buffer = ""
                    await stream_dispatcher.append_text(payload)

            final_dur = max(1, int(time.time() - thinking_start_time))
            has_reasoning = bool(accumulated_thoughts or tool_call_history)

            final_file = None
            stored_attachments = []
            if tool_context.staged_image_bytes:
                b64 = base64.b64encode(tool_context.staged_image_bytes).decode("utf-8")
                stored_attachments.append({"filename": tool_context.staged_image_filename, "data_b64": b64})
                final_file = discord.File(io.BytesIO(tool_context.staged_image_bytes), filename=tool_context.staged_image_filename)

            await stream_dispatcher.finalize(view=None, file=final_file)

            sent_msg = stream_dispatcher.primary_message or (stream_dispatcher.sent_messages[0] if stream_dispatcher.sent_messages else None)
            if sent_msg:
                parsed_content = apply_message_parsers(full_content_accumulator, interaction.guild)
                initial_v_data = {
                    "version_idx": 1,
                    "content": parsed_content,
                    "duration_seconds": final_dur,
                    "has_thoughts": has_reasoning,
                    "thoughts": "".join(accumulated_thoughts),
                    "tool_calls": tool_call_history,
                    "attachments": stored_attachments,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                branch_manager.save_generation(
                    message_id=sent_msg.id,
                    channel_id=interaction.channel.id,
                    guild_id=interaction.guild.id if interaction.guild else None,
                    author_id=interaction.user.id,
                    prompt_text=query,
                    attachments=[],
                    context_xml="<context></context>",
                    initial_version_data=initial_v_data
                )

                if has_reasoning and not is_ephemeral:
                    permanent_view = build_version_switcher_view(
                        message_id=sent_msg.id,
                        active_idx=1,
                        total_versions=1,
                        thought_duration=final_dur,
                        has_thoughts=has_reasoning
                    )
                    if permanent_view is not None:
                        try:
                            await sent_msg.edit(view=permanent_view)
                        except Exception:
                            pass

        except Exception as e:
            logger.exception(f"Error in /ask command: {e}")
            try:
                await interaction.edit_original_response(content=f"⚠️ Error: `{e}`")
            except discord.HTTPException:
                pass

    @tree.command(name="chat", description="Start an interactive conversation session with PriestyAI")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def chat_command(interaction: discord.Interaction):
        session_id = str(uuid.uuid4())[:8]
        fields = [
            {
                "type": "text_display",
                "content": "Start a dedicated conversation session with PriestyAI."
            },
            {
                "type": "text_input",
                "custom_id": "chat_input",
                "label": "Your Message",
                "placeholder": "What would you like to discuss?",
                "style": "paragraph",
                "required": True
            }
        ]
        
        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            user_text = data.get("chat_input", "").strip()
            if not user_text:
                await sub_inter.response.send_message(content="Please provide a message to begin.", ephemeral=True)
                return
            await sub_inter.response.defer(ephemeral=False)
            dispatcher = DiscordStreamDispatcher(interaction=sub_inter, is_ephemeral=False, guild=sub_inter.guild)
            tool_ctx = ToolExecutionContext(channel=sub_inter.channel, guild=sub_inter.guild, author=sub_inter.user, bot=sub_inter.client)

            async for event_type, payload in ChatEngine.stream_chat(
                prompt=user_text,
                context_xml="<context></context>",
                bot_user_id=sub_inter.client.user.id,
                tool_context=tool_ctx
            ):
                if event_type == "CONTENT":
                    await dispatcher.append_text(payload)
            await dispatcher.finalize()

        modal = DynamicModalV2(
            title="Start a Chat Session",
            custom_id=f"chat_modal_{session_id}",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    @tree.command(name="data", description="Inspect, search, edit, or delete data stored by PriestyAI")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(action="Browse database records or open deletion menu directly")
    @app_commands.choices(action=[
        app_commands.Choice(name="Browse", value="browse"),
        app_commands.Choice(name="Delete", value="delete")
    ])
    async def data_command(interaction: discord.Interaction, action: str = "browse"):
        if action == "delete":
            del_view = DataDeletionView(user=interaction.user, guild=interaction.guild, channel=interaction.channel)
            await interaction.response.send_message(view=del_view, ephemeral=True)
        else:
            db_view = DatabaseDashboardView(user=interaction.user, guild=interaction.guild, channel=interaction.channel)
            await interaction.response.send_message(view=db_view, ephemeral=True)

    @tree.command(name="config", description="Configure PriestyAI rules, prompts, personas, and permissions")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(setting="The setting category to configure", scope="The target level where this configuration applies")
    @app_commands.autocomplete(setting=setting_autocomplete, scope=scope_autocomplete)
    async def config_command(interaction: discord.Interaction, setting: str, scope: str):
        scope_clean = scope.lower().replace(" ", "_")
        setting_clean = setting.lower().replace(" ", "_")

        if setting_clean == "help":
            help_view = ConfigHelpView(target_scope=scope)
            await interaction.response.send_message(view=help_view, ephemeral=True)
            return

        allowed, reason = config_manager.check_permission(interaction, setting, scope)
        if not allowed:
            await interaction.response.send_message(content=f"❌ **Access Denied:** {reason}", ephemeral=True)
            return

        if setting_clean == "server_identity":
            dashboard_view = ServerIdentityDashboardView(interaction.guild)
            await interaction.response.send_message(view=dashboard_view, ephemeral=True)
            return

        if setting_clean == "reset":
            entity_id = interaction.guild_id if scope_clean == "server" else (interaction.channel_id if scope_clean == "channel" else interaction.user.id)
            success = config_manager.reset_config(scope_clean, entity_id)
            msg = f"🧹 **Reset Complete:** Custom configurations for `{scope.capitalize()} Scope` have been restored to default." if success else f"ℹ️ No custom configurations were found for `{scope.capitalize()} Scope`."
            await interaction.response.send_message(content=msg, ephemeral=True)
            return

        if setting_clean == "ai_channels":
            if not interaction.guild:
                await interaction.response.send_message(content="❌ **AI Channels** configuration is only available inside a Discord server.", ephemeral=True)
                return

            s_cfg = config_manager.get_server_config(interaction.guild.id)
            current_channels = s_cfg.get("ai_channels", [])

            async def handle_ai_channels_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                selected_channels = data.get("ai_channels", [])
                if isinstance(selected_channels, str):
                    selected_channels = [selected_channels] if selected_channels else []
                elif not isinstance(selected_channels, list):
                    selected_channels = []

                clean_selected = [str(cid).strip() for cid in selected_channels if str(cid).strip()]
                config_manager.set_server_config(interaction.guild.id, ai_channels=clean_selected)
                
                if clean_selected:
                    ch_mentions = ", ".join([f"<#{cid}>" for cid in clean_selected])
                    msg = f"✅ **AI Channels Updated:** PriestyAI will automatically chat in {ch_mentions}."
                else:
                    msg = "✅ **AI Channels Cleared:** Automatic responses in AI channels have been disabled."
                await sub_inter.response.send_message(content=msg, ephemeral=True)

            modal = build_ai_channels_modal(scope_clean, current_channels, handle_ai_channels_submit)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "system_prompt":
            current_prompt = ""
            override_user = False
            if scope_clean == "server":
                s = config_manager.get_server_config(interaction.guild_id)
                current_prompt = s.get("system_prompt", "")
                override_user = bool(s.get("override_user_instructions"))
            elif scope_clean == "channel":
                c = config_manager.get_channel_config(interaction.channel_id)
                current_prompt = c.get("system_prompt", "")
                override_user = bool(c.get("override_user_instructions"))

            async def handle_prompt_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_prompt = data.get("system_prompt", "").strip()
                new_override = bool(data.get("override_user"))
                target_c = data.get("target_channel") or interaction.channel_id
                if scope_clean == "server":
                    config_manager.set_server_config(interaction.guild_id, system_prompt=new_prompt, override_user_instructions=new_override)
                elif scope_clean == "channel":
                    config_manager.set_channel_config(target_c, guild_id=interaction.guild_id, system_prompt=new_prompt, override_user_instructions=new_override)
                await sub_inter.response.send_message(content=f"✅ **System Prompt Updated** for `{scope.capitalize()} Scope`.", ephemeral=True)

            modal = build_system_prompt_modal(scope_clean, current_prompt, override_user, interaction.channel_id, handle_prompt_submit)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "memory":
            u_cfg = config_manager.get_user_config(interaction.user.id)
            s_cfg = config_manager.get_server_config(interaction.guild_id) if interaction.guild else {}
            async def handle_memory_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                if "user_memory_policy" in data:
                    config_manager.set_user_config(interaction.user.id, user_memory_policy=data["user_memory_policy"])
                if "server_lore_policy" in data and interaction.guild:
                    config_manager.set_server_config(interaction.guild.id, server_lore_policy=data["server_lore_policy"])
                await sub_inter.response.send_message(content=f"✅ **Memory Policies Updated** for `{scope.capitalize()} Scope`.", ephemeral=True)

            modal = build_memory_modal(scope_clean, u_cfg.get("user_memory_policy", "read_write"), s_cfg.get("server_lore_policy", "read_write"), handle_memory_submit)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "permissions":
            s_cfg = config_manager.get_server_config(interaction.guild.id)
            async def handle_perm_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                re_entities = data.get("restricted_entities", [])
                if isinstance(re_entities, str):
                    re_entities = [re_entities] if re_entities else []
                elif not isinstance(re_entities, list):
                    re_entities = []

                pb_entities = data.get("permission_bypass", [])
                if isinstance(pb_entities, str):
                    pb_entities = [pb_entities] if pb_entities else []
                elif not isinstance(pb_entities, list):
                    pb_entities = []

                config_manager.set_server_config(
                    interaction.guild.id,
                    access_behavior=data.get("access_behavior", "blacklist"),
                    restricted_entities=[str(x) for x in re_entities],
                    permission_bypass=[str(x) for x in pb_entities],
                    config_manager_role=data.get("config_manager_role", "administrators")
                )
                await sub_inter.response.send_message(content="✅ **Server Permissions & Roles Updated** successfully.", ephemeral=True)

            modal = build_permissions_modal(scope_clean, s_cfg, handle_perm_submit, guild=interaction.guild)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "user_persona":
            if scope_clean in ["server", "channel"]:
                await interaction.response.send_message(content="❌ **User Persona** is a personal setting and is only available in User, Bot DM, or User App scopes.", ephemeral=True)
                return

            u_cfg = config_manager.get_user_config(interaction.user.id)
            async def handle_persona_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                config_manager.set_user_config(
                    interaction.user.id,
                    preferred_name=data.get("preferred_name", "").strip(),
                    special_instructions=data.get("special_instructions", "").strip()
                )
                await sub_inter.response.send_message(content="✅ **User Persona Saved.** PriestyAI will remember your personal preferences.", ephemeral=True)

            modal = build_user_persona_modal(u_cfg, handle_persona_submit)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "reasoning":
            cfg = config_manager.get_user_config(interaction.user.id)
            current_r = cfg.get("preferred_reasoning_level", "AUTO")
            async def handle_reasoning_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                chosen_r = data.get("reasoning_level", "AUTO")
                if scope_clean == "server" and interaction.guild:
                    config_manager.set_server_config(interaction.guild.id, preferred_reasoning_level=chosen_r)
                elif scope_clean == "channel":
                    config_manager.set_channel_config(interaction.channel_id, preferred_reasoning_level=chosen_r)
                else:
                    config_manager.set_user_config(interaction.user.id, preferred_reasoning_level=chosen_r)
                await sub_inter.response.send_message(content=f"✅ **Reasoning Depth Set to `{chosen_r}`** for `{scope.capitalize()} Scope`.", ephemeral=True)

            modal = build_reasoning_modal(scope_clean, current_r, handle_reasoning_submit)
            await interaction.response.send_modal(modal)
            return

        if setting_clean == "tool_permissions":
            s_cfg = config_manager.get_server_config(interaction.guild.id)
            disabled = s_cfg.get("disabled_tools", [])
            async def handle_tool_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                allowed_bundles = data.get("allowed_tools", [])
                all_tools = {
                    "execute_code", "search_web", "read_link", "generate_image",
                    "react", "add_component", "add_modal", "remember", "forget",
                    "read_message_history", "search_channel_history", "ask_expert",
                    "get_user_profile", "get_server_info", "create_thread"
                }
                allowed_individual = set()
                for bundle in allowed_bundles:
                    for t in bundle.split(","):
                        allowed_individual.add(t.strip())

                disabled_tools_list = list(all_tools - allowed_individual)
                if scope_clean == "channel":
                    config_manager.set_channel_config(interaction.channel_id, disabled_tools=disabled_tools_list)
                else:
                    config_manager.set_server_config(interaction.guild.id, disabled_tools=disabled_tools_list)
                await sub_inter.response.send_message(content=f"✅ **Tool Permissions Updated** for `{scope.capitalize()} Scope`.", ephemeral=True)

            modal = build_tool_permissions_modal(scope_clean, disabled, handle_tool_submit)
            await interaction.response.send_modal(modal)
            return

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
                history_msgs.append({
                    "id": str(m.id),
                    "role": "assistant" if m.author.id == interaction.client.user.id else "user",
                    "author": m.author.display_name,
                    "author_id": str(m.author.id),
                    "content": m.clean_content,
                    "timestamp": m.created_at.isoformat()
                })
        except Exception as ex:
            logger.warning(f"Branch history fetch exception: {ex}")

        title = "Exploration Branch"
        try:
            title_prompt = f"Generate a clean 3 to 5 word topic title for this discussion. Output ONLY the title:\n{message.clean_content[:300]}"
            client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
            if client:
                res = await client.aio.models.generate_content(model=active_model, contents=title_prompt)
                if res.text:
                    title = res.text.strip().replace('"', '').replace("'", "")[:60]
        except Exception:
            pass

        thread = await interaction.channel.create_thread(
            name=title,
            type=discord.ChannelType.public_thread
        )

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

        header_card_text = (
            f"# Branch: {title}\n"
            f"Created from message: {message.jump_url}\n\n"
            f"**Notes:**\n"
            f"- Message context is preserved through deletion.\n"
            f"- PriestyAI will automatically respond in this thread without @mentions."
        )
        header_view = BranchHeaderView(branch_id=branch_id)
        await thread.send(content=header_card_text, view=header_view)

        await interaction.followup.send(content=f"🧵 **Branch Created:** Joined thread <#{thread.id}>.", ephemeral=True)

    @tree.context_menu(name="Retry")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def retry_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="❌ You can only retry PriestyAI's responses.", ephemeral=True)
            return

        gen_record = branch_manager.get_generation(message.id)

        if not gen_record:
            if message.reference and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    clean_p = re.sub(rf'<@!?{interaction.client.user.id}>', '', ref_msg.content).strip()
                    if not clean_p:
                        clean_p = "Analyze attached content" if ref_msg.attachments else "Hello!"

                    initial_v_data = {
                        "version_idx": 1,
                        "content": apply_message_parsers(message.content, message.guild),
                        "duration_seconds": 0,
                        "has_thoughts": False,
                        "thoughts": "",
                        "tool_calls": [],
                        "attachments": [],
                        "created_at": message.created_at.isoformat()
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
                except Exception as ex:
                    logger.warning(f"Legacy retry reconstruction exception: {ex}")

        if not gen_record:
            await interaction.response.send_message(content="❌ No prompt history or reference found for this message.", ephemeral=True)
            return

        total_v = len(gen_record.get("versions", [])) + 1
        active_witty_statuses = [f"Generating version {total_v}", "Consulting neural cores", "Formulating response"]
        active_tool_subtext: str | None = None
        answer_now_event = asyncio.Event()
        stop_loop = asyncio.Event()
        start_t = time.time()

        async def on_retry_answer_now(inter: discord.Interaction):
            answer_now_event.set()
            stop_loop.set()

        initial_text = format_placeholder_content(active_witty_statuses[0], None)
        placeholder_view = PlaceholderLayoutView(
            loading_text=initial_text,
            duration_seconds=0,
            is_enabled=False,
            on_answer_now_callback=on_retry_answer_now,
            thought_data={"thoughts": "", "tool_calls": []}
        )

        await interaction.response.send_message(view=placeholder_view, ephemeral=True)

        def get_active_subtext():
            return active_tool_subtext

        placeholder_task = asyncio.create_task(
            update_ephemeral_retry_placeholder(
                interaction=interaction,
                placeholder_view=placeholder_view,
                statuses=active_witty_statuses,
                get_active_subtext_func=get_active_subtext,
                start_time=start_t,
                stop_event=stop_loop
            )
        )

        tool_context = ToolExecutionContext(
            channel=message.channel,
            guild=message.guild,
            author=interaction.user,
            bot=interaction.client
        )

        prompt = gen_record.get("prompt_text", "")
        context_xml = gen_record.get("context_xml", "<context></context>")

        accumulated_thoughts = []
        tool_call_history = []
        active_tool_start_times = {}
        new_content_buffer = ""

        try:
            async for event_type, payload in ChatEngine.stream_chat(
                prompt=prompt,
                context_xml=context_xml,
                bot_user_id=interaction.client.user.id,
                tool_context=tool_context,
                answer_now_event=answer_now_event
            ):
                if event_type == "ROUTED" and payload.witty_statuses:
                    active_witty_statuses = payload.witty_statuses

                elif event_type == "RECALLED_MEMORIES":
                    placeholder_view.enable_thinking()
                    count = payload.get("count", 0)
                    tool_call_history.insert(0, {
                        "name": "recall_memories",
                        "args": {"count": count},
                        "result": payload,
                        "duration_ms": 0,
                        "order": -1.0
                    })
                    placeholder_view.thought_data["tool_calls"] = tool_call_history
                    await placeholder_view.push_live_update()

                elif event_type == "THOUGHT":
                    placeholder_view.enable_thinking()
                    accumulated_thoughts.append(payload)
                    placeholder_view.thought_data["thoughts"] = "".join(accumulated_thoughts)
                    await placeholder_view.push_live_update()

                elif event_type == "TOOL_START":
                    tool_name = payload.get("name", "Tool")
                    args = payload.get("args", {})
                    active_tool_start_times[tool_name] = time.perf_counter()
                    active_tool_subtext = get_tool_subtext(tool_name, args)
                    placeholder_view.enable_thinking()

                elif event_type == "TOOL_END":
                    tool_name = payload.get("name", "Tool")
                    st = active_tool_start_times.pop(tool_name, time.perf_counter())
                    dur_ms = int((time.perf_counter() - st) * 1000)
                    tool_call_history.append({"name": tool_name, "args": payload.get("args", {}), "result": payload.get("result", {}), "duration_ms": dur_ms})
                    active_tool_subtext = None
                    placeholder_view.enable_thinking()
                    placeholder_view.thought_data["tool_calls"] = tool_call_history
                    await placeholder_view.push_live_update()

                elif event_type == "CONTENT":
                    new_content_buffer += payload

            stop_loop.set()
            if placeholder_task:
                placeholder_task.cancel()

            dur_sec = max(1, int(time.time() - start_t))
            has_thoughts = bool(accumulated_thoughts or tool_call_history)
            parsed_final_content = apply_message_parsers(new_content_buffer, interaction.guild)

            stored_attachments = []
            final_file = None
            if tool_context.staged_image_bytes:
                b64 = base64.b64encode(tool_context.staged_image_bytes).decode("utf-8")
                stored_attachments.append({
                    "filename": tool_context.staged_image_filename,
                    "data_b64": b64
                })
                final_file = discord.File(io.BytesIO(tool_context.staged_image_bytes), filename=tool_context.staged_image_filename)

            new_v_data = {
                "version_idx": total_v,
                "content": parsed_final_content,
                "duration_seconds": dur_sec,
                "has_thoughts": has_thoughts,
                "thoughts": "".join(accumulated_thoughts),
                "tool_calls": tool_call_history,
                "attachments": stored_attachments,
                "created_at": datetime.now(timezone.utc).isoformat()
            }

            new_active_idx = branch_manager.add_retry_version(message.id, new_v_data)

            switcher_view = build_version_switcher_view(
                message_id=message.id,
                active_idx=new_active_idx,
                total_versions=total_v,
                thought_duration=dur_sec,
                has_thoughts=has_thoughts
            )

            if final_file:
                await message.edit(content=parsed_final_content, view=switcher_view, attachments=[final_file])
            else:
                await message.edit(content=parsed_final_content, view=switcher_view)

            try:
                await interaction.delete_original_response()
            except Exception:
                pass

        except Exception as e:
            stop_loop.set()
            if placeholder_task:
                placeholder_task.cancel()
            logger.exception(f"Retry error: {e}")
            try:
                await interaction.delete_original_response()
            except Exception:
                pass
            await interaction.followup.send(content=f"⚠️ Retry generation failed: `{e}`", ephemeral=True)

    @tree.context_menu(name="View Prompt")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def view_prompt_context_menu(interaction: discord.Interaction, message: discord.Message):
        if message.author.id != interaction.client.user.id:
            await interaction.response.send_message(content="❌ You can only inspect prompts on PriestyAI's messages.", ephemeral=True)
            return

        gen_record = branch_manager.get_generation(message.id)

        if not gen_record and message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                clean_p = re.sub(rf'<@!?{interaction.client.user.id}>', '', ref_msg.content).strip()
                if not clean_p:
                    clean_p = "Analyze attached content" if ref_msg.attachments else "Hello!"

                initial_v_data = {
                    "version_idx": 1,
                    "content": apply_message_parsers(message.content, message.guild),
                    "duration_seconds": 0,
                    "has_thoughts": False,
                    "thoughts": "",
                    "tool_calls": [],
                    "attachments": [],
                    "created_at": message.created_at.isoformat()
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
            except Exception as ex:
                logger.warning(f"Legacy view prompt reconstruction exception: {ex}")

        if not gen_record:
            await interaction.response.send_message(content="❌ No prompt record or reference found for this response.", ephemeral=True)
            return

        active_idx = gen_record.get("active_version", 1)
        versions = gen_record.get("versions", [])
        v_data = versions[active_idx - 1] if 1 <= active_idx <= len(versions) else {}

        prompt_txt = gen_record.get("prompt_text", "*Empty prompt*")
        author_id = gen_record.get("author_id", "0")
        dur = v_data.get("duration_seconds", 0)

        created_raw = v_data.get("created_at") or gen_record.get("created_at")
        created_ts = int(time.time())
        if created_raw:
            try:
                if isinstance(created_raw, (int, float)):
                    created_ts = int(created_raw)
                else:
                    dt = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
                    created_ts = int(dt.timestamp())
            except Exception:
                created_ts = int(message.created_at.timestamp())
        else:
            created_ts = int(message.created_at.timestamp())

        card_text = (
            f"### Prompt Inspector (Version {active_idx}/{len(versions)})\n"
            f"**Invoking User:** <@{author_id}>\n"
            f"**Input Prompt:**\n> {prompt_txt}\n\n"
            f"-# Duration: `{dur}s` • Created: <t:{created_ts}:R>"
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

        fields = [
            {
                "type": "text_display",
                "content": "Edit this response directly. Modifications will update the message in-place and save to persistent history."
            },
            {
                "type": "text_input",
                "custom_id": "edited_content",
                "label": "Response Content",
                "description": "The updated markdown message text",
                "style": "paragraph",
                "value": message.content,
                "required": True,
                "max_length": 4000
            },
            {
                "type": "file_upload",
                "custom_id": "edited_files",
                "label": "Upload Attachments",
                "description": "Upload up to 10 replacement or additional files",
                "required": False,
                "max_values": 10
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_text = data.get("edited_content", "").strip()
            parsed_text = apply_message_parsers(new_text, interaction.guild)
            uploaded_files = data.get("edited_files", [])

            discord_files = []
            stored_attachments = []

            if uploaded_files and isinstance(uploaded_files, list):
                async with aiohttp.ClientSession() as session:
                    for f in uploaded_files:
                        if isinstance(f, dict) and "url" in f:
                            try:
                                async with session.get(f["url"]) as resp:
                                    if resp.status == 200:
                                        raw_bytes = await resp.read()
                                        fname = f.get("filename", "attachment.png")
                                        b64 = base64.b64encode(raw_bytes).decode("utf-8")
                                        stored_attachments.append({"filename": fname, "data_b64": b64})
                                        discord_files.append(discord.File(io.BytesIO(raw_bytes), filename=fname))
                            except Exception as ex:
                                logger.warning(f"Failed to process edit attachment: {ex}")

            branch_manager.update_active_version_content(
                message.id,
                parsed_text,
                new_attachments=stored_attachments if stored_attachments else None
            )

            if discord_files:
                await message.edit(content=parsed_text, attachments=discord_files)
            else:
                await message.edit(content=parsed_text)

            await sub_inter.response.send_message(content="✅ **Response edited in-place successfully.**", ephemeral=True)

        modal = DynamicModalV2(
            title="Edit PriestyAI Response",
            custom_id="modal_edit_response",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    logger.info("Registered Slash Commands & Context Menus: /ask, /chat, /data, /config, Branch, Retry, View Prompt, Edit.")