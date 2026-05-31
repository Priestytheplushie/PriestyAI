
import os
import json
import logging
import aiohttp
import tempfile
import mimetypes
import io
import re
import zoneinfo
from datetime import datetime, timedelta, time as dt_time
from PIL import Image
from google import genai
from google.genai import types

logger = logging.getLogger("ChatHandler")

class ChatHandler:
    def __init__(self, api_key: str, system_prompt_path: str, model_name: str = None):
        self.client = genai.Client(api_key=api_key)
        
        self.premium_model = os.getenv("GEMINI_MODEL_PREMIUM", "gemini-3-flash-preview")
        self.fallback_model = os.getenv("GEMINI_MODEL_TEXT", "gemma-4-31b-it")
        
        self.pt_zone = zoneinfo.ZoneInfo("America/Los_Angeles")
        self.premium_cooldown_until = None
        
        logger.info(f"ChatHandler initialized. Premium: {self.premium_model} | Fallback: {self.fallback_model}")
        
        try:
            with open(system_prompt_path, 'r', encoding='utf-8') as file:
                self.system_instruction = file.read()
        except FileNotFoundError:
            logger.error(f"Could not find system prompt at {system_prompt_path}")
            self.system_instruction = "You are a helpful friend on Discord."

        artist_prompt_path = os.path.join("config", "image_prompt_system.md")
        try:
            with open(artist_prompt_path, 'r', encoding='utf-8') as file:
                self.artist_system_instruction = file.read()
        except FileNotFoundError:
            logger.error(f"Could not find specialized image system prompt at {artist_prompt_path}")
            self.artist_system_instruction = "Translate the user request into a highly detailed Stable Diffusion prompt."

    def _check_and_reset_quota(self):
        now_pt = datetime.now(self.pt_zone)
        if self.premium_cooldown_until and now_pt >= self.premium_cooldown_until:
            logger.info("API daily reset threshold passed. Gemini 3 Premium re-enabled.")
            self.premium_cooldown_until = None
        return now_pt

    async def generate_artist_stream(self, raw_prompt: str, context_history: str = "", base_image_bytes: bytes = None):
        now_pt = self._check_and_reset_quota()
        active_model = self.premium_model if not self.premium_cooldown_until else self.fallback_model
        use_thinking = (active_model == self.premium_model)
        
        contents = []
        if base_image_bytes:
            try:
                img = Image.open(io.BytesIO(base_image_bytes))
                contents.append(img)
            except Exception as e:
                logger.error(f"Failed to parse base image for Artist: {e}")
                
        contents.append(f"--- CONVERSATIONAL REFERENCE HISTORY ---\n{context_history}\n\n")
        contents.append(f"--- TARGET USER REQUEST TO EXPAND/EDIT ---\n{raw_prompt}")

        if use_thinking:
            config = types.GenerateContentConfig(
                system_instruction=self.artist_system_instruction,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(
                    thinking_level="HIGH",
                    include_thoughts=True
                )
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=self.artist_system_instruction,
                temperature=0.7
            )

        try:
            return await self.client.aio.models.generate_content_stream(
                model=active_model,
                contents=contents,
                config=config
            )
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "exhausted" in error_str or "quota" in error_str:
                if active_model == self.premium_model:
                    logger.warning("Premium Rate limit hit during Artist stream init! Locking and falling back.")
                    tomorrow_pt = now_pt + timedelta(days=1)
                    self.premium_cooldown_until = datetime.combine(tomorrow_pt.date(), dt_time(0, 0, 0), tzinfo=self.pt_zone)
                    
                    fallback_config = types.GenerateContentConfig(
                        system_instruction=self.artist_system_instruction,
                        temperature=0.7
                    )
                    return await self.client.aio.models.generate_content_stream(
                        model=self.fallback_model,
                        contents=contents,
                        config=fallback_config
                    )
            raise e

    async def _download_attachment(self, url: str) -> bytes:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                return b""

    def _should_use_search(self, message_content: str, channel_history: str = "") -> bool:
        content = (message_content + " " + channel_history).lower()
        search_keywords = [
            "weather", "temperature", "news", "current", "latest", "time in", "date", 
            "who is", "what is the price of", "stock", "google", "search", "lookup"
        ]
        return any(kw in content for kw in search_keywords)

    def _select_thinking_level(self, message_content: str, channel_history: str) -> str:
        content = message_content.lower().strip()
        
        clean_content = re.sub(r'<@\d+>', '', content).strip()
        clean_content = re.sub(r'[^\w\s]', '', clean_content).strip()
        
        casual_greetings_and_replies = {
            "hi", "hello", "hey", "yo", "sup", "greetings", "howdy", "morning", "afternoon", "evening",
            "thanks", "thank you", "ty", "thx", "ok", "okay", "cool", "nice", "awesome", "lol", "lmao",
            "haha", "hahaha", "yes", "no", "yep", "nope", "sure", "fine", "good", "bad", "sad", "bruh", "wsp"
        }
        
        words = clean_content.split()
        
        if not words or (len(words) <= 3 and all(w in casual_greetings_and_replies for w in words)):
            return "NONE"
            
        if len(clean_content) < 15:
            high_triggers = [
                "proof", "paradox", "riddle", "algorithm", "mathematical", "complex", "debug", 
                "architecture", "scale", "optimize", "system design", "write code", "implement",
                "explain how", "solve", "math", "analysis", "deduce"
            ]
            if not any(t in clean_content for t in high_triggers):
                return "NONE"

        high_triggers = [
            "proof", "paradox", "riddle", "algorithm", "mathematical", "complex", "debug", 
            "architecture", "scale", "optimize", "system design", "write code", "implement",
            "explain how", "solve", "math", "analysis", "deduce"
        ]
        if any(t in clean_content for t in high_triggers):
            return "HIGH"
            
        form_triggers = ["[collect:", "[modal_button:", "[select_string:", "[button:", "[user_select:", "[role_select:", "[channel_select:"]
        if any(f in content for f in form_triggers):
            return "MINIMAL"
            
        return "NONE"

    def _sanitize_mime_type_and_data(self, mime_type: str, filename: str, file_bytes: bytes) -> tuple[str, bytes] | tuple[None, None]:
        if not mime_type:
            return None, None
            
        mime_type = mime_type.lower().split(';')[0].strip()
        supported_images = ["image/png", "image/jpeg", "image/jpg", "image/webp", "image/heic"]
        
        if mime_type.startswith("image/"):
            if mime_type in supported_images:
                return mime_type, file_bytes
            return "image/png", file_bytes
            
        if mime_type.startswith("text/") or filename.endswith(('.py', '.json', '.md', '.txt')):
            return "text/plain", file_bytes
            
        return None, None

    async def generate_reply_stream(self, message_content: str, channel_history: str, attachments: list, user_display_name: str, user_memory: dict, server_context: str, scraped_pages: list[str] = None, user_status: str = None, thinking_level: str = "NONE", is_dm: bool = False):
        now_pt = self._check_and_reset_quota()
        
        if self.premium_cooldown_until:
            active_model = self.fallback_model
        else:
            active_model = self.premium_model
            
        use_thinking = (thinking_level in ("HIGH", "MINIMAL"))

        active_sys_prompt = self.system_instruction
        if is_dm:
            active_sys_prompt = re.sub(r'<!-- THREAD_INSTRUCTIONS_START -->.*?<!-- THREAD_INSTRUCTIONS_END -->', '', active_sys_prompt, flags=re.DOTALL)
            active_sys_prompt = active_sys_prompt.replace("deploy a side-thread using the `[THREAD]` tag, and then deliver your code modules **sequentially and modularly**.", "deliver your code modules sequentially in this DM.")
            active_sys_prompt += "\n\nCRITICAL DM RULE: You are currently chatting in Direct Messages (DMs). There are NO threads in DMs. You are STRICTLY FORBIDDEN from using `[THREAD]` tags, attempting to spawn threads, or referencing thread creation. Treat all exploratory requests inline in this DM."

        parts = []
        status_section = f"--- CURRENT STATUS & ACTIVITY FOR {user_display_name} ---\n{user_status}\n\n" if user_status else ""
        scraped_section = "\n\n--- SCRAPED WEBPAGE CONTENT ---\n" + "\n\n---\n\n".join(scraped_pages) if scraped_pages else ""
        
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
            f"Generate ONLY your single response. Do not continue the transcript. Talk directly to {user_display_name}."
        )
        parts.append(context_prompt)

        for attachment in attachments:
            raw_mime = attachment.content_type
            if not raw_mime:
                raw_mime, _ = mimetypes.guess_type(attachment.filename)
            if not raw_mime:
                continue
                
            file_bytes = await self._download_attachment(attachment.url)
            if not file_bytes:
                continue
                
            mime_type, sanitized_bytes = self._sanitize_mime_type_and_data(raw_mime, attachment.filename, file_bytes)
            if mime_type and sanitized_bytes:
                parts.append(types.Part.from_bytes(data=sanitized_bytes, mime_type=mime_type))

        active_tools = [{"code_execution": {}}]
        if self._should_use_search(message_content, channel_history):
            active_tools.append({"google_search": {}})

        if use_thinking:
            config = types.GenerateContentConfig(
                system_instruction=active_sys_prompt,
                tools=active_tools,
                temperature=0.7,
                thinking_config=types.ThinkingConfig(
                    thinking_level=thinking_level,
                    include_thoughts=True
                )
            )
        else:
            config = types.GenerateContentConfig(
                system_instruction=active_sys_prompt,
                tools=active_tools,
                temperature=0.7
            )

        try:
            return await self.client.aio.models.generate_content_stream(
                model=active_model,
                contents=parts,
                config=config
            )
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "exhausted" in error_str or "quota" in error_str:
                if active_model == self.premium_model:
                    logger.warning("Premium model quota exhausted during stream initialization! Locking premium and falling back.")
                    tomorrow_pt = now_pt + timedelta(days=1)
                    self.premium_cooldown_until = datetime.combine(tomorrow_pt.date(), dt_time(0, 0, 0), tzinfo=self.pt_zone)
                    
                    fallback_config = types.GenerateContentConfig(
                        system_instruction=active_sys_prompt, tools=active_tools, temperature=0.7
                    )
                    return await self.client.aio.models.generate_content_stream(
                        model=self.fallback_model, contents=parts, config=fallback_config
                    )
            raise e