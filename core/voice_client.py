
import os
import logging
from core.voice.bridge import DiscordGeminiVoiceBridge

logger = logging.getLogger("DiscordVoiceSessionCompatibility")

class DiscordVoiceSession:
    def __init__(self, bot_instance, voice_client, guild_id: int, text_channel):
        self.bot = bot_instance
        self.vc = voice_client
        self.guild_id = guild_id
        self.text_channel = text_channel
        
        gemini_key = os.getenv("GEMINI_API_KEY")
        model_name = os.getenv("LIVE_MODEL", "gemini-3.1-flash-live-preview")
        voice_name = os.getenv("GEMINI_VOICE_NAME", "Puck")
        
        prompt_path = os.path.join("config", "voice_prompt.md")
        try:
            with open(prompt_path, 'r', encoding='utf-8') as file:
                system_prompt = file.read()
        except FileNotFoundError:
            system_prompt = "You are a helpful, casual companion in a voice channel."
            
        self.bridge = DiscordGeminiVoiceBridge(
            voice_client=voice_client,
            guild_id=guild_id,
            text_channel=text_channel,
            gemini_key=gemini_key,
            model_name=model_name,
            voice_name=voice_name,
            system_prompt=system_prompt
        )

    async def start(self):
        await self.bridge.start()

    async def stop(self):
        await self.bridge.stop()