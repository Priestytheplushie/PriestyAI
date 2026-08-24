import io
import json
import logging
from typing import Any
import aiohttp
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    Section,
    TextDisplay,
    MediaGallery,
    Thumbnail,
    ActionRow,
    Button,
    Select
)
from core.config_manager import config_manager
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.ConfigViews")

DEFAULT_SERVER_BIO = (
    "PriestyAI — Intelligent server companion & reasoning assistant.\n\n"
    "• Real-time web search, Docker code execution & image generation\n"
    "• Autonomous long-term memory & per-channel directives\n\n"
    "✨ Chat: @mention me or use </ask:1540889817980731543>\n"
    "⚙️ Manage: </config:1541093516078485646> • </data:1541122763044163665>\n"
    "💡 *Tip: Right-click any response to **Branch** or **Retry**.*"
)

SCOPE_DISPLAY_NAMES = {
    "server": "Server",
    "channel": "Channel",
    "user": "User",
    "bot_dm": "Bot DM",
    "user_app": "User App"
}

def format_scope_title(scope: str) -> str:
    return SCOPE_DISPLAY_NAMES.get(scope.lower().strip(), scope.capitalize())


def format_mentionable_defaults(entities: list[Any], guild: discord.Guild | None) -> list[dict[str, str]]:
    defaults = []
    for ent in entities:
        ent_id_str = str(ent).strip()
        if not ent_id_str:
            continue
        ent_type = "user"
        if guild:
            try:
                num_id = int(ent_id_str)
                if guild.get_role(num_id):
                    ent_type = "role"
                elif guild.get_member(num_id):
                    ent_type = "user"
            except Exception:
                pass
        defaults.append({"id": ent_id_str, "type": ent_type})
    return defaults


