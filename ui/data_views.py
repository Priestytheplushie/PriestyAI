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
from core.feedback_manager import feedback_manager
from config.settings import BOT_OWNER_ID
from ui.modals import DynamicModalV2
from agent.constants import OCTICONS_MAP
from parsers.markdown_parser import DFM_EMOJI_MAP

logger = logging.getLogger("PriestyAI.DataViews")

DATABASE_INFO_TEXT = (
    "View, export, edit, or delete the data PriestyAI stores about you and your server.\n\n"
    "• **Memories:** Facts and preferences saved from your conversations.\n"
    "• **Server Lore:** Shared knowledge and facts recorded for this server.\n"
    "• **Configs:** Your persona, preferred name, reasoning depth, and channel settings.\n"
    "• **Export:** 1-click download of all personal data in decrypted JSON format."
)

def truncate_str(text: str, max_chars: int = 160) -> str:
    clean = str(text if text is not None else "").strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "..."

def is_user_bot_admin(user: discord.User | discord.Member, client: discord.Client | None = None) -> bool:
    uid = str(user.id)

    if BOT_OWNER_ID:
        allowed_ids = [i.strip() for i in BOT_OWNER_ID.replace(";", ",").split(",") if i.strip()]
        if uid in allowed_ids:
            return True

    if not client:
        return False

    if hasattr(client, "owner_id") and client.owner_id and str(client.owner_id) == uid:
        return True
    if hasattr(client, "owner_ids") and client.owner_ids and user.id in client.owner_ids:
        return True

    app = getattr(client, "application", None)
    if app:
        owner = getattr(app, "owner", None)
        if owner:
            if isinstance(owner, discord.Team):
                team_owner_id = getattr(owner, "owner_user_id", None) or getattr(owner, "owner_id", None)
                if team_owner_id and str(team_owner_id) == uid:
                    return True

                members = getattr(owner, "members", [])
                for m in members:
                    m_id = getattr(m, "id", None) or getattr(getattr(m, "user", None), "id", None)
                    if m_id and str(m_id) == uid:
                        return True
            else:
                if str(getattr(owner, "id", "")) == uid:
                    return True

    return False


class DatabaseDashboardView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, client: discord.Client | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        is_admin = is_user_bot_admin(self.user, self.client)

        container.add_item(TextDisplay(f"# Database\n{DATABASE_INFO_TEXT}"))
        container.add_item(Separator(visible=True))

        browse_options = [
            discord.SelectOption(
                label="Memories",
                value="browse_memories",
                description="View and edit your personal memories and preferences",
                emoji=OCTICONS_MAP["oct_checklist"]
            ),
            discord.SelectOption(
                label="Server Lore",
                value="browse_server_lore",
                description="View and manage shared lore for this server",
                emoji=OCTICONS_MAP["oct_repo"]
            ),
            discord.SelectOption(
                label="Configs",
                value="browse_configs",
                description="Inspect your persona, server settings, and channel rules",
                emoji=OCTICONS_MAP["oct_terminal"]
            )
        ]

        if is_admin:
            browse_options.append(
                discord.SelectOption(
                    label="Admin",
                    value="browse_admin",
                    description="Inspect all tables and review feedback",
                    emoji=OCTICONS_MAP["oct_info"]
                )
            )

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

        export_btn = Button(
            label="Export Data",
            style=discord.ButtonStyle.secondary,
            emoji="📥",
            custom_id="btn_db_export"
        )
        export_btn.callback = self._on_export_clicked

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

        container.add_item(ActionRow(close_btn, export_btn, search_btn, delete_btn))
        self.add_item(container)

    async def _on_browse_selected(self, interaction: discord.Interaction):
        if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
            return

        choice = interaction.data["values"][0]

        if choice == "browse_memories":
            view = MemoriesBrowserView(user=self.user, guild=self.guild, channel=self.channel, client=self.client, page=0)
            await interaction.response.edit_message(view=view)
        elif choice == "browse_server_lore":
            view = ServerLoreBrowserView(user=self.user, guild=self.guild, channel=self.channel, client=self.client, page=0)
            await interaction.response.edit_message(view=view)
        elif choice == "browse_configs":
            view = ConfigsBrowserView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
            await interaction.response.edit_message(view=view)
        elif choice == "browse_admin":
            if not is_user_bot_admin(self.user, self.client or interaction.client):
                await interaction.response.send_message(content="Access Denied: Admin database viewer is restricted to the Bot Owner / Team.", ephemeral=True)
                return
            view = AdminDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client or interaction.client)
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

    async def _on_export_clicked(self, interaction: discord.Interaction):
        data_bundle = memory_manager.export_user_data_bundle(self.user.id)
        json_bytes = json.dumps(data_bundle, indent=2, ensure_ascii=False).encode("utf-8")
        file_obj = discord.File(
            io.BytesIO(json_bytes),
            filename=f"priestyai_data_export_{self.user.id}.json"
        )
        await interaction.response.send_message(
            content="📥 **Your Data Export is Ready:** Attached is a full, decrypted JSON file of all personal facts, user persona settings, and chat sessions stored for your account.",
            file=file_obj,
            ephemeral=True
        )

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
            view = SearchResultsView(user=self.user, guild=self.guild, channel=self.channel, query=query, client=self.client)
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
        view = DataDeletionView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=view)


