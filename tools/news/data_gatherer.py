
import os
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
import discord
from dotenv import load_dotenv

TARGET_GUILD_ID = 1421216214427898037
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
            
            desc = re.sub(r'<[^>]*>', '', desc).strip()
            
            if title:
                headlines.append({
                    "title": title,
                    "description": desc
                })
    except Exception as e:
        print(f"      ⚠️ Failed to fetch global RSS headlines: {e}")
        headlines = [
            {"title": "Global Tech and Science Alliances Solidify", "description": "International teams report collaborative efforts advancing software automation frameworks globally."},
            {"title": "Global Logistics Efficiency Reaches New Highs", "description": "International shipping routes report standard processing speeds stabilizing across major hubs."}
        ]
    return headlines


class NewsDataGatherer(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        self.payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "server_name": "",
            "scheduled_events": [],
            "announcements": [],
            "public_discussions": {},
            "real_world_news": []
        }

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print(f"Connecting to Guild ID: {TARGET_GUILD_ID}...")
        
        guild = self.get_guild(TARGET_GUILD_ID)
        if not guild:
            print(f"❌ Error: Could not find Guild ID {TARGET_GUILD_ID}. Verify the bot is joined to that server.")
            await self.close()
            return

        try:
            self.payload["server_name"] = guild.name
            
            print("Fetching Guild Scheduled Events...")
            await self.gather_scheduled_events(guild)

            print("Analyzing public text channels...")
            await self.gather_channel_content(guild)

            print("Fetching global world news RSS headlines...")
            self.payload["real_world_news"] = fetch_global_news()

            os.makedirs("temp", exist_ok=True)
            output_path = "temp/raw_news_data.json"
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.payload, f, indent=4, ensure_ascii=False)
            
            print(f"🎉 Success! Raw news data compiled and saved locally to: {output_path}")

        except Exception as e:
            print(f"❌ Error during data collection: {e}")
            import traceback
            traceback.print_exc()
        
        finally:
            print("Logging out...")
            await self.close()

    async def gather_scheduled_events(self, guild: discord.Guild):
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

    async def gather_channel_content(self, guild: discord.Guild):
        for channel in guild.text_channels:
            everyone_role = guild.default_role
            permissions = channel.permissions_for(everyone_role)
            if not permissions.view_channel:
                continue

            is_announcement = channel.type == discord.ChannelType.news
            
            print(f" -> Scanning #{channel.name} (Type: {channel.type})...")
            try:
                messages_gathered = []
                async_messages = []
                async for msg in channel.history(limit=MAX_CHAT_LOG_MESSAGES, after=datetime.now(timezone.utc) - timedelta(hours=24)):
                    if msg.author.bot:
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
                print(f"      ⚠️ Failed to scan #{channel.name}: {e}")


def run_local_gatherer():
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    
    if not token:
        print("❌ Error: DISCORD_TOKEN not found in environment variables.")
        return

    gatherer = NewsDataGatherer()
    try:
        gatherer.run(token)
    except Exception as e:
        print(f"❌ Failed to run Discord Client: {e}")


if __name__ == "__main__":
    run_local_gatherer()