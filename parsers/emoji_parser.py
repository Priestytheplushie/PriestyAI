import re
import discord

def parse_emojis(text: str, guild: discord.Guild | None) -> str:
    if not text:
        return ""

    if guild:
        emoji_map_by_id = {str(e.id): e for e in guild.emojis}
        emoji_map_by_name = {e.name.lower(): e for e in guild.emojis}

        malformed_id_pattern = r'<\s*a?:?([a-zA-Z0-9_]*)[^>]*?(\d{17,21})\s*>'

        def repair_by_id(match):
            e_name = match.group(1).strip()
            e_id = match.group(2).strip()

            if e_id in emoji_map_by_id:
                guild_emoji = emoji_map_by_id[e_id]
                return f"<a:{guild_emoji.name}:{guild_emoji.id}>" if guild_emoji.animated else f"<:{guild_emoji.name}:{guild_emoji.id}>"
            elif e_name and e_name.lower() in emoji_map_by_name:
                guild_emoji = emoji_map_by_name[e_name.lower()]
                return f"<a:{guild_emoji.name}:{guild_emoji.id}>" if guild_emoji.animated else f"<:{guild_emoji.name}:{guild_emoji.id}>"
            elif e_name:
                return f"<:{e_name}:{e_id}>"
            return match.group(0)

        text = re.sub(malformed_id_pattern, repair_by_id, text)

        text = re.sub(r'<\s*:\s*([a-zA-Z0-9_]+)\s*:\s*(\d{17,21})\s*>', r'<:\1:\2>', text)
        text = re.sub(r'<\s*a:\s*([a-zA-Z0-9_]+)\s*:\s*(\d{17,21})\s*>', r'<a:\1:\2>', text)

        colon_pattern = r'(?<!<a:)(?<!<:):([a-zA-Z0-9_]{2,32}):(?!\d+>)'

        def replace_colon_name(match):
            emoji_name = match.group(1).lower()
            if emoji_name in emoji_map_by_name:
                guild_emoji = emoji_map_by_name[emoji_name]
                return f"<a:{guild_emoji.name}:{guild_emoji.id}>" if guild_emoji.animated else f"<:{guild_emoji.name}:{guild_emoji.id}>"
            return match.group(0)

        text = re.sub(colon_pattern, replace_colon_name, text)

    return text