class AdminDashboardView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, client: discord.Client | None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        total_open = feedback_manager.get_feedback_count(status_filter="open")
        total_tickets = feedback_manager.get_feedback_count(status_filter="all")
        tables = feedback_manager.get_all_tables()

        header_text = (
            f"# {OCTICONS_MAP['oct_info']} Admin Database & Feedback Panel\n"
            f"Logged in as Administrator: `{self.user.name}`\n\n"
            f"• **Feedback Tickets:** `{total_tickets}` total (`{total_open}` open)\n"
            f"• **Active Database Tables:** `{len(tables)}` tables"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        admin_options = [
            discord.SelectOption(
                label="Feedback Tickets",
                value="admin_feedback",
                description=f"Inspect and resolve {total_tickets} feedback submissions",
                emoji=OCTICONS_MAP["oct_checklist"]
            ),
            discord.SelectOption(
                label="Data Explorer",
                value="admin_tables",
                description=f"Inspect and manage records across {len(tables)} tables",
                emoji=OCTICONS_MAP["oct_repo"]
            )
        ]

        admin_select = Select(
            custom_id="select_admin_nav",
            placeholder="Select Admin Module...",
            options=admin_options
        )
        admin_select.callback = self._on_nav_selected
        container.add_item(ActionRow(admin_select))

        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back to Dashboard", style=discord.ButtonStyle.primary, custom_id="btn_admin_back_dash")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    async def _on_nav_selected(self, interaction: discord.Interaction):
        if not interaction.data or "values" not in interaction.data or not interaction.data["values"]:
            return
        choice = interaction.data["values"][0]

        if choice == "admin_feedback":
            view = AdminFeedbackBrowserView(user=self.user, guild=self.guild, channel=self.channel, client=self.client, page=0)
            await interaction.response.edit_message(view=view)
        elif choice == "admin_tables":
            view = AdminTableExplorerView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
            await interaction.response.edit_message(view=view)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)


class AdminFeedbackBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, client: discord.Client | None, page: int = 0, status_filter: str = "all"):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self.current_page = page
        self.status_filter = status_filter
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        total_tickets = feedback_manager.get_feedback_count(status_filter=self.status_filter)
        page_size = 4
        total_pages = max(1, (total_tickets + page_size - 1) // page_size)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_text = (
            f"# {OCTICONS_MAP['oct_checklist']} Feedback Tickets ({self.status_filter.capitalize()})\n"
            f"Showing `{total_tickets}` ticket(s) recorded in database.\n"
            f"Click **View** on any ticket to inspect full details, attachments, and update status."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        tickets = feedback_manager.get_all_feedback(
            status_filter=self.status_filter,
            limit=page_size,
            offset=self.current_page * page_size
        )

        if not tickets:
            container.add_item(TextDisplay("*No feedback tickets found matching this filter.*"))
        else:
            for t in tickets:
                t_id = t["id"]
                t_user = t.get("user_name", "Unknown")
                t_uid = t.get("user_id", "0")
                t_type = t.get("feedback_type", "Feedback")
                t_status = t.get("status", "open").upper()
                t_content = t.get("content", "").strip()
                t_created = t.get("created_at", "")

                preview_text = truncate_str(t_content, max_chars=120)
                snippet = f"**Feedback Ticket - #{t_id}**\n`{t_type}` • Submitted by {t_user} (<@{t_uid}>) • Status: `{t_status}`\n> {preview_text}\n-# Created: `{t_created}`"

                view_btn = Button(
                    label="View",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_view_feedback_{t_id}"
                )
                view_btn.callback = self._create_view_callback(t_id)

                section = Section(TextDisplay(snippet), accessory=view_btn)
                container.add_item(section)

        container.add_item(Separator(visible=True))

        row_items = []
        if total_pages > 1:
            prev_btn = Button(label="◀", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), custom_id="btn_fb_prev")
            ind_btn = Button(label=f"{self.current_page + 1} / {total_pages}", style=discord.ButtonStyle.secondary, disabled=True, custom_id="btn_fb_ind")
            next_btn = Button(label="▶", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), custom_id="btn_fb_next")

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

        back_btn = Button(label="◀ Admin Panel", style=discord.ButtonStyle.primary, custom_id="btn_fb_back_admin")
        back_btn.callback = self._on_back_clicked
        row_items.append(back_btn)

        container.add_item(ActionRow(*row_items))
        self.add_item(container)

    def _create_view_callback(self, ticket_id: int):
        async def callback(interaction: discord.Interaction):
            ticket_data = feedback_manager.get_feedback_ticket(ticket_id)
            if not ticket_data:
                await interaction.response.send_message(content="Ticket not found.", ephemeral=True)
                return
            detail_view = AdminFeedbackDetailView(
                ticket_data=ticket_data,
                user=self.user,
                guild=self.guild,
                channel=self.channel,
                client=self.client,
                parent_browser_view=self
            )
            await interaction.response.edit_message(view=detail_view)
        return callback

    async def _on_back_clicked(self, interaction: discord.Interaction):
        view = AdminDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=view)


