
import os
import logging
import aiohttp

logger = logging.getLogger("DiscordVoiceSessionProxy")

class MockVC:
    def __init__(self, voice_channel):
        self.channel = voice_channel

class DiscordVoiceSession:
    def __init__(self, bot_instance, voice_channel, guild_id: int, text_channel):
        self.bot = bot_instance
        self.vc = MockVC(voice_channel)
        self.guild_id = guild_id
        self.text_channel = text_channel
        self.channel_id = voice_channel.id if voice_channel else None
        
        self.node_api_url = os.getenv("NODE_VOICE_API_URL", "http://localhost:3000")

    async def start(self):
        is_dm = False
        config = await self.bot.get_config(self.guild_id, is_dm=is_dm)
        
        system_prompt = config.get("system_prompt", "")
        if not system_prompt:
            prompt_path = os.path.join("config", "voice_prompt.md")
            try:
                with open(prompt_path, 'r', encoding='utf-8') as file:
                    system_prompt = file.read()
            except FileNotFoundError:
                system_prompt = "You are a helpful, casual companion in a voice channel."
                
        voice_name = os.getenv("GEMINI_VOICE_NAME", "Puck")
        gemini_key = os.getenv("GEMINI_API_KEY")
        model_name = os.getenv("LIVE_MODEL", "gemini-3.1-flash-live-preview")
        
        payload = {
            "guild_id": str(self.guild_id),
            "channel_id": str(self.channel_id),
            "text_channel_id": str(self.text_channel.id) if self.text_channel else "",
            "system_prompt": system_prompt,
            "voice_name": voice_name,
            "gemini_api_key": gemini_key,
            "model_name": model_name
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.node_api_url}/join", json=payload, timeout=5) as response:
                    if response.status == 200:
                        logger.info(f"Node.js voice service registered guild {self.guild_id} cleanly.")
                    else:
                        txt = await response.text()
                        logger.error(f"Node.js voice service registration failed: {txt}")
                        raise RuntimeError(f"Node.js voice service rejected join: {txt}")
        except Exception as e:
            logger.error(f"Failed to communicate with Node.js voice service: {e}")
            raise RuntimeError(f"Failed to communicate with Node.js voice service: {e}")

    async def handle_gateway_packet(self, packet_type: str, data: dict):
        payload = {
            "guild_id": str(self.guild_id),
            "type": packet_type,
            "data": data
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.node_api_url}/gateway-packet", json=payload, timeout=5) as response:
                    if response.status != 200:
                        txt = await response.text()
                        logger.error(f"Node.js voice service rejected gateway packet: {txt}")
        except Exception as e:
            logger.error(f"Error forwarding gateway packet to Node.js: {e}")

    async def stop(self):
        payload = {
            "guild_id": str(self.guild_id)
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{self.node_api_url}/leave", json=payload, timeout=5) as response:
                    if response.status == 200:
                        logger.info(f"Node.js voice service left successfully for guild {self.guild_id}")
                    else:
                        txt = await response.text()
                        logger.warning(f"Node.js voice service leave failed: {txt}")
        except Exception as e:
            logger.error(f"Failed to communicate with Node.js voice service on leave: {e}")