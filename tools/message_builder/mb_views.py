import discord
import logging
import io
import uuid
import copy
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger("MBViews")


DSL_STATE_STORAGE: Dict[int, Dict[int, Any]] = {}


class DSLRuntimeView(discord.ui.LayoutView):
    """
    Subclass representing compiled Layout V2 configurations, managing
    timeout hooks, expirations, and safe callbacks.
    """

    def __init__(
        self,
        bot_instance,
        channel,
        dsl_view_config: Dict[str, Any],
        initial_prompt: str,
        user_app_session_id: Optional[int] = None,
    ):
        super().__init__(timeout=300.0)
        self.bot = bot_instance
        self.channel = channel
        self.config = dsl_view_config
        self.initial_prompt = initial_prompt
        self.user_app_session_id = user_app_session_id

        build_layout_tree(self, [dsl_view_config])

    async def on_timeout(self) -> None:
        """Handles visual disabling of items once the timeout threshold expires."""
        logger.info(
            f"MessageBuilder LayoutView timed out for channel {self.channel.id}"
        )
        for child in self.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True

        on_expire_action = self.config.get("kwargs", {}).get("on_expire")
        if on_expire_action:
            try:

                payload_state = DSL_STATE_STORAGE.pop(self.channel.id, {})
                context_summary = f"[System Alert: Layout expired. Compiled survey inputs: {payload_state}]"
                await self.bot.trigger_message_builder_ai_turn(None, context_summary)
            except Exception as e:
                logger.error(f"Failed to execute layout expire callback: {e}")


class DSLButton(discord.ui.Button):
    def __init__(
        self,
        label: str,
        style: discord.ButtonStyle,
        url: Optional[str],
        on_click_actions: Any,
        custom_id: str,
        bot_instance,
        channel,
    ):
        super().__init__(label=label, style=style, url=url, custom_id=custom_id)
        self.actions = (
            on_click_actions
            if isinstance(on_click_actions, list)
            else [on_click_actions]
        )
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        await process_action_chain(self.bot, interaction, self.channel, self.actions)


class DSLUserSelect(discord.ui.UserSelect):
    def __init__(
        self,
        placeholder: str,
        min_values: int,
        max_values: int,
        on_select_actions: Any,
        custom_id: str,
        bot_instance,
        channel,
    ):
        super().__init__(
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            custom_id=custom_id,
        )
        self.actions = (
            on_select_actions
            if isinstance(on_select_actions, list)
            else [on_select_actions]
        )
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0] if self.values else "None"
        summary = f"{selected.display_name} (@{selected.name}) [ID: {selected.id}]"

        if self.channel.id not in DSL_STATE_STORAGE:
            DSL_STATE_STORAGE[self.channel.id] = {}
        DSL_STATE_STORAGE[self.channel.id][interaction.user.id] = summary

        await process_action_chain(
            self.bot, interaction, self.channel, self.actions, summary
        )


class DSLStringSelect(discord.ui.Select):
    def __init__(
        self,
        placeholder: str,
        options: List[discord.SelectOption],
        min_values: int,
        max_values: int,
        on_select_actions: Any,
        custom_id: str,
        bot_instance,
        channel,
    ):
        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=min_values,
            max_values=max_values,
            custom_id=custom_id,
        )
        self.actions = (
            on_select_actions
            if isinstance(on_select_actions, list)
            else [on_select_actions]
        )
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0] if self.values else "None"

        if self.channel.id not in DSL_STATE_STORAGE:
            DSL_STATE_STORAGE[self.channel.id] = {}
        DSL_STATE_STORAGE[self.channel.id][interaction.user.id] = selected_value

        await process_action_chain(
            self.bot, interaction, self.channel, self.actions, selected_value
        )


