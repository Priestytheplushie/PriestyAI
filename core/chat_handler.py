import os
import logging
import aiohttp
from google import genai
from google.genai import types

logger = logging.getLogger("ChatHandler")

class ChatHandler:
    def __init__(self, api_key: str, system_prompt_path: str):
        self.client = genai.Client(api_key=api_key)
        
        try:
            with open(system_prompt_path, 'r', encoding='utf-8') as file:
                self.system_instruction = file.read()
        except FileNotFoundError:
            logger.error(f"Could not find system prompt at {system_prompt_path}")
            self.system_instruction = "You are a helpful friend on Discord."

    async def _download_attachment(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    logger.warning(f"Failed to download attachment: {url}")
                    return b""

    async def generate_reply(self, message_content: str, channel_history: str, attachments: list, user_display_name: str) -> str:
        
        parts = []
        
        context_prompt = (
            f"{self.system_instruction}\n\n"
            f"--- CURRENT CONVERSATION HISTORY ---\n"
            f"{channel_history}\n\n"
            f"--- NEW MESSAGE FROM {user_display_name} ---\n"
            f"{message_content}"
        )
        parts.append(context_prompt)

        for attachment in attachments:
            mime_type = attachment.content_type
            if mime_type and (mime_type.startswith('image/') or mime_type.startswith('video/') or mime_type.startswith('text/')):
                file_bytes = await self._download_attachment(attachment.url)
                if file_bytes:
                    parts.append(
                        types.Part.from_bytes(
                            data=file_bytes,
                            mime_type=mime_type
                        )
                    )

        try:
            response = self.client.models.generate_content(
                model='gemini-2.5-flash',
                contents=parts
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini Generation Error: {e}")
            return f"*(An error occurred in my brain: {str(e)})*"