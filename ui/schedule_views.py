import uuid
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
from core.schedule_manager import schedule_manager, parse_schedule_time_expression
from core.config_manager import config_manager
from ui.modals import DynamicModalV2
from agent.constants import OCTICONS_MAP, BETA_EMOJI

logger = logging.getLogger("PriestyAI.ScheduleUI")

def is_user_server_admin(user: discord.User | discord.Member, guild: discord.Guild | None) -> bool:
    if not guild or not isinstance(user, discord.Member):
        return False
    if guild.owner_id == user.id or user.guild_permissions.administrator:
        return True
    if user.guild_permissions.manage_guild or user.guild_permissions.manage_channels:
        return True
    
    s_cfg = config_manager.get_server_config(guild.id)
    bypass_list = s_cfg.get("permission_bypass", [])
    user_roles = [str(r.id) for r in user.roles]
    if str(user.id) in bypass_list or any(rid in bypass_list for rid in user_roles):
        return True

    return False

def build_create_schedule_modal(
    scope: str,
    guild: discord.Guild | None,
    on_submit: Callable
) -> DynamicModalV2:
    scope_title = "Server" if scope == "server" else "Personal"
    fields = [
        {
            "type": "text_display",
            "content": (
                f"# Create {scope_title} Task {BETA_EMOJI}\n"
                "Schedule an autonomous AI prompt to run once or on a recurring timeline.\n\n"
                "-# 💡 **Time Examples:** `in 2 hours`, `tomorrow at 9am`, `every day at 8am`, `every friday at 12pm`, `every 4 hours`"
            )
        },
        {
            "type": "text_input",
            "custom_id": "prompt",
            "label": "Prompt to Execute",
            "description": "The AI instructions, research query, or workflow to run",
            "placeholder": "e.g. Search web for AI news and summarize, analyze weekly repository updates...",
            "style": "paragraph",
            "required": True,
            "max_length": 2000
        },
        {
            "type": "text_input",
            "custom_id": "time_expr",
            "label": "Schedule (Time or Recurring)",
            "description": "When or how often should this task run?",
            "placeholder": "e.g. in 2 hours, daily at 8am, every friday at 12pm...",
            "style": "short",
            "required": True,
            "max_length": 100
        }
    ]

    if scope == "server" and guild:
        fields.append({
            "type": "channel_select",
            "custom_id": "target_channel",
            "label": "Target Channel",
            "description": "The text channel where PriestyAI will post output",
            "placeholder": "Select a text channel...",
            "required": True
        })
        fields.append({
            "type": "radio_group",
            "custom_id": "dm_delivery",
            "label": "Notification Delivery",
            "description": "Choose delivery options",
            "value": "channel_only",
            "options": [
                {"label": "Post in Target Channel Only", "value": "channel_only", "description": "Deliver directly to selected text channel", "default": True},
                {"label": "Post in Channel + Send DM Copy to Me", "value": "channel_and_dm", "description": "Public channel post plus private DM copy"}
            ],
            "required": True
        })

    return DynamicModalV2(
        title=f"New {scope_title} Task",
        custom_id=f"modal_create_sched_{scope}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )

def build_edit_schedule_modal(
    task: dict[str, Any],
    guild: discord.Guild | None,
    on_submit: Callable
) -> DynamicModalV2:
    scope = task.get("scope", "personal")
    scope_title = "Server" if scope == "server" else "Personal"
    t_id = task["task_id"]

    fields = [
        {
            "type": "text_display",
            "content": f"# Edit Task #{t_id} ({scope_title})\nUpdate prompt, schedule, or check the box below to delete."
        },
        {
            "type": "text_input",
            "custom_id": "prompt",
            "label": "Prompt",
            "style": "paragraph",
            "value": task.get("prompt_text", ""),
            "required": True,
            "max_length": 2000
        },
        {
            "type": "text_input",
            "custom_id": "time_expr",
            "label": "Schedule (Time or Recurring)",
            "style": "short",
            "value": task.get("time_expression", ""),
            "required": True,
            "max_length": 100
        }
    ]

    if scope == "server" and guild:
        chan_id = task.get("channel_id")
        fields.append({
            "type": "channel_select",
            "custom_id": "target_channel",
            "label": "Target Channel",
            "default_values": [{"id": str(chan_id), "type": "channel"}] if chan_id else None,
            "required": True
        })

    fields.append({
        "type": "checkbox",
        "custom_id": "delete_task",
        "label": "Delete Task Permanently",
        "description": "Check this box to remove this schedule completely",
        "default": False
    })

    return DynamicModalV2(
        title=f"Edit Task #{t_id}"[:45],
        custom_id=f"modal_edit_sched_{t_id}",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


class ScheduleDashboardView(LayoutView):
    def __init__(
        self,
        user: discord.User | discord.Member,
        guild: discord.Guild | None = None,
        active_tab: str = "all"
    ):
        super().__init__(timeout=600)
        self.user = user
        self.guild = guild
        self.active_tab = "personal" if not guild else active_tab.lower()
        self._build_dashboard()

    def _build_dashboard(self):
        self.clear_items()
        container = Container()

        is_admin = is_user_server_admin(self.user, self.guild)
        has_guild = self.guild is not None

        server_tasks = schedule_manager.get_tasks_for_guild(self.guild.id) if has_guild else []
        personal_tasks = schedule_manager.get_tasks_for_user(self.user.id)
        total_tasks = len(server_tasks) + len(personal_tasks)

        header_lines = [
            f"# {OCTICONS_MAP['oct_calendar']} Scheduled Tasks {BETA_EMOJI}",
            f"Automate recurring AI research, daily briefs, and custom workflows on a timeline.\n"
        ]
        if has_guild:
            header_lines.append(f"• **All Tasks:** `{total_tasks}` active")
            header_lines.append(f"• **Server Tasks ({self.guild.name}):** `{len(server_tasks)}`")
        header_lines.append(f"• **Personal Tasks (Your DMs):** `{len(personal_tasks)}`")
        
        container.add_item(TextDisplay("\n".join(header_lines)))
        container.add_item(Separator(visible=True))

        tab_options = []
        if has_guild:
            tab_options.append(
                discord.SelectOption(
                    label="All Tasks",
                    value="all",
                    description=f"Combined view ({total_tasks})",
                    emoji=OCTICONS_MAP["oct_checklist"],
                    default=(self.active_tab == "all")
                )
            )
            tab_options.append(
                discord.SelectOption(
                    label="Server Tasks",
                    value="server",
                    description=f"Guild channel AI workflows ({len(server_tasks)})",
                    emoji=OCTICONS_MAP["oct_server"],
                    default=(self.active_tab == "server")
                )
            )
        
        tab_options.append(
            discord.SelectOption(
                label="Personal Tasks",
                value="personal",
                description=f"Your private AI DM schedules ({len(personal_tasks)})",
                emoji=OCTICONS_MAP["oct_person"],
                default=(self.active_tab == "personal")
            )
        )

        tab_select = Select(
            custom_id="select_sched_tab",
            placeholder="Switch Tab...",
            options=tab_options
        )
        tab_select.callback = self._on_tab_switched
        container.add_item(ActionRow(tab_select))
        container.add_item(Separator(visible=True))

        if self.active_tab == "all" and has_guild:
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_checklist']} All Tasks\nUnified overview of server workflows and your private DM schedules."))
            combined = sorted(server_tasks + personal_tasks, key=lambda x: x.get("next_run_timestamp", 0))

            if not combined:
                container.add_item(TextDisplay("*No active scheduled tasks found.*"))
            else:
                for t in combined[:8]:
                    t_id = t["task_id"]
                    prompt = t["prompt_text"]
                    time_sum = t["summary_schedule"]
                    next_ts = t["next_run_timestamp"]
                    scope = t.get("scope", "personal")
                    c_id = t.get("channel_id")
                    active_tag = "" if t.get("is_active", 1) else " `(Paused)`"

                    if scope == "server":
                        badge = f"{OCTICONS_MAP['oct_server']} <#{c_id}>" if c_id else f"{OCTICONS_MAP['oct_server']} *Server*"
                        can_edit = is_admin
                    else:
                        badge = f"{OCTICONS_MAP['oct_person']} *Private DM*"
                        can_edit = (str(self.user.id) == str(t.get("user_id")))

                    snippet = prompt[:85] + ("..." if len(prompt) > 85 else "")
                    body = (
                        f"**Task #{t_id}** — {badge}{active_tag}\n"
                        f"> {snippet}\n"
                        f"-# Schedule: **{time_sum}** • Next run: <t:{next_ts}:R>"
                    )

                    if can_edit:
                        edit_btn = Button(
                            label="Edit",
                            style=discord.ButtonStyle.secondary,
                            custom_id=f"btn_edit_task_{t_id}"
                        )
                        edit_btn.callback = self._create_edit_callback(t)
                        container.add_item(Section(TextDisplay(body), accessory=edit_btn))
                    else:
                        container.add_item(TextDisplay(body))

        elif self.active_tab == "server" and has_guild:
            if not is_admin:
                container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_server']} Server Tasks — {self.guild.name} `(Read-Only)`\n-# You have view-only access. Server Administrators and Managers configure guild tasks."))
            else:
                container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_server']} Server Tasks — {self.guild.name}\nAutomated AI prompts delivering to designated server text channels."))

            if not server_tasks:
                container.add_item(TextDisplay("*No server tasks currently scheduled for this guild.*"))
            else:
                for t in server_tasks[:6]:
                    t_id = t["task_id"]
                    prompt = t["prompt_text"]
                    time_sum = t["summary_schedule"]
                    next_ts = t["next_run_timestamp"]
                    c_id = t.get("channel_id")
                    chan_str = f"<#{c_id}>" if c_id else "*Unassigned*"
                    active_tag = "" if t.get("is_active", 1) else " `(Paused)`"

                    snippet = prompt[:90] + ("..." if len(prompt) > 90 else "")
                    body = (
                        f"**Task #{t_id}** — {chan_str}{active_tag}\n"
                        f"> {snippet}\n"
                        f"-# Schedule: **{time_sum}** • Next run: <t:{next_ts}:R>"
                    )

                    if is_admin:
                        edit_btn = Button(
                            label="Edit",
                            style=discord.ButtonStyle.secondary,
                            custom_id=f"btn_edit_task_{t_id}"
                        )
                        edit_btn.callback = self._create_edit_callback(t)
                        container.add_item(Section(TextDisplay(body), accessory=edit_btn))
                    else:
                        container.add_item(TextDisplay(body))

        else:
            container.add_item(TextDisplay(f"### {OCTICONS_MAP['oct_person']} Personal Tasks (Bot DMs)\nPrivate AI tasks and reminders delivered directly to your DMs."))
            if not personal_tasks:
                container.add_item(TextDisplay("*No personal DM tasks currently scheduled.*"))
            else:
                for t in personal_tasks[:6]:
                    t_id = t["task_id"]
                    prompt = t["prompt_text"]
                    time_sum = t["summary_schedule"]
                    next_ts = t["next_run_timestamp"]
                    active_tag = "" if t.get("is_active", 1) else " `(Paused)`"

                    snippet = prompt[:90] + ("..." if len(prompt) > 90 else "")
                    body = (
                        f"**Task #{t_id}** (Private DM){active_tag}\n"
                        f"> {snippet}\n"
                        f"-# Schedule: **{time_sum}** • Next run: <t:{next_ts}:R>"
                    )
                    edit_btn = Button(
                        label="Edit",
                        style=discord.ButtonStyle.secondary,
                        custom_id=f"btn_edit_task_{t_id}"
                    )
                    edit_btn.callback = self._create_edit_callback(t)
                    container.add_item(Section(TextDisplay(body), accessory=edit_btn))

        container.add_item(Separator(visible=True))

        bottom_buttons = []
        if self.active_tab == "all" and has_guild:
            if is_admin:
                btn_srv = Button(label="+ Server Task", style=discord.ButtonStyle.primary, custom_id="btn_add_srv_task")
                btn_srv.callback = lambda i: self._trigger_create_modal(i, scope="server")
                bottom_buttons.append(btn_srv)

            btn_usr = Button(label="+ Personal Task", style=discord.ButtonStyle.secondary, custom_id="btn_add_usr_task")
            btn_usr.callback = lambda i: self._trigger_create_modal(i, scope="personal")
            bottom_buttons.append(btn_usr)

        elif self.active_tab == "server" and has_guild:
            if is_admin:
                create_btn = Button(label="+ Create Server Task", style=discord.ButtonStyle.primary, custom_id="btn_create_srv_only")
                create_btn.callback = lambda i: self._trigger_create_modal(i, scope="server")
                bottom_buttons.append(create_btn)

        else:
            create_btn = Button(label="+ Create Personal Task", style=discord.ButtonStyle.primary, custom_id="btn_create_usr_only")
            create_btn.callback = lambda i: self._trigger_create_modal(i, scope="personal")
            bottom_buttons.append(create_btn)

        refresh_btn = Button(
            label="Refresh",
            style=discord.ButtonStyle.secondary,
            emoji=OCTICONS_MAP["oct_sync"],
            custom_id="btn_refresh_sched_dash"
        )
        refresh_btn.callback = self._on_refresh_clicked
        bottom_buttons.append(refresh_btn)

        container.add_item(ActionRow(*bottom_buttons))
        self.add_item(container)

    async def _on_tab_switched(self, interaction: discord.Interaction):
        if interaction.data and "values" in interaction.data and interaction.data["values"]:
            self.active_tab = interaction.data["values"][0]
        self._build_dashboard()
        await interaction.response.edit_message(view=self)

    async def _on_refresh_clicked(self, interaction: discord.Interaction):
        self._build_dashboard()
        await interaction.response.edit_message(view=self)

    async def _trigger_create_modal(self, interaction: discord.Interaction, scope: str):
        is_admin = is_user_server_admin(interaction.user, self.guild)
        if scope == "server" and not is_admin:
            await interaction.response.send_message(
                content=f"{OCTICONS_MAP['oct_lock']} Only Server Administrators and Managers can schedule tasks for this server.",
                ephemeral=True
            )
            return

        async def on_create_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            prompt = data.get("prompt", "").strip()
            time_expr = data.get("time_expr", "").strip()
            target_chan = data.get("target_channel")
            if isinstance(target_chan, list) and target_chan:
                target_chan = target_chan[0]

            raw_delivery = data.get("dm_delivery", "dm_only" if scope == "personal" else "channel_only")
            if isinstance(raw_delivery, list) and raw_delivery:
                raw_delivery = raw_delivery[0]

            if not prompt or not time_expr:
                await sub_inter.response.send_message(content="❌ Prompt and Schedule time are required.", ephemeral=True)
                return

            try:
                next_ts, interval_type, interval_sec, summary_str = parse_schedule_time_expression(time_expr)
            except Exception as e:
                await sub_inter.response.send_message(content=f"❌ **Invalid Time Expression:** {e}", ephemeral=True)
                return

            task_id = str(uuid.uuid4())[:8]
            schedule_manager.create_task(
                task_id=task_id,
                user_id=interaction.user.id,
                user_name=interaction.user.display_name,
                guild_id=self.guild.id if self.guild else None,
                channel_id=target_chan if (scope == "server" and target_chan) else None,
                scope=scope,
                prompt_text=prompt,
                time_expression=time_expr,
                summary_schedule=summary_str,
                next_run_timestamp=next_ts,
                interval_type=interval_type,
                interval_seconds=interval_sec,
                dm_delivery=raw_delivery
            )

            self._build_dashboard()
            await sub_inter.response.edit_message(view=self)

        modal = build_create_schedule_modal(scope, self.guild, on_create_submit)
        await interaction.response.send_modal(modal)

    def _create_edit_callback(self, task: dict[str, Any]):
        async def callback(interaction: discord.Interaction):
            is_admin = is_user_server_admin(interaction.user, self.guild)
            is_creator = str(interaction.user.id) == str(task.get("user_id"))

            if task.get("scope") == "server" and not is_admin:
                await interaction.response.send_message(content="❌ You lack permission to edit server tasks.", ephemeral=True)
                return
            if task.get("scope") == "personal" and not is_creator:
                await interaction.response.send_message(content="❌ You can only edit your own personal tasks.", ephemeral=True)
                return

            async def on_edit_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                is_delete = bool(data.get("delete_task", False))

                if is_delete:
                    schedule_manager.delete_task(task["task_id"])
                    self._build_dashboard()
                    await sub_inter.response.edit_message(view=self)
                    return

                new_prompt = data.get("prompt", "").strip() or task["prompt_text"]
                new_time_expr = data.get("time_expr", "").strip() or task["time_expression"]

                target_chan = data.get("target_channel")
                if isinstance(target_chan, list) and target_chan:
                    target_chan = target_chan[0]
                elif not target_chan:
                    target_chan = task.get("channel_id")

                try:
                    next_ts, interval_type, interval_sec, summary_str = parse_schedule_time_expression(new_time_expr)
                except Exception as e:
                    await sub_inter.response.send_message(content=f"❌ **Invalid Time Expression:** {e}", ephemeral=True)
                    return

                schedule_manager.update_task(
                    task_id=task["task_id"],
                    prompt_text=new_prompt,
                    time_expression=new_time_expr,
                    summary_schedule=summary_str,
                    next_run_timestamp=next_ts,
                    interval_type=interval_type,
                    interval_seconds=interval_sec,
                    channel_id=target_chan,
                    is_active=1
                )

                self._build_dashboard()
                await sub_inter.response.edit_message(view=self)

            modal = build_edit_schedule_modal(task, self.guild, on_edit_submit)
            await interaction.response.send_modal(modal)

        return callback