class DSLV2Modal(discord.ui.Modal):
    """
    Dedicated V2 Modal supporting labels, checkboxes, radio groups, and native file uploads.
    """

    def __init__(self, title: str, on_submit_actions: Any, bot_instance, channel):

        super().__init__(title=(title[:45] if title else "Form Popup"))
        self.bot = bot_instance
        self.channel = channel
        self.actions = (
            on_submit_actions
            if isinstance(on_submit_actions, list)
            else [on_submit_actions]
        )

    async def on_submit(self, interaction: discord.Interaction):
        results = []
        for child in self.children:
            if isinstance(child, discord.ui.Label):
                nested = child.component
                label_text = child.text or "Input"

                if isinstance(nested, discord.ui.TextInput):
                    results.append(f"{label_text}: {nested.value}")
                elif isinstance(
                    nested, (discord.ui.CheckboxGroup, discord.ui.RadioGroup)
                ):
                    selected_vals = nested.values
                    results.append(
                        f"{label_text}: {', '.join(selected_vals) if selected_vals else 'None'}"
                    )
                elif isinstance(nested, discord.ui.FileUpload):
                    uploaded_files = nested.files
                    file_names = (
                        [f.filename for f in uploaded_files] if uploaded_files else []
                    )
                    results.append(
                        f"{label_text}: [Uploaded Files: {', '.join(file_names) if file_names else 'None'}]"
                    )
                elif isinstance(
                    nested,
                    (
                        discord.ui.Select,
                        discord.ui.UserSelect,
                        discord.ui.RoleSelect,
                        discord.ui.ChannelSelect,
                        discord.ui.MentionableSelect,
                    ),
                ):
                    selected_vals = []
                    for val in nested.values:
                        if hasattr(val, "mention"):
                            selected_vals.append(f"{val} [ID: {val.id}]")
                        elif hasattr(val, "name"):
                            selected_vals.append(val.name)
                        else:
                            selected_vals.append(str(val))
                    results.append(
                        f"{label_text}: {', '.join(selected_vals) if selected_vals else 'None'}"
                    )

        summary_payload = " | ".join(results)
        await process_action_chain(
            self.bot, interaction, self.channel, self.actions, summary_payload
        )


