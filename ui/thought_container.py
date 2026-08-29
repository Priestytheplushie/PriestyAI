import re
import time
import asyncio
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
    View,
    File as ComponentFile
)
from config.settings import LOADING_EMOJI
from core.thought_stream import standardize_thoughts_text, format_thoughts_with_llm
from ui.artifact_views import get_file_icon
from core.branch_manager import branch_manager
from agent.constants import OCTICONS_MAP
from parsers.markdown_parser import DFM_EMOJI_MAP

logger = logging.getLogger("PriestyAI.ThoughtUI")

TOOL_META_MAP = {
    "github_repo": ("<:github:1542000155371507802>", "GitHub"),
    "fetch_github": ("<:github:1542000155371507802>", "GitHub Repo"),
    "recall_memories": ("🧠", "Recalled Memories"),
    "remember": ("💾", "Memory Saved"),
    "forget": ("🧹", "Memory Forgotten"),
    "search_memories": ("🧠", "Memory Search"),
    "react": ("✨", "Message Reaction"),
    "execute_code": ("💻", "Code Sandbox"),
    "search_web": ("🔍", "Web Search"),
    "read_link": ("📄", "Article / Video"),
    "create_poll": ("📊", "Created Poll"),
    "calc": ("🔢", "Math Calculator"),
    "search_image": ("🖼️", "Image Search"),
    "search_gif": ("🎞️", "GIF Search"),
    "edit_image": ("🎨", "Edit Image"),
    "ask_expert": ("🧠", "Deep Reasoning"),
    "generate_image": ("🎨", "Generate Image"),
    "create_artifact": ("📦", "Created Artifact"),
    "update_artifact": ("🔄", "Updated Artifact"),
    "create_thread": ("🧵", "Thread Created"),
    "get_user_profile": ("👤", "User Profile"),
    "get_server_info": ("🏰", "Server Details"),
    "add_modal": ("📋", "Interactive Form"),
    "add_component": ("🔘", "Interactive Component"),
    "send_message": ("💬", "Sent Message"),
    "read_message_history": ("📜", "Chat History"),
    "search_channel_history": ("🔎", "Channel Search"),
    "clear_conversation": ("🧹", "Context Reset"),
    "agent_terminal": (OCTICONS_MAP["oct_terminal"], "Terminal"),
    "agent_read_file": (OCTICONS_MAP["oct_checklist"], "Read File"),
    "agent_write_file": (OCTICONS_MAP["oct_pencil"], "Write File"),
    "agent_edit_diff": (OCTICONS_MAP["oct_diff"], "Patch Diff"),
    "agent_list_dir": (OCTICONS_MAP["oct_search"], "List Directory"),
    "agent_search_web": (OCTICONS_MAP["oct_search"], "Web Search"),
    "agent_read_link": (OCTICONS_MAP["oct_link"], "Read Link"),
    "agent_search_discord_history": (OCTICONS_MAP["oct_search"], "Discord History"),
    "clone_repo": (OCTICONS_MAP["oct_repo"], "Clone Repo")
}

def format_truncated_block(code: str, lang: str, max_chars: int = 3000, label: str = "Code") -> str:
    clean_code = code.strip()
    if len(clean_code) <= max_chars:
        return f"-# 🔍 {label}:\n```{lang}\n{clean_code}\n```"

    split_idx = clean_code.rfind("\n", 0, max_chars)
    if split_idx == -1:
        split_idx = max_chars

    truncated = clean_code[:split_idx].rstrip()
    return (
        f"-# 🔍 {label}:\n```{lang}\n{truncated}\n```\n"
        f"-# ⚠️ *Preview truncated. Download the full file above to view all lines.*"
    )

