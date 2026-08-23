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

    def _build_child_component_dict(self, field: dict[str, Any]) -> dict[str, Any]:
        field_type = str(field.get("type", "text_input")).lower()
        field_id = field.get("custom_id", field.get("id", f"field_{id(field)}"))
        placeholder = field.get("placeholder", "")
        required = field.get("required", True)

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
                "min_values": 1 if required else 0,
                "max_values": min(field.get("max_values", 1), len(options))
            }
            if placeholder:
                payload["placeholder"] = placeholder[:100]
            return payload

        elif field_type in ["userselect", "user_select"]:
            return {"type": 5, "custom_id": field_id, "placeholder": placeholder[:100]}
        elif field_type in ["roleselect", "role_select"]:
            return {"type": 6, "custom_id": field_id, "placeholder": placeholder[:100]}
        elif field_type in ["mentionableselect", "mentionable_select"]:
            return {"type": 7, "custom_id": field_id, "placeholder": placeholder[:100]}
        elif field_type in ["channelselect", "channel_select"]:
            return {"type": 8, "custom_id": field_id, "placeholder": placeholder[:100]}

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
                    **({"description": opt["description"][:100]} if opt.get("description") else {})
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

        for field in self.fields_schema:
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
                child_payload = self._build_child_component_dict(field)
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
                if "value" in inner:
                    collected_data[cid] = inner["value"]
                elif "values" in inner:
                    collected_data[cid] = inner["values"]
            
            elif comp_type == 1 and "components" in comp:
                for sub in comp["components"]:
                    cid = sub.get("custom_id", f"field_{len(collected_data)}")
                    if "value" in sub:
                        collected_data[cid] = sub["value"]
                    elif "values" in sub:
                        collected_data[cid] = sub["values"]
            
            else:
                cid = comp.get("custom_id", f"field_{len(collected_data)}")
                if "value" in comp:
                    collected_data[cid] = comp["value"]
                elif "values" in comp:
                    collected_data[cid] = comp["values"]

        logger.info(f"Modal '{self.title}' submitted by {interaction.user}: {collected_data}")
        await self.on_submit_callback(interaction, collected_data)


class DynamicActionView(ui.View):
    def __init__(
        self,
        components_schema: list[dict[str, Any]],
        modals_map: dict[str, dict[str, Any]],
        interaction_dispatcher: Callable[[discord.Interaction, str, Any], Any]
    ):
        super().__init__(timeout=600)
        self.components_schema = components_schema
        self.modals_map = modals_map
        self.interaction_dispatcher = interaction_dispatcher

        self._build_view()

    def _build_view(self):
        for comp in self.components_schema:
            ctype = comp.get("type", "button").lower()
            label = comp.get("label", "Action")
            cid = comp.get("custom_id", f"btn_{len(self.children)}")
            style_str = comp.get("style", "primary").lower()
            modal_id = comp.get("modal_id", None)

            if ctype in ["button", "btn"]:
                style_map = {
                    "primary": discord.ButtonStyle.primary,
                    "secondary": discord.ButtonStyle.secondary,
                    "success": discord.ButtonStyle.success,
                    "danger": discord.ButtonStyle.danger,
                    "link": discord.ButtonStyle.link
                }
                style = style_map.get(style_str, discord.ButtonStyle.primary)
                btn = ui.Button(label=label[:80], style=style, custom_id=cid, emoji=comp.get("emoji"))
                
                async def button_callback(interaction: discord.Interaction, m_id=modal_id, c_id=cid, b_lbl=label):
                    if m_id and m_id in self.modals_map:
                        modal_spec = self.modals_map[m_id]
                        
                        async def handle_modal_submit(sub_interaction: discord.Interaction, data: dict[str, Any]):
                            await self.interaction_dispatcher(sub_interaction, "modal_submit", {
                                "modal_id": m_id,
                                "title": modal_spec.get("title", "Form"),
                                "values": data
                            })

                        modal_obj = DynamicModalV2(
                            title=modal_spec.get("title", "Form"),
                            custom_id=m_id,
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

            elif ctype in ["select", "stringselect", "string_select"]:
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
                    options = [discord.SelectOption(label="Default Option", value="default")]

                sel = ui.Select(
                    custom_id=cid,
                    placeholder=comp.get("placeholder", "Select an option...")[:100],
                    options=options[:25]
                )

                async def select_callback(interaction: discord.Interaction, c_id=cid):
                    selected_values = sel.values
                    await self.interaction_dispatcher(interaction, "select_option", {
                        "custom_id": c_id,
                        "selected": selected_values
                    })

                sel.callback = select_callback
                self.add_item(sel)