def build_layout_item(cfg: Dict[str, Any], bot_instance, channel) -> Optional[Any]:
    """Unified recursive factory method returning fully configured, native V2 items."""
    if not isinstance(cfg, dict):
        return None

    comp_type = cfg.get("type", "").split(".")[-1]
    args = cfg.get("args", [])
    kwargs = cfg.get("kwargs", {})

    custom_id = kwargs.get("id") or f"mb_{uuid.uuid4().hex[:12]}"

    if comp_type == "Container":
        accent = kwargs.get("accent_colour")
        accent_col = None
        if accent is not None:
            if isinstance(accent, int):
                accent_col = discord.Color(accent)
            elif isinstance(accent, str):
                try:
                    accent_col = discord.Color(int(accent, 16))
                except Exception:
                    pass

        container = (
            discord.ui.Container(accent_colour=accent_col)
            if accent_col
            else discord.ui.Container()
        )

        sub_children = args or kwargs.get("children", [])
        for sc in sub_children:
            child_item = build_layout_item(sc, bot_instance, channel)
            if child_item:
                container.add_item(child_item)
        return container

    elif comp_type == "Section":
        accessory_cfg = kwargs.get("accessory")
        accessory_item = None
        if accessory_cfg and isinstance(accessory_cfg, dict):
            accessory_item = build_layout_item(accessory_cfg, bot_instance, channel)

        section = discord.ui.Section(accessory=accessory_item)

        sub_children = []
        if args:
            if isinstance(args[0], list):
                sub_children = args[0]
            else:
                sub_children = args
        elif "children" in kwargs:
            sub_children = kwargs["children"]
            if not isinstance(sub_children, list):
                sub_children = [sub_children]

        for sc in sub_children:
            child_item = build_layout_item(sc, bot_instance, channel)
            if child_item:
                section.add_item(child_item)
        return section

    elif comp_type == "ActionRow":
        action_row = discord.ui.ActionRow()
        sub_children = args or kwargs.get("children", [])
        for sc in sub_children:
            child_item = build_layout_item(sc, bot_instance, channel)
            if child_item:
                action_row.add_item(child_item)
        try:
            if (
                not getattr(action_row, "children", None)
                or len(action_row.children) == 0
            ):
                return None
        except Exception:
            pass
        return action_row

    elif comp_type == "TextDisplay":
        content = args[0] if args else kwargs.get("content", "")

        if content:
            content = content[:1500] + ("..." if len(content) > 1500 else "")
        return discord.ui.TextDisplay(content=content)

    elif comp_type == "Separator":
        spacing_val = kwargs.get("spacing", "small")
        spacing_enum = (
            discord.SeparatorSpacing.large
            if spacing_val == "large"
            else discord.SeparatorSpacing.small
        )
        visible = kwargs.get("visible", True)
        return discord.ui.Separator(spacing=spacing_enum, visible=visible)

    elif comp_type == "Button":
        label = args[0] if args else kwargs.get("label", "Button")
        style_str = kwargs.get("style", "secondary").lower().strip()

        style_map = {
            "primary": discord.ButtonStyle.primary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
            "link": discord.ButtonStyle.link,
            "secondary": discord.ButtonStyle.secondary,
        }
        style = style_map.get(style_str, discord.ButtonStyle.secondary)
        url = kwargs.get("url")
        on_click = kwargs.get("on_click")

        if style_str == "link" or url:
            custom_id = None

        label_clean = label[:80] if label else "Button"

        return DSLButton(
            label=label_clean,
            style=style,
            url=url,
            on_click_actions=on_click,
            custom_id=custom_id,
            bot_instance=bot_instance,
            channel=channel,
        )

    elif comp_type == "UserSelect":
        placeholder = args[0] if args else kwargs.get("placeholder", "Select User...")
        min_v = kwargs.get("min_values", 1)
        max_v = kwargs.get("max_values", 1)
        on_select = kwargs.get("on_select")
        return DSLUserSelect(
            placeholder=placeholder[:150],
            min_values=min_v,
            max_values=max_v,
            on_select_actions=on_select,
            custom_id=custom_id,
            bot_instance=bot_instance,
            channel=channel,
        )

    elif comp_type == "StringSelect":
        placeholder = args[0] if args else kwargs.get("placeholder", "Select Choice...")
        min_v = kwargs.get("min_values", 1)
        max_v = kwargs.get("max_values", 1)
        on_select = kwargs.get("on_select")

        raw_options = kwargs.get("options", [])
        discord_options = []
        for opt_cfg in raw_options:
            if not isinstance(opt_cfg, dict):
                continue
            o_args = opt_cfg.get("args", [])
            o_kwargs = opt_cfg.get("kwargs", {})
            o_label = o_args[0] if len(o_args) > 0 else o_kwargs.get("label", "Option")
            o_value = o_args[1] if len(o_args) > 1 else o_kwargs.get("value", o_label)

            o_label_clean = o_label[:100] if o_label else "Option"
            o_value_clean = o_value[:100] if o_value else o_label_clean
            o_desc = o_kwargs.get("description")
            o_desc_clean = o_desc[:100] if o_desc else None

            discord_options.append(
                discord.SelectOption(
                    label=o_label_clean,
                    value=o_value_clean,
                    description=o_desc_clean,
                    emoji=o_kwargs.get("emoji"),
                )
            )

        return DSLStringSelect(
            placeholder=placeholder[:150],
            options=discord_options,
            min_values=min_v,
            max_values=max_v,
            on_select_actions=on_select,
            custom_id=custom_id,
            bot_instance=bot_instance,
            channel=channel,
        )

    return None


def build_layout_tree(
    parent_view: discord.ui.LayoutView, children_configs: List[Dict[str, Any]]
):
    """Recursively instantiates pure V2 layout items and attaches them directly to the active LayoutView."""
    for cfg in children_configs:
        item = build_layout_item(cfg, parent_view.bot, parent_view.channel)
        if item:
            parent_view.add_item(item)


