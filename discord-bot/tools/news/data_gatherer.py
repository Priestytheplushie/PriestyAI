import os
import json
import logging
import urllib.request
import re
import xml.etree.ElementTree as ET
import discord
from datetime import datetime as dt_class, timezone, timedelta

logger = logging.getLogger("NewsGatherer")

MAX_CHAT_LOG_MESSAGES = 300

STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "is",
    "are",
    "was",
    "were",
    "to",
    "for",
    "with",
    "of",
    "on",
    "at",
    "in",
    "by",
    "from",
    "who",
    "whom",
    "how",
    "what",
    "which",
    "this",
    "that",
    "these",
    "those",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "their",
    "our",
    "its",
    "be",
    "been",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "shall",
    "should",
    "can",
    "could",
    "may",
    "might",
    "must",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "ought",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "got",
    "just",
    "get",
    "like",
}


def fetch_global_news() -> list:
    """
    Queries World, Technology, and Science RSS feeds from NPR to compile a broad backup reservoir.
    """
    feeds = {
        "World": "https://feeds.npr.org/1004/rss.xml",
        "Technology": "https://feeds.npr.org/1019/rss.xml",
        "Science": "https://feeds.npr.org/1007/rss.xml",
    }
    headlines = []
    for category, url in feeds.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_data = response.read()

            root = ET.fromstring(xml_data)
            items = root.findall(".//item")
            for item in items[:4]:
                title_el = item.find("title")
                desc_el = item.find("description")
                title = title_el.text.strip() if title_el is not None else ""
                desc = desc_el.text.strip() if desc_el is not None else ""

                desc = re.sub(r"<[^>]*>", "", desc).strip()

                if title:
                    headlines.append(
                        {"category": category, "title": title, "description": desc}
                    )
        except Exception as e:
            logger.warning(f"Failed to fetch {category} RSS headlines: {e}")

    if not headlines:
        headlines = [
            {
                "category": "World",
                "title": "Global Tech and Science Alliances Solidify",
                "description": "International teams report collaborative efforts advancing software automation frameworks globally.",
            },
            {
                "category": "Technology",
                "title": "Automated Tool Integration Accelerates",
                "description": "New developer workflow platforms report significant gains in compiler speeds and workspace automation.",
            },
            {
                "category": "Science",
                "title": "Computational Physics Models Advance",
                "description": "Researchers deploy advanced matrix algorithms to map complex dynamic structures in real-time.",
            },
        ]
    return headlines