class AdminFeedbackDetailView(LayoutView):
    def __init__(self, ticket_data: dict[str, Any], user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, client: discord.Client | None, parent_browser_view: Any):
        super().__init__(timeout=600)
        self.ticket = ticket_data
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self.parent_browser_view = parent_browser_view
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        t_id = self.ticket["id"]
        t_user = self.ticket.get("user_name", "Unknown")
        t_uid = self.ticket.get("user_id", "0")
        t_gid = self.ticket.get("guild_id")
        t_cid = self.ticket.get("channel_id")
        t_type = self.ticket.get("feedback_type", "Feedback")
        t_status = self.ticket.get("status", "open").upper()
        t_content = self.ticket.get("content", "").strip()
        t_created = self.ticket.get("created_at", "")
        t_notes = self.ticket.get("admin_notes", "").strip() or "*No admin notes added.*"
        attachments = self.ticket.get("attachments", [])

        location_str = f"Guild ID: `{t_gid or 'DM'}` • Channel ID: `{t_cid or 'DM'}`"
        header_text = (
            f"# {OCTICONS_MAP['oct_checklist']} Feedback Ticket #{t_id}\n"
            f"• **Category:** `{t_type}`\n"
            f"• **Status:** `{t_status}`\n"
            f"• **Submitted by:** {t_user} (<@{t_uid}> • ID: `{t_uid}`)\n"
            f"• **Location:** {location_str}\n"
            f"• **Timestamp:** `{t_created}`"
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        body_block = f"### Content:\n{truncate_str(t_content, max_chars=1800)}"
        container.add_item(TextDisplay(body_block))

        if attachments:
            container.add_item(Separator(visible=True))
            att_lines = ["### Attached Files:"]
            for a in attachments:
                fname = a.get("filename", "Attachment")
                url = a.get("url", "#")
                att_lines.append(f"• [{fname}]({url})")
            container.add_item(TextDisplay("\n".join(att_lines)))

        container.add_item(Separator(visible=True))
        container.add_item(TextDisplay(f"### Admin Notes:\n{truncate_str(t_notes, max_chars=800)}"))
        container.add_item(Separator(visible=True))

        in_review_btn = Button(label="Mark In Review", style=discord.ButtonStyle.secondary, custom_id="btn_stat_review")
        in_review_btn.callback = self._create_status_callback("in_review")

        resolve_btn = Button(label="Resolve", style=discord.ButtonStyle.success, custom_id="btn_stat_resolve")
        resolve_btn.callback = self._create_status_callback("resolved")

        note_btn = Button(label="Edit Note", style=discord.ButtonStyle.secondary, custom_id="btn_stat_note")
        note_btn.callback = self._on_edit_note_clicked

        del_btn = Button(label="Delete", style=discord.ButtonStyle.danger, custom_id="btn_stat_delete")
        del_btn.callback = self._on_delete_clicked

        container.add_item(ActionRow(in_review_btn, resolve_btn, note_btn, del_btn))

        back_btn = Button(label="◀ Back to Tickets", style=discord.ButtonStyle.primary, custom_id="btn_ticket_back")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    def _create_status_callback(self, new_status: str):
        async def callback(interaction: discord.Interaction):
            feedback_manager.update_ticket_status(self.ticket["id"], new_status)
            self.ticket["status"] = new_status
            self._build_layout()
            await interaction.response.edit_message(view=self)
        return callback

    async def _on_edit_note_clicked(self, interaction: discord.Interaction):
        fields = [
            {
                "type": "text_display",
                "content": f"Update internal admin notes for Ticket #{self.ticket['id']}."
            },
            {
                "type": "text_input",
                "custom_id": "admin_notes",
                "label": "Admin Notes",
                "style": "paragraph",
                "value": self.ticket.get("admin_notes", ""),
                "required": False,
                "max_length": 1500
            }
        ]

        async def on_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_note = data.get("admin_notes", "").strip()
            feedback_manager.update_ticket_status(self.ticket["id"], self.ticket.get("status", "open"), admin_notes=new_note)
            self.ticket["admin_notes"] = new_note
            self._build_layout()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title=f"Edit Notes - Ticket #{self.ticket['id']}",
            custom_id=f"modal_edit_note_{self.ticket['id']}",
            fields_schema=fields,
            on_submit_callback=on_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_delete_clicked(self, interaction: discord.Interaction):
        feedback_manager.delete_ticket(self.ticket["id"])
        self.parent_browser_view._build_layout()
        await interaction.response.edit_message(view=self.parent_browser_view)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        self.parent_browser_view._build_layout()
        await interaction.response.edit_message(view=self.parent_browser_view)


class AdminTableExplorerView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, client: discord.Client | None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self.selected_table = "memories"
        self.current_page = 0
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        all_tables = feedback_manager.get_all_tables()
        table_options = []
        for t in all_tables:
            t_name = t["name"]
            t_cnt = t["row_count"]
            table_options.append(
                discord.SelectOption(
                    label=t_name,
                    value=t_name,
                    description=f"{t_cnt} row(s)",
                    default=(t_name == self.selected_table)
                )
            )

        header_text = (
            f"# {OCTICONS_MAP['oct_repo']} Data Explorer\n"
            f"Table: `{self.selected_table}` • Select a table below to browse, add, inspect, or edit records."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        table_select = Select(
            custom_id="select_admin_table",
            placeholder="Choose database table...",
            options=table_options[:25]
        )
        table_select.callback = self._on_table_selected
        container.add_item(ActionRow(table_select))
        container.add_item(Separator(visible=True))

        add_record_btn = Button(
            label="+ Add Record",
            style=discord.ButtonStyle.success,
            custom_id="btn_add_table_row"
        )
        add_record_btn.callback = self._on_add_record_clicked
        container.add_item(ActionRow(add_record_btn))
        container.add_item(Separator(visible=True))

        page_size = 2
        rows = feedback_manager.get_table_rows(
            table_name=self.selected_table,
            limit=page_size,
            offset=self.current_page * page_size
        )
        pk_col = feedback_manager.get_table_pk_column(self.selected_table)

        if not rows:
            container.add_item(TextDisplay(f"*Table `{self.selected_table}` is currently empty.*"))
        else:
            for idx, r in enumerate(rows):
                pk_val = r.get(pk_col, idx + 1)
                lines = [f"**`{self.selected_table}` Record #{pk_val}**"]
                
                visible_items = list(r.items())[:5]
                for col_k, col_v in visible_items:
                    if col_k == "embedding":
                        lines.append(f"• **{col_k}:** `<768-dim float blob>`")
                    else:
                        str_val = truncate_str(col_v, max_chars=55)
                        lines.append(f"• **{col_k}:** {str_val}")

                if len(r) > 5:
                    lines.append(f"-# ... and {len(r) - 5} more columns")

                snippet_text = "\n".join(lines)

                inspect_edit_btn = Button(
                    label="Inspect / Edit ↗",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_open_rec_{pk_val}_{idx}"
                )
                inspect_edit_btn.callback = self._create_open_record_callback(pk_col, pk_val)

                section = Section(TextDisplay(snippet_text), accessory=inspect_edit_btn)
                container.add_item(section)

        container.add_item(Separator(visible=True))

        row_items = []
        prev_btn = Button(label="◀", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), custom_id="btn_tbl_prev")
        ind_btn = Button(label=f"Page {self.current_page + 1}", style=discord.ButtonStyle.secondary, disabled=True, custom_id="btn_tbl_ind")
        next_btn = Button(label="▶", style=discord.ButtonStyle.secondary, disabled=(len(rows) < page_size), custom_id="btn_tbl_next")

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

        back_btn = Button(label="◀ Admin Panel", style=discord.ButtonStyle.primary, custom_id="btn_tbl_back_admin")
        back_btn.callback = self._on_back_clicked
        row_items.append(back_btn)

        container.add_item(ActionRow(*row_items))
        self.add_item(container)

    def _create_open_record_callback(self, pk_col: str, pk_val: Any):
        async def callback(interaction: discord.Interaction):
            row_data = feedback_manager.get_table_row_by_pk(self.selected_table, pk_col, pk_val)
            if not row_data:
                await interaction.response.send_message(content="Record not found.", ephemeral=True)
                return

            editor_view = AdminRecordEditorView(
                user=self.user,
                guild=self.guild,
                channel=self.channel,
                client=self.client,
                selected_table=self.selected_table,
                pk_col=pk_col,
                pk_val=pk_val,
                parent_explorer_view=self,
                current_page=0
            )
            await interaction.response.edit_message(view=editor_view)

        return callback

    async def _on_add_record_clicked(self, interaction: discord.Interaction):
        cols = feedback_manager.get_table_columns_info(self.selected_table)
        fields = [
            {
                "type": "text_display",
                "content": f"Insert a new record into table `{self.selected_table}`."
            }
        ]

        insertable_cols = [c for c in cols if c["name"] not in ("id", "embedding", "created_at", "updated_at", "last_accessed_at", "last_active_at")]
        for col_info in insertable_cols[:4]:
            cname = col_info["name"]
            is_long = "json" in cname or "content" in cname or "prompt" in cname or "text" in cname
            fields.append({
                "type": "text_input",
                "custom_id": f"new_{cname}",
                "label": f"{cname}"[:45],
                "placeholder": f"Enter {cname}...",
                "style": "paragraph" if is_long else "short",
                "required": col_info["notnull"] and col_info["dflt_value"] is None
            })

        async def on_add_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            new_row_dict = {}
            for col_info in insertable_cols[:4]:
                cname = col_info["name"]
                field_key = f"new_{cname}"
                if field_key in data:
                    new_row_dict[cname] = data[field_key]

            if new_row_dict:
                feedback_manager.insert_table_row(self.selected_table, new_row_dict)

            self.current_page = 0
            self._build_layout()
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title=f"Add to `{self.selected_table}`"[:45],
            custom_id=f"modal_add_row_{self.selected_table}",
            fields_schema=fields,
            on_submit_callback=on_add_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_table_selected(self, interaction: discord.Interaction):
        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            self.selected_table = interaction.data["values"][0]
            self.current_page = 0
            self._build_layout()
            await interaction.response.edit_message(view=self)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        view = AdminDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=view)


