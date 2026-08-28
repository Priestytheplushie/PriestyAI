import json
import logging
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.UITools")

@tool_registry.register(
    name="add_modal",
    description=(
        "Defines an interactive Modal form popup specification.\n"
        "You can link this modal to:\n"
        "1. A Button via add_component(component_type='Button', modal_id='...'), OR\n"
        "2. A Dropdown option via add_component(component_type='StringSelect', options=[{'label':'...', 'value':'...', 'modal_id':'...'}]).\n\n"
        "Each field in 'fields' should be a dictionary with:\n"
        "- 'type': 'TextInput' | 'StringSelect' | 'RoleSelect' | 'ChannelSelect' | 'MentionableSelect' | 'RadioGroup' | 'CheckboxGroup' | 'FileUpload' | 'TextDisplay'\n"
        "- 'label': Main bold title of the field (max 45 chars)\n"
        "- 'description': Subtitle description underneath the label (max 100 chars)\n"
        "- 'placeholder': Ghost hint text inside the input field\n"
        "- 'required': bool (default True)\n"
        "- 'options': list of {'label': '...', 'value': '...', 'description': '...'} (for selects/radios/checkboxes)\n"
        "- 'style': 'Short' or 'Paragraph' (for TextInput)\n"
        "Must be called before or alongside referencing the modal_id in add_component."
    )
)
async def add_modal(
    modal_id: str,
    title: str,
    fields: Any,
    context: ToolExecutionContext
) -> dict[str, Any]:
    if isinstance(fields, str):
        try:
            parsed_fields = json.loads(fields)
        except Exception:
            parsed_fields = []
    else:
        parsed_fields = fields or []

    modal_spec = {
        "modal_id": modal_id,
        "title": title,
        "fields": parsed_fields
    }

    if context:
        if not hasattr(context, "staged_modals"):
            context.staged_modals = []

        existing_idx = None
        for idx, existing_m in enumerate(context.staged_modals):
            if existing_m.get("modal_id") == modal_id:
                existing_idx = idx
                break

        if existing_idx is not None:
            context.staged_modals[existing_idx] = modal_spec
            logger.info(f"[add_modal] Updated modal '{modal_id}' ({title})")
        else:
            context.staged_modals.append(modal_spec)
            logger.info(f"[add_modal] Staging modal '{modal_id}' ({title}) with {len(parsed_fields)} fields.")

    return {
        "status": "staged",
        "modal_id": modal_id,
        "field_count": len(parsed_fields),
        "message": f"Modal '{title}' successfully staged. Attach it to a button or select option using add_component."
    }

@tool_registry.register(
    name="clear_conversation",
    description=(
        "Clears the AI's conversation memory and prevents reading channel history "
        "for the upcoming turn, resetting context cleanly."
    )
)
async def clear_conversation(context: ToolExecutionContext) -> dict[str, Any]:
    if context:
        context.clear_history_requested = True
    logger.info("[clear_conversation] Conversation context reset requested.")
    return {"status": "cleared", "message": "Conversation history cleared successfully."}