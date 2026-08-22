import re
import discord

def parse_emojis(text: str, guild: discord.Guild | None) -> str:
    if not guild:
        return text

    pattern = r'(?<!<a:)(?<!<:):([a-zA-Z0-9_]{2,32}):(?!\d+>)'

    def replace_emoji(match):
        emoji_name = match.group(1)
        for emoji in guild.emojis:
            if emoji.name == emoji_name:
                return str(emoji)
        return match.group(0)

    return re.sub(pattern, replace_emoji, text)