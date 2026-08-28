import re
import datetime
from typing import List, Optional
import dateparser
import discord

class BidirectionalNormalizer:
    def __init__(self):
        self.user_mention_re = re.compile(r"<@!?(\d+)>")
        self.channel_mention_re = re.compile(r"<#(\d+)>")
        self.role_mention_re = re.compile(r"<@&(\d+)>")
        self.discord_ts_re = re.compile(r"<t:(\d+)(?::[a-zA-Z])?>")

        self.outbound_user_tag_re = re.compile(r'<mention\s+type="user"\s+id="(\d+)"[^>]*>')
        self.outbound_channel_tag_re = re.compile(r'<mention\s+type="channel"\s+id="(\d+)"[^>]*>')
        self.outbound_role_tag_re = re.compile(r'<mention\s+type="role"\s+id="(\d+)"[^>]*>')
        self.outbound_date_tag_re = re.compile(r'<date\s+unix="(\d+)"[^>]*>')

    def inbound_normalize(
        self,
        current_message: discord.Message,
        history: List[discord.Message],
        bot_user: discord.ClientUser
    ) -> str:
        guild = current_message.guild
        channel = current_message.channel
        now = datetime.datetime.now(datetime.timezone.utc)

        xml_lines = [
            f'<context server="{guild.name if guild else "Direct Message"}" '
            f'channel="{channel.name if hasattr(channel, "name") else "DM"}" '
            f'current_utc="{now.isoformat()}">'
        ]

        for msg in history:
            if not msg.content and not msg.attachments:
                continue

            author_name = msg.author.display_name
            author_id = str(msg.author.id)
            is_bot = "true" if msg.author.bot else "false"
            mentions_bot = "true" if bot_user in msg.mentions else "false"
            reply_id = str(msg.reference.message_id) if msg.reference and msg.reference.message_id else ""

            content = msg.content
            content = self._resolve_inbound_mentions(content, guild)
            content = self._extract_and_tag_dates(content)

            reply_attr = f' reply_to="{reply_id}"' if reply_id else ""
            xml_lines.append(
                f'  <message id="{msg.id}" author="{author_name}" author_id="{author_id}" '
                f'is_bot="{is_bot}" mentions_bot="{mentions_bot}"{reply_attr}>\n'
                f'    {content.strip()}\n'
                f'  </message>'
            )

        xml_lines.append('</context>')
        return "\n".join(xml_lines)

    def _resolve_inbound_mentions(self, text: str, guild: Optional[discord.Guild]) -> str:
        if not text:
            return ""

        def user_sub(match):
            user_id = match.group(1)
            member = guild.get_member(int(user_id)) if guild else None
            name = member.display_name if member else f"User_{user_id}"
            return f'<mention type="user" id="{user_id}" name="{name}" />'

        def channel_sub(match):
            chan_id = match.group(1)
            chan = guild.get_channel(int(chan_id)) if guild else None
            name = chan.name if chan else f"channel_{chan_id}"
            return f'<mention type="channel" id="{chan_id}" name="{name}" />'

        def role_sub(match):
            role_id = match.group(1)
            role = guild.get_role(int(role_id)) if guild else None
            name = role.name if role else f"role_{role_id}"
            return f'<mention type="role" id="{role_id}" name="{name}" />'

        text = self.user_mention_re.sub(user_sub, text)
        text = self.channel_mention_re.sub(channel_sub, text)
        text = self.role_mention_re.sub(role_sub, text)
        return text

    def _extract_and_tag_dates(self, text: str) -> str:
        words = text.split()
        if len(words) < 2:
            return text

        temporal_keywords = ["tomorrow", "yesterday", "next week", "in 1 hour", "in 2 hours", "tonight", "next monday"]
        lower_text = text.lower()
        for kw in temporal_keywords:
            if kw in lower_text:
                parsed_dt = dateparser.parse(kw, settings={"PREFER_DATES_FROM": "future"})
                if parsed_dt:
                    ts = int(parsed_dt.timestamp())
                    text += f' <temporal text="{kw}" unix="{ts}" iso="{parsed_dt.isoformat()}" />'
                break
        return text

    def outbound_normalize(self, text: str, guild: Optional[discord.Guild]) -> str:
        if not text:
            return ""

        text = re.sub(r'(?m)^#{4,}\s*', '### ', text)

        text = re.sub(r'\$\\text\{([^}]+)\}\$', r'**\1**', text)
        text = re.sub(r'\$\\mathbf\{([^}]+)\}\$', r'**\1**', text)
        text = re.sub(r'\$([^$\n]+)\$', r'*\1*', text)

        text = self.outbound_user_tag_re.sub(r'<@\1>', text)
        text = self.outbound_channel_tag_re.sub(r'<#\1>', text)
        text = self.outbound_role_tag_re.sub(r'<@&\1>', text)
        text = self.outbound_date_tag_re.sub(r'<t:\1:F>', text)

        if guild:
            for channel in guild.text_channels:
                pattern = re.compile(rf'(?<!<#)(?:#){re.escape(channel.name)}\b', re.IGNORECASE)
                text = pattern.sub(f'<#{channel.id}>', text)

        text = re.sub(r'</?(?:context|message)[^>]*>', '', text)
        return text.strip()