class NewsScraper:
    """
    A stateless, thread-safe helper class that utilizes the bot's running client
    to scrape server data without logging out or initiating dual-connections.
    """

    def __init__(self, bot, guild_id: int, edition: str = "morning"):
        self.bot = bot
        self.guild_id = guild_id
        self.edition = edition.lower()
        self.payload = {
            "timestamp": dt_class.now(timezone.utc).isoformat(),
            "server_name": "",
            "server_metadata": {"owner": "Priesty", "admins": [], "moderators": []},
            "scheduled_events": [],
            "announcements": [],
            "public_discussions": {},
            "real_world_news": [],
            "qa_questions": [],
            "active_poll": {},
            "member_profiles": {},
            "server_stats": {
                "velocity": {},
                "channel_volume": {},
                "top_games": {},
                "top_chatters": {},
                "word_frequencies": {},
            },
        }

    def get_member_profile_data(self, member: discord.Member) -> dict:
        """Compiles standard roles, custom statuses, and rich presences for active users."""
        profile = {
            "roles": [r.name for r in member.roles if not r.is_default()][:5],
            "custom_status": "",
            "active_activities": [],
        }
        for act in member.activities:
            if isinstance(act, discord.CustomActivity):
                if act.state or act.name:
                    profile["custom_status"] = act.state or act.name
            elif act.type == discord.ActivityType.playing and act.name:
                if act.name.lower() not in ["spotify", "custom status"]:
                    profile["active_activities"].append(f"Playing {act.name}")
            elif act.type == discord.ActivityType.listening and act.name:
                if hasattr(act, "title") and hasattr(act, "artist"):
                    profile["active_activities"].append(
                        f"Listening to Spotify: {act.title} by {act.artist}"
                    )
            elif act.type == discord.ActivityType.streaming and act.name:
                profile["active_activities"].append(f"Streaming on Twitch: {act.name}")
        return profile

    async def scavenge_active_polls(self, guild: discord.Guild, config_state: dict):
        """Scans the broadcast news channel for the latest active poll and compiles its current voting results."""
        news_channel_id = config_state.get("news_channel_id")
        if not news_channel_id:
            return

        channel = guild.get_channel(news_channel_id)
        if not channel:
            return

        try:
            async for msg in channel.history(limit=25):
                if msg.poll:
                    msg = await channel.fetch_message(msg.id)
                    self.payload["active_poll"] = {
                        "question": msg.poll.question,
                        "answers": [
                            {"text": ans.text, "votes": ans.vote_count}
                            for ans in msg.poll.answers
                        ],
                    }
                    break
        except Exception as e:
            logger.warning(f"Failed to gather poll results: {e}")

    async def gather_all_data(self, config_state: dict) -> str:
        """
        Coordinates the scraping flow, aggregates server context, and writes
        the results to a local staging file in 'temp/'. Returns the output file path.
        """
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            guild = await self.bot.fetch_guild(self.guild_id)

        if not guild:
            raise ValueError(
                f"Could not locate Guild with ID {self.guild_id} inside the bot's active gateway session."
            )

        try:
            await guild.chunk()
        except Exception as chunk_err:
            logger.warning(f"Could not force-chunk members: {chunk_err}")

        logger.info(f"Gathering server data for: '{guild.name}' (ID: {guild.id})")
        self.payload["server_name"] = guild.name

        admins = []
        moderators = []

        for member in guild.members:
            if member.bot:
                continue

            member_roles = [r.id for r in member.roles]
            is_excluded = any(
                r_id in config_state.get("excluded_roles", []) for r_id in member_roles
            )
            if is_excluded:
                continue

            if member.guild_permissions.administrator or member.id == guild.owner_id:
                admins.append(member.display_name)
            elif (
                member.guild_permissions.manage_messages
                or member.guild_permissions.kick_members
            ):
                moderators.append(member.display_name)

        owner_name = guild.owner.display_name if guild.owner else "Priesty"

        self.payload["server_metadata"] = {
            "server_name": guild.name,
            "owner": owner_name,
            "admins": list(set(admins))[:5],
            "moderators": list(set(moderators))[:8],
        }

        self.gather_server_presence_stats(guild)

        logger.info("Extracting scheduled events...")
        try:
            events = await guild.fetch_scheduled_events()
            for event in events:
                if event.status in (
                    discord.EventStatus.scheduled,
                    discord.EventStatus.active,
                ):
                    self.payload["scheduled_events"].append(
                        {
                            "id": event.id,
                            "name": event.name,
                            "description": event.description
                            or "No description provided.",
                            "start_time": (
                                event.start_time.isoformat()
                                if event.start_time
                                else None
                            ),
                            "status": str(event.status),
                            "subscriber_count": event.user_count,
                        }
                    )
        except Exception as e:
            logger.warning(f"Could not fetch scheduled events: {e}")

        logger.info("Analyzing text channel transcripts...")
        await self.gather_channel_content(guild, config_state)

        if self.edition == "night":
            await self.scavenge_qa_thread_questions(guild, config_state)

        await self.scavenge_active_polls(guild, config_state)

        logger.info("Extracting global headlines...")
        self.payload["real_world_news"] = fetch_global_news()

        total_messages = len(self.payload["public_discussions"])

        os.makedirs("temp", exist_ok=True)
        rolling_path = f"temp/news_state_rolling_{self.guild_id}.json"
        rolling_history = []
        if os.path.exists(rolling_path):
            try:
                with open(rolling_path, "r", encoding="utf-8") as rf:
                    rolling_history = json.load(rf)
            except Exception:
                rolling_history = []

        rolling_history.append(total_messages)
        rolling_history = rolling_history[-5:]

        try:
            with open(rolling_path, "w", encoding="utf-8") as wf:
                json.dump(rolling_history, wf)
        except Exception:
            pass

        avg_volume = (
            sum(rolling_history) / len(rolling_history) if rolling_history else 1.0
        )
        ratio = total_messages / avg_volume if avg_volume > 0 else 1.0

        self.payload["server_stats"]["rolling_average_volume"] = int(avg_volume)
        self.payload["server_stats"]["current_volume"] = total_messages
        self.payload["server_stats"]["activity_ratio"] = round(ratio, 2)

        output_path = f"temp/raw_news_data_{self.guild_id}.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.payload, f, indent=4, ensure_ascii=False)

        logger.info(f"Staged news raw data written successfully to: {output_path}")
        return output_path

    def gather_server_presence_stats(self, guild: discord.Guild):
        """Compiles active desktop gaming distributions from presence data."""
        presence_stats = {}
        for member in guild.members:
            if member.bot:
                continue
            for act in member.activities:
                if act.type == discord.ActivityType.playing and act.name:
                    game_name = act.name
                    if game_name.lower() not in ["spotify", "custom status"]:
                        presence_stats[game_name] = presence_stats.get(game_name, 0) + 1

        self.payload["server_stats"]["top_games"] = dict(
            sorted(presence_stats.items(), key=lambda x: x[1], reverse=True)[:5]
        )

    async def scavenge_qa_thread_questions(
        self, guild: discord.Guild, config_state: dict
    ):
        """Scans designated broadcast channel for morning Q&A threads, extracting user questions."""
        news_channel_id = config_state.get("news_channel_id")
        if not news_channel_id:
            return

        channel = guild.get_channel(news_channel_id)
        if not channel:
            return

        try:
            active_threads = await guild.active_threads()
            for thread in active_threads:
                if thread.parent_id == channel.id and "Q&A" in thread.name:
                    async for msg in thread.history(limit=50):
                        if msg.author.bot:
                            continue

                        self.payload["qa_questions"].append(
                            {
                                "author": msg.author.display_name,
                                "username": msg.author.name,
                                "question": msg.clean_content,
                                "timestamp": msg.created_at.isoformat(),
                            }
                        )

                        if msg.author.id not in self.payload["member_profiles"]:
                            member_obj = guild.get_member(msg.author.id)
                            if member_obj:
                                self.payload["member_profiles"][msg.author.id] = (
                                    self.get_member_profile_data(member_obj)
                                )
                    break
        except Exception as e:
            logger.warning(f"Failed to gather thread questions: {e}")

    async def gather_channel_content(self, guild: discord.Guild, config_state: dict):
        """Scans public channels, filtering out excluded roles and compiling volume velocity metrics."""
        lookback_hours = 12.5 if self.edition == "morning" else 11
        time_limit = dt_class.now(timezone.utc) - timedelta(hours=lookback_hours)
        excluded_roles = config_state.get("excluded_roles", [])

        logger.info(f"Lookback window set to: last {lookback_hours} hours...")

        hourly_velocity = {}
        channel_volumes = {}
        word_freqs = {}
        top_chatters = {}

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

                async for msg in channel.history(
                    limit=MAX_CHAT_LOG_MESSAGES, after=time_limit
                ):
                    if msg.author.bot:
                        continue

                    if hasattr(msg.author, "roles"):
                        member_role_ids = [role.id for role in msg.author.roles]
                        if any(r_id in excluded_roles for r_id in member_role_ids):
                            continue

                    async_messages.append(msg)

                    hour_bucket = msg.created_at.strftime("%H:00")
                    hourly_velocity[hour_bucket] = (
                        hourly_velocity.get(hour_bucket, 0) + 1
                    )

                for msg in async_messages:
                    avatar_url = ""
                    if msg.author.display_avatar:
                        avatar_url = msg.author.display_avatar.with_format("png").url

                    messages_gathered.append(
                        {
                            "author": msg.author.display_name,
                            "username": msg.author.name,
                            "author_avatar_url": avatar_url,
                            "content": msg.clean_content,
                            "timestamp": msg.created_at.isoformat(),
                            "attachments": [
                                att.url
                                for att in msg.attachments
                                if att.content_type
                                and att.content_type.startswith("image/")
                            ],
                            "reply_to": (
                                msg.reference.message_id if msg.reference else None
                            ),
                        }
                    )

                    author_name = msg.author.display_name
                    top_chatters[author_name] = top_chatters.get(author_name, 0) + 1

                    words = msg.clean_content.lower().split()
                    for w in words:
                        w_strip = re.sub(r"[^a-z]", "", w)
                        if w_strip and len(w_strip) > 2 and w_strip not in STOP_WORDS:
                            word_freqs[w_strip] = word_freqs.get(w_strip, 0) + 1

                    if msg.author.name not in self.payload["member_profiles"]:
                        member_obj = guild.get_member(msg.author.id)
                        if member_obj:
                            self.payload["member_profiles"][msg.author.name] = (
                                self.get_member_profile_data(member_obj)
                            )

                if not messages_gathered:
                    continue

                channel_volumes[channel.name] = len(messages_gathered)

                if is_announcement:
                    self.payload["announcements"].append(
                        {
                            "channel_name": channel.name,
                            "channel_id": channel.id,
                            "messages": messages_gathered,
                        }
                    )
                else:
                    self.payload["public_discussions"][channel.name] = {
                        "channel_id": channel.id,
                        "messages": messages_gathered,
                    }

            except Exception as e:
                logger.warning(f"Could not scan channel #{channel.name}: {e}")

        self.payload["server_stats"]["velocity"] = dict(sorted(hourly_velocity.items()))
        self.payload["server_stats"]["channel_volume"] = dict(
            sorted(channel_volumes.items(), key=lambda x: x[1], reverse=True)[:5]
        )
        self.payload["server_stats"]["top_chatters"] = dict(
            sorted(top_chatters.items(), key=lambda x: x[1], reverse=True)[:5]
        )
        self.payload["server_stats"]["word_frequencies"] = dict(
            sorted(word_freqs.items(), key=lambda x: x[1], reverse=True)[:15]
        )