class ServerIdentityDashboardView(LayoutView):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=600)
        self.guild = guild
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        member = self.guild.me
        avatar_url = member.display_avatar.url
        nick = member.nick or f"{member.name} (Default)"

        s_cfg = config_manager.get_server_config(self.guild.id)
        bio = s_cfg.get("server_bio", "").strip() or f"{DEFAULT_SERVER_BIO} (Default)"

        guild_icon_url = self.guild.icon.url if self.guild.icon else member.display_avatar.url
        header_section = Section(
            TextDisplay(f"# Server Identity: {self.guild.name}\nCustomize how PriestyAI appears to members in this server."),
            accessory=Thumbnail(guild_icon_url)
        )
        container.add_item(header_section)

        container.add_item(MediaGallery(discord.MediaGalleryItem(url=avatar_url)))

        info_text = (
            f"**Display Nickname:** `{nick}`\n"
            f"**Server Bio:**\n{bio}"
        )
        container.add_item(TextDisplay(info_text))

        edit_bio_btn = Button(label="Edit Name & Bio", style=discord.ButtonStyle.primary, custom_id="btn_cfg_edit_identity")
        edit_bio_btn.callback = self._on_edit_bio_clicked

        avatar_btn = Button(label="Upload Avatar", style=discord.ButtonStyle.secondary, custom_id="btn_cfg_upload_avatar")
        avatar_btn.callback = self._on_upload_avatar_clicked

        reset_btn = Button(label="Reset to Default ↺", style=discord.ButtonStyle.danger, custom_id="btn_cfg_reset_identity")
        reset_btn.callback = self._on_reset_identity_clicked

        container.add_item(ActionRow(edit_bio_btn, avatar_btn, reset_btn))
        self.add_item(container)

    async def _on_edit_bio_clicked(self, interaction: discord.Interaction):
        s_cfg = config_manager.get_server_config(self.guild.id)
        current_bio = s_cfg.get("server_bio", "")
        current_nick = self.guild.me.nick or ""

        fields = [
            {
                "type": "text_input",
                "custom_id": "nickname",
                "label": "Server Nickname",
                "description": "Leave blank to reset back to the default bot name",
                "placeholder": "PriestyAI",
                "value": current_nick,
                "style": "short",
                "required": False
            },
            {
                "type": "text_input",
                "custom_id": "bio",
                "label": "Server Bio",
                "description": "A description of PriestyAI's role in this server (max 400 chars)",
                "placeholder": DEFAULT_SERVER_BIO,
                "value": current_bio,
                "style": "paragraph",
                "required": False,
                "max_length": 400
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_nick = data.get("nickname", "").strip() or None
            new_bio = data.get("bio", "").strip()

            try:
                await self.guild.me.edit(nick=new_nick)
            except Exception as e:
                logger.warning(f"Failed to change guild nickname: {e}")

            config_manager.set_server_config(self.guild.id, server_bio=new_bio)
            self._build_dashboard()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title="Edit Server Identity",
            custom_id="modal_server_identity",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_upload_avatar_clicked(self, interaction: discord.Interaction):
        fields = [
            {
                "type": "file_upload",
                "custom_id": "avatar_file",
                "label": "Server Avatar Image",
                "description": "Upload a PNG or JPG to set as PriestyAI's server profile picture",
                "required": True,
                "max_values": 1
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            files = data.get("avatar_file", [])
            if files and isinstance(files, list):
                f_obj = files[0]
                url = f_obj.get("url") if isinstance(f_obj, dict) else None
                if url:
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    img_bytes = await resp.read()
                                    await self.guild.me.edit(avatar=img_bytes)
                    except Exception as e:
                        logger.warning(f"Failed to update guild avatar: {e}")

            self._build_dashboard()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title="Upload Server Avatar",
            custom_id="modal_avatar_upload",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_reset_identity_clicked(self, interaction: discord.Interaction):
        try:
            await self.guild.me.edit(nick=None, avatar=None)
        except Exception:
            pass

        config_manager.set_server_config(self.guild.id, server_bio="")
        self._build_dashboard()
        await interaction.response.edit_message(view=self)



SCOPE_EXPLANATIONS = {
    "server": (
        "### Server Scope Context\n"
        "Configurations set at the Server Scope apply guild-wide defaults across all text channels. "
        "They establish who can use the bot, set global lore policies, designate AI channels, and define server-wide rules."
    ),
    "channel": (
        "### Channel Scope Context\n"
        "Configurations set at the Channel Scope govern channel-specific behaviors, such as designating dedicated AI channels, "
        "enforcing channel-specific prompts (e.g. `#dev-lab`), or toggling tool restrictions."
    ),
    "user": (
        "### User Scope Context\n"
        "Configurations set at the User Scope define your personal identity (preferred name, coding habits) "
        "and control personal facts memory banks. These settings follow you across all servers."
    ),
    "bot_dm": (
        "### Bot DM Scope Context\n"
        "Configurations set at the Bot DM Scope govern private direct messages between you and PriestyAI. "
        "Server lore and server permission rules are ignored in private DMs."
    ),
    "user_app": (
        "### User App Scope Context\n"
        "Configurations set at the User App Scope apply whenever you invoke PriestyAI anywhere across Discord "
        "via your personal user application installation."
    )
}

SETTING_HELP_TEXTS = {
    "server_identity": (
        "### Server Identity\n"
        "Customize how PriestyAI presents itself in this specific Discord server.\n\n"
        "• **Server Nickname:** Overrides the bot's display name inside this guild.\n"
        "• **Server Bio:** Sets a custom description of what PriestyAI does in this server (max 400 chars).\n"
        "• **Server Avatar:** Uploads an avatar image specific to this guild without altering the global bot avatar.\n"
        "• **Reset:** Restores the bot's default global name, avatar, and bio."
    ),
    "system_prompt": (
        "### System Prompt\n"
        "Direct the behavior, tone, constraints, and personality of PriestyAI.\n\n"
        "• **System Instructions:** Custom guidelines injected into the reasoning engine.\n"
        "• **Target Channel:** Allows selecting a specific channel when configuring in Channel Scope.\n"
        "• **Override User Persona:** When enabled, forces the AI to prioritize server/channel rules over individual user personas."
    ),
    "ai_channels": (
        "### AI Channels\n"
        "Designate dedicated text channels where PriestyAI acts as a permanent, automatic conversational participant.\n\n"
        "• **Auto-Response:** In designated AI channels, PriestyAI automatically responds to all user messages without requiring an `@mention` or direct reply.\n"
        "• **Multi-Channel Selection:** Select multiple channels across the server to activate AI channel behavior.\n"
        "• **Management:** Open `/config setting:AI Channels scope:Channel` to pick or remove channels with default selections pre-filled."
    ),
    "memory": (
        "### Memory\n"
        "Manage how PriestyAI records and recalls persistent knowledge.\n\n"
        "• **Personal Facts Memory (User Scope):**\n"
        "  - Read and Write: Automatically records personal facts and recalls them in conversation.\n"
        "  - Read-Only: Recalls existing facts without saving new memories.\n"
        "  - Disabled: Ignores personal memories entirely.\n\n"
        "• **Server Lore Memory (Server/Channel Scope):**\n"
        "  - Controls whether the bot saves and recalls guild-wide lore, events, and project facts."
    ),
    "permissions": (
        "### Permissions\n"
        "Control access policies and determine who can configure the bot.\n\n"
        "• **Access Policy:**\n"
        "  - Blacklist: Bans selected roles or members from interacting with PriestyAI.\n"
        "  - Whitelist: Bans everyone except selected roles or members.\n"
        "• **Target Roles & Members:** Multi-select dropdown to choose roles and members subject to the policy.\n"
        "• **Permission Bypass:** Multi-select dropdown to exempt roles/members from bans and grant /config access.\n"
        "• **Settings Manager Permission:** Sets the minimum role level needed to manage general settings."
    ),
    "user_persona": (
        "### User Persona\n"
        "Tailor how PriestyAI addresses you and remembers your personal background across all servers.\n\n"
        "• **Preferred Name:** The name PriestyAI uses to address you, overriding Discord display names.\n"
        "• **Personal Context & Habits:** Your technical background, preferred languages, and response tone preferences.\n"
        "• **Scope:** Strictly available under User, Bot DM, and User App scopes."
    ),
    "reasoning": (
        "### Reasoning\n"
        "Control the thinking depth and token budgets across model cascades.\n\n"
        "• **Auto:** Dynamic model and reasoning routing based on input complexity (Recommended).\n"
        "• **High:** Maximum reasoning depth with multi-step logical verification.\n"
        "• **Medium:** Standard reasoning for research and conceptual explanations.\n"
        "• **Low:** Minimal reasoning budget with faster response latency.\n"
        "• **Minimal:** Instant response streaming with zero thinking token latency."
    ),
    "tool_permissions": (
        "### Tool Permissions\n"
        "Enable or disable specific tool capabilities per server or channel.\n\n"
        "• **Docker Code Execution:** Runs code in an isolated sandbox container.\n"
        "• **Web Search & Article Reader:** Real-time web querying and article reading.\n"
        "• **Image Generation:** AI artwork generation with inline attachments.\n"
        "• **Message Reactions:** Proactive emoji reactions on chat messages.\n"
        "• **Interactive Components:** Staging interactive buttons and Modals v2 forms.\n"
        "• **Long-Term Memory:** Storing and forgetting persistent facts.\n"
        "• **Chat & Channel History:** Reading channel history and context."
    ),
    "reset": (
        "### Reset\n"
        "Restores custom configurations back to defaults for a specific scope.\n\n"
        "• **Server Scope:** Clears server lore, permissions, and server prompts (Admins only).\n"
        "• **Channel Scope:** Clears channel prompt overrides and tool locks.\n"
        "• **User / User App Scope:** Wipes personal preferred name and custom persona."
    )
}

class ConfigHelpView(LayoutView):
    def __init__(self, target_scope: str):
        super().__init__(timeout=600)
        self.target_scope = target_scope.lower().strip()
        self.selected_setting = "system_prompt"
        self._build_help_card()

    def _build_help_card(self):
        self.clear_items()
        container = Container()

        scope_title = format_scope_title(self.target_scope)
        scope_intro = SCOPE_EXPLANATIONS.get(self.target_scope, f"### {scope_title} Scope Context\nGeneral configuration rules.")

        header_text = (
            f"# PriestyAI Configuration Guide\n"
            f"{scope_intro}\n\n"
            f"Select a setting from the menu below to view a detailed breakdown of its fields and options."
        )
        container.add_item(TextDisplay(header_text))

        select_options = [
            discord.SelectOption(label="System Prompt", value="system_prompt", description="Custom instructions, behavior rules, and persona overrides"),
            discord.SelectOption(label="AI Channels", value="ai_channels", description="Designate channels for automatic AI conversation without mentions"),
            discord.SelectOption(label="Server Identity", value="server_identity", description="Server nickname, bio, and custom guild avatar"),
            discord.SelectOption(label="Memory", value="memory", description="Personal facts memory and shared server lore policies"),
            discord.SelectOption(label="Permissions", value="permissions", description="Access control, blacklists, whitelists, and bypass roles"),
            discord.SelectOption(label="User Persona", value="user_persona", description="Preferred name, coding background, and response preferences"),
            discord.SelectOption(label="Reasoning", value="reasoning", description="Thinking depth levels and token budget controls"),
            discord.SelectOption(label="Tool Permissions", value="tool_permissions", description="Toggle sandbox execution, web search, reactions, and lookups"),
            discord.SelectOption(label="Reset", value="reset", description="Restore custom configurations back to defaults per scope")
        ]

        for opt in select_options:
            if opt.value == self.selected_setting:
                opt.default = True

        sel = Select(
            custom_id="select_help_setting",
            placeholder="Select a setting to learn more...",
            options=select_options
        )
        sel.callback = self._on_setting_selected
        container.add_item(ActionRow(sel))

        detail_text = SETTING_HELP_TEXTS.get(self.selected_setting, "Select a setting to view details.")
        container.add_item(TextDisplay(detail_text))

        self.add_item(container)

    async def _on_setting_selected(self, interaction: discord.Interaction):
        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            self.selected_setting = interaction.data["values"][0]
        self._build_help_card()
        await interaction.response.edit_message(view=self)



def build_ai_channels_modal(scope: str, current_channels: list[str | int], on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    default_vals = [{"id": str(cid), "type": "channel"} for cid in current_channels] if current_channels else []

    fields = [
        {
            "type": "text_display",
            "content": (
                "Designate channels where PriestyAI acts as an automatic conversational participant.\n\n"
                "In configured AI channels, PriestyAI automatically responds to all user messages without requiring an `@mention` or reply."
            )
        },
        {
            "type": "channel_select",
            "custom_id": "ai_channels",
            "label": "Designated AI Channels",
            "description": "Select text channels where PriestyAI will automatically chat",
            "placeholder": "Choose AI channels...",
            "required": False,
            "min_values": 0,
            "max_values": 25,
            "default_values": default_vals
        }
    ]

    return DynamicModalV2(
        title=f"AI Channels ({scope_title})",
        custom_id=f"modal_ai_channels_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_system_prompt_modal(scope: str, current_val: str, override_user: bool, target_channel_id: int | None, on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    fields = [
        {
            "type": "text_display",
            "content": f"Define custom instructions for the {scope_title} scope to tailor tone, formatting constraints, or server lore."
        }
    ]

    if scope == "channel":
        fields.append({
            "type": "channel_select",
            "custom_id": "target_channel",
            "label": "Target Channel",
            "description": "The specific channel to apply this prompt override to",
            "placeholder": "Select channel...",
            "default_values": [{"id": str(target_channel_id), "type": "channel"}] if target_channel_id else None
        })

    fields.append({
        "type": "text_input",
        "custom_id": "system_prompt",
        "label": "System Instructions",
        "description": "Rules, constraints, or personality guidelines for PriestyAI in this scope",
        "style": "paragraph",
        "placeholder": "Define personality rules, formatting constraints, or channel directives...",
        "value": current_val,
        "required": False,
        "max_length": 3000
    })

    fields.append({
        "type": "checkbox_group",
        "custom_id": "override_user",
        "label": "User Persona Override",
        "description": "When enabled, PriestyAI will strictly follow this prompt and ignore user-level personas.",
        "options": [
            {
                "label": "Enforce this prompt over user personas",
                "value": "true",
                "default": override_user,
                "description": "Suppress personal user instructions in favor of this scope's directives"
            }
        ],
        "required": False
    })

    return DynamicModalV2(
        title=f"System Prompt ({scope_title})",
        custom_id=f"modal_sys_prompt_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_memory_modal(scope: str, user_policy: str, lore_policy: str, on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    fields = [
        {
            "type": "text_display",
            "content": "Configure persistent memory storage rules. Run `/data` at any time to inspect or wipe stored memories."
        }
    ]

    if scope in ["user", "bot_dm", "user_app"]:
        fields.append({
            "type": "radio_group",
            "custom_id": "user_memory_policy",
            "label": "Personal Facts Memory",
            "description": "Control whether PriestyAI remembers your personal facts and preferences",
            "value": user_policy or "read_write",
            "options": [
                {"label": "Read and Write", "value": "read_write", "description": "Save new preferences and recall past memories"},
                {"label": "Read-Only", "value": "read_only", "description": "Recall past facts, but do not record new ones"},
                {"label": "Disabled", "value": "disabled", "description": "Completely ignore all personal memory banks"}
            ],
            "required": True
        })

    if scope in ["server", "channel"]:
        fields.append({
            "type": "radio_group",
            "custom_id": "server_lore_policy",
            "label": "Server Lore Memory",
            "description": "Control whether PriestyAI stores and recalls guild-wide lore, events, and project facts",
            "value": lore_policy or "read_write",
            "options": [
                {"label": "Read and Write", "value": "read_write", "description": "Record and recall shared server lore facts"},
                {"label": "Read-Only", "value": "read_only", "description": "Recall existing lore without saving new facts"},
                {"label": "Disabled", "value": "disabled", "description": "Completely ignore server lore memory banks"}
            ],
            "required": True
        })

    return DynamicModalV2(
        title=f"Memory Policy ({scope_title})",
        custom_id=f"modal_memory_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_permissions_modal(scope: str, s_cfg: dict[str, Any], on_submit: Any, guild: discord.Guild | None = None) -> DynamicModalV2:
    re_defaults = format_mentionable_defaults(s_cfg.get("restricted_entities", []), guild)
    pb_defaults = format_mentionable_defaults(s_cfg.get("permission_bypass", []), guild)

    fields = [
        {
            "type": "text_display",
            "content": (
                "Control who is allowed or banned from using PriestyAI on this server.\n\n"
                "• **Blacklist:** BANS selected roles/members from using the bot. Everyone else is allowed.\n"
                "• **Whitelist:** ALLOWS ONLY selected roles/members to use the bot. Everyone else is banned.\n"
                "• **Permission Bypass:** Overrides bans and grants permission to manage bot settings."
            )
        },
        {
            "type": "radio_group",
            "custom_id": "access_behavior",
            "label": "Access Policy",
            "description": "Choose whether selected roles/members are banned or exclusively allowed",
            "value": s_cfg.get("access_behavior", "blacklist"),
            "options": [
                {"label": "Blacklist Mode", "value": "blacklist", "description": "Ban selected roles/members from using the bot (Default)"},
                {"label": "Whitelist Mode", "value": "whitelist", "description": "Ban everyone except selected roles/members"}
            ],
            "required": True
        },
        {
            "type": "mentionable_select",
            "custom_id": "restricted_entities",
            "label": "Target Roles & Members",
            "description": "Select the roles or members to apply the blacklist or whitelist to",
            "placeholder": "Select roles or users...",
            "required": False,
            "min_values": 0,
            "max_values": 25,
            "default_values": re_defaults
        },
        {
            "type": "mentionable_select",
            "custom_id": "permission_bypass",
            "label": "Permission Bypass",
            "description": "Exempts selected roles/members from all bans and grants /config access",
            "placeholder": "Select bypass roles or users...",
            "required": False,
            "min_values": 0,
            "max_values": 25,
            "default_values": pb_defaults
        },
        {
            "type": "string_select",
            "custom_id": "config_manager_role",
            "label": "Settings Manager Permission",
            "description": "Select the minimum server permission required to configure general bot settings",
            "value": s_cfg.get("config_manager_role", "administrators"),
            "options": [
                {"label": "Administrators", "value": "administrators", "description": "Users with Administrator permissions (Default)"},
                {"label": "Managers", "value": "managers", "description": "Users with Manage Server or Manage Channels permissions"},
                {"label": "Server Owner Only", "value": "owner_only", "description": "Restrict general settings to the Server Owner only"},
                {"label": "Everyone", "value": "everyone", "description": "Allow all server members to adjust general settings"}
            ],
            "required": True
        }
    ]

    return DynamicModalV2(
        title="Server Permissions",
        custom_id="modal_permissions",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_user_persona_modal(u_cfg: dict[str, Any], on_submit: Any) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": "Personalize how PriestyAI addresses you and tailor your personal background across Discord."
        },
        {
            "type": "text_input",
            "custom_id": "preferred_name",
            "label": "Preferred Name",
            "description": "How PriestyAI should address you, overriding Discord display names",
            "placeholder": "e.g. Alex, Captain...",
            "value": u_cfg.get("preferred_name", ""),
            "style": "short",
            "required": False
        },
        {
            "type": "text_input",
            "custom_id": "special_instructions",
            "label": "Personal Context & Habits",
            "description": "Your background, preferred programming languages, and response tone preferences",
            "placeholder": "e.g. Senior Rust engineer, prefer brief answers, use dark mode...",
            "value": u_cfg.get("special_instructions", ""),
            "style": "paragraph",
            "required": False,
            "max_length": 1500
        }
    ]

    return DynamicModalV2(
        title="User Persona",
        custom_id="modal_user_persona",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_reasoning_modal(scope: str, current_level: str, on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    fields = [
        {
            "type": "text_display",
            "content": "Select the preferred reasoning depth for PriestyAI. The engine will select the optimal model to satisfy this budget."
        },
        {
            "type": "radio_group",
            "custom_id": "reasoning_level",
            "label": "Reasoning Depth",
            "description": "Control thinking depth and token budgets across model cascades",
            "value": current_level or "AUTO",
            "options": [
                {"label": "Auto", "value": "AUTO", "description": "Smart Model & Thought Routing based on query complexity (Default)"},
                {"label": "High", "value": "HIGH", "description": "Deep thinking, derivations, and multi-step verification"},
                {"label": "Medium", "value": "MEDIUM", "description": "Standard reasoning for research and conceptual questions"},
                {"label": "Low", "value": "LOW", "description": "Light context processing with faster response time"},
                {"label": "Minimal", "value": "MINIMAL", "description": "Instant responses with zero thinking token latency"}
            ],
            "required": True
        }
    ]

    return DynamicModalV2(
        title=f"Reasoning Depth ({scope_title})",
        custom_id=f"modal_reasoning_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_tool_permissions_modal(scope: str, disabled_tools: list[str], on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    disabled_set = set(disabled_tools or [])

    tool_options = [
        {"label": "Docker Code Execution", "value": "execute_code", "description": "Isolated sandbox execution for Python, JS, C++, Rust, Go", "default": "execute_code" not in disabled_set},
        {"label": "Web Search & Article Reader", "value": "search_web,read_link", "description": "Real-time web search and full webpage content extraction", "default": "search_web" not in disabled_set},
        {"label": "Image Generation", "value": "generate_image", "description": "AI image rendering and automatic inline attachments", "default": "generate_image" not in disabled_set},
        {"label": "Message Reactions", "value": "react", "description": "Proactive and contextual emoji reactions to messages", "default": "react" not in disabled_set},
        {"label": "Interactive Components", "value": "add_component,add_modal", "description": "Interactive buttons, selects, and Modals v2 forms", "default": "add_component" not in disabled_set},
        {"label": "Long-Term Memory", "value": "remember,forget", "description": "Persistent memory storage and forgetting capabilities", "default": "remember" not in disabled_set},
        {"label": "Chat & Channel History", "value": "read_message_history,search_channel_history", "description": "Reading and searching previous messages in the channel", "default": "read_message_history" not in disabled_set},
        {"label": "Reasoning Expert", "value": "ask_expert", "description": "Escalating difficult mathematical derivations to reasoning models", "default": "ask_expert" not in disabled_set},
        {"label": "Server & User Lookups", "value": "get_user_profile,get_server_info,create_thread", "description": "Fetching member roles, server info, and managing threads", "default": "get_user_profile" not in disabled_set}
    ]

    fields = [
        {
            "type": "text_display",
            "content": "Check the capabilities that are ALLOWED to execute in this scope. Unchecked capabilities will be blocked."
        },
        {
            "type": "checkbox_group",
            "custom_id": "allowed_tools",
            "label": "Enabled Tool Capabilities",
            "description": "Select capabilities that PriestyAI is authorized to invoke",
            "options": tool_options,
            "required": False
        }
    ]

    return DynamicModalV2(
        title=f"Tool Permissions ({scope_title})",
        custom_id=f"modal_tools_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )