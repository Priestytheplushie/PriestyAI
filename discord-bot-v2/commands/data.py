import discord
from discord import app_commands
from ui.data_views import DatabaseDashboardView, DataDeletionView

def setup_data_commands(tree: app_commands.CommandTree):

    @tree.command(name="data", description="Inspect, search, edit, or delete data stored by PriestyAI")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(action="Browse database records or open deletion menu directly")
    @app_commands.choices(action=[
        app_commands.Choice(name="Browse", value="browse"),
        app_commands.Choice(name="Delete", value="delete")
    ])
    async def data_command(interaction: discord.Interaction, action: str = "browse"):
        if action == "delete":
            del_view = DataDeletionView(user=interaction.user, guild=interaction.guild, channel=interaction.channel)
            await interaction.response.send_message(view=del_view, ephemeral=True)
        else:
            db_view = DatabaseDashboardView(user=interaction.user, guild=interaction.guild, channel=interaction.channel)
            await interaction.response.send_message(view=db_view, ephemeral=True)