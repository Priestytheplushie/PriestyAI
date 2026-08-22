import re
import discord

def parse_mentions(text: str, guild: discord.Guild | None) -> str:
    if not guild:
        return text

    def replace_user(match):
        name = match.group(1).lower()
        for member in guild.members:
            if member.name.lower() == name or (member.nick and member.nick.lower() == name):
                return f"<@{member.id}>"
        return match.group(0)

    text = re.sub(r'(?<!<)@([a-zA-Z0-9_\.\-]+)(?!>)', replace_user, text)

    def replace_channel(match):
        cname = match.group(1).lower()
        for ch in guild.channels:
            if ch.name.lower() == cname:
                return f"<#{ch.id}>"
        return match.group(0)

    text = re.sub(r'(?<!<)#([a-zA-Z0-9_\-]+)(?!>)', replace_channel, text)

    def replace_role(match):
        rname = match.group(1).lower()
        for role in guild.roles:
            if role.name.lower() == rname and role.name != "@everyone":
                return f"<@&role.id>"
        return match.group(0)

    text = re.sub(r'(?<!<)@&([a-zA-Z0-9_\s\-]+)(?!>)', replace_role, text)

    return text