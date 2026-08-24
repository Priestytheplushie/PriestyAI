import re
import discord

def parse_mentions(text: str, guild: discord.Guild | None) -> str:
    if not guild:
        return text

    sorted_roles = sorted(
        [r for r in guild.roles if r.name != "@everyone"],
        key=lambda r: len(r.name),
        reverse=True
    )
    for role in sorted_roles:
        escaped_rname = re.escape(role.name)
        pattern = rf'(?<!<)@&?{escaped_rname}(?![a-zA-Z0-9_\|])'
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, f"<@&{role.id}>", text, flags=re.IGNORECASE)

    sorted_members = sorted(
        guild.members,
        key=lambda m: len(m.global_name or m.display_name or m.name),
        reverse=True
    )

    for member in sorted_members:
        names_to_check = {
            member.name,
            member.display_name,
            getattr(member, 'global_name', None),
            getattr(member, 'nick', None)
        }
        names_to_check = {n for n in names_to_check if n}

        for name in names_to_check:
            escaped_name = re.escape(name)
            pattern = rf'(?<!<)@{escaped_name}(?![a-zA-Z0-9_\|])'
            if re.search(pattern, text, flags=re.IGNORECASE):
                text = re.sub(pattern, f"<@{member.id}>", text, flags=re.IGNORECASE)
                break

    sorted_channels = sorted(
        guild.channels,
        key=lambda c: len(c.name),
        reverse=True
    )

    for ch in sorted_channels:
        escaped_cname = re.escape(ch.name)
        pattern = rf'(?<!<)#{escaped_cname}(?![a-zA-Z0-9_\-\|])'
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, f"<#{ch.id}>", text, flags=re.IGNORECASE)
        else:
            clean_ch_name = re.sub(r'^[^\w]+', '', ch.name).strip()
            if clean_ch_name and len(clean_ch_name) >= 3:
                escaped_clean = re.escape(clean_ch_name)
                pattern_clean = rf'(?<!<)#{escaped_clean}(?![a-zA-Z0-9_\-\|])'
                if re.search(pattern_clean, text, flags=re.IGNORECASE):
                    text = re.sub(pattern_clean, f"<#{ch.id}>", text, flags=re.IGNORECASE)

    return text