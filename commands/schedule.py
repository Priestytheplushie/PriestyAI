import discord
from discord import app_commands
from core.config_manager import config_manager
from core.moderation import is_user_banned
from ui.schedule_views import ScheduleDashboardView
from ui.onboarding_views import build_welcome_terms_modal, BannedUserNoticeView

def setup_schedule_commands(tree: app_commands.CommandTree):

    @tree.command(name="schedule", description="Schedule recurring AI workflows, daily briefs, or personal DM tasks")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def schedule_command(interaction: discord.Interaction):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                await sub_inter.response.send_message("✅ Terms accepted! You can now use `/schedule`.", ephemeral=True)

            modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(modal)
            return

        is_guild = interaction.guild is not None
        dash_view = ScheduleDashboardView(
            user=interaction.user,
            guild=interaction.guild,
            active_tab="all" if is_guild else "personal"
        )
        await interaction.response.send_message(view=dash_view, ephemeral=True)