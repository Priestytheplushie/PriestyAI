import logging
from typing import Any, Callable
import discord
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
from core.thought_stream import standardize_thoughts_text

logger = logging.getLogger("PriestyAI.ThoughtUI")

TOOL_META_MAP = {
    "recall_memories": ("🧠", "Recalled Memories"),
    "remember": ("💾", "Memory Saved"),
    "forget": ("🧹", "Memory Forgotten"),
    "search_memories": ("🧠", "Memory Search"),
    "react": ("✨", "Message Reaction"),
    "execute_code": ("💻", "Code Sandbox"),
    "search_web": ("🔍", "Web Search"),
    "read_link": ("📄", "Article Reader"),
    "ask_expert": ("🧠", "Deep Reasoning"),
    "generate_image": ("🎨", "Image Studio"),
    "create_thread": ("🧵", "Thread Created"),
    "get_user_profile": ("👤", "User Profile"),
    "get_server_info": ("🏰", "Server Details"),
    "add_modal": ("📋", "Interactive Form"),
    "add_component": ("🔘", "Interactive Button"),
    "send_message": ("💬", "Sent Message"),
    "read_message_history": ("📜", "Chat History"),
    "search_channel_history": ("🔎", "Channel Search"),
    "clear_conversation": ("🧹", "Context Reset")
}

def format_tool_display_text(tool_name: str, args: dict[str, Any], result: dict[str, Any], duration_ms: int) -> str:
    icon, name_clean = TOOL_META_MAP.get(tool_name, ("⚙️", tool_name.replace("_", " ").title()))
    time_tag = f"`{duration_ms}ms`" if duration_ms > 0 else ""

    if tool_name in ["recall_memories", "search_memories"]:
        count = args.get("count") or len(result.get("user_memories", []) + result.get("server_lore", []))
        return f"🧠 Recalled **{count}** memories".strip()
    elif tool_name == "remember":
        return f"💾 **PriestyAI will remember that.** {time_tag}".strip()
    elif tool_name == "forget":
        return f"🧹 **PriestyAI will forget that.** {time_tag}".strip()
    elif tool_name == "react":
        em = args.get("emoji") or (result.get("emoji") if isinstance(result, dict) else None) or "🎲"
        return f"{icon} Reacted with **{em}** {time_tag}".strip()
    elif tool_name == "execute_code":
        lang = args.get("language", "code")
        return f"{icon} Executed **Code Sandbox** (`{lang}` • {time_tag})".strip()
    elif tool_name == "search_web":
        q = args.get("query", "")[:30]
        return f"{icon} Searched **Web** (`{q}`) {time_tag}".strip()
    elif tool_name == "read_link":
        u = args.get("url", "")[:35]
        return f"{icon} Read **Article** (`{u}`) {time_tag}".strip()
    elif tool_name == "ask_expert":
        q = args.get("question", "")[:30]
        return f"{icon} Consulted **Expert** (`{q}`) {time_tag}".strip()
    elif tool_name == "generate_image":
        return f"{icon} Generated **Image** {time_tag}".strip()
    elif tool_name == "add_modal":
        t = args.get("title", "Form")[:25]
        return f"{icon} Staged **Form** (`{t}`)".strip()

    return f"{icon} Used **{name_clean}** {time_tag}".strip()


