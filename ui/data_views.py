import io
import json
import logging
from typing import Any
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    Section,
    TextDisplay,
    Separator,
    ActionRow,
    Button,
    Select
)
from core.memory_manager import memory_manager
from core.config_manager import config_manager
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.DataViews")

DATABASE_INFO_TEXT = (
    "View, edit, or delete the data PriestyAI stores about you and your server.\n\n"
    "• **Memories:** Facts and preferences saved from your conversations.\n"
    "• **Server Lore:** Shared knowledge and facts recorded for this server.\n"
    "• **Configs:** Your persona, preferred name, reasoning depth, and channel settings."
)

class DatabaseDashboardView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        container.add_item(TextDisplay(f"# Database\n{DATABASE_INFO_TEXT}"))
        container.add_item(Separator(visible=True))

        browse_options = [
            discord.SelectOption(
                label="Memories",
                value="browse_memories",
                description="View and edit your personal memories and preferences",
                emoji="🧠"
            ),
            discord.SelectOption(
                label="Server Lore",
                value="browse_server_lore",
                description="View and manage shared lore for this server",
                emoji="🏰"
            ),
            discord.SelectOption(
                label="Configs",
                value="browse_configs",
                description="Inspect your persona, server settings, and channel rules",
                emoji="⚙️"
            )
        ]

        browse_select = Select(
            custom_id="select_browse_db",
            placeholder="Browse Database...",
            options=browse_options
        )
        browse_select.callback = self._on_browse_selected
        container.add_item(ActionRow(browse_select))

        container.add_item(Separator(visible=True))

        close_btn = Button(
            label="Close",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_db_close"
        )
        close_btn.callback = self._on_close_clicked

        search_btn = Button(
            label="Search",
            style=discord.ButtonStyle.primary,
            custom_id="btn_db_search"
        )
        search_btn.callback = self._on_search_clicked

        delete_btn = Button(
            label="Delete...",
            style=discord.ButtonStyle.danger,
            custom_id="btn_db_delete_nav"
        )
        delete_btn.callback = self._on_delete_nav_clicked

        container.add_item(ActionRow(close_btn, search_btn, delete_btn))
        self.add_item(container)

    async def _on_browse_selected(self, interaction: discord.Interaction):
        if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
            return

        choice = interaction.data["values"][0]

        if choice == "browse_memories":
            view = MemoriesBrowserView(user=self.user, guild=self.guild, channel=self.channel, page=0)
            await interaction.response.edit_message(view=view)
        elif choice == "browse_server_lore":
            view = ServerLoreBrowserView(user=self.user, guild=self.guild, channel=self.channel, page=0)
            await interaction.response.edit_message(view=view)
        elif choice == "browse_configs":
            view = ConfigsBrowserView(user=self.user, guild=self.guild, channel=self.channel)
            await interaction.response.edit_message(view=view)

    async def _on_close_clicked(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
            await interaction.delete_original_response()
        except Exception:
            try:
                await interaction.response.edit_message(content="*Closed.*", view=None)
            except Exception:
                pass

    async def _on_search_clicked(self, interaction: discord.Interaction):
        fields = [
            {
                "type": "text_display",
                "content": "Search across your memories and server lore."
            },
            {
                "type": "text_input",
                "custom_id": "search_query",
                "label": "Search Query",
                "placeholder": "Enter keyword or phrase...",
                "style": "short",
                "required": True
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            query = data.get("search_query", "").strip()
            view = SearchResultsView(user=self.user, guild=self.guild, channel=self.channel, query=query)
            await view.perform_search()
            await sub_inter.response.edit_message(view=view)

        modal = DynamicModalV2(
            title="Search Database",
            custom_id="modal_search_db",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_delete_nav_clicked(self, interaction: discord.Interaction):
        view = DataDeletionView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=view)


class MemoriesBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, page: int = 0):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.current_page = page
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        memories = memory_manager.get_all_memories_for_entity("user", self.user.id)
        total_mems = len(memories)
        page_size = 4
        total_pages = max(1, (total_mems + page_size - 1) // page_size)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_text = (
            "# Personal Memories\n"
            "Facts and preferences saved across your conversations.\n"
            "Click **Edit** to modify a fact, or clear the text to delete it."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        if not memories:
            container.add_item(TextDisplay("*No personal memories stored.*"))
        else:
            start_idx = self.current_page * page_size
            page_mems = memories[start_idx:start_idx + page_size]

            for mem in page_mems:
                mem_id = mem["id"]
                mem_text = mem["memory_text"]
                created_raw = mem.get("created_at", "")
                
                display_content = f"**Fact #{mem_id}:**\n> {mem_text}\n-# Added: `{created_raw}`"
                
                edit_btn = Button(
                    label="Edit",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_edit_mem_{mem_id}"
                )
                edit_btn.callback = self._create_edit_callback(mem_id, mem_text)

                section = Section(TextDisplay(display_content), accessory=edit_btn)
                container.add_item(section)

        container.add_item(Separator(visible=True))

        row_items = []
        if total_pages > 1:
            prev_btn = Button(label="◀", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), custom_id="btn_mem_prev")
            ind_btn = Button(label=f"{self.current_page + 1} / {total_pages}", style=discord.ButtonStyle.secondary, disabled=True, custom_id="btn_mem_ind")
            next_btn = Button(label="▶", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), custom_id="btn_mem_next")

            async def on_prev(inter: discord.Interaction):
                self.current_page -= 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            async def on_next(inter: discord.Interaction):
                self.current_page += 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            prev_btn.callback = on_prev
            next_btn.callback = on_next
            row_items.extend([prev_btn, ind_btn, next_btn])

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_mem_back_dash")
        back_btn.callback = self._on_back_clicked
        row_items.append(back_btn)

        container.add_item(ActionRow(*row_items))
        self.add_item(container)

    def _create_edit_callback(self, mem_id: int, current_text: str):
        async def callback(interaction: discord.Interaction):
            fields = [
                {
                    "type": "text_display",
                    "content": "Edit this memory, or clear the text completely to delete it."
                },
                {
                    "type": "text_input",
                    "custom_id": "memory_content",
                    "label": "Memory Content",
                    "description": "Leave blank to delete",
                    "placeholder": "Enter text or leave blank to delete...",
                    "style": "paragraph",
                    "value": current_text,
                    "required": False,
                    "max_length": 1000
                }
            ]

            async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_text = data.get("memory_content", "").strip()

                if not new_text:
                    await memory_manager.forget(mem_id, reason="Cleared via edit")
                else:
                    await memory_manager.update_memory_text(mem_id, new_text)

                self._build_layout()
                await sub_inter.response.edit_message(view=self)

            modal = DynamicModalV2(
                title=f"Edit Memory #{mem_id}",
                custom_id=f"modal_edit_mem_{mem_id}",
                fields_schema=fields,
                on_submit_callback=on_submit
            )
            await interaction.response.send_modal(modal)

        return callback

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=dash)


class ServerLoreBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, page: int = 0):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.current_page = page
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        if not self.guild:
            container.add_item(TextDisplay("# Server Lore\n*Server Lore is only available inside Discord servers.*"))
            back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_lore_back_dm")
            back_btn.callback = self._on_back_clicked
            container.add_item(ActionRow(back_btn))
            self.add_item(container)
            return

        is_admin = False
        if isinstance(self.user, discord.Member):
            is_admin = self.user.guild_permissions.administrator or (self.guild.owner_id == self.user.id) or self.user.guild_permissions.manage_guild

        lore_items = memory_manager.get_all_memories_for_entity("server", self.guild.id)
        total_items = len(lore_items)
        page_size = 4
        total_pages = max(1, (total_items + page_size - 1) // page_size)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_text = (
            f"# Server Lore: {self.guild.name}\n"
            "Shared lore and facts recorded for this server.\n"
            + ("Click **Edit** to modify or clear text to delete." if is_admin else "*Viewing mode (requires Manage Server permission to edit).*")
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        if not lore_items:
            container.add_item(TextDisplay("*No server lore currently recorded.*"))
        else:
            start_idx = self.current_page * page_size
            page_lore = lore_items[start_idx:start_idx + page_size]

            for lore in page_lore:
                lore_id = lore["id"]
                lore_text = lore["memory_text"]
                created_raw = lore.get("created_at", "")
                
                display_content = f"**Lore #{lore_id}:**\n> {lore_text}\n-# Recorded: `{created_raw}`"
                
                if is_admin:
                    edit_btn = Button(label="Edit", style=discord.ButtonStyle.secondary, custom_id=f"btn_edit_lore_{lore_id}")
                    edit_btn.callback = self._create_edit_callback(lore_id, lore_text)
                    section = Section(TextDisplay(display_content), accessory=edit_btn)
                    container.add_item(section)
                else:
                    container.add_item(TextDisplay(display_content))

        container.add_item(Separator(visible=True))

        row_items = []
        if total_pages > 1:
            prev_btn = Button(label="◀", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), custom_id="btn_lore_prev")
            ind_btn = Button(label=f"{self.current_page + 1} / {total_pages}", style=discord.ButtonStyle.secondary, disabled=True, custom_id="btn_lore_ind")
            next_btn = Button(label="▶", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), custom_id="btn_lore_next")

            async def on_prev(inter: discord.Interaction):
                self.current_page -= 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            async def on_next(inter: discord.Interaction):
                self.current_page += 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            prev_btn.callback = on_prev
            next_btn.callback = on_next
            row_items.extend([prev_btn, ind_btn, next_btn])

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_lore_back_dash")
        back_btn.callback = self._on_back_clicked
        row_items.append(back_btn)

        container.add_item(ActionRow(*row_items))
        self.add_item(container)

    def _create_edit_callback(self, lore_id: int, current_text: str):
        async def callback(interaction: discord.Interaction):
            fields = [
                {
                    "type": "text_display",
                    "content": "Edit this server fact, or clear the text completely to delete it."
                },
                {
                    "type": "text_input",
                    "custom_id": "lore_content",
                    "label": "Server Lore Content",
                    "description": "Leave blank to delete",
                    "placeholder": "Enter text or leave blank to delete...",
                    "style": "paragraph",
                    "value": current_text,
                    "required": False,
                    "max_length": 1000
                }
            ]

            async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_text = data.get("lore_content", "").strip()
                if not new_text:
                    await memory_manager.forget(lore_id, reason="Cleared via edit")
                else:
                    await memory_manager.update_memory_text(lore_id, new_text)
                self._build_layout()
                await sub_inter.response.edit_message(view=self)

            modal = DynamicModalV2(
                title=f"Edit Server Lore #{lore_id}",
                custom_id=f"modal_edit_lore_{lore_id}",
                fields_schema=fields,
                on_submit_callback=on_submit
            )
            await interaction.response.send_modal(modal)

        return callback

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=dash)


class ConfigsBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        u_cfg = config_manager.get_user_config(self.user.id)
        s_cfg = config_manager.get_server_config(self.guild.id) if self.guild else None
        c_cfg = config_manager.get_channel_config(getattr(self.channel, "id", 0)) if self.channel else None

        p_name = u_cfg.get("preferred_name") or "*None (Using Discord display name)*"
        habits = u_cfg.get("special_instructions") or "*None set*"
        r_level = u_cfg.get("preferred_reasoning_level", "AUTO")
        mem_pol = u_cfg.get("user_memory_policy", "read_write")

        user_block = (
            f"### 👤 User Persona\n"
            f"• **Preferred Name:** `{p_name}`\n"
            f"• **Personal Context:** {habits}\n"
            f"• **Reasoning Preference:** `{r_level}`\n"
            f"• **Memory Policy:** `{mem_pol}`"
        )

        server_block = ""
        if s_cfg and self.guild:
            s_bio = s_cfg.get("server_bio") or "*Default*"
            s_prompt = s_cfg.get("system_prompt") or "*None set*"
            s_ai_chans = s_cfg.get("ai_channels", [])
            s_ai_str = ", ".join([f"<#{cid}>" for cid in s_ai_chans]) if s_ai_chans else "*None designated*"
            s_access = s_cfg.get("access_behavior", "blacklist").capitalize()

            server_block = (
                f"\n\n### 🏰 Server Configs ({self.guild.name})\n"
                f"• **Server Bio:** {s_bio}\n"
                f"• **Server Prompt:** {s_prompt}\n"
                f"• **AI Channels:** {s_ai_str}\n"
                f"• **Access Policy:** `{s_access}`"
            )

        channel_block = ""
        if c_cfg and self.channel and hasattr(self.channel, "name"):
            c_prompt = c_cfg.get("system_prompt") or "*Inherits Server Prompt*"
            channel_block = (
                f"\n\n### 💬 Channel Directives (#{self.channel.name})\n"
                f"• **Prompt Override:** {c_prompt}"
            )

        container.add_item(TextDisplay(f"# Configs\n{user_block}{server_block}{channel_block}"))
        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_cfg_back_dash")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=dash)


