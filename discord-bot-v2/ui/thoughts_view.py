import time
import re
import logging
from typing import Dict, List, Optional, Tuple, Any
import discord

import config

logger = logging.getLogger("PriestyAI.ThoughtsView")

THOUGHT_SESSIONS: Dict[str, "ThoughtSession"] = {}


def format_thought_content(raw_text: str) -> str:
    text = raw_text.strip()
    if not text:
        return "*Reasoning in progress or no internal thoughts recorded yet.*"

    text = re.sub(r'\$\\text\{([^}]+)\}\$', r'**\1**', text)
    text = re.sub(r'\$\\mathbf\{([^}]+)\}\$', r'**\1**', text)
    text = re.sub(r'(?m)^\s{4,}', '', text)
    text = re.sub(r'(?m)^\s*\*\s*$', '', text)
    text = re.sub(r'(?i)\bDraft (\d+):\s*', r'\n* **Option \1:** ', text)

    patterns = [
        (r'(?i)\bcontext:\s*', '\n\n**Context & Intent**\n'),
        (r'(?i)\brole:\s*', '\n\n**Persona & Role**\n'),
        (r'(?i)\btone:\s*', '\n\n**Tone Strategy**\n'),
        (r'(?i)\bgoal:\s*', '\n\n**Objective**\n'),
        (r'(?i)\bserver:\s*', '\n**Server:** '),
        (r'(?i)\bchannel:\s*', '\n**Channel:** ')
    ]

    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)

    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


class ThoughtSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.chronological_stream: str = ""
        self.elapsed_seconds: float = 0.0
        self.tool_count: int = 0
        self.is_complete: bool = False
        self.created_at = time.time()
        self.active_listeners: List[Tuple[discord.Interaction, "ThoughtsPagingView"]] = []

    def append_thought(self, text_chunk: str):
        if text_chunk:
            self.chronological_stream += text_chunk

    def append_tool_event(self, tool_name: str, args: Dict[str, Any], result: Dict[str, Any]):
        self.tool_count += 1
        event_md = "\n\n"

        if tool_name == "search_web":
            query = args.get("query", "")
            results = result.get("results", [])
            count = len(results)
            event_md += f"> 🔍 **Used Web Search** (`{query}`) — *{count} results*\n"
            for item in results[:3]:
                title = item.get("title", "Link")
                url = item.get("url", "#")
                snippet = item.get("snippet", "")[:100]
                event_md += f"> • [{title}]({url})\n>   -# {snippet}...\n"
        elif tool_name == "execute_code":
            lang = args.get("language", "python")
            stdout = result.get("stdout") or "*(No output)*"
            event_md += f"> 💻 **Executed {lang.capitalize()} Sandbox**\n> ```\n> {stdout[:300]}\n> ```\n"
        elif tool_name == "generate_image":
            prompt = args.get("prompt", "")
            url = result.get("image_url", "#")
            event_md += f"> 🎨 **Generated Image** (`{prompt}`) — [Open Image]({url})\n"
        elif tool_name == "react":
            emoji = args.get("emoji", "")
            event_md += f"> 🎭 **Reacted** {emoji}\n"
        elif tool_name == "ask_expert":
            event_md += f"> 🧠 **Escalated to Gemini 3.7 Expert**\n"
        else:
            event_md += f"> 🛠️ **Used {tool_name}**\n"

        event_md += "\n"
        self.chronological_stream += event_md

    def get_pages(self) -> List[str]:
        formatted = format_thought_content(self.chronological_stream)
        full_content = f"**Reasoning Breakdown**\n\n{formatted}"

        pages = []
        lines = full_content.split("\n")
        current_page = ""

        for line in lines:
            if len(current_page) + len(line) + 1 > 1400:
                if current_page.strip():
                    pages.append(current_page.strip())
                current_page = line
            else:
                current_page = f"{current_page}\n{line}" if current_page else line

        if current_page.strip():
            pages.append(current_page.strip())

        return pages or ["*Reasoning in progress or no internal thoughts recorded yet.*"]


class ThoughtsPagingView(discord.ui.LayoutView):
    def __init__(self, session: ThoughtSession, current_page: int = 0):
        super().__init__(timeout=180)
        self.session = session
        self.current_page = current_page

        self.container = discord.ui.Container()
        self.header_display = discord.ui.TextDisplay("")
        self.body_display = discord.ui.TextDisplay("")
        self.separator_top = discord.ui.Separator()
        self.separator_bottom = discord.ui.Separator()

        self.prev_btn = discord.ui.Button(
            label="◀ Prev",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_prev"
        )
        self.prev_btn.callback = self.prev_page

        self.page_btn = discord.ui.Button(
            label="Page 1 / 1",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            custom_id="btn_page"
        )

        self.next_btn = discord.ui.Button(
            label="Next ▶",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_next"
        )
        self.next_btn.callback = self.next_page

        self.action_row = discord.ui.ActionRow()
        self.action_row.add_item(self.prev_btn)
        self.action_row.add_item(self.page_btn)
        self.action_row.add_item(self.next_btn)

        self.container.add_item(self.header_display)
        self.container.add_item(self.separator_top)
        self.container.add_item(self.body_display)
        self.container.add_item(self.separator_bottom)
        self.container.add_item(self.action_row)

        self.add_item(self.container)
        self.update_content()

    def update_content(self):
        pages = self.session.get_pages()
        self.current_page = max(0, min(self.current_page, len(pages) - 1))
        page_content = pages[self.current_page]

        tool_count = self.session.tool_count
        tool_str = "1 Tool" if tool_count == 1 else f"{tool_count} Tools"
        verb = "Took" if self.session.is_complete else "Thinking for"
        header = f"{config.THINKING_EMOJI} **Thoughts** — {verb} {self.session.elapsed_seconds:.1f}s • Used {tool_str}"

        self.header_display.content = header
        self.body_display.content = page_content

        self.prev_btn.disabled = (self.current_page <= 0)
        self.next_btn.disabled = (self.current_page >= len(pages) - 1)
        self.page_btn.label = f"Page {self.current_page + 1} / {len(pages)}"

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
            self.update_content()
            await interaction.edit_original_response(content=None, view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        pages = self.session.get_pages()
        if self.current_page < len(pages) - 1:
            self.current_page += 1
            self.update_content()
            await interaction.edit_original_response(content=None, view=self)


class PersistentThinkingButtonView(discord.ui.View):
    def __init__(self, session_id: str, initial_label: str = "Thinking for 0.0s"):
        super().__init__(timeout=None)
        self.session_id = session_id

        self.thought_button = discord.ui.Button(
            label=initial_label,
            style=discord.ButtonStyle.secondary,
            custom_id=f"inspect_thoughts:{session_id}",
            emoji=config.THINKING_EMOJI
        )
        self.thought_button.callback = self.on_button_click
        self.add_item(self.thought_button)

    def set_label(self, label: str):
        self.thought_button.label = label

    async def on_button_click(self, interaction: discord.Interaction):
        session = THOUGHT_SESSIONS.get(self.session_id)
        if not session:
            await interaction.response.send_message(
                "*(Reasoning trace session expired or was generated prior to bot restart.)*",
                ephemeral=True
            )
            return

        paging_view = ThoughtsPagingView(session=session, current_page=0)
        session.active_listeners.append((interaction, paging_view))

        await interaction.response.send_message(
            view=paging_view,
            ephemeral=True
        )