class ToolInspectorView(LayoutView):
    def __init__(self, tool_call: dict[str, Any], back_callback: Callable):
        super().__init__(timeout=300)
        self.tool_call = tool_call
        self.back_callback = back_callback
        self._build_layout()

    def _build_layout(self):
        name = self.tool_call.get("name", "tool")
        args = self.tool_call.get("args", {})
        result = self.tool_call.get("result", {})
        duration = self.tool_call.get("duration_ms", 0)

        icon, name_clean = TOOL_META_MAP.get(name, ("⚙️", name.replace("_", " ").title()))
        container = Container()

        if name == "recall_memories":
            count = args.get("count") or len(result.get("user_memories", []) + result.get("server_lore", []))
            header_line = f"🧠 **Recalled Memories** ({count} Active)"
        elif name == "remember":
            header_line = "💾 **PriestyAI will remember that.**"
        elif name == "forget":
            header_line = "🧹 **PriestyAI will forget that.**"
        else:
            header_line = f"{icon} **{name_clean}**"

        if duration > 0:
            header_line += f" • `{duration}ms`"
        
        container.add_item(TextDisplay(header_line or "*No Header Details*"))
        container.add_item(Separator(visible=True))

        if name in ["recall_memories", "search_memories"]:
            u_mems = result.get("user_memories", [])
            s_mems = result.get("server_lore", [])
            content_blocks = []
            if u_mems:
                content_blocks.append("👤 **User Memories (Personal Profile):**")
                for m in u_mems:
                    content_blocks.append(f"• `#{m['id']}` {m['text']} *-# ({int(m['similarity'] * 100)}% match)*")
            else:
                content_blocks.append("👤 **User Memories:** *No specific user profile facts matched.*")

            content_blocks.append("")

            if s_mems:
                content_blocks.append("🏰 **Server Lore (Guild Context):**")
                for m in s_mems:
                    content_blocks.append(f"• `#{m['id']}` {m['text']} *-# ({int(m['similarity'] * 100)}% match)*")
            else:
                content_blocks.append("🏰 **Server Lore:** *No local server lore matched.*")

            container.add_item(TextDisplay("\n".join(content_blocks) or "*No memory details available.*"))

        elif name == "remember":
            cat = args.get("category", "user")
            txt = args.get("memory_text", "").strip() or "*No text provided*"
            m_id = result.get("memory_id", "")
            status = result.get("status", "saved")
            container.add_item(TextDisplay(f"**Storage Scope:** `{cat.capitalize()} Memory` • `#{m_id}` ({status.capitalize()})\n\n**Stored Fact:**\n> *{txt}*"))

        elif name == "forget":
            m_id = args.get("memory_id", "")
            deleted = result.get("deleted_text", "").strip() or "*No text deleted*"
            container.add_item(TextDisplay(f"**Forgotten Memory ID:** `#{m_id}`\n\n**Removed Statement:**\n> *{deleted}*"))

        elif name == "react":
            emoji = args.get("emoji") or (result.get("emoji") if isinstance(result, dict) else "🎲")
            container.add_item(TextDisplay(f"**Reaction Added:** {emoji}\n**Status:** Successfully added to message."))

        elif name == "execute_code":
            lang = args.get("language", "python")
            pkgs = result.get("installed_packages", args.get("packages", []))
            code = args.get("code", "").strip() or "# No code snippet"
            pkg_str = f" • Packages: `{pkgs}`" if pkgs else ""
            container.add_item(TextDisplay(f"**Runtime:** `{lang}`{pkg_str}\n\n**Executed Code:**\n```{lang}\n{code}\n```"))

            stdout = result.get("stdout", "").strip() if isinstance(result, dict) else ""
            stderr = result.get("stderr") if isinstance(result, dict) else None

            if stdout:
                container.add_item(TextDisplay(f"**Console Output:**\n```text\n{stdout}\n```"))
            if stderr:
                container.add_item(TextDisplay(f"**Console Alerts / Stderr:**\n```text\n{stderr}\n```"))

        elif name == "search_web":
            query = args.get("query", "") or "*None*"
            container.add_item(TextDisplay(f"**Search Query:** `{query}`"))
            if isinstance(result, dict) and "results" in result:
                links_text = "\n".join([
                    f"• **[{r.get('title', 'Source')}]({r.get('link', '')})**\n  {r.get('snippet', '')}"
                    for r in result.get("results", [])[:4]
                ])
                container.add_item(TextDisplay(f"**Top Sources Found:**\n{links_text}" if links_text else "*No search results found.*"))
            else:
                container.add_item(TextDisplay("*No search results found.*"))

        elif name == "read_link":
            url = args.get("url", "")
            content = result.get('content', '').strip() if isinstance(result, dict) else ""
            content_display = f"\n\n**Extracted Content:**\n```text\n{content}\n```" if content else ""
            container.add_item(TextDisplay(f"**Source URL:** [Read Article]({url}){content_display}" if url else "**URL:** *Not provided*"))

        elif name == "ask_expert":
            question = args.get("question", "") or "*None*"
            solution = (result.get("solution", "") if isinstance(result, dict) else str(result)).strip() or "*No solution output*"
            container.add_item(TextDisplay(f"**Consultation Question:** *{question}*\n\n**Expert Reasoning Solution:**\n```text\n{solution}\n```"))

        elif name == "generate_image":
            prompt = args.get("prompt", "") or "*None*"
            dims = result.get("dimensions", "1024x1024") if isinstance(result, dict) else "1024x1024"
            container.add_item(TextDisplay(f"**Artwork Prompt:** *{prompt}*\n**Resolution:** `{dims}`\n**Status:** Delivered as native file attachment."))

        else:
            arg_lines = "\n".join([f"- **{k}:** `{v}`" for k, v in args.items()]) or "- *(No parameters)*"
            container.add_item(TextDisplay(f"**Inputs:**\n{arg_lines}"))

        container.add_item(Separator(visible=True))

        back_btn = Button(label="◀ Back to Thoughts", style=discord.ButtonStyle.secondary)
        back_btn.callback = self.back_callback
        container.add_item(ActionRow(back_btn))

        self.add_item(container)