class AdminRecordEditorView(LayoutView):
    READ_ONLY_COLUMNS = {"id", "embedding", "created_at", "updated_at", "last_accessed_at", "last_active_at"}

    def __init__(
        self,
        user: discord.User | discord.Member,
        guild: discord.Guild | None,
        channel: discord.abc.Messageable | None,
        client: discord.Client | None,
        selected_table: str,
        pk_col: str,
        pk_val: Any,
        parent_explorer_view: AdminTableExplorerView,
        current_page: int = 0
    ):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self.selected_table = selected_table
        self.pk_col = pk_col
        self.pk_val = pk_val
        self.parent_explorer_view = parent_explorer_view
        self.current_page = current_page
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        row_data = feedback_manager.get_table_row_by_pk(self.selected_table, self.pk_col, self.pk_val)
        if not row_data:
            container.add_item(TextDisplay(f"# Record Not Found\nRecord #{self.pk_val} in `{self.selected_table}` no longer exists."))
            back_btn = Button(label=f"◀ Back to `{self.selected_table}`", style=discord.ButtonStyle.primary, custom_id="btn_rec_not_found_back")
            back_btn.callback = self._on_back_clicked
            container.add_item(ActionRow(back_btn))
            self.add_item(container)
            return

        all_cols_info = feedback_manager.get_table_columns_info(self.selected_table)
        total_cols = len(all_cols_info)
        page_size = 4
        total_pages = max(1, (total_cols + page_size - 1) // page_size)
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_text = (
            f"# {OCTICONS_MAP['oct_checklist']} Record Editor: `{self.selected_table}` (PK: {self.pk_val})\n"
            f"Showing fields {self.current_page * page_size + 1}–{min(total_cols, (self.current_page + 1) * page_size)} of `{total_cols}` total fields.\n"
            f"Click **Edit** next to any field to modify it."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        start_idx = self.current_page * page_size
        page_cols = all_cols_info[start_idx:start_idx + page_size]

        for col in page_cols:
            cname = col["name"]
            ctype = col["type"] or "TEXT"
            cval = row_data.get(cname, "")
            is_read_only = cname in self.READ_ONLY_COLUMNS or (cname == self.pk_col and self.pk_col == "id")

            if cname == "embedding":
                val_disp = "<768-dim float vector blob>"
            elif cval is None:
                val_disp = "*NULL*"
            else:
                str_val = str(cval)
                val_disp = truncate_str(str_val, max_chars=180) or "*(empty string)*"

            ro_tag = " `(Read-Only)`" if is_read_only else ""
            field_display = f"**`{cname}`** ({ctype}){ro_tag}\n```{val_disp}```"

            edit_btn = Button(
                label="Edit",
                style=discord.ButtonStyle.secondary,
                disabled=is_read_only,
                custom_id=f"btn_edit_col_{cname}"
            )
            edit_btn.callback = self._create_field_edit_callback(cname, str(cval if cval is not None else ""))

            section = Section(TextDisplay(field_display), accessory=edit_btn)
            container.add_item(section)

        container.add_item(Separator(visible=True))

        row_items = []
        if total_pages > 1:
            prev_btn = Button(label="▲ Prev Fields", style=discord.ButtonStyle.secondary, disabled=(self.current_page == 0), custom_id="btn_field_prev")
            ind_btn = Button(label=f"Fields {self.current_page + 1}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, custom_id="btn_field_ind")
            next_btn = Button(label="▼ Next Fields", style=discord.ButtonStyle.secondary, disabled=(self.current_page >= total_pages - 1), custom_id="btn_field_next")

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

        del_rec_btn = Button(label="Delete Record", style=discord.ButtonStyle.danger, custom_id=f"btn_del_rec_{self.pk_val}")
        del_rec_btn.callback = self._on_delete_record_clicked
        row_items.append(del_rec_btn)

        container.add_item(ActionRow(*row_items))

        back_btn = Button(label=f"◀ Back to `{self.selected_table}`", style=discord.ButtonStyle.primary, custom_id="btn_rec_back_table")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    def _create_field_edit_callback(self, col_name: str, current_value: str):
        async def callback(interaction: discord.Interaction):
            is_long = (
                len(current_value) > 60
                or "json" in col_name
                or "content" in col_name
                or "prompt" in col_name
                or "text" in col_name
                or "notes" in col_name
            )

            fields = [
                {
                    "type": "text_display",
                    "content": f"Editing column **`{col_name}`** in table `{self.selected_table}` (PK: {self.pk_val})."
                },
                {
                    "type": "text_input",
                    "custom_id": f"input_{col_name}",
                    "label": f"{col_name}"[:45],
                    "value": current_value[:4000],
                    "style": "paragraph" if is_long else "short",
                    "required": False,
                    "max_length": 4000
                }
            ]

            async def on_field_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_val = data.get(f"input_{col_name}", "")
                feedback_manager.update_table_row(
                    self.selected_table,
                    self.pk_col,
                    self.pk_val,
                    {col_name: new_val}
                )
                self._build_layout()
                await sub_inter.response.edit_message(view=self)

            modal = DynamicModalV2(
                title=f"Edit `{col_name}`"[:45],
                custom_id=f"modal_edit_field_{col_name}",
                fields_schema=fields,
                on_submit_callback=on_field_submit
            )
            await interaction.response.send_modal(modal)

        return callback

    async def _on_delete_record_clicked(self, interaction: discord.Interaction):
        fields = [
            {
                "type": "text_display",
                "content": (
                    f"{DFM_EMOJI_MAP['gfm_caution']} **WARNING: Delete Record**\n\n"
                    f"This will permanently delete record **#{self.pk_val}** from table `{self.selected_table}`.\n\n"
                    f"To proceed, type **CONFIRM** below."
                )
            },
            {
                "type": "text_input",
                "custom_id": "confirm_code",
                "label": "Confirmation",
                "description": "Type CONFIRM in uppercase to proceed",
                "placeholder": "CONFIRM",
                "style": "short",
                "required": True,
                "max_length": 10
            }
        ]

        async def on_confirm_delete(sub_inter: discord.Interaction, data: dict[str, Any]):
            typed = data.get("confirm_code", "").strip().upper()
            if typed != "CONFIRM":
                await sub_inter.response.send_message(
                    content=f"{DFM_EMOJI_MAP['gfm_caution']} **Deletion Cancelled:** The confirmation did not match `CONFIRM`.",
                    ephemeral=True
                )
                return

            feedback_manager.delete_table_row(self.selected_table, self.pk_col, self.pk_val)
            self.parent_explorer_view._build_layout()
            await sub_inter.response.edit_message(view=self.parent_explorer_view)

        modal = DynamicModalV2(
            title=f"Delete Record #{self.pk_val}"[:45],
            custom_id=f"modal_del_rec_{self.pk_val}",
            fields_schema=fields,
            on_submit_callback=on_confirm_delete
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        self.parent_explorer_view._build_layout()
        await interaction.response.edit_message(view=self.parent_explorer_view)



class MemoriesBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, client: discord.Client | None = None, page: int = 0):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
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
            f"# {OCTICONS_MAP['oct_checklist']} Personal Memories\n"
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
                
                snippet = truncate_str(mem_text, max_chars=180)
                display_content = f"**Fact #{mem_id}:**\n> {snippet}\n-# Added: `{created_raw}`"
                
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
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)


class ServerLoreBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, client: discord.Client | None = None, page: int = 0):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self.current_page = page
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        if not self.guild:
            container.add_item(TextDisplay(f"# {OCTICONS_MAP['oct_repo']} Server Lore\n*Server Lore is only available inside Discord servers.*"))
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
            f"# {OCTICONS_MAP['oct_repo']} Server Lore: {self.guild.name}\n"
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
                
                snippet = truncate_str(lore_text, max_chars=180)
                display_content = f"**Lore #{lore_id}:**\n> {snippet}\n-# Recorded: `{created_raw}`"
                
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
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)


