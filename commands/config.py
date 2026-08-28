import discord
from discord import app_commands
from typing import Any
from core.config_manager import config_manager
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

def is_user_app_available(interaction: discord.Interaction) -> bool:
    owners = getattr(interaction, "authorizing_integration_owners", {})
    if hasattr(discord, "IntegrationType") and hasattr(discord.IntegrationType, "user_install"):
        return discord.IntegrationType.user_install in owners
    if hasattr(discord, "enums") and hasattr(discord.enums, "ApplicationIntegrationType"):
        return discord.enums.ApplicationIntegrationType.user_install in owners
    return False

async def setting_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    is_in_guild = interaction.guild is not None
    member = interaction.user if isinstance(interaction.user, discord.Member) else (interaction.guild.get_member(interaction.user.id) if is_in_guild else None)
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

def setup_config_commands(tree: app_commands.CommandTree):

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
            s_cfg = config_manager.get_server_config(interaction.guild.id) if interaction.guild else {}
            c_cfg = config_manager.get_channel_config(interaction.channel_id) if scope_clean == "channel" else {}
            disabled = c_cfg.get("disabled_tools", []) if scope_clean == "channel" else s_cfg.get("disabled_tools", [])

            async def handle_tool_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                allowed_bundles = data.get("allowed_tools", [])
                all_tools = {
                    "github_repo", "fetch_github", "create_artifact", "execute_code",
                    "search_web", "read_link", "generate_image", "search_image",
                    "search_gif", "edit_image", "calc", "create_poll", "react",
                    "add_component", "add_modal", "remember", "forget", "search_memories",
                    "read_message_history", "search_channel_history",
                    "ask_expert", "get_user_profile", "get_server_info", "create_thread"
                }
                allowed_individual = set()
                for bundle in allowed_bundles:
                    for t in bundle.split(","):
                        allowed_individual.add(t.strip())

                disabled_tools_list = list(all_tools - allowed_individual)

                raw_targets = data.get("target_entities", [])
                if isinstance(raw_targets, str):
                    raw_targets = [raw_targets] if raw_targets else []
                elif not isinstance(raw_targets, list):
                    raw_targets = []

                target_ids = [str(x).strip() for x in raw_targets if str(x).strip()]

                if scope_clean == "channel":
                    if target_ids:
                        config_manager.set_channel_entity_disabled_tools(
                            channel_id=interaction.channel_id,
                            entity_ids=target_ids,
                            disabled_tools=disabled_tools_list,
                            guild_id=interaction.guild_id
                        )
                        target_mentions = ", ".join([f"<@{tid}>" for tid in target_ids])
                        msg = f"✅ **Channel Tool Restrictions Applied:** Disabled {len(disabled_tools_list)} tool(s) specifically for {target_mentions}."
                    else:
                        config_manager.set_channel_config(interaction.channel_id, disabled_tools=disabled_tools_list)
                        msg = f"✅ **Channel Tool Permissions Updated:** Default permissions for `@everyone` updated."
                else:
                    if target_ids:
                        config_manager.set_server_entity_disabled_tools(
                            guild_id=interaction.guild.id,
                            entity_ids=target_ids,
                            disabled_tools=disabled_tools_list
                        )
                        target_mentions = ", ".join([f"<@{tid}>" for tid in target_ids])
                        msg = f"✅ **Server Tool Restrictions Applied:** Disabled {len(disabled_tools_list)} tool(s) specifically for {target_mentions}."
                    else:
                        config_manager.set_server_config(interaction.guild.id, disabled_tools=disabled_tools_list)
                        msg = f"✅ **Server Tool Permissions Updated:** Default permissions for `@everyone` updated."

                await sub_inter.response.send_message(content=msg, ephemeral=True)

            modal = build_tool_permissions_modal(
                scope=scope_clean,
                disabled_tools=disabled,
                on_submit=handle_tool_submit,
                guild=interaction.guild
            )
            await interaction.response.send_modal(modal)
            return