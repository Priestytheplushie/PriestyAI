import logging
from typing import Any, Callable
import discord
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.FeedbackUI")

FEEDBACK_GUIDELINES_TEXT = """# Community Feedback Guidelines
Submit a bug report, feature request, prompt quality evaluation, or general feedback.

Submission Rules:
- Be specific and descriptive. Include steps to reproduce bugs or exact prompt examples.
- Do not submit spam, abusive language, or false reports. Submissions violating safety terms are subject to account suspension."""

def build_feedback_modal(on_submit: Callable[[discord.Interaction, dict[str, Any]], Any]) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": FEEDBACK_GUIDELINES_TEXT
        },
        {
            "type": "string_select",
            "custom_id": "feedback_type",
            "label": "Feedback Category",
            "description": "Select the topic that best matches your submission",
            "value": "bug_report",
            "options": [
                {"label": "Bug Report", "value": "Bug Report", "description": "Report broken tools, crashes, or rendering errors", "default": True},
                {"label": "Feature Request", "value": "Feature Request", "description": "Suggest a new tool, capability, or improvement"},
                {"label": "Prompt Quality / Hallucination", "value": "Prompt Quality", "description": "Report an inaccurate or unhelpful AI response"},
                {"label": "General Feedback", "value": "General Feedback", "description": "Share overall thoughts or UX feedback"},
                {"label": "Complaint / Policy Report", "value": "Report", "description": "File a report regarding moderation or policy"}
            ],
            "required": True
        },
        {
            "type": "text_input",
            "custom_id": "content",
            "label": "Feedback Details",
            "description": "Describe your issue, suggestion, or experience in detail",
            "placeholder": "Provide detailed context, steps to reproduce, or suggestions...",
            "style": "paragraph",
            "required": True,
            "max_length": 3000
        },
        {
            "type": "file_upload",
            "custom_id": "attachments",
            "label": "Attachments / Screenshots",
            "description": "Upload error logs, images, or reference files (Optional)",
            "required": False,
            "max_values": 3
        },
        {
            "type": "checkbox_group",
            "custom_id": "good_faith_checkbox",
            "label": "Good Faith Acknowledgment",
            "description": "Required to submit feedback",
            "required": True,
            "options": [
                {
                    "label": "I confirm this submission is in good faith and adheres to the Terms of Service",
                    "value": "confirmed",
                    "description": "Spam or abusive submissions are subject to account suspension.",
                    "default": False
                }
            ]
        }
    ]

    return DynamicModalV2(
        title="Submit Feedback",
        custom_id="modal_feedback_submit",
        fields_schema=fields,
        on_submit_callback=on_submit
    )