class ConfigsBrowserView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, client: discord.Client | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        u_cfg = config_manager.get_user_config(self.user.id)
        s_cfg = config_manager.get_server_config(self.guild.id) if self.guild else None
        c_cfg = config_manager.get_channel_config(getattr(self.channel, "id", 0)) if self.channel else None

        p_name = u_cfg.get("preferred_name") or "*None (Using Discord display name)*"
        habits = truncate_str(u_cfg.get("special_instructions") or "*None set*", max_chars=120)
        r_level = u_cfg.get("preferred_reasoning_level", "AUTO")
        mem_pol = u_cfg.get("user_memory_policy", "read_write")

        user_block = (
            f"### {OCTICONS_MAP['oct_checklist']} User Persona\n"
            f"• **Preferred Name:** `{p_name}`\n"
            f"• **Personal Context:** {habits}\n"
            f"• **Reasoning Preference:** `{r_level}`\n"
            f"• **Memory Policy:** `{mem_pol}`"
        )

        server_block = ""
        if s_cfg and self.guild:
            s_bio = truncate_str(s_cfg.get("server_bio") or "*Default*", max_chars=100)
            s_prompt = truncate_str(s_cfg.get("system_prompt") or "*None set*", max_chars=100)
            s_ai_chans = s_cfg.get("ai_channels", [])
            s_ai_str = ", ".join([f"<#{cid}>" for cid in s_ai_chans]) if s_ai_chans else "*None designated*"
            s_access = s_cfg.get("access_behavior", "blacklist").capitalize()

            server_block = (
                f"\n\n### {OCTICONS_MAP['oct_repo']} Server Configs ({self.guild.name})\n"
                f"• **Server Bio:** {s_bio}\n"
                f"• **Server Prompt:** {s_prompt}\n"
                f"• **AI Channels:** {s_ai_str}\n"
                f"• **Access Policy:** `{s_access}`"
            )

        channel_block = ""
        if c_cfg and self.channel and hasattr(self.channel, "name"):
            c_prompt = truncate_str(c_cfg.get("system_prompt") or "*Inherits Server Prompt*", max_chars=100)
            channel_block = (
                f"\n\n### {OCTICONS_MAP['oct_terminal']} Channel Directives (#{self.channel.name})\n"
                f"• **Prompt Override:** {c_prompt}"
            )

        full_config_str = truncate_str(f"# Configs\n{user_block}{server_block}{channel_block}", max_chars=3200)
        container.add_item(TextDisplay(full_config_str))
        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_cfg_back_dash")
        back_btn.callback = self._on_back_clicked
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)