class SearchResultsView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, query: str):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.query = query

    async def perform_search(self):
        self.clear_items()
        container = Container()

        recalled = await memory_manager.recall_relevant_memories(
            query=self.query,
            user_id=self.user.id,
            guild_id=self.guild.id if self.guild else None,
            top_k=6
        )

        user_mems = recalled.get("user_memories", [])
        server_lore = recalled.get("server_lore", [])
        total_found = len(user_mems) + len(server_lore)

        header_text = (
            f"# Search: \"{self.query}\"\n"
            f"Found `{total_found}` matching record(s)."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        if total_found == 0:
            container.add_item(TextDisplay("*No matching records found.*"))
        else:
            for mem in user_mems:
                mem_id = mem["id"]
                mem_text = mem["text"]

                edit_btn = Button(label="Edit", style=discord.ButtonStyle.secondary, custom_id=f"btn_s_edit_mem_{mem_id}")
                edit_btn.callback = self._create_mem_edit_callback(mem_id, mem_text)

                section = Section(
                    TextDisplay(f"👤 **User Memory #{mem_id}:**\n> {mem_text}"),
                    accessory=edit_btn
                )
                container.add_item(section)

            for lore in server_lore:
                lore_id = lore["id"]
                lore_text = lore["text"]

                section = Section(
                    TextDisplay(f"🏰 **Server Lore #{lore_id}:**\n> {lore_text}")
                )
                container.add_item(section)

        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_s_back_dash")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    def _create_mem_edit_callback(self, mem_id: int, current_text: str):
        async def callback(interaction: discord.Interaction):
            fields = [
                {
                    "type": "text_display",
                    "content": "Edit this memory, or leave blank to delete."
                },
                {
                    "type": "text_input",
                    "custom_id": "memory_content",
                    "label": "Memory Content",
                    "value": current_text,
                    "style": "paragraph",
                    "required": False
                }
            ]

            async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_text = data.get("memory_content", "").strip()
                if not new_text:
                    await memory_manager.forget(mem_id, reason="Cleared via search")
                else:
                    await memory_manager.update_memory_text(mem_id, new_text)
                await self.perform_search()
                await sub_inter.response.edit_message(view=self)

            modal = DynamicModalV2(
                title=f"Edit Memory #{mem_id}",
                custom_id=f"modal_s_edit_{mem_id}",
                fields_schema=fields,
                on_submit_callback=on_submit
            )
            await interaction.response.send_modal(modal)

        return callback

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=dash)


class DataDeletionView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        is_admin = False
        if self.guild and isinstance(self.user, discord.Member):
            is_admin = self.user.guild_permissions.administrator or (self.guild.owner_id == self.user.id)

        header_text = (
            "# Delete Data\n"
            "Choose what you want to remove. Deletions cannot be undone."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        delete_options = [
            discord.SelectOption(
                label="Delete Memories",
                value="del_memories",
                description="Deletes all personal memory facts and saved preferences",
                emoji="🧠"
            ),
            discord.SelectOption(
                label="Delete Configs",
                value="del_configs",
                description="Resets preferred name, custom instructions, and persona",
                emoji="⚙️"
            ),
            discord.SelectOption(
                label="Delete Everything",
                value="del_everything",
                description="Deletes all your memories, configs, and chat history",
                emoji="💥"
            )
        ]

        if is_admin and self.guild:
            delete_options.append(
                discord.SelectOption(
                    label="Delete Server Lore",
                    value="del_server_lore",
                    description="Deletes all server lore recorded for this guild",
                    emoji="🏰"
                )
            )

        del_select = Select(
            custom_id="select_data_delete_choice",
            placeholder="Select what to delete...",
            options=delete_options
        )
        del_select.callback = self._on_delete_option_selected
        container.add_item(ActionRow(del_select))

        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_del_back_dash")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    async def _on_delete_option_selected(self, interaction: discord.Interaction):
        if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
            return

        choice = interaction.data["values"][0]

        if choice == "del_memories":
            count = memory_manager.delete_all_user_memories(self.user.id)
            msg = f"Deleted `{count}` personal memory record(s)."

        elif choice == "del_configs":
            config_manager.reset_config("user", self.user.id)
            msg = "Your preferred name and custom persona settings have been reset."

        elif choice == "del_everything":
            res = memory_manager.purge_entire_user_data(self.user.id)
            msg = (
                f"All your data has been deleted:\n"
                f"• Memories deleted: `{res.get('memories', 0)}`\n"
                f"• Configs reset: `{res.get('user_configs', 0)}`\n"
                f"• Chat sessions cleared: `{res.get('chat_sessions', 0)}`\n"
                f"• Generation history cleared: `{res.get('message_generations', 0)}`"
            )

        elif choice == "del_server_lore" and self.guild:
            count = memory_manager.delete_all_server_lore(self.guild.id)
            msg = f"Removed `{count}` server lore record(s) for **{self.guild.name}**."

        else:
            msg = "No action taken."

        container = Container()
        container.add_item(TextDisplay(f"# Deleted\n{msg}"))
        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_del_done_back")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.clear_items()
        self.add_item(container)
        await interaction.response.edit_message(view=self)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel)
        await interaction.response.edit_message(view=dash)