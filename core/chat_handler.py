import os
import json
import logging
import aiohttp
import tempfile
from google import genai
from google.genai import types

logger = logging.getLogger("ChatHandler")

class ChatHandler:
    def __init__(self, api_key: str, system_prompt_path: str, model_name: str = "gemini-2.5-flash"):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        
        try:
            with open(system_prompt_path, 'r', encoding='utf-8') as file:
                self.system_instruction = file.read()
        except FileNotFoundError:
            logger.error(f"Could not find system prompt at {system_prompt_path}")
            self.system_instruction = "You are a helpful friend on Discord."

        self.active_tools = [
            {"google_search": {}},
            {"code_execution": {}}
        ]

    async def _download_attachment(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                return b""

    async def generate_reply(self, message_content: str, channel_history: str, attachments: list, user_display_name: str, user_memory: dict, server_context: str):
        parts = []
        uploaded_cloud_files = []
        
        context_prompt = (
            f"{self.system_instruction}\n\n"
            f"--- SERVER ENVIRONMENT DATA ---\n"
            f"{server_context}\n\n"
            f"--- LONG TERM MEMORY FILE FOR {user_display_name} ---\n"
            f"{json.dumps(user_memory, indent=2)}\n\n"
            f"--- MULTI-USER CONVERSATION TRANSCRIPT ---\n"
            f"{channel_history}\n\n"
            f"--- NEW MESSAGE FROM {user_display_name} ---\n"
            f"{message_content}"
        )
        parts.append(context_prompt)

        for attachment in attachments:
            mime_type = attachment.content_type
            if not mime_type:
                continue
                
            if mime_type.startswith('video/'):
                file_bytes = await self._download_attachment(attachment.url)
                if file_bytes:
                    temp_dir = tempfile.gettempdir()
                    temp_path = os.path.join(temp_dir, attachment.filename)
                    with open(temp_path, "wb") as f:
                        f.write(file_bytes)
                    
                    try:
                        logger.info(f"Uploading video to Gemini Cloud File Storage: {attachment.filename}")
                        cloud_file = await self.client.aio.files.upload(file=temp_path)
                        parts.append(cloud_file)
                        uploaded_cloud_files.append(cloud_file)
                    except Exception as upload_err:
                        logger.error(f"Failed to stream video to Files API: {upload_err}")
                    finally:
                        if os.path.exists(temp_path):
                            os.remove(temp_path)
            
            elif mime_type.startswith('image/') or mime_type.startswith('text/') or mime_type == 'application/pdf':
                file_bytes = await self._download_attachment(attachment.url)
                if file_bytes:
                    parts.append(types.Part.from_bytes(data=file_bytes, mime_type=mime_type))

        config = types.GenerateContentConfig(
            tools=self.active_tools,
            temperature=0.7
        )

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=parts,
                config=config
            )
            
            for cloud_file in uploaded_cloud_files:
                try:
                    await self.client.aio.files.delete(name=cloud_file.name)
                    logger.info(f"Cleaned up cloud file: {cloud_file.name}")
                except Exception as del_err:
                    logger.warning(f"Failed to delete cloud file: {del_err}")
                    
            return response
            
        except Exception as e:
            for cloud_file in uploaded_cloud_files:
                try:
                    await self.client.aio.files.delete(name=cloud_file.name)
                except Exception:
                    pass
            logger.error(f"Gemini Generation Error: {e}")
            raise e