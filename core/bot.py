import discord
import os
import logging
from core.memory import ChatHistoryTracker
from core.chat_handler import ChatHandler

logger = logging.getLogger("DiscordFriend")

class FriendBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.brain_server_id = int(os.getenv("BRAIN_SERVER_ID", 0))
        
        prompt_path = os.path.join("config", "system_prompt.md")
        
        self.history_tracker = ChatHistoryTracker(limit=25)
        self.chat_handler = ChatHandler(api_key=self.gemini_key, system_prompt_path=prompt_path)
        
        self.active_channels = set()

    async def on_ready(self):
        logger.info(f"Bot logged in as {self.user.name} (ID: {self.user.id})")
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="Hanging out"))

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id:
            self.history_tracker.add_message(message)
            return

        self.history_tracker.add_message(message)

        is_mentioned = self.user in message.mentions
        
        is_reply_to_bot = False
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                if message.reference.resolved.author.id == self.user.id:
                    is_reply_to_bot = True

        is_watched_channel = message.channel.id in self.active_channels

        should_respond = is_mentioned or is_reply_to_bot or is_watched_channel

        if should_respond:
            async with message.channel.typing():
                channel_history = self.history_tracker.get_formatted_history(message.channel.id)
                
                display_name = message.author.nick if isinstance(message.author, discord.Member) and message.author.nick else message.author.display_name
                
                response_text = await self.chat_handler.generate_reply(
                    message_content=message.clean_content,
                    channel_history=channel_history,
                    attachments=message.attachments,
                    user_display_name=display_name
                )
                
                if is_mentioned or is_reply_to_bot:
                    await message.reply(response_text)
                else:
                    await message.channel.send(response_text)

    def run_bot(self):
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.critical("DISCORD_TOKEN is missing from .env!")
            return
        self.run(token)