def format_tool_display_text(tool_name: str, args: dict[str, Any], result: dict[str, Any], duration_ms: int, tool_call: dict[str, Any] | None = None) -> str:
    icon, name_clean = TOOL_META_MAP.get(tool_name, ("⚙️", tool_name.replace("_", " ").title()))
    time_tag = f"`{duration_ms}ms`" if duration_ms > 0 else ""

    if tool_name == "agent_terminal":
        cmd = args.get("command", "")[:35]
        return f"{OCTICONS_MAP['oct_terminal']} Executed **`{cmd}`** {time_tag}".strip()
    elif tool_name == "agent_read_file":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        return f"{OCTICONS_MAP['oct_checklist']} Read {f_icon} **`{p}`** {time_tag}".strip()
    elif tool_name == "agent_write_file":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        return f"{OCTICONS_MAP['oct_pencil']} Created {f_icon} **`{p}`** {time_tag}".strip()
    elif tool_name == "agent_edit_diff":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        adds = (tool_call.get("additions", 0) if tool_call else 0) or (result.get("additions", 0) if isinstance(result, dict) else 0)
        dels = (tool_call.get("deletions", 0) if tool_call else 0) or (result.get("deletions", 0) if isinstance(result, dict) else 0)
        diff_tag = f" (+{adds} -{dels})" if (adds > 0 or dels > 0) else ""
        return f"{OCTICONS_MAP['oct_diff']} Patched {f_icon} **`{p}`**{diff_tag} {time_tag}".strip()
    elif tool_name == "agent_list_dir":
        sub = args.get("subpath") or "./"
        return f"{OCTICONS_MAP['oct_search']} Listed directory **`{sub}`** {time_tag}".strip()
    elif tool_name == "agent_search_web":
        q = args.get("query", "")[:30]
        return f'{OCTICONS_MAP["oct_search"]} Searched web **"{q}"** {time_tag}'.strip()
    elif tool_name == "agent_read_link":
        u = args.get("url", "")[:35]
        return f"{OCTICONS_MAP['oct_link']} Read link **`{u}`** {time_tag}".strip()
    elif tool_name == "agent_search_discord_history":
        q = args.get("query", "")[:30]
        return f'{OCTICONS_MAP["oct_search"]} Searched Discord history **"{q}"** {time_tag}'.strip()
    elif tool_name == "clone_repo":
        repo = args.get("repo", "repository")
        return f"{OCTICONS_MAP['oct_repo']} Cloned repository **`{repo}`** {time_tag}".strip()
    elif tool_name in ["github_repo", "fetch_github"]:
        r = args.get("repo") or args.get("repo_url", "")
        action = args.get("action", "digest")
        path = args.get("path") or args.get("subpath", "")
        detail = f"`{path}`" if path else f"`{r}`"
        return f"<:github:1542000155371507802> GitHub **{action.replace('_', ' ').title()}** ({detail}) {time_tag}".strip()
    elif tool_name == "create_artifact":
        fname = args.get("filename") or args.get("title", "Artifact")
        file_icon = get_file_icon(fname)
        return f"{file_icon} Created **{fname}** {time_tag}".strip()
    elif tool_name == "update_artifact":
        fname = args.get("filename") or "Artifact"
        file_icon = get_file_icon(fname)
        v_new = result.get("new_version", 2) if isinstance(result, dict) else 2
        adds = result.get("additions", 0) if isinstance(result, dict) else 0
        dels = result.get("deletions", 0) if isinstance(result, dict) else 0
        diff_tag = f" (+{adds} -{dels})" if (adds > 0 or dels > 0) else ""
        return f"{file_icon} Updated **{fname}** (`v{v_new}`{diff_tag}) {time_tag}".strip()
    elif tool_name in ["recall_memories", "search_memories"]:
        count = args.get("count") or len(result.get("user_memories", []) + result.get("server_lore", []))
        return f"🧠 Recalled **{count}** memories".strip()
    elif tool_name == "calc":
        expr = args.get("expression", "")[:25]
        return f"🔢 Calculated **`{expr}`** {time_tag}".strip()
    elif tool_name == "search_image":
        q = args.get("query", "")[:30]
        t = result.get("title") if isinstance(result, dict) else None
        label_text = f"**{t[:28]}**" if t else f"`{q}`"
        return f"🖼️ Found **Image** ({label_text}) {time_tag}".strip()
    elif tool_name == "search_gif":
        q = args.get("query", "")[:30]
        return f"🎞️ Found **GIF** (`{q}`) {time_tag}".strip()
    elif tool_name == "edit_image":
        p = args.get("prompt", "")[:30]
        return f"🎨 **Edit Image** (`{p}`) {time_tag}".strip()
    elif tool_name == "create_poll":
        q = args.get("question", "")[:25]
        return f"📊 Created **Poll** (`{q}`)".strip()
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
        return f"{icon} Read **Link** (`{u}`) {time_tag}".strip()
    elif tool_name == "ask_expert":
        q = args.get("question", "")[:30]
        return f"{icon} Consulted **Expert** (`{q}`) {time_tag}".strip()
    elif tool_name == "generate_image":
        return f"{icon} Generated **Artwork** {time_tag}".strip()
    elif tool_name == "add_modal":
        t = args.get("title", "Form")[:25]
        return f"📋 Staged **Modal Form** (`{t}`)".strip()
    elif tool_name == "add_component":
        raw_type = str(args.get("component_type", "component")).lower().strip().replace(" ", "_")
        type_names = {
            "button": ("🔘", "Button"),
            "btn": ("🔘", "Button"),
            "string_select": ("📋", "Select Menu"),
            "select": ("📋", "Select Menu"),
            "user_select": ("👥", "User Select"),
            "role_select": ("🛡️", "Role Select"),
            "channel_select": ("📢", "Channel Select"),
            "mentionable_select": ("🎯", "Mentionable Select")
        }
        c_icon, c_name = type_names.get(raw_type, ("🔘", "Component"))
        lbl = args.get("label") or args.get("placeholder") or args.get("custom_id", "")
        label_part = f" (`{lbl[:25]}`)" if lbl else ""
        return f"{c_icon} Staged **{c_name}**{label_part}".strip()

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

        if name in ["github_repo", "fetch_github"]:
            repo = args.get("repo") or args.get("repo_url", "Repository")
            action = args.get("action", "digest").replace("_", " ").title()
            header_line = f"<:github:1542000155371507802> **GitHub {action}:** `{repo}`"
        elif name == "agent_terminal":
            cmd = args.get("command", "command")
            header_line = f"{OCTICONS_MAP['oct_terminal']} **Terminal:** `{cmd}`"
        elif name == "agent_read_file":
            p = args.get("path", "file")
            f_icon = get_file_icon(p)
            header_line = f"{OCTICONS_MAP['oct_checklist']} **Read File:** {f_icon} `{p}`"
        elif name == "agent_write_file":
            p = args.get("path", "file")
            f_icon = get_file_icon(p)
            header_line = f"{OCTICONS_MAP['oct_pencil']} **Created File:** {f_icon} `{p}`"
        elif name == "agent_edit_diff":
            p = args.get("path", "file")
            f_icon = get_file_icon(p)
            adds = self.tool_call.get("additions", 0) or (result.get("additions", 0) if isinstance(result, dict) else 0)
            dels = self.tool_call.get("deletions", 0) or (result.get("deletions", 0) if isinstance(result, dict) else 0)
            diff_stat = f" (+{adds} -{dels})" if (adds > 0 or dels > 0) else ""
            header_line = f"{OCTICONS_MAP['oct_diff']} **Patched File:** {f_icon} `{p}`{diff_stat}"
        elif name == "agent_list_dir":
            sub = args.get("subpath") or "./"
            header_line = f"{OCTICONS_MAP['oct_search']} **Directory List:** `{sub}`"
        elif name == "agent_search_web":
            q = args.get("query", "")
            header_line = f'{OCTICONS_MAP["oct_search"]} **Web Search:** "{q}"'
        elif name == "agent_read_link":
            u = args.get("url", "")
            header_line = f"{OCTICONS_MAP['oct_link']} **Article Reader:** `{u}`"
        elif name == "agent_search_discord_history":
            q = args.get("query", "")
            header_line = f'{OCTICONS_MAP["oct_search"]} **Discord History Search:** "{q}"'
        elif name == "clone_repo":
            repo = args.get("repo", "repository")
            header_line = f"{OCTICONS_MAP['oct_repo']} **Cloned Repository:** `{repo}`"
        elif name == "create_artifact":
            fname = args.get("filename", "artifact.zip")
            file_icon = get_file_icon(fname)
            header_line = f"{file_icon} **Created Artifact:** `{fname}`"
        elif name == "update_artifact":
            fname = args.get("filename", "artifact.zip")
            file_icon = get_file_icon(fname)
            v_new = result.get("new_version", 2) if isinstance(result, dict) else 2
            adds = result.get("additions", 0) if isinstance(result, dict) else 0
            dels = result.get("deletions", 0) if isinstance(result, dict) else 0
            diff_stat = f" (+{adds} -{dels})" if (adds > 0 or dels > 0) else ""
            header_line = f"{file_icon} **Updated Artifact:** `{fname}` (`v{v_new}`{diff_stat})"
        elif name in ["recall_memories", "search_memories"]:
            count = args.get("count") or len(result.get("user_memories", []) + result.get("server_lore", []))
            header_line = f"🧠 **Recalled Memories** ({count} Active)"
        elif name == "search_image":
            header_line = "🖼️ **Visual Image Search & Attachment**"
        elif name == "search_gif":
            header_line = "🎞️ **Animated GIF Search & Attachment**"
        elif name == "edit_image":
            header_line = "🎨 **Local Image Editing**"
        elif name == "calc":
            header_line = "🔢 **Math Calculation**"
        elif name == "create_poll":
            header_line = "📊 **Discord Native Poll**"
        elif name == "remember":
            header_line = "💾 **PriestyAI will remember that.**"
        elif name == "forget":
            header_line = "🧹 **PriestyAI will forget that.**"
        elif name == "add_component":
            raw_type = str(args.get("component_type", "component")).lower().strip().replace(" ", "_")
            type_icons = {
                "button": "🔘", "string_select": "📋", "user_select": "👥",
                "role_select": "🛡️", "channel_select": "📢", "mentionable_select": "🎯"
            }
            c_icon = type_icons.get(raw_type, "🔘")
            header_line = f"{c_icon} **Interactive Component:** `{raw_type.replace('_', ' ').title()}`"
        else:
            header_line = f"{icon} **{name_clean}**"

        if duration > 0:
            header_line += f" • `{duration}ms`"
        
        container.add_item(TextDisplay(header_line or "*No Header Details*"))
        container.add_item(Separator(visible=True))

        if name == "agent_edit_diff":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            diff_text = self.tool_call.get("diff_text", "")
            adds = self.tool_call.get("additions", 0)
            dels = self.tool_call.get("deletions", 0)
            stats_tag = f" (+{adds} additions • -{dels} deletions)" if (adds > 0 or dels > 0) else ""
            container.add_item(TextDisplay(f"### {f_icon} Patch Diff: `{path}`{stats_tag}\n```diff\n{(diff_text or '# Direct patch applied')[:3000]}\n```"))

        elif name == "agent_write_file":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            ext = path.split(".")[-1].lower() if "." in path else "text"
            content = args.get("content", "")
            lines = len(content.splitlines())
            container.add_item(TextDisplay(f"### {f_icon} Created File: `{path}` ({lines} lines)\n```{ext}\n{content[:3000]}\n```"))

        elif name == "agent_read_file":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            ext = path.split(".")[-1].lower() if "." in path else "text"
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            lines_tag = result.get("showing_lines", "") if isinstance(result, dict) else ""
            container.add_item(TextDisplay(f"### {f_icon} Source View: `{path}` (Lines {lines_tag})\n```{ext}\n{content[:3000]}\n```"))

        elif name == "agent_terminal":
            cmd = args.get("command", "")
            exit_code = result.get("exit_code", 0) if isinstance(result, dict) else 0
            stdout = result.get("stdout", "(no output)") if isinstance(result, dict) else str(result)
            stderr = result.get("stderr") if isinstance(result, dict) else None
            status_tag = "✅ Exit Code: `0` (Success)" if exit_code == 0 else f"❌ Exit Code: `{exit_code}` (Failed)"
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_terminal']} Command: `{cmd}`\n{status_tag}\n\n**Output:**\n```text\n{stdout[:2200]}\n```"))
            if stderr:
                container.add_item(TextDisplay(f"**Alerts & Stderr:**\n```text\n{stderr[:1000]}\n```"))

        elif name == "agent_list_dir":
            subpath = args.get("subpath") or "./"
            files = result.get("files", []) if isinstance(result, dict) else []
            total_count = result.get("file_count", len(files)) if isinstance(result, dict) else len(files)
            file_list_str = "\n".join([f"• `{f}`" for f in files[:45]])
            if total_count > 45:
                file_list_str += f"\n-# ... and {total_count - 45} more files"
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Workspace Directory: `{subpath}` ({total_count} files total)\n{file_list_str}"))

        elif name == "agent_search_web":
            query = args.get("query", "")
            res_items = result.get("results", []) if isinstance(result, dict) else []
            links_text = "\n".join([f"• **[{r.get('title', 'Source')}]({r.get('link', '')})**\n  {r.get('snippet', '')}" for r in res_items[:4]])
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Query: \"{query}\"\n\n**Sources Found:**\n{links_text}" if links_text else "*No search results found*"))

        elif name == "agent_read_link":
            url = args.get("url", "")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_link']} Source Article: [Read Link]({url})\n\n```text\n{content[:2500]}\n```"))

        elif name == "agent_search_discord_history":
            query = args.get("query", "")
            matched = result.get("results", []) if isinstance(result, dict) else []
            history_text = "\n".join([f"• **{m.get('author', 'User')}**: {m.get('content', '')}" for m in matched[:8]])
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Discord History Query: \"{query}\"\n\n{history_text or '*No matches found*'}\n"))

        elif name == "clone_repo":
            repo = args.get("repo", "")
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_repo']} Cloned Repository: `{repo}`\nRepository source files have been synchronized into the workspace root `./`."))

        elif name in ["github_repo", "fetch_github"]:
            repo = result.get("repo", args.get("repo", args.get("repo_url", "")))
            action_type = str(args.get("action", "digest")).lower()

            if "error" in result:
                container.add_item(TextDisplay(f"⚠️ **Error:** {result['error']}"))
            elif action_type in ["read_file", "file", "read"] and "content" in result:
                path = result.get("path", "")
                fname = path.split("/")[-1] if path else "file.txt"
                total_l = result.get("total_lines", 0)
                lang = result.get("language", "text")
                code_txt = result.get("content", "")
                showing = result.get("showing_lines", f"1 - {total_l}")

                container.add_item(TextDisplay(f"**Repository:** `{repo}`\n**File:** `{path}` ({total_l:,} total lines • showing lines {showing})\n\n```{lang}\n{code_txt[:2500]}\n```"))
                container.add_item(ComponentFile(f"attachment://{fname}"))
            elif "tree" in result:
                tree_txt = result.get("tree", "")
                container.add_item(TextDisplay(f"**Directory Tree ({result.get('subpath_filter', 'root')}):**\n\n{tree_txt[:2500]}"))
            elif "description" in result:
                desc = result.get("description", "")
                lang = result.get("primary_language", "Unknown")
                stars = result.get("stars", 0)
                manifests = result.get("manifest_dependencies", "")
                info_lines = [
                    f"**Repository:** `{repo}`",
                    f"**Description:** {desc}",
                    f"**Language:** `{lang}` • **Stars:** `{stars:,}`"
                ]
                if manifests:
                    info_lines.append(f"\n{manifests}")
                container.add_item(TextDisplay("\n".join(info_lines)[:2500]))
            else:
                container.add_item(TextDisplay(f"**Result Data:**\n```json\n{str(result)[:2500]}\n```"))

        elif name == "search_image":
            query = args.get("query", "")
            title = result.get("title") or args.get("caption") or "Image Asset"
            source = result.get("source", "Web")
            img_url = result.get("image_url", "#")
            size_b = result.get("size_bytes", 0)

            lines = [
                f"**Search Query:** `{query}`",
                f"**Selected Asset:** `{title}`",
                f"**Host Source:** `{source}`",
                f"**Direct Link:** [🔗 Open Original Asset]({img_url})"
            ]
            if size_b:
                lines.append(f"**Attached Size:** `{size_b / 1024:.1f} KB`")

            container.add_item(TextDisplay("\n".join(lines)))

        elif name == "search_gif":
            query = args.get("query", "")
            title = result.get("title") or args.get("caption") or "Animated GIF"
            source = result.get("source", "Web")
            img_url = result.get("image_url", "#")
            size_b = result.get("size_bytes", 0)

            lines = [
                f"**Search Query:** `{query}`",
                f"**Selected Asset:** `{title}`",
                f"**Host Source:** `{source}`",
                f"**Direct Link:** [🔗 Open Original GIF]({img_url})"
            ]
            if size_b:
                lines.append(f"**Attached Size:** `{size_b / 1024:.1f} KB`")

            container.add_item(TextDisplay("\n".join(lines)))

        elif name == "edit_image":
            prompt = args.get("prompt", "")
            strength = args.get("strength", 0.52)
            model = result.get("model", "DreamShaper-8-LCM")
            container.add_item(TextDisplay(f"**Prompt:** *{prompt}*\n**Model:** `{model}`\n**Transformation Strength:** `{strength}`"))

        elif name == "calc":
            expr = args.get("expression", "")
            res = result.get("result", "")
            container.add_item(TextDisplay(f"**Expression:**\n```python\n{expr}\n```\n**Evaluated Result:**\n`{res}`"))

        elif name == "generate_image":
            prompt = args.get("prompt", "")
            dims = result.get("dimensions", "512x512")
            model = result.get("model", "DreamShaper-8-LCM")
            container.add_item(TextDisplay(f"**Prompt:** *{prompt}*\n**Dimensions:** `{dims}`\n**Model:** `{model}`"))

        elif name == "create_poll":
            q = args.get("question", "")
            opts = args.get("options", [])
            dur = args.get("duration_hours", 24)
            opts_str = "\n".join([f"• {o}" for o in opts])
            container.add_item(TextDisplay(f"**Question:** {q}\n**Duration:** `{dur} hours`\n\n**Options:**\n{opts_str}"))

        elif name == "update_artifact":
            fname = args.get("filename", "artifact.zip")
            summary = args.get("change_summary", "")
            diff_text = result.get("diff", "") if isinstance(result, dict) else ""
            v_old = result.get("old_version", 1) if isinstance(result, dict) else 1
            v_new = result.get("new_version", 2) if isinstance(result, dict) else 2
            adds = result.get("additions", 0) if isinstance(result, dict) else 0
            dels = result.get("deletions", 0) if isinstance(result, dict) else 0
            file_icon = get_file_icon(fname)

            header_lines = [f"{file_icon} **Updated: `{fname}` (v{v_old} ➔ v{v_new})**"]
            if summary:
                header_lines.append(f"*{summary}*")
            if adds > 0 or dels > 0:
                header_lines.append(f"-# Lines Changed: `+{adds} additions` • `-{dels} deletions`")

            container.add_item(TextDisplay("\n".join(header_lines)))
            container.add_item(ComponentFile(f"attachment://{fname}"))
            container.add_item(Separator(visible=True))

            if diff_text:
                container.add_item(TextDisplay(format_truncated_block(diff_text, lang="diff", max_chars=3000, label="Code Changes (Diff)")))
            else:
                raw_code = args.get("content", "")
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                container.add_item(TextDisplay(format_truncated_block(raw_code, lang=ext, max_chars=3000, label="Updated Source Code")))

        elif name == "create_artifact":
            fname = args.get("filename", "artifact.zip")
            title = args.get("title", fname)
            desc = args.get("description", "")
            raw_content = args.get("content", "")
            files = args.get("files", [])
            file_icon = get_file_icon(fname)

            header_lines = [f"{file_icon} **{title}**"]
            if desc:
                header_lines.append(f"*{desc}*")
            container.add_item(TextDisplay("\n".join(header_lines)))
            container.add_item(ComponentFile(f"attachment://{fname}"))
            container.add_item(Separator(visible=True))

            if files and isinstance(files, list) and len(files) > 1:
                manifest_lines = ["**Included Files in Archive:**"]
                for f in files[:8]:
                    f_name = f.get("filename", "file")
                    f_lines = len(f.get("content", "").splitlines())
                    f_icon = get_file_icon(f_name)
                    manifest_lines.append(f"• {f_icon} `{f_name}` — *{f_lines:,} lines*")
                if len(files) > 8:
                    manifest_lines.append(f"-# ... and {len(files) - 8} more files")

                container.add_item(TextDisplay("\n".join(manifest_lines)))
                first_f = files[0]
                first_name = first_f.get("filename", "index.html")
                first_code = first_f.get("content", "")
                ext = first_name.rsplit(".", 1)[-1].lower() if "." in first_name else ""
                container.add_item(TextDisplay(f"\n**Entry File (`{first_name}`):**\n" + format_truncated_block(first_code, lang=ext, max_chars=2200, label=first_name)))
            else:
                code = raw_content or (files[0].get("content", "") if files else "")
                ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
                lines = len(code.splitlines())
                container.add_item(TextDisplay(f"-# Source Code ({lines:,} lines):\n" + format_truncated_block(code, lang=ext, max_chars=3000, label=fname)))

        elif name in ["recall_memories", "search_memories"]:
            u_mems = result.get("user_memories", [])
            s_mems = result.get("server_lore", [])
            content_blocks = []
            if u_mems:
                content_blocks.append("👤 **User Profile Facts:**")
                for m in u_mems:
                    content_blocks.append(f"• {m['text']}")
            else:
                content_blocks.append("👤 **User Profile Facts:** *None found.*")

            content_blocks.append("")
            if s_mems:
                content_blocks.append("🏰 **Server Lore:**")
                for m in s_mems:
                    content_blocks.append(f"• {m['text']}")
            else:
                content_blocks.append("🏰 **Server Lore:** *None found.*")

            container.add_item(TextDisplay("\n".join(content_blocks) or "*No memory details available.*"))

        elif name == "execute_code":
            lang = args.get("language", "python")
            pkgs = result.get("installed_packages", args.get("packages", []))
            code = args.get("code", "").strip() or "# No code snippet"
            pkg_str = f" • Packages: `{pkgs}`" if pkgs else ""
            container.add_item(TextDisplay(f"**Runtime:** `{lang}`{pkg_str}\n\n```{lang}\n{code[:2500]}\n```"))

            stdout = result.get("stdout", "").strip() if isinstance(result, dict) else ""
            stderr = result.get("stderr") if isinstance(result, dict) else None

            if stdout:
                container.add_item(TextDisplay(f"**Output:**\n```text\n{stdout[:1500]}\n```"))
            if stderr:
                container.add_item(TextDisplay(f"**Alerts:**\n```text\n{stderr[:1000]}\n```"))

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
            r_type = result.get("type", "webpage")
            content = result.get('content', '').strip() if isinstance(result, dict) else ""
            
            if r_type == "youtube_video_summary":
                container.add_item(TextDisplay(f"📺 **YouTube Video:** [Watch Video]({url})\n\n{content[:2500]}"))
            else:
                content_display = f"\n\n```text\n{content[:2500]}\n```" if content else ""
                container.add_item(TextDisplay(f"**Source URL:** [Read Article]({url}){content_display}" if url else "**URL:** *Not provided*"))

        elif name == "ask_expert":
            question = args.get("question", "") or "*None*"
            solution = (result.get("solution", "") if isinstance(result, dict) else str(result)).strip() or "*No solution output*"
            container.add_item(TextDisplay(f"**Question:** *{question}*\n\n**Expert Solution:**\n{solution[:2500]}"))

        else:
            arg_lines = "\n".join([f"- **{k}:** `{v}`" for k, v in args.items()]) or "- *(No parameters)*"
            container.add_item(TextDisplay(f"**Inputs:**\n{arg_lines}"))

        container.add_item(Separator(visible=True))

        back_btn = Button(
            label="◀ Back to Thoughts",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_back_thoughts"
        )
        back_btn.callback = self.back_callback
        container.add_item(ActionRow(back_btn))

        self.add_item(container)

class ThoughtContainerView(LayoutView):
    def __init__(
        self,
        raw_thoughts: str,
        tool_calls: list[dict[str, Any]],
        duration_seconds: int,
        formatted_thoughts: str | None = None,
        is_thinking: bool = False,
        is_raw_mode: bool | None = None,
        show_toggle: bool | None = None,
        parent_view: Any = None,
        message_id: str | int | None = None,
        version_idx: int = 1,
        model_name: str | None = None
    ):
        super().__init__(timeout=600)
        self.raw_thoughts = raw_thoughts
        self.formatted_thoughts = formatted_thoughts
        self.tool_calls = tool_calls
        self.duration_seconds = max(1, duration_seconds) if duration_seconds > 0 else 1
        self.is_thinking = is_thinking
        self.model_name = (model_name or "").lower().strip()

        is_gemma = bool(self.model_name and "gemma" in self.model_name)

        if self.is_thinking:
            self.show_toggle = False
        elif show_toggle is not None:
            self.show_toggle = bool(show_toggle and is_gemma)
        else:
            self.show_toggle = is_gemma and not self.is_thinking

        if is_raw_mode is None:
            self.is_raw_mode = is_gemma and not bool(formatted_thoughts)
        else:
            self.is_raw_mode = is_raw_mode

        self.parent_view = parent_view
        self.message_id = str(message_id) if message_id else None
        self.version_idx = version_idx
        
        self.is_inspecting_tool = False
        self.is_paginating = False
        self.is_formatting = False
        self.last_user_action_time: float = 0.0
        self.current_page = 0
        self._refresh_content()

    def _refresh_content(self):
        active_text = self.raw_thoughts if (self.is_raw_mode or self.is_thinking) else (self.formatted_thoughts or self.raw_thoughts)

        if not active_text.strip() and self.tool_calls:
            self.thought_blocks = [
                "**Orchestrating Actions**\nExecuting requested tools and analyzing context."
            ]
        elif not active_text.strip():
            self.thought_blocks = [
                "**Analyzing Request**\nProcessing input query and preparing context."
            ]
        else:
            if self.is_raw_mode or self.is_thinking:
                blocks = [p.strip() for p in active_text.split("\n\n") if p.strip()]
                self.thought_blocks = blocks if blocks else [active_text.strip()]
            else:
                std_text = standardize_thoughts_text(active_text)
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
            tool_name = tool_call.get("name", "")
            
            if tool_name in ["recall_memories", "search_memories"] or tool_call.get("order") == -1.0:
                order = -1.0 + (i * 0.001)
            else:
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

        container = Container()
        time_display = f"({self.duration_seconds}s)"

        if self.is_thinking:
            header_text = f"<:thinking:1540750574851723385> **Thinking...** `{time_display}`"
            container.add_item(TextDisplay(header_text))
        else:
            if self.is_raw_mode:
                header_text = f"<:thinking:1540750574851723385> **Raw Thoughts** `{time_display}`"
                if self.show_toggle and not self.is_thinking:
                    toggle_btn = Button(
                        label="Formatting..." if self.is_formatting else "Format",
                        emoji=LOADING_EMOJI if self.is_formatting else None,
                        style=discord.ButtonStyle.secondary,
                        custom_id="btn_toggle_thought_fmt",
                        disabled=self.is_formatting
                    )
                    toggle_btn.callback = self._on_toggle_view_mode
                    container.add_item(Section(TextDisplay(header_text), accessory=toggle_btn))
                else:
                    container.add_item(TextDisplay(header_text))
            else:
                header_text = f"<:thinking:1540750574851723385> **Thoughts** `{time_display}`"
                if self.show_toggle and not self.is_thinking:
                    toggle_btn = Button(
                        label="View Raw",
                        style=discord.ButtonStyle.secondary,
                        custom_id="btn_toggle_thought_raw"
                    )
                    toggle_btn.callback = self._on_toggle_view_mode
                    container.add_item(Section(TextDisplay(header_text), accessory=toggle_btn))
                else:
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
                display_str = format_tool_display_text(t_name, t_args, t_res, t_dur, tool_call=tool_call).strip() or "*Tool Action*"

                acc_btn = Button(
                    label="View ↗",
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"btn_inspect_tool_{g_idx}",
                    disabled=self.is_thinking
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
                disabled=(self.current_page == 0),
                custom_id="btn_prev_page"
            )
            prev_btn.callback = self._on_prev_page

            indicator_btn = Button(
                label=f"Page {self.current_page + 1} / {total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="btn_page_indicator"
            )

            next_btn = Button(
                label="▶",
                style=discord.ButtonStyle.primary,
                disabled=(self.current_page == total_pages - 1),
                custom_id="btn_next_page"
            )
            next_btn.callback = self._on_next_page

            container.add_item(ActionRow(prev_btn, indicator_btn, next_btn))

        self.add_item(container)

    async def _on_toggle_view_mode(self, interaction: discord.Interaction):
        self.last_user_action_time = time.time()
        if self.is_raw_mode:
            if not self.formatted_thoughts and self.raw_thoughts:
                self.is_formatting = True
                self._render_page()
                try:
                    await interaction.response.edit_message(view=self)
                except Exception:
                    pass

                formatted = await format_thoughts_with_llm(self.raw_thoughts)
                self.formatted_thoughts = formatted
                self.is_formatting = False

                if self.message_id:
                    gen = branch_manager.get_generation(self.message_id)
                    if gen:
                        versions = gen.get("versions", [])
                        if 1 <= self.version_idx <= len(versions):
                            v_data = versions[self.version_idx - 1]
                            v_data["formatted_thoughts"] = formatted
                            branch_manager.update_version_data(self.message_id, self.version_idx, v_data)

                if self.parent_view and hasattr(self.parent_view, "thought_data"):
                    self.parent_view.thought_data["formatted_thoughts"] = formatted

                self.is_raw_mode = False
                self.current_page = 0
                self._refresh_content()
                try:
                    await interaction.edit_original_response(view=self)
                except Exception:
                    pass
                return

            self.is_raw_mode = False
        else:
            self.is_raw_mode = True

        self.current_page = 0
        self._refresh_content()
        try:
            await interaction.response.edit_message(view=self)
        except Exception as ex:
            logger.debug(f"Toggle thought mode error: {ex}")

    def _create_inspector_callback(self, tool_call: dict[str, Any]):
        async def callback(interaction: discord.Interaction):
            self.last_user_action_time = time.time()
            self.is_inspecting_tool = True
            if self.parent_view:
                self.parent_view.is_inspecting = True

            async def back_to_container(back_interaction: discord.Interaction):
                self.last_user_action_time = time.time()
                self.is_inspecting_tool = False
                if self.parent_view:
                    self.parent_view.is_inspecting = False
                    self.parent_view.active_interaction = back_interaction
                self._render_page()
                try:
                    await back_interaction.response.edit_message(view=self)
                except Exception as ex:
                    logger.debug(f"Back button edit exception: {ex}")

            inspector = ToolInspectorView(tool_call, back_callback=back_to_container)
            try:
                await interaction.response.edit_message(view=inspector)
            except Exception as ex:
                logger.debug(f"Inspect button edit exception: {ex}")

        return callback

    async def _on_prev_page(self, interaction: discord.Interaction):
        self.last_user_action_time = time.time()
        self.is_paginating = True

        lock = self.parent_view.update_lock if (self.parent_view and hasattr(self.parent_view, "update_lock")) else None
        if lock:
            await lock.acquire()

        try:
            if self.current_page > 0:
                self.current_page -= 1
                if self.parent_view:
                    self.parent_view.active_interaction = interaction
                self._render_page()
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as ex:
                    logger.debug(f"Prev page edit exception: {ex}")
            else:
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
        finally:
            if lock and lock.locked():
                lock.release()
            self.is_paginating = False

    async def _on_next_page(self, interaction: discord.Interaction):
        self.last_user_action_time = time.time()
        self.is_paginating = True

        lock = self.parent_view.update_lock if (self.parent_view and hasattr(self.parent_view, "update_lock")) else None
        if lock:
            await lock.acquire()

        try:
            if self.current_page < len(self.pages) - 1:
                self.current_page += 1
                if self.parent_view:
                    self.parent_view.active_interaction = interaction
                self._render_page()
                try:
                    await interaction.response.edit_message(view=self)
                except Exception as ex:
                    logger.debug(f"Next page edit exception: {ex}")
            else:
                try:
                    await interaction.response.defer()
                except Exception:
                    pass
        finally:
            if lock and lock.locked():
                lock.release()
            self.is_paginating = False

class PlaceholderLayoutView(LayoutView):
    def __init__(
        self,
        loading_text: str,
        duration_seconds: int = 0,
        is_enabled: bool = False,
        on_answer_now_callback: Callable | None = None,
        thought_data: dict[str, Any] | None = None,
        model_name: str | None = None,
        is_quiz: bool = False
    ):
        super().__init__(timeout=900)
        self.loading_text = loading_text
        self.duration_seconds = max(1, duration_seconds) if duration_seconds > 0 else 0
        self.is_enabled = is_enabled
        self.is_quiz = is_quiz
        self.is_spoiler_bypassed = False
        self.on_answer_now_callback = on_answer_now_callback
        self.thought_data = thought_data or {"thoughts": "", "tool_calls": []}
        self.model_name = (model_name or (self.thought_data.get("model") if self.thought_data else "")).lower()

        self.active_container: ThoughtContainerView | None = None
        self.active_interaction: discord.Interaction | None = None
        self.is_inspecting: bool = False
        self.update_lock = asyncio.Lock()

        self.text_display = TextDisplay(self.loading_text)
        self.answer_now_btn = Button(
            label="Answer Now",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_answer_now"
        )
        self.answer_now_btn.callback = self._on_answer_now_clicked

        self.section = Section(
            self.text_display,
            accessory=self.answer_now_btn
        )

        time_label = f"🧠 Thinking for {self.duration_seconds}s..."
        self.thinking_btn = Button(
            label=time_label,
            style=discord.ButtonStyle.secondary,
            disabled=not self.is_enabled,
            custom_id="priesty_placeholder_thought_btn"
        )
        self.thinking_btn.callback = self._on_thought_button_clicked
        self.action_row = ActionRow(self.thinking_btn)

        self.add_item(self.section)
        self.add_item(self.action_row)

    def enable_thinking(self):
        self.is_enabled = True
        self.thinking_btn.disabled = False

    def update_state(self, loading_text: str, duration_seconds: int):
        self.loading_text = loading_text
        self.duration_seconds = max(1, duration_seconds) if duration_seconds > 0 else 0
        self.text_display.content = self.loading_text
        self.thinking_btn.label = f"🧠 Thinking for {self.duration_seconds}s..."
        self.thinking_btn.disabled = not self.is_enabled

    async def push_live_update(self):
        if not self.active_container or not self.active_interaction:
            return

        raw_thoughts = self.thought_data.get("thoughts", "")
        tool_calls = self.thought_data.get("tool_calls", [])
        self.active_container.raw_thoughts = raw_thoughts
        self.active_container.formatted_thoughts = raw_thoughts
        self.active_container.tool_calls = tool_calls
        self.active_container.duration_seconds = max(1, self.duration_seconds)

        if (self.is_quiz or self.thought_data.get("is_quiz")) and not self.is_spoiler_bypassed:
            return

        if self.is_inspecting or self.active_container.is_inspecting_tool or self.active_container.is_paginating:
            return

        if self.active_container.current_page > 0 or (time.time() - getattr(self.active_container, "last_user_action_time", 0.0)) < 2.0:
            return

        if self.update_lock.locked():
            return

        async with self.update_lock:
            try:
                self.active_container.is_thinking = True
                self.active_container.show_toggle = False
                self.active_container._refresh_content()
                await self.active_interaction.edit_original_response(view=self.active_container)
            except (discord.HTTPException, discord.NotFound):
                pass
            except Exception as e:
                logger.debug(f"Live container update error: {e}")

    async def _on_answer_now_clicked(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        if self.on_answer_now_callback:
            await self.on_answer_now_callback(interaction)

    async def _on_thought_button_clicked(self, interaction: discord.Interaction):
        if not self.is_enabled:
            return

        raw_thoughts = self.thought_data.get("thoughts", "")
        tool_calls = self.thought_data.get("tool_calls", [])

        self.active_container = ThoughtContainerView(
            raw_thoughts=raw_thoughts,
            formatted_thoughts=raw_thoughts,
            tool_calls=tool_calls,
            duration_seconds=max(1, self.duration_seconds),
            is_thinking=True,
            show_toggle=False,
            parent_view=self,
            model_name=self.model_name
        )
        self.active_interaction = interaction
        self.is_inspecting = False

        if (self.is_quiz or self.thought_data.get("is_quiz")) and not self.is_spoiler_bypassed:
            warning_view = LayoutView(timeout=300)
            warning_text = (
                f"{DFM_EMOJI_MAP['gfm_warning']} **Quiz Spoilers**\n"
                "This thought process contains the AI's internal reasoning and answer key for the quiz.\n"
                "Opening it before finishing may spoil the questions and answers."
            )
            warning_view.add_item(TextDisplay(warning_text))
            warning_view.add_item(Separator(visible=True))

            async def on_show_anyway(sub_inter: discord.Interaction):
                self.is_spoiler_bypassed = True
                self.active_container._refresh_content()
                await sub_inter.response.edit_message(view=self.active_container)

            show_btn = Button(label="Show Anyway", style=discord.ButtonStyle.danger)
            show_btn.callback = on_show_anyway
            warning_view.add_item(ActionRow(show_btn))

            try:
                await interaction.response.send_message(view=warning_view, ephemeral=True)
            except Exception as ex:
                logger.debug(f"Placeholder thought click error: {ex}")
            return

        try:
            await interaction.response.send_message(
                view=self.active_container,
                ephemeral=True
            )
        except Exception as ex:
            logger.debug(f"Placeholder thought click error: {ex}")