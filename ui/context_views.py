import io
import re
import json
import base64
import logging
from typing import Any, Callable
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
    Select,
    View
)
from core.branch_manager import branch_manager
from agent.constants import OCTICONS_MAP
from config.settings import THINKING_EMOJI
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.ContextViews")

def format_transcript_message(content: str, max_chars: int = 380) -> str:
    content = content.strip()
    if len(content) <= max_chars:
        return content

    split_idx = content.rfind("\n\n", 0, max_chars)
    if split_idx == -1:
        split_idx = content.rfind("\n", 0, max_chars)
    if split_idx == -1:
        split_idx = content.rfind(". ", 0, max_chars)
    if split_idx == -1 or split_idx < 120:
        split_idx = max_chars

    chunk = content[:split_idx].rstrip()

    fences = re.findall(r"```([a-zA-Z0-9_+-]*)", chunk)
    if len(fences) % 2 != 0:
        chunk += "\n```"

    return chunk + " *(continued...)*"


def build_branch_settings_modal(branch: dict[str, Any], on_submit: Any, guild: discord.Guild | None = None) -> DynamicModalV2:
    branch_id = branch.get("branch_id", "")
    current_title = branch.get("title", "Branch Discussion")
    collabs = branch.get("collaborators", [])
    auto_reply = branch.get("auto_reply", 1)

    defaults = []
    for c_id in collabs:
        defaults.append({"id": str(c_id), "type": "user"})

    fields = [
        {
            "type": "text_display",
            "content": f"# Branch Settings ({current_title})\nCustomize branch title, auto-reply policy, and authorized collaborators."
        },
        {
            "type": "text_input",
            "custom_id": "branch_title",
            "label": "Branch & Thread Name",
            "placeholder": "Enter branch name...",
            "value": current_title,
            "style": "short",
            "required": True,
            "max_length": 60
        },
        {
            "type": "radio_group",
            "custom_id": "auto_reply",
            "label": "Auto-Reply Mode",
            "description": "Control whether PriestyAI responds to all messages in this thread",
            "value": str(auto_reply),
            "options": [
                {
                    "label": "Auto-Reply to all messages",
                    "value": "1",
                    "description": "PriestyAI responds automatically without needing an @mention",
                    "default": (int(auto_reply) == 1)
                },
                {
                    "label": "Only when @mentioned",
                    "value": "0",
                    "description": "PriestyAI only responds when explicitly tagged or replied to",
                    "default": (int(auto_reply) == 0)
                }
            ],
            "required": True
        },
        {
            "type": "user_select",
            "custom_id": "collaborators",
            "label": "Branch Collaborators",
            "description": "Users authorized to manage settings, edit history, and chat in this branch",
            "placeholder": "Select collaborators...",
            "required": False,
            "min_values": 0,
            "max_values": 25,
            "default_values": defaults
        }
    ]

    return DynamicModalV2(
        title="Branch Settings",
        custom_id=f"modal_branch_settings_{branch_id}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_branch_message_edit_modal(
    branch_id: str,
    msg_idx: int,
    msg_data: dict[str, Any],
    on_submit: Any,
    guild: discord.Guild | None = None
) -> DynamicModalV2:
    role = msg_data.get("role", "user")
    is_user_role = (role != "assistant")
    current_author_id = msg_data.get("author_id", "")
    current_content = msg_data.get("content", "")

    fields = [
        {
            "type": "text_display",
            "content": f"# Edit Captured Message #{msg_idx + 1}\nModify message text, author attribution, or upload attachments."
        }
    ]

    if is_user_role:
        default_author = [{"id": str(current_author_id), "type": "user"}] if (current_author_id and str(current_author_id) != "0") else None
        fields.append({
            "type": "user_select",
            "custom_id": "author_id",
            "label": "Author Attribution",
            "description": "Select the member this message is attributed to",
            "placeholder": "Select author...",
            "required": False,
            "min_values": 0,
            "max_values": 1,
            "default_values": default_author
        })
    else:
        fields.append({
            "type": "text_display",
            "content": "• **Author:** `PriestyAI` *(AI responses cannot be attributed to users)*"
        })

    fields.append({
        "type": "text_input",
        "custom_id": "content",
        "label": "Message Content",
        "placeholder": "Enter message text...",
        "value": current_content,
        "style": "paragraph",
        "required": True,
        "max_length": 3500
    })

    fields.append({
        "type": "file_upload",
        "custom_id": "attachments",
        "label": "Attachments (Cap of 10)",
        "description": "Upload files or leave blank to clear attachments",
        "required": False,
        "max_values": 10
    })

    return DynamicModalV2(
        title=f"Edit Message #{msg_idx + 1}",
        custom_id=f"modal_branch_edit_msg_{branch_id}_{msg_idx}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_branch_version_picker_modal(
    message_id: str | int,
    versions: list[dict[str, Any]],
    on_submit: Any
) -> DynamicModalV2:
    options = []
    total_v = len(versions)
    for v_idx, v_data in enumerate(versions):
        v_num = v_idx + 1
        dur = max(1, v_data.get("duration_seconds", 1))
        content_preview = v_data.get("content", "")[:60].replace("\n", " ").strip() or "Snapshot"
        is_active = (v_num == total_v)
        active_label = " (Latest)" if is_active else ""
        options.append({
            "label": f"Version {v_num}{active_label}",
            "value": str(v_num),
            "description": f"{content_preview} ({dur}s)",
            "default": is_active
        })

    fields = [
        {
            "type": "text_display",
            "content": (
                "# Branch from Version Snapshot\n"
                "This response has multiple versions. Select which version snapshot to fork into your new branch:"
            )
        },
        {
            "type": "string_select",
            "custom_id": "chosen_version",
            "label": "Snapshot Version",
            "description": "Choose the version to branch from",
            "options": options[:25],
            "required": True
        }
    ]

    return DynamicModalV2(
        title="Select Version to Branch",
        custom_id=f"modal_branch_vpick_{message_id}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


class BranchHeaderView(LayoutView):
    def __init__(self, branch_id: str):
        super().__init__(timeout=None)
        self.branch_id = branch_id
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        branch = branch_manager.get_branch_by_id(self.branch_id)
        if not branch:
            container.add_item(TextDisplay(f"# {OCTICONS_MAP['oct_branch']} Branch Inactive\nThis branch has been archived or removed."))
            self.add_item(container)
            return

        title = branch.get("title", "Branch Discussion")
        creator_id = branch.get("creator_id", "0")
        parent_thread_id = branch.get("parent_thread_id", "")
        origin_channel_id = branch.get("origin_channel_id", "")
        collabs = branch.get("collaborators", [])
        auto_reply = branch.get("auto_reply", 1)
        total_msgs = len(branch.get("messages", []))

        auto_reply_str = "Auto-Reply: Enabled" if int(auto_reply) == 1 else "Auto-Reply: Mention-Only"
        collab_mentions = ", ".join([f"<@{c}>" for c in collabs[:6]]) or f"<@{creator_id}>"
        if len(collabs) > 6:
            collab_mentions += f" and {len(collabs) - 6} more"

        header_lines = [f"# {OCTICONS_MAP['oct_branch']} {title}"]
        if parent_thread_id:
            header_lines.append(f"Forked from <#{parent_thread_id}>")
        elif origin_channel_id:
            header_lines.append(f"Captured from <#{origin_channel_id}>")

        header_lines.append(f"• **Collaborators:** {collab_mentions}")
        header_lines.append(f"• **Settings:** `{auto_reply_str}` • `{total_msgs}` messages in history")

        container.add_item(TextDisplay("\n".join(header_lines)))
        container.add_item(Separator(visible=True))

        history_btn = Button(
            label="Message History ↗",
            style=discord.ButtonStyle.secondary,
            emoji=OCTICONS_MAP["oct_history"],
            custom_id=f"branch_view_{self.branch_id}"
        )
        settings_btn = Button(
            label="Branch Settings",
            style=discord.ButtonStyle.secondary,
            emoji=OCTICONS_MAP["oct_pencil"],
            custom_id=f"branch_settings_{self.branch_id}"
        )
        export_btn = Button(
            label="Export",
            style=discord.ButtonStyle.secondary,
            emoji=OCTICONS_MAP["oct_rocket"],
            custom_id=f"branch_export_{self.branch_id}"
        )
        del_branch_btn = Button(
            label="Delete Branch",
            style=discord.ButtonStyle.danger,
            emoji=OCTICONS_MAP["oct_trash"],
            custom_id=f"branch_del_{self.branch_id}"
        )

        container.add_item(ActionRow(history_btn, settings_btn, export_btn, del_branch_btn))
        self.add_item(container)


class BranchTranscriptView(LayoutView):
    def __init__(self, branch_id: str, page: int = 0):
        super().__init__(timeout=600)
        self.branch_id = branch_id
        self.current_page = page
        self._build_layout()

    def _build_pages(self, messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        if not messages:
            return []

        pages = []
        current_page = []
        current_char_count = 0

        for idx, msg in enumerate(messages):
            author = msg.get("author", "User")
            author_id = msg.get("author_id", "0")
            role = msg.get("role", "user")
            content = msg.get("content", "").strip() or "*No text content*"
            formatted_content = format_transcript_message(content, max_chars=380)
            attachments = msg.get("attachments", [])

            entry = {
                "global_idx": idx,
                "author": author,
                "author_id": author_id,
                "role": role,
                "content": formatted_content,
                "attachments": attachments,
                "timestamp": msg.get("timestamp", "")
            }

            entry_len = len(formatted_content) + len(author) + 60

            if (current_char_count + entry_len > 1300 or len(current_page) >= 4) and current_page:
                pages.append(current_page)
                current_page = [entry]
                current_char_count = entry_len
            else:
                current_page.append(entry)
                current_char_count += entry_len

        if current_page:
            pages.append(current_page)

        return pages

    def _build_layout(self):
        self.clear_items()
        container = Container()

        branch = branch_manager.get_branch_by_id(self.branch_id)
        if not branch:
            container.add_item(TextDisplay(f"# {OCTICONS_MAP['oct_branch']} Branch Inactive\nThis branch record was not found in the database."))
            self.add_item(container)
            return

        messages = branch.get("messages", [])
        total_msgs = len(messages)
        pages = self._build_pages(messages)
        total_pages = max(1, len(pages))
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_lines = [
            f"# {OCTICONS_MAP['oct_history']} Message History: {branch.get('title', 'Branch')}",
            f"Stored Messages: `{total_msgs}` • Preserved across channel deletions.\n",
            "Click **Edit** to modify text/attribution or select messages below to bulk-prune."
        ]
        container.add_item(TextDisplay("\n".join(header_lines)))
        container.add_item(Separator(visible=True))

        if not pages:
            container.add_item(TextDisplay("*No stored messages in this branch history yet.*"))
        else:
            current_page_entries = pages[self.current_page]

            for entry in current_page_entries:
                global_idx = entry["global_idx"]
                author = entry["author"]
                author_id = entry["author_id"]
                role = entry["role"]
                content = entry["content"]
                attachments = entry.get("attachments", [])

                icon = THINKING_EMOJI if role == "assistant" else OCTICONS_MAP["oct_person"]
                author_tag = f"<@{author_id}>" if (author_id and str(author_id) != "0" and role == "user") else author

                formatted_lines = "\n".join([f"> {line}" for line in content.split("\n")])
                att_note = f"\n> -# 📎 `{len(attachments)} attachment(s)`" if attachments else ""
                display_text = f"{icon} **{author_tag}** (Msg #{global_idx + 1}):\n{formatted_lines}{att_note}"

                edit_btn = Button(
                    label="Edit",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"branch_edit_msg_{self.branch_id}_{global_idx}"
                )
                section = Section(TextDisplay(display_text), accessory=edit_btn)
                container.add_item(section)

            if len(messages) > 1:
                container.add_item(Separator(visible=True))
                prune_options = []
                for entry in current_page_entries:
                    g_idx = entry["global_idx"]
                    auth_name = entry["author"]
                    snip = entry["content"][:40].replace("\n", " ").strip()
                    prune_options.append(
                        discord.SelectOption(
                            label=f"Delete Msg #{g_idx + 1} ({auth_name})",
                            value=str(g_idx),
                            description=snip,
                            emoji=OCTICONS_MAP["oct_trash"]
                        )
                    )

                bulk_select = Select(
                    custom_id=f"branch_bulk_prune_{self.branch_id}",
                    placeholder="Select messages to delete from history...",
                    options=prune_options[:25],
                    min_values=1,
                    max_values=len(prune_options)
                )
                container.add_item(ActionRow(bulk_select))

        child_forks = branch_manager.get_child_forks(self.branch_id)
        if child_forks:
            container.add_item(Separator(visible=True))
            fork_lines = ["**Active Downstream Forks:**"]
            for f in child_forks[:5]:
                f_th = f.get("thread_id")
                f_title = f.get("title", "Fork")
                fork_lines.append(f"• {OCTICONS_MAP['oct_branch']} <#{f_th}> — `{f_title}`")
            container.add_item(TextDisplay("\n".join(fork_lines)))

        if total_pages > 1:
            container.add_item(Separator(visible=True))
            prev_btn = Button(
                label="◀",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page == 0),
                custom_id="btn_tr_prev"
            )
            ind_btn = Button(
                label=f"Page {self.current_page + 1} / {total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="btn_tr_ind"
            )
            next_btn = Button(
                label="▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                custom_id="btn_tr_next"
            )

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
            container.add_item(ActionRow(prev_btn, ind_btn, next_btn))

        self.add_item(container)