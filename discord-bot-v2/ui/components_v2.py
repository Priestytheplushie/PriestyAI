import discord
from typing import List, Optional

HAS_LAYOUT_VIEW = hasattr(discord.ui, "LayoutView") and hasattr(discord.ui, "Container")

def create_thoughts_container(header_text: str, body_text: str) -> Optional[discord.ui.Item]:
    if not HAS_LAYOUT_VIEW:
        return None

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(header_text))
    if hasattr(discord.ui, "Separator"):
        container.add_item(discord.ui.Separator(divider=True))
    container.add_item(discord.ui.TextDisplay(body_text))
    return container