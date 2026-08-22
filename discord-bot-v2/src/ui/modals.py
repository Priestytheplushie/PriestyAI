import json
import logging
from typing import Dict, Any, List, Optional
import discord
from src.ui.components_v2 import ModalV2Builder

logger = logging.getLogger("PriestyAI.Modals")

class DynamicModalV2Handler:

    @staticmethod
    def build_modal_payload(
        title: str,
        custom_id: str,
        raw_components: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        components_payload = []

        for item in raw_components:
            ctype = item.get("type", "text_input").lower()

            if ctype == "text_display":
                components_payload.append(ModalV2Builder.text_display(item.get("content", "")))

            elif ctype in ("text_input", "text"):
                components_payload.append(ModalV2Builder.text_input(
                    custom_id=item.get("custom_id", "input"),
                    label=item.get("label", "Input"),
                    style=item.get("style", "short"),
                    placeholder=item.get("placeholder"),
                    default=item.get("default"),
                    description=item.get("description"),
                    required=item.get("required", True)
                ))

            elif ctype in ("string_select", "dropdown", "select"):
                components_payload.append(ModalV2Builder.string_select(
                    custom_id=item.get("custom_id", "select"),
                    label=item.get("label", "Select Option"),
                    options=item.get("options", []),
                    placeholder=item.get("placeholder"),
                    description=item.get("description"),
                    required=item.get("required", True)
                ))

            elif ctype == "channel_select":
                components_payload.append(ModalV2Builder.channel_select(
                    custom_id=item.get("custom_id", "channel_select"),
                    label=item.get("label", "Select Channel"),
                    placeholder=item.get("placeholder"),
                    description=item.get("description"),
                    channel_types=item.get("channel_types")
                ))

            elif ctype in ("radio_group", "radio"):
                components_payload.append(ModalV2Builder.radio_group(
                    custom_id=item.get("custom_id", "radio_group"),
                    label=item.get("label", "Choose Option"),
                    options=item.get("options", []),
                    description=item.get("description")
                ))

            elif ctype in ("checkbox_group", "checkboxes"):
                components_payload.append(ModalV2Builder.checkbox_group(
                    custom_id=item.get("custom_id", "checkbox_group"),
                    label=item.get("label", "Select Items"),
                    options=item.get("options", []),
                    min_values=item.get("min_values", 0),
                    max_values=item.get("max_values", 10),
                    description=item.get("description")
                ))

            elif ctype in ("file_upload", "file"):
                components_payload.append(ModalV2Builder.file_upload(
                    custom_id=item.get("custom_id", "file_upload"),
                    label=item.get("label", "Upload Attachment"),
                    min_values=item.get("min_values", 1),
                    max_values=item.get("max_values", 3),
                    required=item.get("required", False),
                    description=item.get("description")
                ))

        return {
            "type": 9,
            "data": {
                "title": title[:45],
                "custom_id": custom_id,
                "components": components_payload
            }
        }

    @staticmethod
    def parse_modal_submission(interaction_data: Dict[str, Any]) -> Dict[str, Any]:
        results = {}
        components = interaction_data.get("components", [])

        for comp in components:
            inner = comp.get("component", comp)
            cid = inner.get("custom_id")
            if not cid:
                continue

            if inner.get("type") == 4:
                results[cid] = inner.get("value", "")

            elif inner.get("type") in (3, 5, 6, 7, 8):
                results[cid] = inner.get("values", [])

            elif inner.get("type") == 21:
                results[cid] = inner.get("value") or (inner.get("values", [None])[0])

            elif inner.get("type") == 22:
                results[cid] = inner.get("values", [])

            elif inner.get("type") == 19:
                results[cid] = inner.get("values", [])

        return results


class DynamicModalLauncherView(discord.ui.View):
    def __init__(
        self,
        button_label: str,
        modal_title: str,
        modal_custom_id: str,
        modal_components: List[Dict[str, Any]],
        emoji: Optional[str] = None
    ):
        super().__init__(timeout=600)
        self.modal_title = modal_title
        self.modal_custom_id = modal_custom_id
        self.modal_components = modal_components

        button = discord.ui.Button(
            label=button_label,
            custom_id=f"btn_open_modal_{modal_custom_id}",
            style=discord.ButtonStyle.primary,
            emoji=emoji or "📝"
        )
        button.callback = self._on_button_click
        self.add_item(button)

    async def _on_button_click(self, interaction: discord.Interaction):
        modal_payload = DynamicModalV2Handler.build_modal_payload(
            title=self.modal_title,
            custom_id=self.modal_custom_id,
            raw_components=self.modal_components
        )
        await interaction.response._parent._client.http.create_interaction_response(
            interaction.id,
            interaction.token,
            session=interaction.response._parent._session,
            type=9,
            data=modal_payload["data"]
        )