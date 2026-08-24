import json
import logging
from typing import Any, Callable
import discord
from discord import ui

logger = logging.getLogger("PriestyAI.Modals")

class DynamicModalV2(ui.Modal):
    def __init__(
        self,
        title: str,
        custom_id: str,
        fields_schema: Any,
        on_submit_callback: Callable[[discord.Interaction, dict[str, Any]], Any]
    ):
        super().__init__(title=title[:45], custom_id=custom_id)
        
        if isinstance(fields_schema, str):
            try:
                self.fields_schema = json.loads(fields_schema)
            except Exception:
                self.fields_schema = []
        else:
            self.fields_schema = fields_schema or []

        self.on_submit_callback = on_submit_callback

    def _build_child_component_dict(self, field: dict[str, Any], idx: int) -> dict[str, Any]:
        field_type = str(field.get("type", "text_input")).lower().strip().replace(" ", "_")
        
        label_slug = field.get("label", "").lower().strip().replace(" ", "_")
        label_slug = "".join(c for c in label_slug if c.isalnum() or c == "_")
        field_id = field.get("custom_id") or field.get("id") or label_slug or f"field_{idx}"
        
        placeholder = field.get("placeholder", "")
        required = field.get("required", True)
        default_val = field.get("value") if field.get("value") is not None else field.get("default")

        if field_type in ["text", "textinput", "text_input", "paragraph"]:
            style_str = str(field.get("style", "short")).lower()
            style_val = 2 if (style_str == "paragraph" or field_type == "paragraph") else 1
            payload = {
                "type": 4,
                "custom_id": field_id,
                "style": style_val,
                "required": required
            }
            if placeholder:
                payload["placeholder"] = placeholder[:100]
            if default_val:
                payload["value"] = str(default_val)[:4000]
            if field.get("max_length"):
                payload["max_length"] = min(int(field["max_length"]), 4000)
            if field.get("min_length"):
                payload["min_length"] = int(field["min_length"])
            return payload

        elif field_type in ["select", "stringselect", "string_select"]:
            raw_options = field.get("options", [])
            if isinstance(raw_options, str):
                try:
                    raw_options = json.loads(raw_options)
                except Exception:
                    raw_options = []

            options = [
                {
                    "label": opt.get("label", "Option")[:100],
                    "value": opt.get("value", opt.get("label", ""))[:100],
                    **({"description": opt["description"][:100]} if opt.get("description") else {}),
                    **({"default": True} if opt.get("default") or (default_val and str(opt.get("value")) == str(default_val)) else {}),
                    **({"emoji": {"name": opt["emoji"]}} if opt.get("emoji") else {})
                }
                for opt in raw_options if isinstance(opt, dict)
            ]
            if not options:
                options = [{"label": "Default", "value": "default"}]

            payload = {
                "type": 3,
                "custom_id": field_id,
                "options": options[:25],
                "min_values": int(field.get("min_values", 1 if required else 0)),
                "max_values": min(int(field.get("max_values", 1)), len(options))
            }
            if placeholder:
                payload["placeholder"] = placeholder[:100]
            return payload

        elif field_type in ["userselect", "user_select", "roleselect", "role_select", "mentionableselect", "mentionable_select", "channelselect", "channel_select"]:
            type_map = {
                "userselect": (5, "user"), "user_select": (5, "user"),
                "roleselect": (6, "role"), "role_select": (6, "role"),
                "mentionableselect": (7, None), "mentionable_select": (7, None),
                "channelselect": (8, "channel"), "channel_select": (8, "channel")
            }
            comp_type_num, default_type_name = type_map.get(field_type, (8, "channel"))
            payload = {
                "type": comp_type_num,
                "custom_id": field_id,
                "placeholder": placeholder[:100],
                "required": required,
                "min_values": int(field.get("min_values", 1 if required else 0)),
                "max_values": int(field.get("max_values", 1))
            }

            if field.get("default_values"):
                raw_defs = field["default_values"]
                if isinstance(raw_defs, list):
                    formatted_defs = []
                    for d in raw_defs:
                        if isinstance(d, dict):
                            formatted_defs.append(d)
                        else:
                            dtype = default_type_name or "user"
                            formatted_defs.append({"id": str(d), "type": dtype})
                    if formatted_defs:
                        payload["default_values"] = formatted_defs
            elif default_val and default_type_name:
                if isinstance(default_val, list):
                    payload["default_values"] = [{"id": str(v), "type": default_type_name} for v in default_val]
                else:
                    payload["default_values"] = [{"id": str(default_val), "type": default_type_name}]

            if field.get("channel_types"):
                payload["channel_types"] = [getattr(ct, "value", ct) for ct in field["channel_types"]]

            return payload

        elif field_type in ["fileupload", "file_upload"]:
            return {
                "type": 19,
                "custom_id": field_id,
                "min_values": 1 if required else 0,
                "max_values": field.get("max_values", 10),
                "required": required
            }

        elif field_type in ["radiogroup", "radio_group", "checkboxgroup", "checkbox_group"]:
            comp_type = 21 if "radio" in field_type else 22
            raw_options = field.get("options", [])
            if isinstance(raw_options, str):
                try:
                    raw_options = json.loads(raw_options)
                except Exception:
                    raw_options = []

            options = [
                {
                    "label": opt.get("label", "Option")[:100],
                    "value": opt.get("value", opt.get("label", ""))[:100],
                    **({"description": opt["description"][:100]} if opt.get("description") else {}),
                    **({"default": True} if opt.get("default") or (default_val and str(opt.get("value")) == str(default_val)) else {})
                }
                for opt in raw_options if isinstance(opt, dict)
            ]
            return {
                "type": comp_type,
                "custom_id": field_id,
                "options": options[:10],
                "required": required
            }

        return {
            "type": 4,
            "custom_id": field_id,
            "style": 1,
            "required": required,
            **({"placeholder": placeholder[:100]} if placeholder else {})
        }

    def to_dict(self) -> dict[str, Any]:
        components_payload = []

        for idx, field in enumerate(self.fields_schema):
            if isinstance(field, str):
                continue

            field_type = str(field.get("type", "text_input")).lower()

            if field_type in ["textdisplay", "text_display"]:
                content = field.get("content", field.get("label", ""))
                components_payload.append({
                    "type": 10,
                    "content": content[:4000]
                })
            else:
                child_payload = self._build_child_component_dict(field, idx)
                label_payload = {
                    "type": 18,
                    "label": field.get("label", "Field")[:45],
                    "component": child_payload
                }
                if field.get("description"):
                    label_payload["description"] = field.get("description")[:100]

                components_payload.append(label_payload)

        return {
            "title": self.title,
            "custom_id": self.custom_id,
            "components": components_payload
        }

    async def on_submit(self, interaction: discord.Interaction):
        collected_data = {}
        raw_components = getattr(interaction, "data", {}).get("components", [])

        for comp in raw_components:
            comp_type = comp.get("type")
            
            if comp_type == 18 and "component" in comp:
                inner = comp["component"]
                cid = inner.get("custom_id", f"field_{len(collected_data)}")
                if "values" in inner:
                    collected_data[cid] = inner["values"]
                elif "value" in inner:
                    collected_data[cid] = inner["value"]
            
            elif comp_type == 1 and "components" in comp:
                for sub in comp["components"]:
                    cid = sub.get("custom_id", f"field_{len(collected_data)}")
                    if "values" in sub:
                        collected_data[cid] = sub["values"]
                    elif "value" in sub:
                        collected_data[cid] = sub["value"]
            
            else:
                cid = comp.get("custom_id", f"field_{len(collected_data)}")
                if "values" in comp:
                    collected_data[cid] = comp["values"]
                elif "value" in comp:
                    collected_data[cid] = comp["value"]

        logger.info(f"Modal '{self.title}' submitted by {interaction.user}: {collected_data}")
        await self.on_submit_callback(interaction, collected_data)


