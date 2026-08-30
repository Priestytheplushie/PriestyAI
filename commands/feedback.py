import aiohttp
import logging
from typing import Any
import discord
from discord import app_commands
from core.feedback_manager import feedback_manager
from core.config_manager import config_manager
from core.moderation import (
    check_moderation,
    log_moderation_violation,
    is_user_banned,
    ban_user,
    generate_friendly_refusal
)
from ui.feedback_views import build_feedback_modal
from ui.onboarding_views import build_welcome_terms_modal, BannedUserNoticeView

logger = logging.getLogger("PriestyAI.Commands.Feedback")

def setup_feedback_commands(tree: app_commands.CommandTree):

    @tree.command(name="feedback", description="Submit a bug report, feature suggestion, or general feedback to the developer")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def feedback_command(interaction: discord.Interaction):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.send_message("Terms accepted. You can now use `/feedback`.", ephemeral=True)

            modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(modal)
            return

        async def on_feedback_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            good_faith = data.get("good_faith_checkbox", [])
            is_confirmed = False
            if isinstance(good_faith, list):
                is_confirmed = "confirmed" in good_faith
            elif isinstance(good_faith, str):
                is_confirmed = (good_faith == "confirmed")

            if not is_confirmed:
                await sub_inter.response.send_message(
                    content="You must confirm the good faith checkbox to submit feedback.",
                    ephemeral=True
                )
                return

            feedback_type = data.get("feedback_type", "General Feedback")
            if isinstance(feedback_type, list):
                feedback_type = feedback_type[0] if feedback_type else "General Feedback"

            content = data.get("content", "").strip()
            if not content:
                await sub_inter.response.send_message(content="Feedback content cannot be empty.", ephemeral=True)
                return

            await sub_inter.response.defer(ephemeral=True)

            raw_data = getattr(sub_inter, "data", {})
            resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
            attachment_records = []

            if resolved_attachments:
                for att_id, att_obj in resolved_attachments.items():
                    att_url = att_obj.get("url")
                    att_fname = att_obj.get("filename", f"file_{att_id}")
                    if att_url:
                        attachment_records.append({"filename": att_fname, "url": att_url})

            is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(content)
            if is_flagged:
                log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
                if is_zero_tolerance:
                    ban_user(interaction.user.id, reason=f"Zero-tolerance violation in feedback: {', '.join(flagged_cats)}")
                    ban_view = BannedUserNoticeView(author=interaction.user)
                    await sub_inter.followup.send(view=ban_view, ephemeral=True)
                    return

                refusal_text = await generate_friendly_refusal(flagged_cats)
                await sub_inter.followup.send(content=refusal_text, ephemeral=True)
                return

            ticket_id = feedback_manager.submit_feedback(
                user_id=interaction.user.id,
                user_name=interaction.user.display_name,
                guild_id=interaction.guild_id,
                channel_id=interaction.channel_id,
                feedback_type=feedback_type,
                content=content,
                attachments=attachment_records
            )

            response_msg = (
                f"### Feedback Submitted — Ticket #{ticket_id}\n"
                f"Thank you, **{interaction.user.display_name}**. Your feedback has been recorded into our database.\n\n"
                f"- **Category:** `{feedback_type}`\n"
                f"- **Status:** `Open`\n\n"
                f"-# You can view or delete your stored submissions at any time via </data:1541122763044163665>."
            )
            await sub_inter.followup.send(content=response_msg, ephemeral=True)

        modal = build_feedback_modal(on_submit=on_feedback_submit)
        await interaction.response.send_modal(modal)