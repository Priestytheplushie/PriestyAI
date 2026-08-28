import io
import aiohttp
import logging
from typing import List, Optional
import discord
from google.genai import types

logger = logging.getLogger("PriestyAI.Multimodal")

SUPPORTED_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "pdf": "application/pdf",
    "txt": "text/plain",
    "py": "text/x-python",
    "json": "application/json",
    "csv": "text/csv",
    "log": "text/plain"
}

class MultimodalProcessor:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    def _get_mime_type(self, filename: str, content_type: Optional[str] = None) -> Optional[str]:
        if content_type and content_type in SUPPORTED_MIME_TYPES.values():
            return content_type
        
        ext = filename.split(".")[-1].lower() if "." in filename else ""
        return SUPPORTED_MIME_TYPES.get(ext)

    async def process_attachments(self, message: discord.Message) -> List[types.Part]:
        parts: List[types.Part] = []
        if not message.attachments:
            return parts

        for attachment in message.attachments:
            if attachment.size > 20 * 1024 * 1024:
                logger.warning(f"Skipping attachment {attachment.filename}: File size {attachment.size} exceeds 20MB.")
                continue

            mime_type = self._get_mime_type(attachment.filename, attachment.content_type)
            if not mime_type:
                logger.info(f"Unsupported attachment type for {attachment.filename}, skipping.")
                continue

            try:
                data = await attachment.read()
                part = types.Part.from_bytes(data=data, mime_type=mime_type)
                parts.append(part)
                logger.info(f"Loaded attachment '{attachment.filename}' as {mime_type} ({len(data)} bytes).")
            except Exception as e:
                logger.error(f"Failed to read attachment {attachment.filename}: {e}")

        return parts

    async def extract_parent_attachments(self, message: discord.Message) -> List[types.Part]:
        if not message.reference or not message.reference.message_id:
            return []

        try:
            channel = message.channel
            parent_msg = await channel.fetch_message(message.reference.message_id)
            if parent_msg and parent_msg.attachments:
                logger.info(f"Extracting {len(parent_msg.attachments)} attachment(s) from replied parent message.")
                return await self.process_attachments(parent_msg)
        except Exception as e:
            logger.warning(f"Could not fetch replied parent message attachments: {e}")

        return []

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()