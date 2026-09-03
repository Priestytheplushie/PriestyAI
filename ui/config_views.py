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
from config.settings import BOT_OWNER_ID
from core.config_manager import config_manager
from core.github_app_client import github_app_client
from core.custom_tool_manager import (
    custom_tool_manager,
    OWNER_TOOL_CAP,
    SERVER_TOOL_CAP,
    USER_TOOL_CAP,
    is_owner_user
)
from agent.constants import OCTICONS_MAP, BETA_EMOJI, GITHUB_APP_INSTALL_URL
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.ConfigViews")

DEFAULT_SERVER_BIO = (
    "**PriestyAI** — Intelligent server companion & assistant.\n\n"
    "• Real-time web search, image creation & file downloads\n"
    "• Autonomous workspace agents & personal memory\n\n"
    "✨ **Chat:** @mention • </ask:1540889817980731543> • </agent:1542280617515950221>\n"
    "⚙️ **Manage:** </config:1541093516078485646> • </data:1541122763044163665>\n"
    "💡 Tip: Right-click any response to **Branch** or **Retry**."
)

SCOPE_DISPLAY_NAMES = {
    "server": "Server",
    "channel": "Channel",
    "user": "User"
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

def build_custom_tool_modal(scope: str, on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)

    fields = [
        {
            "type": "text_display",
            "content": (
                f"# Register Custom Tool {BETA_EMOJI}\n"
                "Connect a public API or webhook endpoint for PriestyAI to query during chat.\n\n"
                "• **Dynamic Inputs:** Place `{param}` in the URL (e.g. `https://api.example.com/user/{username}`). PriestyAI automatically extracts parameters without coding.\n"
                "• **Requirements:** Endpoints must strictly use HTTPS and be publicly reachable."
            )
        },
        {
            "type": "text_input",
            "custom_id": "tool_name",
            "label": "Tool Name",
            "description": "Unique identifier using letters and underscores only",
            "placeholder": "e.g. mc_status, clan_leaderboard, crypto_price",
            "style": "short",
            "required": True,
            "max_length": 30
        },
        {
            "type": "text_input",
            "custom_id": "tool_description",
            "label": "Tool Description (When to use it)",
            "description": "Explain when PriestyAI should invoke this tool",
            "placeholder": "e.g. Check online status, player count, and MOTD for our Minecraft server",
            "style": "paragraph",
            "required": True,
            "max_length": 250
        },
        {
            "type": "text_input",
            "custom_id": "endpoint_url",
            "label": "Endpoint URL",
            "description": "Must strictly use HTTPS and be publicly reachable",
            "placeholder": "e.g. https://api.mcsrvstat.us/3/{server_ip}",
            "style": "short",
            "required": True,
            "max_length": 500
        },
        {
            "type": "text_input",
            "custom_id": "extra_headers",
            "label": "Extra Headers / Content-Type (Optional)",
            "description": "Non-sensitive headers like Accept: application/json",
            "placeholder": "Accept: application/json",
            "style": "paragraph",
            "required": False,
            "max_length": 200
        }
    ]

    return DynamicModalV2(
        title=f"New Custom Tool ({scope_title})",
        custom_id=f"modal_custom_tool_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

def build_edit_custom_tool_modal(tool_data: dict[str, Any], scope: str, on_submit: Any) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    tool_name = tool_data.get("name", "tool")
    headers = tool_data.get("headers", {})
    headers_str = "\n".join(f"{k}: {v}" for k, v in headers.items()) if isinstance(headers, dict) else ""

    fields = [
        {
            "type": "text_display",
            "content": (
                f"# Edit Custom Tool: `{tool_name}` {BETA_EMOJI}\n"
                "Modify description, endpoint URL, or extra headers.\n"
                "To remove this tool completely, check the box at the bottom."
            )
        },
        {
            "type": "text_input",
            "custom_id": "tool_description",
            "label": "Tool Description (When to use it)",
            "description": "Explain when PriestyAI should invoke this tool",
            "value": tool_data.get("description", ""),
            "style": "paragraph",
            "required": True,
            "max_length": 250
        },
        {
            "type": "text_input",
            "custom_id": "endpoint_url",
            "label": "Endpoint URL",
            "description": "Must strictly use HTTPS and be publicly reachable",
            "value": tool_data.get("url_template", ""),
            "style": "short",
            "required": True,
            "max_length": 500
        },
        {
            "type": "text_input",
            "custom_id": "extra_headers",
            "label": "Extra Headers / Content-Type (Optional)",
            "description": "Non-sensitive headers like Accept: application/json",
            "value": headers_str,
            "style": "paragraph",
            "required": False,
            "max_length": 200
        },
        {
            "type": "checkbox",
            "custom_id": "delete_tool",
            "label": "Delete Tool Permanently",
            "description": "Check this box to remove this custom tool completely",
            "default": False
        }
    ]

    return DynamicModalV2(
        title=f"Edit Tool: {tool_name}"[:45],
        custom_id=f"modal_edit_custom_tool_{tool_name[:20]}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

class CustomToolsDashboardView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, scope: str = "server"):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.scope = "user" if scope == "user" or not guild else "server"
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        is_server = (self.scope == "server" and self.guild)
        entity_id = self.guild.id if is_server else self.user.id
        
        is_owner = is_owner_user(self.user.id)
        if is_owner:
            cap = OWNER_TOOL_CAP
        elif is_server:
            cap = SERVER_TOOL_CAP
        else:
            cap = USER_TOOL_CAP

        tools = custom_tool_manager.get_tools_for_entity(self.scope, entity_id)

        header_text = (
            f"# {OCTICONS_MAP['oct_link']} Custom Tools {BETA_EMOJI}\n"
            f"Extend PriestyAI with custom public webhooks and REST endpoints.\n"
            f"Active tools: `{len(tools)}/{cap}` • Endpoints are called dynamically during chat turns."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(discord.ui.Separator(visible=True))

        if not tools:
            container.add_item(TextDisplay("*No custom tools registered yet. Click '+ Add Custom Tool' below.*"))
        else:
            for t in tools[:10]:
                t_id = t["tool_id"]
                t_name = t["name"]
                t_desc = t["description"][:120]
                t_url = t["url_template"][:70]
                params = t.get("parameters", [])
                param_str = f" • Params: `{', '.join(params)}`" if params else " • Static Endpoint"

                snippet = (
                    f"**`{t_name}`**{param_str}\n"
                    f"> {t_desc}\n"
                    f"-# `{t_url}`"
                )

                edit_btn = Button(
                    label="Edit",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_edit_ct_{t_id}"
                )
                edit_btn.callback = self._create_edit_callback(t)
                container.add_item(Section(TextDisplay(snippet), accessory=edit_btn))

        container.add_item(discord.ui.Separator(visible=True))

        add_btn = Button(
            label="+ Add Custom Tool",
            style=discord.ButtonStyle.primary,
            disabled=(len(tools) >= cap),
            custom_id="btn_add_custom_tool"
        )
        add_btn.callback = self._on_add_tool_clicked

        container.add_item(ActionRow(add_btn))
        self.add_item(container)

    def _create_edit_callback(self, tool_data: dict[str, Any]):
        async def callback(interaction: discord.Interaction):
            async def on_edit_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                await sub_inter.response.defer(ephemeral=True)

                is_delete = bool(data.get("delete_tool", False))
                if is_delete:
                    custom_tool_manager.delete_tool(tool_data["tool_id"])
                    self._build_dashboard()
                    await sub_inter.edit_original_response(view=self)
                    return

                t_desc = data.get("tool_description", tool_data["description"]).strip()
                t_url = data.get("endpoint_url", tool_data["url_template"]).strip()
                t_headers = data.get("extra_headers", "").strip()

                entity_id = self.guild.id if (self.scope == "server" and self.guild) else self.user.id
                success, msg, _ = await custom_tool_manager.register_tool(
                    scope=self.scope,
                    entity_id=entity_id,
                    name=tool_data["name"],
                    description=t_desc,
                    url_template=t_url,
                    headers_str=t_headers,
                    created_by=interaction.user.id
                )

                if not success:
                    await sub_inter.followup.send(content=f"❌ **Update Rejected:** {msg}", ephemeral=True)
                    return

                self._build_dashboard()
                await sub_inter.edit_original_response(view=self)

            modal = build_edit_custom_tool_modal(tool_data, self.scope, on_edit_submit)
            await interaction.response.send_modal(modal)
        return callback

    async def _on_add_tool_clicked(self, interaction: discord.Interaction):
        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            await sub_inter.response.defer(ephemeral=True)

            t_name = data.get("tool_name", "")
            t_desc = data.get("tool_description", "")
            t_url = data.get("endpoint_url", "")
            t_headers = data.get("extra_headers", "")

            entity_id = self.guild.id if (self.scope == "server" and self.guild) else self.user.id
            success, msg, _ = await custom_tool_manager.register_tool(
                scope=self.scope,
                entity_id=entity_id,
                name=t_name,
                description=t_desc,
                url_template=t_url,
                headers_str=t_headers,
                created_by=interaction.user.id
            )

            if not success:
                await sub_inter.followup.send(content=f"❌ **Registration Rejected:** {msg}", ephemeral=True)
                return

            self._build_dashboard()
            await sub_inter.edit_original_response(view=self)

        modal = build_custom_tool_modal(self.scope, on_submit)
        await interaction.response.send_modal(modal)

class GitHubConfigDashboardView(LayoutView):
    def __init__(self, user: discord.User | discord.Member):
        super().__init__(timeout=600)
        self.user = user
        self.authorized_repos: list[dict[str, Any]] = []
        self._is_loading = True

    async def initialize(self):
        self.authorized_repos = await github_app_client.list_authorized_repositories()
        self._is_loading = False
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        u_cfg = config_manager.get_user_config(self.user.id)
        git_name = u_cfg.get("git_name", "").strip() or "*Not configured*"
        git_email = u_cfg.get("git_email", "").strip() or "*Not configured*"

        header_text = (
            f"# <:github:1542000155371507802> GitHub Configuration\n"
            f"Manage your default Git commit attribution and authorized repositories."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(discord.ui.Separator(visible=True))

        identity_block = (
            f"**Git Identity (Commit Attribution):**\n"
            f"• **Git Name:** `{git_name}`\n"
            f"• **Git Email:** `{git_email}`\n"
            f"-# Pre-fills automatically when signing off on agent commits."
        )
        container.add_item(TextDisplay(identity_block))

        repo_lines = ["\n**Authorized Repositories:**"]
        if self.authorized_repos:
            for r in self.authorized_repos[:12]:
                priv_tag = " 🔒" if r.get("private") else ""
                repo_lines.append(f"• {OCTICONS_MAP['oct_repo']} [{r['full_name']}]({r['html_url']}){priv_tag}")
            if len(self.authorized_repos) > 12:
                repo_lines.append(f"-# ... and {len(self.authorized_repos) - 12} more repositories")
        else:
            repo_lines.append(f"-# *No repositories installed yet. Click **Install App ↗** to grant access.*")

        container.add_item(TextDisplay("\n".join(repo_lines)))
        container.add_item(discord.ui.Separator(visible=True))

        config_id_btn = Button(
            label="Configure Identity",
            style=discord.ButtonStyle.primary,
            custom_id="btn_cfg_git_identity"
        )
        config_id_btn.callback = self._on_configure_identity_clicked

        install_btn = Button(
            label="Install App ↗",
            style=discord.ButtonStyle.link,
            url=GITHUB_APP_INSTALL_URL
        )

        container.add_item(ActionRow(config_id_btn, install_btn))
        self.add_item(container)

    async def _on_configure_identity_clicked(self, interaction: discord.Interaction):
        u_cfg = config_manager.get_user_config(self.user.id)
        current_name = u_cfg.get("git_name", "")
        current_email = u_cfg.get("git_email", "")

        fields = [
            {
                "type": "text_display",
                "content": (
                    "# Default Git Author Identity\n"
                    "Configure your default name and email for `Co-authored-by` git commit attribution."
                )
            },
            {
                "type": "text_input",
                "custom_id": "git_name",
                "label": "Default Git Name",
                "placeholder": "e.g. Alex Rivers",
                "value": current_name,
                "style": "short",
                "required": False,
                "max_length": 100
            },
            {
                "type": "text_input",
                "custom_id": "git_email",
                "label": "Default Git Email",
                "placeholder": "e.g. alex.rivers@example.com",
                "value": current_email,
                "style": "short",
                "required": False,
                "max_length": 150
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_name = data.get("git_name", "").strip()
            new_email = data.get("git_email", "").strip()

            config_manager.set_user_config(self.user.id, git_name=new_name, git_email=new_email)
            self._build_dashboard()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title="Configure Git Identity",
            custom_id="modal_config_git_identity",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

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

        gallery_items = [discord.MediaGalleryItem(avatar_url)]
        banner_attr = getattr(member, "banner", None)
        if banner_attr and hasattr(banner_attr, "url") and banner_attr.url:
            gallery_items.append(discord.MediaGalleryItem(banner_attr.url))

        container.add_item(MediaGallery(*gallery_items))

        info_text = (
            f"**Display Nickname:** `{nick}`\n"
            f"**Server Bio:**\n{bio}"
        )
        container.add_item(TextDisplay(info_text))

        edit_bio_btn = Button(label="Edit Name & Bio", style=discord.ButtonStyle.primary, custom_id="btn_cfg_edit_identity")
        edit_bio_btn.callback = self._on_edit_bio_clicked

        avatar_banner_btn = Button(label="Upload Avatar & Banner", style=discord.ButtonStyle.secondary, custom_id="btn_cfg_upload_avatar_banner")
        avatar_banner_btn.callback = self._on_upload_avatar_banner_clicked

        reset_btn = Button(label="Reset to Default ↺", style=discord.ButtonStyle.danger, custom_id="btn_cfg_reset_identity")
        reset_btn.callback = self._on_reset_identity_clicked

        container.add_item(ActionRow(edit_bio_btn, avatar_banner_btn, reset_btn))
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

    async def _on_upload_avatar_banner_clicked(self, interaction: discord.Interaction):
        fields = [
            {
                "type": "file_upload",
                "custom_id": "avatar_file",
                "label": "Server Avatar Image",
                "description": "Upload a PNG or JPG to set as PriestyAI's server profile picture",
                "required": False,
                "max_values": 1
            },
            {
                "type": "file_upload",
                "custom_id": "banner_file",
                "label": "Server Banner Image",
                "description": "Upload a PNG or JPG to set as PriestyAI's server profile banner",
                "required": False,
                "max_values": 1
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            avatar_files = data.get("avatar_file", [])
            banner_files = data.get("banner_file", [])

            avatar_bytes = None
            banner_bytes = None

            async with aiohttp.ClientSession() as session:
                if avatar_files and isinstance(avatar_files, list):
                    f_obj = avatar_files[0]
                    url = f_obj.get("url") if isinstance(f_obj, dict) else None
                    if url:
                        try:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    avatar_bytes = await resp.read()
                        except Exception as e:
                            logger.warning(f"Failed to download avatar image: {e}")

                if banner_files and isinstance(banner_files, list):
                    b_obj = banner_files[0]
                    b_url = b_obj.get("url") if isinstance(b_obj, dict) else None
                    if b_url:
                        try:
                            async with session.get(b_url) as resp:
                                if resp.status == 200:
                                    banner_bytes = await resp.read()
                        except Exception as e:
                            logger.warning(f"Failed to download banner image: {e}")

            edit_kwargs = {}
            if avatar_bytes is not None:
                edit_kwargs["avatar"] = avatar_bytes
            if banner_bytes is not None:
                edit_kwargs["banner"] = banner_bytes

            if edit_kwargs:
                try:
                    await self.guild.me.edit(**edit_kwargs)
                except TypeError:
                    if "avatar" in edit_kwargs:
                        try:
                            await self.guild.me.edit(avatar=edit_kwargs["avatar"])
                        except Exception as e:
                            logger.warning(f"Failed to update guild avatar: {e}")
                    if "banner" in edit_kwargs:
                        logger.warning("Guild member banner editing is not supported on this library build.")
                except Exception as e:
                    logger.warning(f"Failed to update guild avatar/banner: {e}")

            self._build_dashboard()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title="Upload Server Avatar & Banner",
            custom_id="modal_avatar_banner_upload",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_reset_identity_clicked(self, interaction: discord.Interaction):
        try:
            await self.guild.me.edit(nick=None, avatar=None)
        except Exception:
            pass

        try:
            await self.guild.me.edit(banner=None)
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
        "Configurations set at the User Scope define your personal identity (preferred name, Git credentials, coding habits) "
        "and control personal facts memory banks. These settings follow you across all servers and DMs."
    )
}

SETTING_HELP_TEXTS = {
    "custom_tools": (
        "### Custom Tools\n"
        "Extend PriestyAI with custom web endpoints, REST APIs, or public webhooks.\n\n"
        "• **Server Scope:** Custom tools configured here are available to all members in this server (max 10).\n"
        "• **User Scope:** Personal custom tools that follow you into DMs and enabled servers (max 5).\n"
        "• **Parameter Placeholders:** Use `{param}` in the endpoint URL for dynamic inputs.\n"
        "• **Member Tool Policy:** Server administrators can toggle whether member-defined personal tools are allowed under Tool Permissions."
    ),
    "github": (
        "### GitHub Configuration\n"
        "Manage your Git author identity and view repositories authorized with the PriestyAI GitHub App.\n\n"
        "• **Git Name & Email:** Sets your default co-author credit for Pull Request commits.\n"
        "• **Authorized Repositories:** Lists repositories linked with the GitHub App.\n"
        "• **Install App:** Direct link to authorize new repositories with 1 click."
    ),
    "server_identity": (
        "### Server Identity\n"
        "Customize how PriestyAI presents itself in this specific Discord server.\n\n"
        "• **Server Nickname:** Overrides the bot's display name inside this guild.\n"
        "• **Server Bio:** Sets a custom description of what PriestyAI does in this server (max 400 chars).\n"
        "• **Server Avatar:** Uploads an avatar image specific to this guild without altering the global bot avatar.\n"
        "• **Server Banner:** Uploads a banner image specific to this guild's profile.\n"
        "• **Reset:** Restores the bot's default global name, avatar, banner, and bio."
    ),
    "system_prompt": (
        "### System Prompt\n"
        "Direct the behavior, tone, constraints, and personality of PriestyAI for a server or channel.\n\n"
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
        "• **Scope:** Strictly available under User scope."
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
        "• **Target Roles & Members:** Select specific roles or users to restrict tools for. Leave empty to apply default tool rules to `@everyone`.\n"
        "• **Native Capabilities:** Toggle code execution, web search, reactions, artifacts, etc.\n"
        "• **Custom Tools:** Enable or disable registered custom tools for selected roles or members.\n"
        "• **Member Tool Policy:** Control whether personal user-scoped tools are permitted in this server."
    ),
    "reset": (
        "### Reset\n"
        "Restores custom configurations back to defaults for a specific scope.\n\n"
        "• **Server Scope:** Clears server lore, permissions, and server prompts (Admins only).\n"
        "• **Channel Scope:** Clears channel prompt overrides and tool locks.\n"
        "• **User Scope:** Wipes personal preferred name, Git identity, and custom persona."
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
            discord.SelectOption(label="Custom Tools", value="custom_tools", description="Configure custom public REST API endpoints and webhooks"),
            discord.SelectOption(label="GitHub", value="github", description="Git author attribution and authorized repositories"),
            discord.SelectOption(label="AI Channels", value="ai_channels", description="Designate channels for automatic AI conversation without mentions"),
            discord.SelectOption(label="Server Identity", value="server_identity", description="Server nickname, bio, avatar, and server banner"),
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

    if scope == "user":
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

def build_tool_permissions_modal(
    scope: str,
    disabled_tools: list[str],
    on_submit: Any,
    target_entities: list[Any] | None = None,
    guild: discord.Guild | None = None,
    custom_tools: list[dict[str, Any]] | None = None,
    allow_user_custom_tools: bool = True
) -> DynamicModalV2:
    scope_title = format_scope_title(scope)
    disabled_set = set(disabled_tools or [])
    target_defaults = format_mentionable_defaults(target_entities or [], guild)

    native_tool_options = [
        {"label": "Code Artifacts", "value": "create_artifact", "description": "Standalone script cards & zip project packaging", "default": "create_artifact" not in disabled_set},
        {"label": "Docker Code Execution", "value": "execute_code", "description": "Isolated sandbox execution for Python, JS, C++, Rust, Go", "default": "execute_code" not in disabled_set},
        {"label": "Web Search & Reader", "value": "search_web,read_link", "description": "Real-time web search and full webpage content extraction", "default": "search_web" not in disabled_set},
        {"label": "Image Generation & Search", "value": "generate_image,search_image,search_gif,edit_image", "description": "AI image rendering, web image search, GIFs, and image editing", "default": "generate_image" not in disabled_set},
        {"label": "Message Reactions", "value": "react", "description": "Proactive and contextual emoji reactions to messages", "default": "react" not in disabled_set},
        {"label": "Interactive Components", "value": "add_component,add_modal", "description": "Interactive buttons, selects, and Modals v2 forms", "default": "add_component" not in disabled_set},
        {"label": "Long-Term Memory", "value": "remember,forget,search_memories", "description": "Persistent memory storage, search, and forgetting capabilities", "default": "remember" not in disabled_set},
        {"label": "Chat & Channel History", "value": "read_message_history,search_channel_history", "description": "Reading and searching previous messages in the channel", "default": "read_message_history" not in disabled_set},
        {"label": "Reasoning Expert", "value": "ask_expert", "description": "Escalating difficult mathematical derivations to reasoning models", "default": "ask_expert" not in disabled_set},
        {"label": "Server & User Lookups", "value": "get_user_profile,get_server_info,create_thread", "description": "Fetching member roles, server info, and managing threads", "default": "get_user_profile" not in disabled_set}
    ]

    fields = [
        {
            "type": "text_display",
            "content": (
                "Configure capability restrictions for members or roles.\n"
                "Leave Target Roles & Members empty to set server-wide defaults for `@everyone`."
            )
        },
        {
            "type": "mentionable_select",
            "custom_id": "target_entities",
            "label": "Target Roles & Members",
            "description": "Leave blank for @everyone, or select specific roles/members to restrict",
            "placeholder": "Select roles or members (default: @everyone)...",
            "required": False,
            "min_values": 0,
            "max_values": 25,
            "default_values": target_defaults
        },
        {
            "type": "checkbox_group",
            "custom_id": "allowed_tools",
            "label": "Enabled Native Capabilities",
            "description": "Select built-in capabilities PriestyAI is authorized to invoke",
            "options": native_tool_options,
            "required": False
        }
    ]

    c_tools = custom_tools or []
    if c_tools:
        custom_tool_options = []
        for t in c_tools[:10]:
            t_name = t.get("name", "custom_tool")
            t_desc = t.get("description", "Custom API Tool")[:100]
            is_enabled = t_name not in disabled_set
            custom_tool_options.append({
                "label": t_name,
                "value": t_name,
                "description": t_desc,
                "default": is_enabled
            })

        fields.append({
            "type": "checkbox_group",
            "custom_id": "allowed_custom_tools",
            "label": "Enabled Custom Tools",
            "description": "Select server custom tools authorized for these targets",
            "options": custom_tool_options,
            "required": False
        })

    if scope == "server":
        fields.append({
            "type": "checkbox_group",
            "custom_id": "allow_user_custom_tools",
            "label": "Member Tool Policy",
            "description": "Control personal user-scoped tools within this server",
            "options": [
                {
                    "label": "Allow Personal User Tools",
                    "value": "allow",
                    "description": "Permit members to invoke personal user tools in this server",
                    "default": allow_user_custom_tools
                }
            ],
            "required": False
        })

    return DynamicModalV2(
        title=f"Tool Permissions ({scope_title})",
        custom_id=f"modal_tools_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )
