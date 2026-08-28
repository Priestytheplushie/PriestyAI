import difflib
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
    Select
)
from agent.constants import OCTICONS_MAP, BETA_EMOJI
from ui.modals import DynamicModalV2
from ui.artifact_views import get_file_icon, build_artifact_components_for_message
from handlers.stream_handler import apply_message_parsers
from config.settings import LOADING_EMOJI

logger = logging.getLogger("PriestyAI.Agent.Views")

def compute_unified_diff_str(search_block: str, replace_block: str, filename: str) -> tuple[str, int, int]:
    old_lines = search_block.splitlines(keepends=True)
    new_lines = replace_block.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        n=2
    ))
    diff_text = "".join(diff)
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    return diff_text or f"# Direct Replacement in {filename}", additions, deletions

def build_agent_create_modal(default_user_id: int | str, on_submit: Callable) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": (
                f"# Create Agent Session {BETA_EMOJI}\n"
                "Launch an autonomous agent in a private workspace thread to research, plan, and build your task.\n\n"
                "-# 💡 **Tip:** Clearly specify research questions, technical constraints, or reference links for best results."
            )
        },
        {
            "type": "text_input",
            "custom_id": "prompt",
            "label": "Objective & Prompt",
            "description": "Specify research questions, features to build, or technical constraints",
            "placeholder": "Describe what to investigate or build (include reference links if applicable)...",
            "style": "paragraph",
            "required": True,
            "max_length": 3000
        },
        {
            "type": "user_select",
            "custom_id": "collaborators",
            "label": "Collaborators",
            "description": "Members authorized to guide the agent and approve plans",
            "placeholder": "Select collaborators...",
            "required": False,
            "min_values": 0,
            "max_values": 10,
            "default_values": [{"id": str(default_user_id), "type": "user"}]
        },
        {
            "type": "text_input",
            "custom_id": "repo_url",
            "label": "GitHub Repository",
            "description": "Repository URL or owner/repo to clone into workspace (Optional)",
            "placeholder": "e.g. owner/repo or https://github.com/owner/repo",
            "style": "short",
            "required": False
        },
        {
            "type": "file_upload",
            "custom_id": "initial_files",
            "label": "Context Files",
            "description": "Upload specs, datasets, or reference files",
            "required": False,
            "max_values": 10
        }
    ]

    return DynamicModalV2(
        title="Create Agent Session",
        custom_id="modal_create_agent_session",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

def build_agent_new_task_modal(session_id: str, on_submit: Callable) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": (
                f"# {OCTICONS_MAP['oct_checklist']} Start Next Agent Task\n"
                "Plan and execute another research or engineering task inside the current workspace.\n\n"
                "-# 💡 Existing workspace files, git history, and prior research whitepapers are preserved."
            )
        },
        {
            "type": "text_input",
            "custom_id": "prompt",
            "label": "Next Task Prompt",
            "description": "What would you like PriestyAI to investigate or modify next?",
            "placeholder": "Describe the next feature, research inquiry, or bugfix...",
            "style": "paragraph",
            "required": True,
            "max_length": 3000
        },
        {
            "type": "file_upload",
            "custom_id": "additional_files",
            "label": "Additional Context Files",
            "description": "Upload new specs, datasets, or reference files",
            "required": False,
            "max_values": 5
        }
    ]

    return DynamicModalV2(
        title="Next Agent Task",
        custom_id=f"modal_agent_new_task_{session_id}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

def build_agent_header_layout(status_message: str, duration_seconds: int = 1, session_id: str = "", phase: str = "planning") -> LayoutView:
    view = LayoutView(timeout=900)
    
    if phase == "execution":
        header_title = f"{OCTICONS_MAP['oct_check']} **Working...**"
    else:
        header_title = f"{OCTICONS_MAP['oct_checklist']} **Planning...**"

    body_text = f"{header_title}\n{LOADING_EMOJI} *{status_message}...*"
    text_item = TextDisplay(body_text)

    time_str = f"{max(1, duration_seconds)}s"
    thought_btn = Button(
        label=f"🧠 Thinking for {time_str}...",
        style=discord.ButtonStyle.secondary,
        custom_id=f"gen_thought_agent_{session_id}"
    )

    stop_btn = Button(
        label="Stop Agent",
        style=discord.ButtonStyle.danger,
        custom_id=f"agent_stop_{session_id}"
    )

    view.add_item(text_item)
    view.add_item(ActionRow(thought_btn, stop_btn))
    return view

def build_agent_completed_header_layout(duration_seconds: int, session_id: str, phase: str = "planning", was_stopped: bool = False) -> LayoutView:
    view = LayoutView(timeout=900)
    time_str = f"{max(1, duration_seconds)}s"

    if was_stopped:
        status_badge = "(Stopped)"
    else:
        status_badge = ""

    if phase == "execution":
        text_content = f"{OCTICONS_MAP['oct_check']} Worked for `{time_str}` {status_badge}".strip()
    else:
        text_content = f"{OCTICONS_MAP['oct_checklist']} Planned for `{time_str}` {status_badge}".strip()

    view_btn = Button(
        label="View ↗",
        style=discord.ButtonStyle.secondary,
        custom_id=f"gen_thought_agent_{session_id}"
    )
    view.add_item(Section(TextDisplay(text_content), accessory=view_btn))
    return view

def format_agent_step_summary(tool_name: str, args: dict[str, Any], additions: int = 0, deletions: int = 0) -> str:
    if tool_name == "agent_read_file":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        return f"{OCTICONS_MAP['oct_checklist']} Read {f_icon} **`{p}`**"
    elif tool_name == "agent_write_file":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        return f"{OCTICONS_MAP['oct_pencil']} {f_icon} Created **`{p}`**"
    elif tool_name == "agent_edit_diff":
        p = args.get("path", "file")
        f_icon = get_file_icon(p)
        diff_tag = f" (+{additions} -{deletions})" if (additions > 0 or deletions > 0) else ""
        return f"{OCTICONS_MAP['oct_diff']} {f_icon} Patched **`{p}`**{diff_tag}"
    elif tool_name == "agent_list_dir":
        sub = args.get("subpath") or "./"
        return f"{OCTICONS_MAP['oct_search']} Listed directory **`{sub}`**"
    elif tool_name == "agent_terminal":
        cmd = args.get("command", "")[:35]
        return f"{OCTICONS_MAP['oct_terminal']} Executed **`{cmd}`**"
    elif tool_name == "agent_search_web":
        q = args.get("query", "")[:35]
        return f'{OCTICONS_MAP["oct_search"]} Searched web **"{q}"**'
    elif tool_name == "agent_read_link":
        u = args.get("url", "")[:35]
        return f"{OCTICONS_MAP['oct_link']} Read link **`{u}`**"
    elif tool_name == "agent_search_discord_history":
        q = args.get("query", "")[:35]
        return f'{OCTICONS_MAP["oct_search"]} Searched Discord history **"{q}"**'
    elif tool_name == "clone_repo":
        repo = args.get("repo", "repository")
        return f"{OCTICONS_MAP['oct_repo']} Cloned repository **`{repo}`**"
    elif tool_name == "github_repo":
        repo = args.get("repo", "repository")
        action = args.get("action", "digest")
        return f"{OCTICONS_MAP['oct_repo']} GitHub **{action}** (`{repo}`)"
    
    return f"{OCTICONS_MAP.get('oct_terminal', '⚙️')} Used **{tool_name}**"

def build_agent_step_layout(tool_name: str, args: dict[str, Any], duration_ms: int, session_id: str, step_idx: int, additions: int = 0, deletions: int = 0) -> LayoutView:
    view = LayoutView(timeout=900)
    summary_text = format_agent_step_summary(tool_name, args, additions, deletions)
    time_badge = f" • `{duration_ms}ms`" if duration_ms > 0 else ""
    full_text = f"{summary_text}{time_badge}"
    view_btn = Button(
        label="View ↗",
        style=discord.ButtonStyle.secondary,
        custom_id=f"agent_step_view_{session_id}_{step_idx}"
    )
    view.add_item(Section(TextDisplay(full_text[:1500]), accessory=view_btn))
    return view

class AgentStepInspectorView(LayoutView):
    def __init__(self, step_data: dict[str, Any]):
        super().__init__(timeout=600)
        self.step_data = step_data
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        container = Container()

        t_name = self.step_data.get("name", "Tool")
        args = self.step_data.get("args", {})
        result = self.step_data.get("result", {})
        dur_ms = self.step_data.get("duration_ms", 0)
        diff_text = self.step_data.get("diff_text", "")
        adds = self.step_data.get("additions", 0)
        dels = self.step_data.get("deletions", 0)

        summary_line = format_agent_step_summary(t_name, args, adds, dels)
        time_tag = f" • `{dur_ms}ms`" if dur_ms > 0 else ""

        container.add_item(TextDisplay(f"# {summary_line}{time_tag}"))
        container.add_item(Separator(visible=True))

        if t_name == "agent_edit_diff":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            stats_tag = f" (+{adds} additions • -{dels} deletions)" if (adds > 0 or dels > 0) else ""
            container.add_item(TextDisplay(f"### {f_icon} Patch Diff: `{path}`{stats_tag}\n```diff\n{diff_text[:3000]}\n```"))

        elif t_name == "agent_write_file":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            ext = path.split(".")[-1].lower() if "." in path else "text"
            content = args.get("content", "")
            lines = len(content.splitlines())
            container.add_item(TextDisplay(f"### {f_icon} Created File: `{path}` ({lines} lines)\n```{ext}\n{content[:3000]}\n```"))

        elif t_name == "agent_read_file":
            path = args.get("path", "file")
            f_icon = get_file_icon(path)
            ext = path.split(".")[-1].lower() if "." in path else "text"
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            lines_tag = result.get("showing_lines", "") if isinstance(result, dict) else ""
            container.add_item(TextDisplay(f"### {f_icon} Source View: `{path}` (Lines {lines_tag})\n```{ext}\n{content[:3000]}\n```"))

        elif t_name == "agent_terminal":
            cmd = args.get("command", "")
            exit_code = result.get("exit_code", 0) if isinstance(result, dict) else 0
            stdout = result.get("stdout", "(no output)") if isinstance(result, dict) else str(result)
            stderr = result.get("stderr") if isinstance(result, dict) else None
            status_tag = "✅ Exit Code: `0` (Success)" if exit_code == 0 else f"❌ Exit Code: `{exit_code}` (Failed)"
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_terminal']} Command: `{cmd}`\n{status_tag}\n\n**Output:**\n```text\n{stdout[:2200]}\n```"))
            if stderr:
                container.add_item(TextDisplay(f"**Alerts & Stderr:**\n```text\n{stderr[:1000]}\n```"))

        elif t_name == "agent_list_dir":
            subpath = args.get("subpath") or "./"
            files = result.get("files", []) if isinstance(result, dict) else []
            total_count = result.get("file_count", len(files)) if isinstance(result, dict) else len(files)
            file_list_str = "\n".join([f"• `{f}`" for f in files[:45]])
            if total_count > 45:
                file_list_str += f"\n-# ... and {total_count - 45} more files"
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Workspace Directory: `{subpath}` ({total_count} files total)\n{file_list_str}"))

        elif t_name == "agent_search_web":
            query = args.get("query", "")
            res_items = result.get("results", []) if isinstance(result, dict) else []
            links_text = "\n".join([f"• **[{r.get('title', 'Source')}]({r.get('link', '')})**\n  {r.get('snippet', '')}" for r in res_items[:4]])
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Query: \"{query}\"\n\n**Sources Found:**\n{links_text}" if links_text else "*No search results found*"))

        elif t_name == "agent_read_link":
            url = args.get("url", "")
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_link']} Source Article: [Read Link]({url})\n\n```text\n{content[:2500]}\n```"))

        elif t_name == "agent_search_discord_history":
            query = args.get("query", "")
            matched = result.get("results", []) if isinstance(result, dict) else []
            history_text = "\n".join([f"• **{m.get('author', 'User')}**: {m.get('content', '')}" for m in matched[:8]])
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_search']} Discord History Query: \"{query}\"\n\n{history_text or '*No matches found*'}\n"))

        elif t_name == "github_repo":
            repo = args.get("repo", "")
            action = args.get("action", "digest")
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_repo']} GitHub {action.capitalize()}: `{repo}`\n```json\n{str(result)[:2500]}\n```"))

        elif t_name == "clone_repo":
            repo = args.get("repo", "")
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_repo']} Cloned Repository: `{repo}`\nRepository source files have been synchronized into the workspace root `./`."))

        else:
            arg_lines = "\n".join([f"• **{k}**: `{v}`" for k, v in args.items()]) or "*None*"
            container.add_item(TextDisplay(f"**Parameters:**\n{arg_lines}\n\n**Result:**\n```json\n{str(result)[:2500]}\n```"))

        self.add_item(container)

class AgentQuestionView(LayoutView):
    def __init__(
        self,
        conversational_text: str,
        questions: list[dict[str, Any]],
        session: dict[str, Any],
        citations: list[str] | None = None,
        thought_duration: int = 1,
        guild: discord.Guild | None = None,
        on_submit_callback: Callable = None,
        disabled: bool = False
    ):
        super().__init__(timeout=1800)
        self.conversational_text = conversational_text
        self.questions = questions
        self.session = session
        self.citations = citations or []
        self.thought_duration = max(1, thought_duration)
        self.guild = guild
        self.on_submit_callback = on_submit_callback
        self.disabled = disabled
        self.selected_answers: dict[str, str] = {}
        self.custom_options: dict[str, str] = {}
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        if self.conversational_text.strip():
            parsed_text = apply_message_parsers(self.conversational_text, self.guild)
            self.add_item(TextDisplay(parsed_text))
            self.add_item(Separator(visible=True))

        container = Container()
        header_str = f"{OCTICONS_MAP['oct_question']} **Clarifications Needed**\nPlease select your preferences to guide the plan/research:"
        container.add_item(TextDisplay(header_str))
        container.add_item(Separator(visible=True))

        for q in self.questions:
            q_id = q["id"]
            q_label = q["label"]
            options_data = q.get("options", [])

            discord_opts = []
            for opt in options_data:
                is_def = (self.selected_answers.get(q_id) == opt["value"])
                discord_opts.append(
                    discord.SelectOption(
                        label=opt["label"][:100],
                        value=opt["value"][:100],
                        description=opt.get("description", "")[:100] or None,
                        default=is_def
                    )
                )

            if q_id in self.custom_options:
                c_val = self.custom_options[q_id]
                discord_opts.append(
                    discord.SelectOption(
                        label=f"Custom: {c_val[:80]}",
                        value=c_val[:100],
                        default=(self.selected_answers.get(q_id) == c_val)
                    )
                )

            discord_opts.append(
                discord.SelectOption(
                    label="Custom...",
                    value=f"__custom__{q_id}",
                    description="Enter your own response"
                )
            )

            sel = Select(
                custom_id=f"agent_q_{q_id}",
                placeholder=q_label[:100],
                options=discord_opts[:25],
                disabled=self.disabled
            )
            sel.callback = self._create_select_callback(q_id, sel)
            container.add_item(ActionRow(sel))

        self.add_item(container)

        if self.citations:
            self.add_item(Separator(visible=True))
            cit_lines = [f"{OCTICONS_MAP['oct_info']} **Sources & Citations:**"]
            for cit in self.citations[:8]:
                cit_lines.append(f"• {cit}")
            self.add_item(TextDisplay("\n".join(cit_lines)))

        self.add_item(Separator(visible=True))
        submit_btn = Button(
            label="Submit Answers",
            style=discord.ButtonStyle.primary,
            custom_id="btn_agent_submit_answers",
            disabled=self.disabled or len(self.selected_answers) < len(self.questions)
        )
        submit_btn.callback = self._on_submit_clicked

        time_str = f"{max(1, self.thought_duration)}s"
        thought_btn = Button(
            label=f"🧠 Thought for {time_str}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gen_thought_agent_{self.session['session_id']}"
        )

        self.add_item(ActionRow(submit_btn, thought_btn))

    def _create_select_callback(self, q_id: str, select_comp: Select):
        async def callback(interaction: discord.Interaction):
            from agent.session_manager import session_manager
            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(self.session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can answer.", ephemeral=True)
                return

            chosen = select_comp.values[0] if select_comp.values else ""
            if chosen == f"__custom__{q_id}":
                fields = [
                    {
                        "type": "text_input",
                        "custom_id": "custom_val",
                        "label": "Custom Answer",
                        "style": "short",
                        "required": True,
                        "max_length": 100
                    }
                ]
                async def on_custom_sub(s_inter: discord.Interaction, data: dict[str, Any]):
                    val = data.get("custom_val", "").strip()
                    if val:
                        self.custom_options[q_id] = val
                        self.selected_answers[q_id] = val
                    self._build_layout()
                    await s_inter.response.edit_message(view=self)

                modal = DynamicModalV2(title="Custom Response", custom_id=f"modal_q_custom_{q_id}", fields_schema=fields, on_submit_callback=on_custom_sub)
                await interaction.response.send_modal(modal)
            else:
                self.selected_answers[q_id] = chosen
                self._build_layout()
                await interaction.response.edit_message(view=self)
        return callback

    async def _on_submit_clicked(self, interaction: discord.Interaction):
        from agent.session_manager import session_manager
        perms = getattr(interaction.user, "guild_permissions", None)
        if not session_manager.is_collaborator(self.session, interaction.user.id, perms):
            await interaction.response.send_message(content="❌ Only session collaborators can submit answers.", ephemeral=True)
            return

        self.disabled = True
        self._build_layout()
        await interaction.response.edit_message(view=self)

        if self.on_submit_callback:
            await self.on_submit_callback(interaction, self.selected_answers)

class AgentPlanApprovalView(LayoutView):
    def __init__(
        self,
        conversational_text: str,
        artifact: dict[str, Any],
        session: dict[str, Any],
        citations: list[str] | None = None,
        thought_duration: int = 1,
        guild: discord.Guild | None = None,
        on_approve_callback: Callable = None,
        disabled: bool = False
    ):
        super().__init__(timeout=3600)
        self.conversational_text = conversational_text
        self.artifact = artifact
        self.session = session
        self.citations = citations or []
        self.thought_duration = max(1, thought_duration)
        self.guild = guild
        self.on_approve_callback = on_approve_callback
        self.disabled = disabled
        self._build_layout()

    def _build_layout(self):
        self.clear_items()
        fn = self.artifact.get("filename", "plan.md")
        is_research_plan = "research" in fn or self.session.get("task_type") == "research"
        plan_title = "Research Plan" if is_research_plan else ("Implementation Plan" if self.session.get("task_type") == "coding" else "Plan")

        if self.conversational_text.strip():
            parsed_text = apply_message_parsers(self.conversational_text, self.guild)
            self.add_item(TextDisplay(parsed_text))
            self.add_item(Separator(visible=True))

        container = Container()
        content = self.artifact.get("content", "")
        lines = self.artifact.get("total_lines") or len(content.splitlines()) or 1
        size_b = self.artifact.get("size_bytes") or len(content.encode("utf-8")) or 100
        size_str = f"{size_b / 1024:.1f} KB" if size_b >= 1024 else f"{size_b} B"

        icon = OCTICONS_MAP["oct_book"] if is_research_plan else OCTICONS_MAP["oct_checklist"]
        display_text = f"{icon} **{plan_title}** (`{fn}`)\n-# {lines:,} lines • {size_str}"

        art_id = self.artifact.get("artifact_id", "plan")
        preview_btn = Button(
            label="Preview",
            style=discord.ButtonStyle.secondary,
            custom_id=f"artprev:{self.session['session_id']}:{art_id}:1"
        )
        container.add_item(Section(TextDisplay(display_text), accessory=preview_btn))
        self.add_item(container)

        if self.citations:
            self.add_item(Separator(visible=True))
            cit_lines = [f"{OCTICONS_MAP['oct_info']} **Sources & Citations:**"]
            for cit in self.citations[:8]:
                cit_lines.append(f"• {cit}")
            self.add_item(TextDisplay("\n".join(cit_lines)))

        self.add_item(Separator(visible=True))

        time_str = f"{max(1, self.thought_duration)}s"
        thought_btn = Button(
            label=f"🧠 Thought for {time_str}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gen_thought_agent_{self.session['session_id']}"
        )

        approve_btn = Button(
            label="Approve Plan",
            style=discord.ButtonStyle.primary,
            custom_id=f"agent_approve_plan_{self.session['session_id']}",
            disabled=self.disabled
        )
        approve_btn.callback = self._on_approve_clicked

        self.add_item(ActionRow(thought_btn, approve_btn))

    async def _on_approve_clicked(self, interaction: discord.Interaction):
        from agent.session_manager import session_manager
        perms = getattr(interaction.user, "guild_permissions", None)
        if not session_manager.is_collaborator(self.session, interaction.user.id, perms):
            await interaction.response.send_message(content="❌ Only session collaborators can approve the plan.", ephemeral=True)
            return

        self.disabled = True
        self._build_layout()
        await interaction.response.edit_message(view=self)

        if self.on_approve_callback:
            await self.on_approve_callback(interaction)

class AgentFinalDeliverableView(LayoutView):
    def __init__(
        self,
        summary_text: str,
        artifact: dict[str, Any] | None,
        session: dict[str, Any],
        citations: list[str] | None = None,
        thought_duration: int = 1,
        guild: discord.Guild | None = None
    ):
        super().__init__(timeout=None)
        self.summary_text = summary_text
        self.artifact = artifact
        self.session = session
        self.citations = citations or []
        self.thought_duration = max(1, thought_duration)
        self.guild = guild
        self._build_layout()

    def _build_layout(self):
        self.clear_items()

        if self.summary_text.strip():
            parsed_summary = apply_message_parsers(self.summary_text, self.guild)
            self.add_item(TextDisplay(parsed_summary))

        if self.artifact:
            if self.summary_text.strip():
                self.add_item(Separator(visible=True))
            art_items = build_artifact_components_for_message(
                self.artifact,
                message_id=self.session["session_id"],
                is_live_stream=False
            )
            for item in art_items:
                self.add_item(item)

        if self.citations:
            self.add_item(Separator(visible=True))
            cit_lines = [f"{OCTICONS_MAP['oct_info']} **Sources & Citations:**"]
            for cit in self.citations[:10]:
                cit_lines.append(f"• {cit}")
            self.add_item(TextDisplay("\n".join(cit_lines)))

        self.add_item(Separator(visible=True))
        completion_text = (
            f"{OCTICONS_MAP['oct_check']} **Agent Task Completed!** Deliverables are saved in workspace.\n"
            f"-# Click **New Task** to assign another task in this workspace, or continue chatting."
        )
        self.add_item(TextDisplay(completion_text))

        self.add_item(Separator(visible=True))
        time_str = f"{max(1, self.thought_duration)}s"
        thought_btn = Button(
            label=f"🧠 Thought for {time_str}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"gen_thought_agent_{self.session['session_id']}"
        )

        new_task_btn = Button(
            label="New Task",
            style=discord.ButtonStyle.primary,
            emoji=OCTICONS_MAP["oct_checklist"],
            custom_id=f"agent_new_task_{self.session['session_id']}"
        )

        self.add_item(ActionRow(thought_btn, new_task_btn))