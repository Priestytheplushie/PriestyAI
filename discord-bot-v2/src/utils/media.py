import aiohttp
import logging
from typing import Optional
import discord
from google.genai import types

logger = logging.getLogger("PriestyAI.Media")

SUPPORTED_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "pdf": "application/pdf",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "mp3": "audio/mp3",
    "wav": "audio/wav",
    "ogg": "audio/ogg"
}

class MediaProcessor:

    @staticmethod
    def get_mime_type(filename: str, content_type: Optional[str] = None) -> Optional[str]:
        if content_type and any(content_type.startswith(prefix) for prefix in ("image/", "video/", "audio/", "application/pdf")):
            return content_type.split(";")[0]

        ext = filename.split(".")[-1].lower() if "." in filename else ""
        return SUPPORTED_MIME_TYPES.get(ext)

    @classmethod
    async def attachment_to_part(cls, attachment: discord.Attachment) -> Optional[types.Part]:
        mime_type = cls.get_mime_type(attachment.filename, attachment.content_type)
        if not mime_type:
            logger.info(f"Skipping unsupported attachment format: {attachment.filename}")
            return None

        if attachment.size > 20 * 1024 * 1024:
            logger.warning(f"Attachment {attachment.filename} exceeds 20MB direct buffer limit.")
            return None

        try:
            data = await attachment.read()
            return types.Part.from_bytes(data=data, mime_type=mime_type)
        except Exception as e:
            logger.error(f"Failed to read attachment {attachment.filename}: {e}")
            return None