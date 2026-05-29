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

    async def _download_attachment(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                return b""

    def _should_use_search(self, text: str) -> bool:
        content = text.lower()
        
        search_keywords = [
            "weather", "temperature", "news", "current", "latest", "time in", "date", 
            "who is", "what is the price of", "stock", "google", "search", "lookup", 
            "status of", "score of", "match today", "ranking", "how tall is", "how old is"
        ]
        
        return any(kw in content for kw in search_keywords)

    def _sanitize_mime_type_and_data(self, mime_type: str, filename: str, file_bytes: bytes) -> tuple[str, bytes] | tuple[None, None]:
        if not mime_type:
            return None, None
            
        mime_type = mime_type.lower().split(';')[0].strip()
        
        supported_images = ["image/png", "image/jpeg", "image/jpg", "image/webp", "image/heic", "image/heif"]
        if mime_type.startswith("image/"):
            if mime_type in supported_images:
                return mime_type, file_bytes
            return "image/png", file_bytes
            
        supported_videos = ["video/mp4", "video/mpeg", "video/mov", "video/avi", "video/webm", "video/x-flv", "video/x-ms-wmv", "video/3gpp"]
        if mime_type.startswith("video/"):
            if mime_type in supported_videos:
                return mime_type, file_bytes
            return "video/mp4", file_bytes
            
        supported_audios = ["audio/mp3", "audio/wav", "audio/aac", "audio/flac", "audio/ogg", "audio/m4a", "audio/webm", "audio/mpeg", "audio/x-wav"]
        if mime_type.startswith("audio/"):
            if mime_type in supported_audios:
                return mime_type, file_bytes
            return "audio/mp3", file_bytes
            
        supported_docs = [
            "application/pdf", 
            "text/plain", 
            "text/html", 
            "text/css", 
            "text/csv", 
            "text/xml", 
            "text/markdown", 
            "text/javascript", 
            "application/json"
        ]
        if mime_type in supported_docs:
            return mime_type, file_bytes
            
        if mime_type.startswith("text/") or filename.endswith(('.py', '.java', '.js', '.ts', '.cpp', '.c', '.cs', '.h', '.sh', '.bat', '.yaml', '.yml', '.ini', '.conf', '.json', '.md', '.txt')):
            try:
                file_bytes.decode('utf-8', errors='ignore')
                return "text/plain", file_bytes
            except Exception:
                pass
                
        logger.warning(f"File '{filename}' has unsupported MIME type '{mime_type}'. Skipping file payload to protect API.")
        return None, None

    async def generate_reply(self, message_content: str, channel_history: str, attachments: list, user_display_name: str, user_memory: dict, server_context: str, scraped_pages: list[str] = None, user_status: str = None):
        parts = []
        uploaded_cloud_files = []
        
        status_section = ""
        if user_status:
            status_section = f"--- CURRENT STATUS & ACTIVITY FOR {user_display_name} ---\n{user_status}\n\n"
        
        scraped_section = ""
        if scraped_pages:
            scraped_section = "\n\n--- SCRAPED WEBPAGE CONTENT ---\n" + "\n\n---\n\n".join(scraped_pages)
        
        context_prompt = (
            f"--- SERVER ENVIRONMENT DATA ---\n"
            f"{server_context}\n\n"
            f"--- LONG TERM MEMORY FILE FOR {user_display_name} ---\n"
            f"{json.dumps(user_memory, indent=2)}\n\n"
            f"--- MULTI-USER CONVERSATION TRANSCRIPT ---\n"
            f"{channel_history}\n\n"
            f"{status_section}"
            f"{scraped_section}\n\n"
            f"--- NEW MESSAGE FROM {user_display_name} ---\n"
            f"{message_content}\n\n"
            f"--- YOUR TURN ---\n"
            f"Generate ONLY your single response. Do not continue the transcript or write dialog for other users. Talk directly to {user_display_name}."
        )
        parts.append(context_prompt)

        for attachment in attachments:
            raw_mime = attachment.content_type
            if not raw_mime:
                continue
                
            file_bytes = await self._download_attachment(attachment.url)
            if not file_bytes:
                continue
                
            mime_type, sanitized_bytes = self._sanitize_mime_type_and_data(raw_mime, attachment.filename, file_bytes)
            if not mime_type or not sanitized_bytes:
                continue
                
            if mime_type.startswith('video/'):
                temp_dir = tempfile.gettempdir()
                temp_path = os.path.join(temp_dir, attachment.filename)
                with open(temp_path, "wb") as f:
                    f.write(sanitized_bytes)
                
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
            
            else:
                parts.append(types.Part.from_bytes(data=sanitized_bytes, mime_type=mime_type))

        active_tools = [{"code_execution": {}}]
        
        if self._should_use_search(message_content) or self._should_use_search(channel_history[-500:]):
            active_tools.append({"google_search": {}})
            logger.info("Dynamic tools routing: Google Search Grounding ENABLED.")
        else:
            logger.info("Dynamic tools routing: Google Search Grounding DISABLED (optimizing latency).")

        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=active_tools,
            temperature=0.7,
            thinking_config=types.ThinkingConfig(include_thoughts=False)
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