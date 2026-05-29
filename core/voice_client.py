
import logging

logger = logging.getLogger("VoiceClient")

class DiscordVoiceSession:
    def __init__(self, bot_instance, voice_client, guild_id: int, text_channel):
        self.bot = bot_instance
        self.vc = voice_client
        self.guild_id = guild_id
        self.text_channel = text_channel

    async def start(self):
        logger.info("Voice session startup requested, but voice is temporarily disabled.")
        await self.stop()

    async def stop(self):
        logger.info("Voice session stop requested.")