async def process_action_chain(
    bot_instance,
    interaction: discord.Interaction,
    channel,
    actions_list: List[Any],
    context_value: Optional[str] = None,
):
    """Iterates and runs chained DSL actions sequentially while enforcing API limitations."""
    for action in actions_list:
        if not action:
            continue

        action_type = ""
        action_args = []
        action_kwargs = {}

        if isinstance(action, dict):
            action_type = action.get("type", "").split(".")[-1].lower().strip()
            action_args = action.get("args", [])
            action_kwargs = action.get("kwargs", {})
        elif isinstance(action, str):
            parts = action.split(":", 1)
            action_type = parts[0].lower().strip()
            if len(parts) > 1:
                action_args = [parts[1]]

        if action_type in ("ai", "trigger_ai", "ai_turn"):
            action_type = "ai"
        elif action_type in ("pass", "pass_input", "passinput"):
            action_type = "pass"
        elif action_type in ("disable_components", "disablecomponents", "disable"):
            action_type = "disable_components"
        elif action_type in ("reply_private", "replyprivate", "private_reply"):
            action_type = "reply_private"
        elif action_type in ("reply_public", "replypublic", "public_reply"):
            action_type = "reply_public"
        elif action_type in ("delete_message", "deletemessage", "delete"):
            action_type = "delete_message"
        elif action_type in ("trigger_image_generation", "image_generation", "image"):
            action_type = "trigger_image_generation"
        elif action_type in ("open_modal", "openmodal", "modal"):
            action_type = "open_modal"

        if action_type == "disable_components":
            message = interaction.message
            if message and message.view:
                new_view = copy.copy(message.view)
                for child in new_view.walk_children():
                    if hasattr(child, "disabled"):
                        child.disabled = True
                try:
                    await interaction.response.edit_message(view=new_view)
                except Exception:
                    pass

        elif action_type == "reply_private":
            reply_text = (
                action_args[0]
                if action_args
                else action_kwargs.get("text_content", "Acknowledge selection.")
            )
            if context_value:
                reply_text = reply_text.replace("{value}", context_value)

            if interaction.response.is_done():
                await interaction.followup.send(content=reply_text, ephemeral=True)
            else:
                await interaction.response.send_message(
                    content=reply_text, ephemeral=True
                )

        elif action_type == "reply_public":
            reply_text = (
                action_args[0] if action_args else action_kwargs.get("text_content", "")
            )
            if context_value:
                reply_text = reply_text.replace("{value}", context_value)

            if interaction.response.is_done():
                await interaction.followup.send(content=reply_text)
            else:
                await interaction.response.send_message(content=reply_text)

        elif action_type == "delete_message":
            try:
                await interaction.message.delete()
            except Exception:
                pass

        elif action_type == "pass":
            pass_confirmation = "Selection recorded. You can close this message."
            if interaction.response.is_done():
                await interaction.followup.send(
                    content=pass_confirmation, ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    content=pass_confirmation, ephemeral=True
                )

        elif action_type == "watch_channel":
            channel_id = (
                int(context_value)
                if (context_value and context_value.isdigit())
                else channel.id
            )
            bot_instance.active_channels.add(channel_id)
            await interaction.response.send_message(
                f"✅ Now watching channel: <#{channel_id}>", ephemeral=True
            )

        elif action_type == "trigger_image_generation":
            img_prompt = (
                action_args[0] if action_args else action_kwargs.get("prompt", "")
            )
            if context_value:
                img_prompt = img_prompt.replace("{value}", context_value)

            if interaction.response.is_done():
                placeholder = await interaction.followup.send(
                    "🎨 *Generating Image...*", wait=True
                )
            else:
                await interaction.response.send_message("🎨 *Generating Image...*")
                placeholder = await interaction.original_response()

            is_dm = isinstance(channel, discord.DMChannel)
            target_id = interaction.user.id if is_dm else channel.id
            active_config = await bot_instance.get_config(target_id, is_dm)

            bot_instance.loop.create_task(
                bot_instance._generate_decoupled_image(
                    channel=channel,
                    author=interaction.user,
                    raw_image_prompt=img_prompt,
                    placeholder_msg=placeholder,
                    context_history=bot_instance.history_tracker.get_formatted_history(
                        channel.id
                    ),
                    is_edit_flow=False,
                    original_message=None,
                    banter="",
                    disabled_triggers={},
                    config=active_config,
                )
            )

        elif action_type == "open_modal":
            modal_cfg = action_args[0] if action_args else action_kwargs.get("modal")
            if not modal_cfg or not isinstance(modal_cfg, dict):
                continue

            m_args = modal_cfg.get("args", [])
            m_kwargs = modal_cfg.get("kwargs", {})
            m_title = (
                m_args[0] if len(m_args) > 0 else m_kwargs.get("title", "Form Popup")
            )

            if interaction.response.is_done():
                await interaction.followup.send(
                    "❌ Discord API Constraint Error: Modals cannot be launched after "
                    "another interaction response has been triggered.",
                    ephemeral=True,
                )
                return

            modal_obj = DSLV2Modal(
                title=m_title,
                on_submit_actions=m_kwargs.get("on_submit"),
                bot_instance=bot_instance,
                channel=channel,
            )

            fields = m_args[1:] if len(m_args) > 1 else m_kwargs.get("children", [])
            for field in fields:
                if not isinstance(field, dict):
                    continue
                field_type = field.get("type", "").split(".")[-1]
                f_args = field.get("args", [])
                f_kwargs = field.get("kwargs", {})

                if field_type == "Label":
                    lbl_text = (
                        f_args[0]
                        if len(f_args) > 0
                        else f_kwargs.get("text", "Input Slot")
                    )
                    sub_comp_cfg = (
                        f_args[1] if len(f_args) > 1 else f_kwargs.get("component")
                    )

                    lbl_text_clean = lbl_text[:45] if lbl_text else "Field"

                    sub_comp = None
                    if sub_comp_cfg and isinstance(sub_comp_cfg, dict):
                        sc_type = sub_comp_cfg.get("type", "").split(".")[-1]
                        sc_args = sub_comp_cfg.get("args", [])
                        sc_kwargs = sub_comp_cfg.get("kwargs", {})

                        custom_field_id = (
                            sc_kwargs.get("id") or f"fld_{uuid.uuid4().hex[:8]}"
                        )

                        if sc_type == "CheckboxGroup":
                            options_cfg = sc_kwargs.get("options", [])
                            opts = []
                            for o in options_cfg:
                                if isinstance(o, dict):
                                    o_args = o.get("args", [])
                                    o_kwargs = o.get("kwargs", {})
                                    lbl = (
                                        o_args[0]
                                        if len(o_args) > 0
                                        else o_kwargs.get("label", "Option")
                                    )
                                    val = (
                                        o_args[1]
                                        if len(o_args) > 1
                                        else o_kwargs.get("value", lbl)
                                    )
                                    opts.append(
                                        discord.CheckboxGroupOption(
                                            label=lbl[:100],
                                            value=val[:100],
                                            description=(
                                                o_kwargs.get("description")[:100]
                                                if o_kwargs.get("description")
                                                else None
                                            ),
                                            default=o_kwargs.get("default", False),
                                        )
                                    )
                            sub_comp = discord.ui.CheckboxGroup(
                                options=opts, custom_id=custom_field_id
                            )

                        elif sc_type == "RadioGroup":
                            options_cfg = sc_kwargs.get("options", [])
                            opts = []
                            for o in options_cfg:
                                if isinstance(o, dict):
                                    o_args = o.get("args", [])
                                    o_kwargs = o.get("kwargs", {})
                                    lbl = (
                                        o_args[0]
                                        if len(o_args) > 0
                                        else o_kwargs.get("label", "Option")
                                    )
                                    val = (
                                        o_args[1]
                                        if len(o_args) > 1
                                        else o_kwargs.get("value", lbl)
                                    )
                                    opts.append(
                                        discord.RadioGroupOption(
                                            label=lbl[:100],
                                            value=val[:100],
                                            description=(
                                                o_kwargs.get("description")[:100]
                                                if o_kwargs.get("description")
                                                else None
                                            ),
                                            default=o_kwargs.get("default", False),
                                        )
                                    )
                            sub_comp = discord.ui.RadioGroup(
                                options=opts, custom_id=custom_field_id
                            )

                        elif sc_type == "FileUpload":
                            sub_comp = discord.ui.FileUpload(
                                min_values=sc_kwargs.get("min_values", 1),
                                max_values=sc_kwargs.get("max_values", 1),
                                custom_id=custom_field_id,
                            )
                        elif sc_type == "StringSelect":
                            raw_opts = sc_kwargs.get("options", [])
                            opts = []
                            for opt in raw_opts:
                                if isinstance(opt, dict):
                                    o_args = opt.get("args", [])
                                    o_kwargs = opt.get("kwargs", {})
                                    lbl = (
                                        o_args[0]
                                        if len(o_args) > 0
                                        else o_kwargs.get("label", "Option")
                                    )
                                    val = (
                                        o_args[1]
                                        if len(o_args) > 1
                                        else o_kwargs.get("value", lbl)
                                    )
                                    opts.append(
                                        discord.SelectOption(
                                            label=lbl[:100],
                                            value=val[:100],
                                            description=(
                                                o_kwargs.get("description")[:100]
                                                if o_kwargs.get("description")
                                                else None
                                            ),
                                            emoji=o_kwargs.get("emoji"),
                                        )
                                    )
                            sub_comp = discord.ui.Select(
                                options=opts, custom_id=custom_field_id
                            )
                        elif sc_type == "UserSelect":
                            sub_comp = discord.ui.UserSelect(custom_id=custom_field_id)
                        elif sc_type == "RoleSelect":
                            sub_comp = discord.ui.RoleSelect(custom_id=custom_field_id)
                        elif sc_type == "ChannelSelect":
                            sub_comp = discord.ui.ChannelSelect(
                                custom_id=custom_field_id
                            )
                        elif sc_type == "MentionableSelect":
                            sub_comp = discord.ui.MentionableSelect(
                                custom_id=custom_field_id
                            )
                        elif sc_type == "TextInput":
                            inp_style_str = sc_kwargs.get("style", "short").lower()
                            inp_style = (
                                discord.TextStyle.long
                                if inp_style_str == "long"
                                else discord.TextStyle.short
                            )

                            ti_label = sc_kwargs.get("label") or lbl_text_clean
                            sub_comp = discord.ui.TextInput(
                                label=ti_label[:45],
                                style=inp_style,
                                custom_id=custom_field_id,
                            )
                        else:
                            sub_comp = discord.ui.TextInput(
                                label=lbl_text_clean,
                                style=discord.TextStyle.short,
                                custom_id=custom_field_id,
                            )

                    if sub_comp is not None:
                        label_comp = discord.ui.Label(
                            text=lbl_text_clean,
                            component=sub_comp,
                            description=(
                                f_kwargs.get("description")[:100]
                                if f_kwargs.get("description")
                                else None
                            ),
                        )
                        modal_obj.add_item(label_comp)

            await interaction.response.send_modal(modal_obj)

        elif action_type == "ai":
            instruction_payload = (
                action_args[0]
                if action_args
                else action_kwargs.get("instruction_payload", "")
            )
            if context_value:
                instruction_payload = (
                    f"{instruction_payload} | Value Selected: {context_value}"
                )

            if not interaction.response.is_done():
                await interaction.response.defer()

            await bot_instance.trigger_message_builder_ai_turn(
                interaction, instruction_payload
            )

    if not interaction.response.is_done():
        try:
            await interaction.response.defer()
        except Exception:
            pass
