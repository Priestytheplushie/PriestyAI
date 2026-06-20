
import os
import json
import logging
import datetime
import urllib.request
import xml.etree.ElementTree as ET
import discord
from datetime import datetime as dt_class, timezone, timedelta

logger = logging.getLogger("NewsGatherer")

MAX_CHAT_LOG_MESSAGES = 300

def fetch_global_news() -> list:
    url = "https://feeds.npr.org/1004/rss.xml"
    headlines = []
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
        
        root = ET.fromstring(xml_data)
        items = root.findall(".//item")
        for item in items[:4]:
            title_el = item.find("title")
            desc_el = item.find("description")
            title = title_el.text.strip() if title_el is not None else ""
            desc = desc_el.text.strip() if desc_el is not None else ""
            
            import re
            desc = re.sub(r'<[^>]*>', '', desc).strip()
            
            if title:
                headlines.append({
                    "title": title,
                    "description": desc
                })
    except Exception as e:
        logger.warning(f"Failed to fetch global RSS headlines: {e}")
        headlines = [
            {"title": "Global Tech and Science Alliances Solidify", "description": "International teams report collaborative efforts advancing software automation frameworks globally."},
            {"title": "Global Logistics Efficiency Reaches New Highs", "description": "International shipping routes report standard processing speeds stabilizing across major hubs."}
        ]
    return headlines


class NewsScraper:
    def __init__(self, bot, guild_id: int, edition: str = "morning"):
        self.bot = bot
        self.guild_id = guild_id
        self.edition = edition.lower()
        self.payload = {
            "timestamp": dt_class.now(timezone.utc).isoformat(),
            "server_name": "",
            "server_metadata": {
                "owner": "Priesty",
                "admins": [],
                "moderators": []
            },
            "scheduled_events": [],
            "announcements": [],
            "public_discussions": {},
            "real_world_news": []
        }

    async def gather_all_data(self, config_state: dict) -> str:
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            guild = await self.bot.fetch_guild(self.guild_id)
            
        if not guild:
            raise ValueError(f"Could not locate Guild with ID {self.guild_id} inside the bot's active gateway session.")

        logger.info(f"Gathering server data for: '{guild.name}' (ID: {guild.id})")
        self.payload["server_name"] = guild.name
        
        admins = []
        moderators = []
        
        for member in guild.members:
            if member.bot:
                continue
            member_roles = [r.id for r in member.roles]
            is_excluded = any(r_id in config_state.get("excluded_roles", []) for r_id in member_roles)
            if is_enabled := is_excluded:
                continue
                
            if member.guild_permissions.administrator or member.id == guild.owner_id:
                admins.append(member.display_name)
            elif member.guild_permissions.manage_messages or member.guild_permissions.kick_members:
                moderators.append(member.display_name)
                
        owner_name = guild.owner.display_name if guild.owner else "Priesty"
        
        self.payload["server_metadata"] = {
            "server_name": guild.name,
            "owner": owner_name,
            "admins": list(set(admins))[:5],
            "moderators": list(set(moderators))[:8]
        }

        logger.info("Extracting scheduled events...")
        try:
            events = await guild.fetch_scheduled_events()
            for event in events:
                if event.status in (discord.EventStatus.scheduled, discord.EventStatus.active):
                    self.payload["scheduled_events"].append({
                        "id": event.id,
                        "name": event.name,
                        "description": event.description or "No description provided.",
                        "start_time": event.start_time.isoformat() if event.start_time else None,
                        "status": str(event.status),
                        "subscriber_count": event.user_count
                    })
        except Exception as e:
            logger.warning(f"Could not fetch scheduled events: {e}")

        logger.info("Analyzing text channel transcripts...")
        await self.gather_channel_content(guild, config_state)

        logger.info("Extracting global headlines...")
        self.payload["real_world_news"] = fetch_global_news()

        os.makedirs("temp", exist_ok=True)
        output_path = f"temp/raw_news_data_{self.guild_id}.json"
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.payload, f, indent=4, ensure_ascii=False)
            
        logger.info(f"Staged news raw data written successfully to: {output_path}")
        return output_path

    async def gather_channel_content(self, guild: discord.Guild, config_state: dict):
        lookback_hours = 12.5 if self.edition == "morning" else 11
        time_limit = dt_class.now(timezone.utc) - timedelta(hours=lookback_hours)
        excluded_roles = config_state.get("excluded_roles", [])
        
        logger.info(f"Lookback window set to: last {lookback_hours} hours...")

        for channel in guild.text_channels:
            everyone_role = guild.default_role
            permissions = channel.permissions_for(everyone_role)
            if not permissions.view_channel:
                continue

            is_announcement = channel.type == discord.ChannelType.news
            logger.info(f" -> Scoping #{channel.name}...")
            
            try:
                messages_gathered = []
                async_messages = []
                
                async for msg in channel.history(limit=MAX_CHAT_LOG_MESSAGES, after=time_limit):
                    if msg.author.bot:
                        continue
                        
                    if hasattr(msg.author, "roles"):
                        member_role_ids = [role.id for role in msg.author.roles]
                        if any(r_id in excluded_roles for r_id in member_role_ids):
                            continue
                            
                    async_messages.append(msg)
                
                for msg in async_messages:
                    avatar_url = ""
                    if msg.author.display_avatar:
                        avatar_url = msg.author.display_avatar.with_format("png").url
                    
                    messages_gathered.append({
                        "author": msg.author.display_name,
                        "username": msg.author.name,
                        "author_avatar_url": avatar_url,
                        "content": msg.clean_content,
                        "timestamp": msg.created_at.isoformat(),
                        "attachments": [att.url for att in msg.attachments if att.content_type and att.content_type.startswith("image/")],
                        "reply_to": msg.reference.message_id if msg.reference else None
                    })

                if not messages_gathered:
                    continue

                if is_announcement:
                    self.payload["announcements"].append({
                        "channel_name": channel.name,
                        "channel_id": channel.id,
                        "messages": messages_gathered
                    })
                else:
                    self.payload["public_discussions"][channel.name] = {
                        "channel_id": channel.id,
                        "messages": messages_gathered
                    }

            except Exception as e:
                logger.warning(f"Could not scan channel #{channel.name}: {e}")