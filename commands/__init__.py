import discord
from discord import app_commands
from commands.chat import setup_chat_commands
from commands.config import setup_config_commands
from commands.data import setup_data_commands
from commands.feedback import setup_feedback_commands
from commands.generate import setup_generate_commands
from commands.agent import setup_agent_commands
from commands.context_menus import setup_context_menus, build_retry_placeholder_layout
from commands.schedule import setup_schedule_commands

def setup_commands(tree: app_commands.CommandTree):
    setup_chat_commands(tree)
    setup_config_commands(tree)
    setup_data_commands(tree)
    setup_feedback_commands(tree)
    setup_generate_commands(tree)
    setup_agent_commands(tree)
    setup_schedule_commands(tree)
    setup_context_menus(tree)

__all__ = ["setup_commands", "build_retry_placeholder_layout"]