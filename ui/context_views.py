import io
import re
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
    View
)
from core.branch_manager import branch_manager

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


def build_version_switcher_view(
    message_id: str | int,
    active_idx: int,
    total_versions: int,
    thought_duration: int = 0,
    has_thoughts: bool = False,
    extra_action_view: ui.View | None = None
) -> ui.View | None:
    view = View(timeout=None)

    if has_thoughts:
        time_str = f"{thought_duration}s" if thought_duration > 0 else "<1s"
        t_btn = Button(
            label=f"🧠 Thought for {time_str}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gen_thought_{message_id}_{active_idx}",
            row=0
        )
        view.add_item(t_btn)

    if extra_action_view and extra_action_view.children:
        start_row = 1 if has_thoughts else 0
        max_row = 3 if total_versions >= 2 else 4
        current_row = start_row
        buttons_in_current_row = 0

        for item in extra_action_view.children:
            is_select = isinstance(
                item,
                (ui.Select, ui.UserSelect, ui.RoleSelect, ui.ChannelSelect, ui.MentionableSelect)
            )

            if is_select:
                if buttons_in_current_row > 0:
                    current_row += 1
                    buttons_in_current_row = 0

                if current_row > max_row:
                    logger.warning(f"Component {item} exceeded available view rows (max {max_row}). Skipping.")
                    continue

                item.row = current_row
                view.add_item(item)
                current_row += 1
            else:
                if buttons_in_current_row >= 5:
                    current_row += 1
                    buttons_in_current_row = 0

                if current_row > max_row:
                    logger.warning(f"Component {item} exceeded available view rows (max {max_row}). Skipping.")
                    continue

                item.row = current_row
                view.add_item(item)
                buttons_in_current_row += 1

    if total_versions >= 2:
        prev_btn = Button(
            label="◀",
            style=discord.ButtonStyle.secondary,
            disabled=(active_idx <= 1),
            custom_id=f"gen_prev_{message_id}",
            row=4
        )
        indicator_btn = Button(
            label=f"{active_idx} / {total_versions}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            custom_id=f"gen_ind_{message_id}",
            row=4
        )
        next_btn = Button(
            label="▶",
            style=discord.ButtonStyle.secondary,
            disabled=(active_idx >= total_versions),
            custom_id=f"gen_next_{message_id}",
            row=4
        )
        view.add_item(prev_btn)
        view.add_item(indicator_btn)
        view.add_item(next_btn)

    if len(view.children) == 0:
        return None

    return view


class BranchHeaderView(View):
    def __init__(self, branch_id: str):
        super().__init__(timeout=None)
        self.branch_id = branch_id

        view_msgs_btn = Button(
            label="View Stored Messages",
            style=discord.ButtonStyle.secondary,
            custom_id=f"branch_view_{branch_id}"
        )
        del_branch_btn = Button(
            label="Delete Branch",
            style=discord.ButtonStyle.danger,
            custom_id=f"branch_del_{branch_id}"
        )
        self.add_item(view_msgs_btn)
        self.add_item(del_branch_btn)


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
            content = msg.get("content", "").strip() or "*No text content*"
            formatted_content = format_transcript_message(content, max_chars=380)

            entry = {
                "global_idx": idx,
                "author": author,
                "content": formatted_content,
                "timestamp": msg.get("timestamp", "")
            }

            entry_len = len(formatted_content) + len(author) + 50

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
            container.add_item(TextDisplay("# Branch Inactive\nThis branch has been deleted or is not found in database."))
            self.add_item(container)
            return

        messages = branch.get("messages", [])
        total_msgs = len(messages)
        pages = self._build_pages(messages)
        total_pages = max(1, len(pages))
        self.current_page = max(0, min(self.current_page, total_pages - 1))

        header_text = (
            f"# Branch Transcript: {branch.get('title', 'Discussion')}\n"
            f"Stored Messages: `{total_msgs}` • Preserved through channel deletions.\n"
            f"Click **Delete** next to any message to prune it from the AI's branch context."
        )
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        if not pages:
            container.add_item(TextDisplay("*No stored messages in this branch yet.*"))
        else:
            current_page_entries = pages[self.current_page]

            for entry in current_page_entries:
                global_idx = entry["global_idx"]
                author = entry["author"]
                content = entry["content"]

                formatted_lines = "\n".join([f"> {line}" for line in content.split("\n")])
                display_text = f"**{author}:**\n{formatted_lines}"

                del_btn = Button(
                    label="Delete",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"branch_prune_{self.branch_id}_{global_idx}"
                )
                section = Section(TextDisplay(display_text), accessory=del_btn)
                container.add_item(section)

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