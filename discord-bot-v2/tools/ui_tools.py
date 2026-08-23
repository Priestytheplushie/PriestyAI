import json
import logging
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.UITools")

@tool_registry.register(
    name="add_modal",
    description=(
        "Defines an interactive Modals form specification. Use this strictly when you need structured multi-field form popups "
        "(e.g., text inputs + selects combined).\n"
        "Each field in 'fields' should be a dictionary with:\n"
        "- 'type': 'TextInput' | 'StringSelect' | 'RoleSelect' | 'ChannelSelect' | 'MentionableSelect' | 'RadioGroup' | 'CheckboxGroup' | 'FileUpload' | 'TextDisplay'\n"
        "- 'label': Main bold title of the field (max 45 chars)\n"
        "- 'description': Helpful subtitle description underneath the label explaining what to input (max 100 chars)\n"
        "- 'placeholder': Ghost hint text inside the input field\n"
        "- 'required': bool (default True)\n"
        "- 'options': list of {'label': '...', 'value': '...', 'description': '...'} (for selects/radios/checkboxes)\n"
        "- 'style': 'Short' or 'Paragraph' (for TextInput)\n"
        "Must be defined before referencing in add_component."
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

    logger.info(f"[add_modal] Staging modal '{modal_id}' ({title}) with {len(parsed_fields)} fields.")
    modal_spec = {
        "modal_id": modal_id,
        "title": title,
        "fields": parsed_fields
    }
    context.staged_modals.append(modal_spec)
    return {
        "status": "staged",
        "modal_id": modal_id,
        "field_count": len(parsed_fields),
        "message": f"Modal '{title}' successfully staged. Attach it to a button using add_component."
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