class DynamicActionView(ui.View):
    def __init__(
        self,
        components_schema: list[dict[str, Any]],
        modals_map: dict[str, dict[str, Any]],
        interaction_dispatcher: Callable[[discord.Interaction, str, Any], Any]
    ):
        super().__init__(timeout=900)
        self.components_schema = components_schema
        self.modals_map = modals_map
        self.interaction_dispatcher = interaction_dispatcher

        self._build_view()

    def _build_view(self):
        current_row = 0
        buttons_in_row = 0

        for comp in self.components_schema:
            raw_type = str(comp.get("type", "button")).lower().strip().replace(" ", "_")
            label = comp.get("label", "Action")
            cid = comp.get("custom_id", f"btn_{len(self.children)}")
            placeholder = comp.get("placeholder") or "Select an option..."
            modal_id = comp.get("modal_id") or cid
            disabled = comp.get("disabled", False)
            min_v = max(0, int(comp.get("min_values", 1)))
            max_v = max(1, min(25, int(comp.get("max_values", 1))))

            if raw_type in ["button", "btn"]:
                style_map = {
                    "primary": discord.ButtonStyle.primary,
                    "secondary": discord.ButtonStyle.secondary,
                    "success": discord.ButtonStyle.success,
                    "danger": discord.ButtonStyle.danger,
                    "link": discord.ButtonStyle.link
                }
                style_str = comp.get("style", "secondary").lower()
                style = style_map.get(style_str, discord.ButtonStyle.secondary)

                if buttons_in_row >= 5:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                btn = ui.Button(
                    label=label[:80],
                    style=style,
                    custom_id=cid,
                    emoji=comp.get("emoji"),
                    disabled=disabled,
                    row=min(4, current_row)
                )

                async def button_callback(interaction: discord.Interaction, m_id=modal_id, c_id=cid, b_lbl=label):
                    target_modal_key = m_id if m_id in self.modals_map else (c_id if c_id in self.modals_map else None)

                    if target_modal_key:
                        modal_spec = self.modals_map[target_modal_key]
                        
                        async def handle_modal_submit(sub_interaction: discord.Interaction, data: dict[str, Any]):
                            await self.interaction_dispatcher(sub_interaction, "modal_submit", {
                                "modal_id": target_modal_key,
                                "title": modal_spec.get("title", "Form"),
                                "values": data
                            })

                        modal_obj = DynamicModalV2(
                            title=modal_spec.get("title", "Form"),
                            custom_id=target_modal_key,
                            fields_schema=modal_spec.get("fields", []),
                            on_submit_callback=handle_modal_submit
                        )
                        await interaction.response.send_modal(modal_obj)
                    else:
                        await self.interaction_dispatcher(interaction, "button_click", {
                            "custom_id": c_id,
                            "label": b_lbl
                        })

                btn.callback = button_callback
                self.add_item(btn)
                buttons_in_row += 1

            elif raw_type in ["select", "stringselect", "string_select"]:
                if buttons_in_row > 0:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                raw_options = comp.get("options", [])
                if isinstance(raw_options, str):
                    try:
                        raw_options = json.loads(raw_options)
                    except Exception:
                        raw_options = []

                options = [
                    discord.SelectOption(
                        label=opt.get("label", "Option")[:100],
                        value=opt.get("value", opt.get("label", ""))[:100],
                        description=opt.get("description", "")[:100] if opt.get("description") else None,
                        emoji=opt.get("emoji")
                    )
                    for opt in raw_options if isinstance(opt, dict)
                ]
                if not options:
                    options = [discord.SelectOption(label="Option", value="default")]

                sel = ui.Select(
                    custom_id=cid,
                    placeholder=placeholder[:100],
                    options=options[:25],
                    min_values=min(min_v, len(options)),
                    max_values=min(max_v, len(options)),
                    disabled=disabled,
                    row=min(4, current_row)
                )

                async def select_callback(interaction: discord.Interaction, c_id=cid, s_comp=sel):
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "component_type": "string_select",
                        "selected": s_comp.values
                    })

                sel.callback = select_callback
                self.add_item(sel)
                current_row = min(4, current_row + 1)

            elif raw_type in ["userselect", "user_select"]:
                if buttons_in_row > 0:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                u_sel = ui.UserSelect(
                    custom_id=cid,
                    placeholder=placeholder[:100],
                    min_values=min_v,
                    max_values=max_v,
                    disabled=disabled,
                    row=min(4, current_row)
                )

                async def user_select_callback(interaction: discord.Interaction, c_id=cid, s_comp=u_sel):
                    selected_ids = [str(u.id) for u in s_comp.values]
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "component_type": "user_select",
                        "selected": selected_ids
                    })

                u_sel.callback = user_select_callback
                self.add_item(u_sel)
                current_row = min(4, current_row + 1)

            elif raw_type in ["roleselect", "role_select"]:
                if buttons_in_row > 0:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                r_sel = ui.RoleSelect(
                    custom_id=cid,
                    placeholder=placeholder[:100],
                    min_values=min_v,
                    max_values=max_v,
                    disabled=disabled,
                    row=min(4, current_row)
                )

                async def role_select_callback(interaction: discord.Interaction, c_id=cid, s_comp=r_sel):
                    selected_ids = [str(r.id) for r in s_comp.values]
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "component_type": "role_select",
                        "selected": selected_ids
                    })

                r_sel.callback = role_select_callback
                self.add_item(r_sel)
                current_row = min(4, current_row + 1)

            elif raw_type in ["channelselect", "channel_select"]:
                if buttons_in_row > 0:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                ch_types = []
                if comp.get("channel_types") and isinstance(comp["channel_types"], list):
                    for ct in comp["channel_types"]:
                        if isinstance(ct, str) and hasattr(discord.ChannelType, ct.lower()):
                            ch_types.append(getattr(discord.ChannelType, ct.lower()))
                        elif isinstance(ct, discord.ChannelType):
                            ch_types.append(ct)

                c_sel = ui.ChannelSelect(
                    custom_id=cid,
                    placeholder=placeholder[:100],
                    min_values=min_v,
                    max_values=max_v,
                    disabled=disabled,
                    channel_types=ch_types or None,
                    row=min(4, current_row)
                )

                async def channel_select_callback(interaction: discord.Interaction, c_id=cid, s_comp=c_sel):
                    selected_ids = [str(ch.id) for ch in s_comp.values]
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "component_type": "channel_select",
                        "selected": selected_ids
                    })

                c_sel.callback = channel_select_callback
                self.add_item(c_sel)
                current_row = min(4, current_row + 1)

            elif raw_type in ["mentionableselect", "mentionable_select"]:
                if buttons_in_row > 0:
                    current_row = min(4, current_row + 1)
                    buttons_in_row = 0

                m_sel = ui.MentionableSelect(
                    custom_id=cid,
                    placeholder=placeholder[:100],
                    min_values=min_v,
                    max_values=max_v,
                    disabled=disabled,
                    row=min(4, current_row)
                )

                async def mentionable_select_callback(interaction: discord.Interaction, c_id=cid, s_comp=m_sel):
                    selected_ids = [str(m.id) for m in s_comp.values]
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "component_type": "mentionable_select",
                        "selected": selected_ids
                    })

                m_sel.callback = mentionable_select_callback
                self.add_item(m_sel)
                current_row = min(4, current_row + 1)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True