class SearchResultsView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None, query: str, client: discord.Client | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.query = query
        self.client = client

    async def perform_search(self):
        self.clear_items()
        container = Container()

        recalled = await memory_manager.recall_relevant_memories(
            query=self.query,
            user_id=self.user.id,
            guild_id=self.guild.id if self.guild else None,
            top_k=4
        )

        user_mems = recalled.get("user_memories", [])
        server_lore = recalled.get("server_lore", [])
        total_found = len(user_mems) + len(server_lore)

        header_text = (
            f"# {OCTICONS_MAP['oct_search']} Search: \"{self.query}\"\n"
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
                snippet = truncate_str(mem_text, max_chars=180)

                edit_btn = Button(label="Edit", style=discord.ButtonStyle.secondary, custom_id=f"btn_s_edit_mem_{mem_id}")
                edit_btn.callback = self._create_mem_edit_callback(mem_id, mem_text)

                section = Section(
                    TextDisplay(f"{OCTICONS_MAP['oct_checklist']} **User Memory #{mem_id}:**\n> {snippet}"),
                    accessory=edit_btn
                )
                container.add_item(section)

            for lore in server_lore:
                lore_id = lore["id"]
                lore_text = lore["text"]
                snippet = truncate_str(lore_text, max_chars=180)

                section = Section(
                    TextDisplay(f"{OCTICONS_MAP['oct_repo']} **Server Lore #{lore_id}:**\n> {snippet}")
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
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)


class DataDeletionView(LayoutView):
    def __init__(self, user: discord.User | discord.Member, guild: discord.Guild | None, channel: discord.abc.Messageable | None = None, client: discord.Client | None = None):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.channel = channel
        self.client = client
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        is_admin = False
        if self.guild and isinstance(self.user, discord.Member):
            is_admin = self.user.guild_permissions.administrator or (self.guild.owner_id == self.user.id)

        header_text = (
            f"# {DFM_EMOJI_MAP['gfm_warning']} Delete Data\n"
            "Choose what you want to remove. A confirmation prompt will appear before deleting."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        delete_options = [
            discord.SelectOption(
                label="Delete Memories",
                value="del_memories",
                description="Deletes all personal memory facts and saved preferences",
                emoji=OCTICONS_MAP["oct_checklist"]
            ),
            discord.SelectOption(
                label="Delete Configs",
                value="del_configs",
                description="Resets preferred name, custom instructions, and persona",
                emoji=OCTICONS_MAP["oct_terminal"]
            ),
            discord.SelectOption(
                label="Delete Everything",
                value="del_everything",
                description="Deletes all your memories, configs, and chat history",
                emoji=DFM_EMOJI_MAP["gfm_caution"]
            )
        ]

        if is_admin and self.guild:
            delete_options.append(
                discord.SelectOption(
                    label="Delete Server Lore",
                    value="del_server_lore",
                    description="Deletes all server lore recorded for this guild",
                    emoji=OCTICONS_MAP["oct_repo"]
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

        warnings_map = {
            "del_memories": (
                "Delete Memories",
                f"{DFM_EMOJI_MAP['gfm_warning']} **WARNING: Irreversible Action**\n\n"
                "This will permanently delete all personal memories, facts, and conversation preferences saved for your account.\n\n"
                "To proceed, type **CONFIRM** below."
            ),
            "del_configs": (
                "Reset Configs",
                f"{DFM_EMOJI_MAP['gfm_warning']} **WARNING: Irreversible Action**\n\n"
                "This will reset your custom persona, preferred name, Git credentials, and reasoning settings back to defaults.\n\n"
                "To proceed, type **CONFIRM** below."
            ),
            "del_everything": (
                "Full Account Data Purge",
                f"{DFM_EMOJI_MAP['gfm_caution']} **CRITICAL WARNING: Irreversible Action**\n\n"
                "This will permanently delete ALL data associated with your account:\n"
                "• All personal memories and preferences\n"
                "• All user persona configurations and Git credentials\n"
                "• All multi-turn chat session logs\n"
                "• All message generation histories\n\n"
                "To proceed, type **CONFIRM** below."
            ),
            "del_server_lore": (
                "Delete Server Lore",
                f"{DFM_EMOJI_MAP['gfm_warning']} **WARNING: Irreversible Action**\n\n"
                f"This will permanently delete all shared server lore and project facts recorded for **{self.guild.name if self.guild else 'this server'}**.\n\n"
                "To proceed, type **CONFIRM** below."
            )
        }

        modal_title, warning_body = warnings_map.get(choice, ("Confirm Deletion", "Type CONFIRM below to execute deletion."))

        fields = [
            {
                "type": "text_display",
                "content": warning_body
            },
            {
                "type": "text_input",
                "custom_id": "confirm_code",
                "label": "Confirmation",
                "description": "Type CONFIRM in uppercase to proceed",
                "placeholder": "CONFIRM",
                "style": "short",
                "required": True,
                "max_length": 10
            }
        ]

        async def on_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            typed_confirm = data.get("confirm_code", "").strip().upper()
            if typed_confirm != "CONFIRM":
                await sub_inter.response.send_message(
                    content=f"{DFM_EMOJI_MAP['gfm_caution']} **Deletion Cancelled:** The confirmation text did not match `CONFIRM`. No data was deleted.",
                    ephemeral=True
                )
                return

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
            container.add_item(TextDisplay(f"# {OCTICONS_MAP['oct_check']} Deleted\n{msg}"))
            container.add_item(Separator(visible=True))

            back_btn = Button(label="◀ Back", style=discord.ButtonStyle.primary, custom_id="btn_del_done_back")
            back_btn.callback = self._on_back_clicked
            container.add_item(ActionRow(back_btn))

            self.clear_items()
            self.add_item(container)
            await sub_inter.response.edit_message(view=self)

        modal = DynamicModalV2(
            title=f"{modal_title}"[:45],
            custom_id=f"modal_confirm_delete_{choice}",
            fields_schema=fields,
            on_submit_callback=on_modal_submit
        )
        await interaction.response.send_modal(modal)

    async def _on_back_clicked(self, interaction: discord.Interaction):
        dash = DatabaseDashboardView(user=self.user, guild=self.guild, channel=self.channel, client=self.client)
        await interaction.response.edit_message(view=dash)