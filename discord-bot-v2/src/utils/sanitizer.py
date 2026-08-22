import re
import logging
from typing import Optional
import discord

logger = logging.getLogger("PriestyAI.Sanitizer")

class Sanitizer:

    @staticmethod
    def clean_incoming_content(message: discord.Message, bot_user: discord.ClientUser) -> str:
        content = message.content

        bot_mention_regex = re.compile(rf"<@!?{bot_user.id}>")
        content = bot_mention_regex.sub("", content).strip()

        if message.guild:
            for member in message.mentions:
                if member.id != bot_user.id:
                    content = re.sub(rf"<@!?{member.id}>", f"@{member.display_name}", content)

            for channel in message.channel_mentions:
                content = re.sub(rf"<#{channel.id}>", f"#{channel.name}", content)

            for role in message.role_mentions:
                content = re.sub(rf"<@&{role.id}>", f"@{role.name}", content)

        return content.strip()

    @staticmethod
    def sanitize_outgoing_content(content: str) -> str:
        content = content.replace("@everyone", "@\u200beveryone")
        content = content.replace("@here", "@\u200bhere")
        return content