class ThoughtContainerView(LayoutView):
    def __init__(
        self,
        raw_thoughts: str,
        tool_calls: list[dict[str, Any]],
        duration_seconds: int,
        is_thinking: bool = False,
        parent_view: Any = None
    ):
        super().__init__(timeout=600)
        self.raw_thoughts = raw_thoughts
        self.tool_calls = tool_calls
        self.duration_seconds = duration_seconds
        self.is_thinking = is_thinking
        self.parent_view = parent_view
        
        self.is_inspecting_tool = False
        self.is_paginating = False
        self.current_page = 0
        self._refresh_content(raw_thoughts, tool_calls, duration_seconds, is_thinking)

    def _refresh_content(self, raw_thoughts: str, tool_calls: list[dict[str, Any]], duration_seconds: int, is_thinking: bool):
        self.raw_thoughts = raw_thoughts
        self.tool_calls = tool_calls
        self.duration_seconds = duration_seconds
        self.is_thinking = is_thinking

        if not raw_thoughts.strip() and tool_calls:
            self.thought_blocks = [
                "**Orchestrating Tool Actions**\nExecuting requested tools and analyzing parameters to formulate response."
            ]
        elif not raw_thoughts.strip():
            self.thought_blocks = [
                "**Initializing Reasoning Loop**\nAnalyzing input query and preparing initial thought context."
            ]
        else:
            std_text = standardize_thoughts_text(raw_thoughts)
            self.thought_blocks = [b.strip() for b in std_text.split("\n\n") if b.strip()]

        self.pages = self._build_pages()
        self._render_page()

    def _build_pages(self) -> list[dict[str, Any]]:
        timeline = []

        for idx, block in enumerate(self.thought_blocks):
            if block:
                timeline.append({
                    "type": "thought",
                    "content": block,
                    "order": float(idx)
                })

        num_thoughts = len(self.thought_blocks)
        num_tools = len(self.tool_calls)

        for i, tool_call in enumerate(self.tool_calls):
            order = tool_call.get("order") or tool_call.get("index") or tool_call.get("step") or tool_call.get("thought_index")
            if order is None:
                order = (i + 1) * (num_thoughts / (num_tools + 1)) - 0.1 if num_thoughts > 0 else float(i)

            timeline.append({
                "type": "tool",
                "tool_call": tool_call,
                "global_tool_index": i,
                "order": float(order)
            })

        timeline.sort(key=lambda x: (x["order"], 0 if x["type"] == "thought" else 1))

        if not timeline:
            return [{"items": [{"type": "thought", "content": "No intermediate reasoning steps recorded."}]}]

        pages = []
        current_page_items = []
        current_char_count = 0

        for item in timeline:
            item_len = len(item["content"]) if item["type"] == "thought" else 150

            if (current_char_count + item_len > 1400 or len(current_page_items) >= 5) and current_page_items:
                pages.append({"items": current_page_items})
                current_page_items = [item]
                current_char_count = item_len
            else:
                current_page_items.append(item)
                current_char_count += item_len

        if current_page_items:
            pages.append({"items": current_page_items})

        return pages

    def _render_page(self):
        self.clear_items()
        
        if not self.pages:
            self.pages = [{"items": [{"type": "thought", "content": "No thought process logged yet."}]}]
        
        self.current_page = max(0, min(self.current_page, len(self.pages) - 1))
        page_data = self.pages[self.current_page]
        items = page_data.get("items", [])

        state_title = "Thinking..." if self.is_thinking else "Thoughts"
        header_text = f"<:thinking:1540750574851723385> **{state_title}** `({self.duration_seconds}s)`"

        container = Container()
        container.add_item(TextDisplay(header_text))
        container.add_item(Separator(visible=True))

        for item in items:
            if item["type"] == "thought":
                content = item["content"].strip() or "*Empty thought section*"
                container.add_item(TextDisplay(content))
            elif item["type"] == "tool":
                tool_call = item["tool_call"]
                g_idx = item["global_tool_index"]
                t_name = tool_call.get("name", "tool")
                t_args = tool_call.get("args", {})
                t_res = tool_call.get("result", {})
                t_dur = tool_call.get("duration_ms", 0)
                display_str = format_tool_display_text(t_name, t_args, t_res, t_dur).strip() or "*Tool Action*"

                acc_btn = Button(
                    label="View ↗",
                    style=discord.ButtonStyle.secondary
                )
                acc_btn.callback = self._create_inspector_callback(tool_call)

                section = Section(TextDisplay(display_str), accessory=acc_btn)
                container.add_item(section)

        total_pages = len(self.pages)
        if total_pages > 1:
            container.add_item(Separator(visible=True))

            prev_btn = Button(
                label="◀",
                style=discord.ButtonStyle.primary,
                disabled=(self.current_page == 0)
            )
            prev_btn.callback = self._on_prev_page

            indicator_btn = Button(
                label=f"Page {self.current_page + 1} / {total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True
            )

            next_btn = Button(
                label="▶",
                style=discord.ButtonStyle.primary,
                disabled=(self.current_page == total_pages - 1)
            )
            next_btn.callback = self._on_next_page

            container.add_item(ActionRow(prev_btn, indicator_btn, next_btn))

        self.add_item(container)

    def _create_inspector_callback(self, tool_call: dict[str, Any]):
        async def callback(interaction: discord.Interaction):
            self.is_inspecting_tool = True
            if self.parent_view:
                self.parent_view.is_inspecting = True

            async def back_to_container(back_interaction: discord.Interaction):
                self.is_inspecting_tool = False
                if self.parent_view:
                    self.parent_view.is_inspecting = False
                    self.parent_view.active_interaction = back_interaction
                self._render_page()
                await back_interaction.response.edit_message(view=self)

            inspector = ToolInspectorView(tool_call, back_callback=back_to_container)
            await interaction.response.edit_message(view=inspector)

        return callback

    async def _on_prev_page(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.is_paginating = True
            self.current_page -= 1
            if self.parent_view:
                self.parent_view.active_interaction = interaction
            self._render_page()
            await interaction.response.edit_message(view=self)
            self.is_paginating = False

    async def _on_next_page(self, interaction: discord.Interaction):
        if self.current_page < len(self.pages) - 1:
            self.is_paginating = True
            self.current_page += 1
            if self.parent_view:
                self.parent_view.active_interaction = interaction
            self._render_page()
            await interaction.response.edit_message(view=self)
            self.is_paginating = False


class ThinkingButtonView(View):
    def __init__(
        self,
        duration_seconds: int = 0,
        is_thinking: bool = True,
        thought_data: dict[str, Any] | None = None
    ):
        super().__init__(timeout=900)
        self.duration_seconds = duration_seconds
        self.is_thinking = is_thinking
        self.thought_data = thought_data or {"thoughts": "", "tool_calls": []}
        
        self.active_container: ThoughtContainerView | None = None
        self.active_interaction: discord.Interaction | None = None
        self.is_inspecting: bool = False

        self.button = Button(
            style=discord.ButtonStyle.secondary,
            custom_id="priesty_thought_btn"
        )
        self.update_label(duration_seconds, is_thinking)
        self.button.callback = self._on_button_click
        self.add_item(self.button)

    def update_label(self, seconds: int, is_thinking: bool):
        self.duration_seconds = seconds
        self.is_thinking = is_thinking
        if is_thinking:
            self.button.label = f"🧠 Thinking for {seconds}s" if seconds > 0 else "🧠 Thinking..."
            self.button.disabled = False
        else:
            time_str = f"{seconds}s" if seconds > 0 else "<1s"
            self.button.label = f"🧠 Thought for {time_str}"
            self.button.disabled = False

    async def push_live_update(self):
        if self.active_container and self.active_interaction:
            if self.is_inspecting or self.active_container.is_inspecting_tool or self.active_container.is_paginating:
                return

            try:
                raw_thoughts = self.thought_data.get("thoughts", "")
                tool_calls = self.thought_data.get("tool_calls", [])
                self.active_container._refresh_content(
                    raw_thoughts=raw_thoughts,
                    tool_calls=tool_calls,
                    duration_seconds=self.duration_seconds,
                    is_thinking=self.is_thinking
                )
                await self.active_interaction.edit_original_response(view=self.active_container)
            except (discord.HTTPException, discord.NotFound):
                pass
            except Exception as e:
                logger.debug(f"Live container update error: {e}")

    async def _on_button_click(self, interaction: discord.Interaction):
        raw_thoughts = self.thought_data.get("thoughts", "")
        tool_calls = self.thought_data.get("tool_calls", [])

        self.active_container = ThoughtContainerView(
            raw_thoughts=raw_thoughts,
            tool_calls=tool_calls,
            duration_seconds=self.duration_seconds,
            is_thinking=self.is_thinking,
            parent_view=self
        )
        self.active_interaction = interaction
        self.is_inspecting = False

        await interaction.response.send_message(
            view=self.active_container,
            ephemeral=True
        )