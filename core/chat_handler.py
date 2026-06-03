
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
                if self.premium_model == self.premium_model:
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
        
        if message_content.startswith("[System") or "system prompt" in content:
            return "HIGH"

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
            
        form_triggers = ["[collect:", "[modal_button:", "[select_string:", "[button:", "[user_select:", "[role_select:", "[channel_select:", "[mentionable_select:"]
        if any(f in content for f in form_triggers):
            return "MINIMAL"
            
        return "NONE"

    def _sanitize_mime_type_and_data(self, mime_type: str, filename: str, file_bytes: bytes) -> tuple[str, bytes] | tuple[None, None]:
        if not mime_type:
            return None, None
            
        mime_type = mime_type.lower().split(';')[0].strip()
        
        supported_images = ["image/png", "image/jpeg", "image/jpg", "image/webp", "image/heic", "image/heif"]
        if mime_type in supported_images or (mime_type.startswith("image/") and mime_type != "image/gif"):
            return mime_type if mime_type in supported_images else "image/png", file_bytes
        
        supported_audio = ["audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac", "audio/mpeg", "audio/x-m4a"]
        if mime_type in supported_audio or mime_type.startswith("audio/"):
            return mime_type if mime_type in supported_audio else "audio/wav", file_bytes
            
        supported_video = ["video/mp4", "video/mpeg", "video/mov", "video/avi", "video/flv", "video/mpg", "video/webm", "video/wmv", "video/3gpp", "video/quicktime"]
        if mime_type in supported_video or mime_type.startswith("video/"):
            return mime_type if mime_type in supported_video else "video/mp4", file_bytes
            
        if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
            return "application/pdf", file_bytes

        text_extensions = (
            '.py', '.json', '.md', '.txt', '.js', '.ts', '.sh', '.css', '.html', 
            '.xml', '.yaml', '.yml', '.c', '.cpp', '.h', '.java', '.go', '.rs', 
            '.sql', '.bat', '.ps1', '.ini', '.conf', '.env', '.log'
        )
        if mime_type.startswith("text/") or filename.lower().endswith(text_extensions):
            return "text/plain", file_bytes

        if filename.lower().endswith(".docx"):
            try:
                import docx
                doc = docx.Document(io.BytesIO(file_bytes))
                text = "\n".join([p.text for p in doc.paragraphs])
                return "text/plain", text.encode('utf-8')
            except Exception as e:
                logger.warning(f"Docx extraction failed for {filename}: {e}")

        if filename.lower().endswith(".xlsx"):
            try:
                import openpyxl
                wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
                lines = []
                for sheet in wb.worksheets:
                    lines.append(f"--- Sheet: {sheet.title} ---")
                    for row in sheet.iter_rows(values_only=True):
                        lines.append("\t".join([str(c) if c is not None else "" for c in row]))
                return "text/plain", "\n".join(lines).encode('utf-8')
            except Exception as e:
                logger.warning(f"Xlsx extraction failed for {filename}: {e}")
            
        return None, None

    def build_tool_definition(self, config: dict) -> str:
        sys_tools = config.get("system_tools", [])
        disc_tools = config.get("discord_tools", [])
        
        lines = ["\n\n=== GRANTED TOOLS & CAPABILITIES ==="]
        lines.append("You have access to the following tools. Use them by outputting the exact tag anywhere in your message.")
        
        if "Generate Images" in sys_tools:
            lines.append("- [IMAGE_PENDING: prompt] : Spawns a new image. Use for drawing, painting, or rendering.")
            lines.append("- [IMAGE_EDIT: instructions] : Edits/modifies an existing base image (img2img).")
        if "Memory Journals" in sys_tools:
            lines.append("- [LEARN: fact] : Saves a fact about the user.")
            lines.append("- [LEARN_SERVER: fact] : Saves a fact about the server.")
            lines.append("- [LEARN_GLOBAL: fact] : Saves a universal fact.")
            lines.append("- [FORGET: fact] : Deletes a saved fact from memory.")
        
        if "Buttons" in disc_tools:
            lines.append("- [BUTTON: Label | color | emoji] : Spawns interactive buttons (colors: primary, secondary, success, danger).")
        if "Modals" in disc_tools:
            lines.append("- [MODAL_BUTTON: Label | Field1:style1:field_description, Field2:style2:field_description] : Spawns an interactive popup form. Styles: short, long, user_select, role_select, channel_select, mentionable_select, or select_string(Choice1:choice_desc:emoji, Choice2:choice_desc:emoji). Both outer field descriptions and inner choice descriptions are supported.")
        if "Threads" in disc_tools:
            lines.append("- [THREAD: Thread Name] : Creates a side-thread for deep-dives.")
            lines.append("- [CLOSE_THREAD] : Archives the current thread.")
        if "Entity Dropdowns" in disc_tools:
            lines.append("- [USER_SELECT: Prompt text] : Renders a user dropdown inside the channel.")
            lines.append("- [CHANNEL_SELECT: Prompt text] : Renders a channel dropdown.")
            lines.append("- [ROLE_SELECT: Prompt text] : Renders a role dropdown.")
            lines.append("- [MENTIONABLE_SELECT: Prompt text] : Renders a dropdown allowing users to select either a user or a role.")
        if "Custom Dropdowns" in disc_tools:
            lines.append("- [SELECT_STRING: Placeholder | Opt1:description:emoji, Opt2:description:emoji] : Renders a dropdown select menu with custom choices. Option descriptions and emojis are optional.")
        if "Double-Texting" in disc_tools:
            lines.append("- [FOLLOW_UP] : Instantly splits your response into a second consecutive message. Use sparingly.")
        if "Reactions" in disc_tools:
            lines.append("- [REACT: emoji] : Adds an emoji directly to your own message.")
            lines.append("- [REACT_USER: emoji] : Adds an immediate reaction to the user's incoming message.")
        if "Native Polls" in disc_tools:
            lines.append("- [POLL: Question | Opt1, Opt2, Opt3 | Hours] : Launches a Discord vote poll.")

        if "Message Builder" in sys_tools:
            lines.append("\n- [BUILD_MESSAGE: python_dsl_code] : Spawns an interactive custom Discord message containing Components V2 (modern borderless containers, column-structured sections, text displays, visual dividers, and interactive dropdowns/buttons).")
            lines.append("  You MUST write valid Python DSL layout constructors inside the tag. Let the layout compiled output represent the UI.")
            lines.append("  ")
            lines.append("  CRITICAL ROUTING RULE (MANDATORY): Do NOT use [BUILD_MESSAGE] for simple tasks (like presenting a few standalone buttons, quick dropdowns, or single text modal triggers). Instead, utilize the lightweight legacy tools (such as [BUTTON], [SELECT_STRING], or [MODAL_BUTTON]) to save space and eliminate the decoupled compilation latency. Only choose [BUILD_MESSAGE] when you are constructing a cohesive multi-element visual card layout containing nested containers, multi-column directory sections, visual separators, and complex data collection forms.")
            lines.append("  ")
            lines.append("  Example call: [BUILD_MESSAGE: Container(Section(TextDisplay(\"Alert Heading\"), accessory=Button(\"Acknowledge\", style=\"success\", on_click=Action.delete_message())), accent_colour=\"0xff0000\")]")
            lines.append("  Available classes to instantiate:")
            lines.append("    * Container(*children, accent_colour=None) : Groups layout items inside a clean bounded card. If accent_colour is omitted, the container integrates seamlessly with zero border lines.")
            lines.append("    * Section(*children, accessory) : Dual column row layout. Houses 1-3 TextDisplays on the left, paired with a single accessory (a Button) on the right column. The 'accessory' parameter is strictly mandatory.")
            lines.append("    * TextDisplay(content) : Rich formatted inline text slot.")
            lines.append("    * Separator(spacing=\"small\", visible=True) : Visual divider line. spacing: \"small\" or \"large\".")
            lines.append("    * ActionRow(*children) : Grid layout row aligning up to 5 Buttons OR exactly 1 dropdown. Do NOT exceed bounds or mix buttons and dropdowns inside a single row.")
            lines.append("    * UserSelect(placeholder, min_values=1, max_values=25, on_select=None) : Dropdown listing server members.")
            lines.append("    * RoleSelect(placeholder, min_values=1, max_values=25, on_select=None) : Dropdown listing server roles.")
            lines.append("    * ChannelSelect(placeholder, min_values=1, max_values=25, on_select=None) : Dropdown listing server channels.")
            lines.append("    * StringSelect(placeholder, options, min_values=1, max_values=25, on_select=None) : Custom Choice menu. 'options' is a list of SelectOption.")
            lines.append("    * SelectOption(label, value, description=None, emoji=None) : Static text choices inside StringSelect.")
            lines.append("    * Button(label, style=\"secondary\", url=None, on_click=None, id=None) : Interactive button. styles: \"primary\", \"secondary\", \"success\", \"danger\", \"link\".")
            lines.append("    * Modal(title, *children, on_submit=None) : Data form pop-up modal (max 45 chars title). children fields: Label, CheckboxGroup, RadioGroup, FileUpload.")
            lines.append("    * Label(text, component, description=None) : Form label pairing.")
            lines.append("    * CheckboxGroup(options) : Multi-choice selection toggles inside a modal.")
            lines.append("    * RadioGroup(options) : Single-choice toggle list inside a modal.")
            lines.append("    * FileUpload(min_values=1, max_values=1) : Interactive file upload slot inside a modal form.")
            lines.append("  Available callback actions (bind to on_click, on_select, on_submit events):")
            lines.append("    * Action.trigger_ai(instruction_payload) : Triggers a background model thinking cycle to perform updates or transition states dynamically.")
            lines.append("    * Action.trigger_image_generation(prompt) : Instantly routes the user's prompt to the visual generation pipeline and spawns an image.")
            lines.append("    * Action.reply_private(text_content) : Fast, private response back to the user (ephemeral, zero latency). Supports variable replacement '{value}'.")
            lines.append("    * Action.reply_public(text_content) : Public followup message sent in the text channel.")
            lines.append("    * Action.delete_message() : Instantly removes the interactive layout message.")
            lines.append("    * Action.disable_components() : Disables all dropdowns and buttons inside the parent message view.")
            lines.append("    * Action.pass_input() : Saves selection quietly to state memory and confirms ephemerally with zero AI latency.")
            lines.append("    * Action.open_modal(modal) : Opens a Modals V2 pop-up form. CRITICAL LIMITATION: Modals cannot be combined in lists with other actions or launched from other modal submits.")
            lines.append("  ")
            lines.append("  CONVERSATIONAL LIFECYCLE RULE (MANDATORY):")
            lines.append("  If you choose to invoke [BUILD_MESSAGE], you MUST format your response strictly as follows:")
            lines.append("  1. Complete brainstorming inside `<thought>` and `</thought>` tags.")
            lines.append("  2. Output your [BUILD_MESSAGE: python_dsl_code] tag.")
            lines.append("  3. Write a single, brief, casual conversational banter sentence in lowercase at the very end of your response.")
            lines.append("  This ensures a natural visual flow where the banter and UI are cleanly split.")
            
        return "\n".join(lines)

    async def generate_reply_stream(self, message_content: str, channel_history: str, attachments: list, user_display_name: str, user_memory: dict, server_context: str, scraped_pages: list[str] = None, user_status=None, thinking_level="NONE", is_dm=False, active_config=None):
        now_pt = self._check_and_reset_quota()
        
        if self.premium_cooldown_until:
            active_model = self.fallback_model
        else:
            active_model = self.premium_model

        if active_config is None: active_config = {}

        custom_prompt = active_config.get("system_prompt", "").strip()
        base_sys_prompt = custom_prompt if custom_prompt else self.system_instruction

        if is_dm:
            base_sys_prompt = re.sub(r'<!-- THREAD_INSTRUCTIONS_START -->.*?<!-- THREAD_INSTRUCTIONS_END -->', '', base_sys_prompt, flags=re.DOTALL)
            base_sys_prompt = base_sys_prompt.replace("deploy a side-thread using the `[THREAD]` tag, and then deliver your code modules **sequentially and modularly**.", "deliver your code modules sequentially in this DM.")
            base_sys_prompt += "\n\nCRITICAL DM RULE: You are currently chatting in Direct Messages (DMs). There are NO threads in DMs. You are STRICTLY FORBIDDEN from using `[THREAD]` tags, attempting to spawn threads, or referencing thread creation. Treat all exploratory requests inline in this DM."
            base_sys_prompt += "\n\nCRITICAL DM RULE 2: Since you are currently chatting in Direct Messages (DMs), there are no server-side roles, channels, or mentionables. You are STRICTLY FORBIDDEN from instantiating or using RoleSelect, ChannelSelect, or MentionableSelect components inside your visual layouts or modal popups."

        tool_mode = active_config.get("tool_mode", "Auto")
        disc_tools = active_config.get("discord_tools", [])
        
        if tool_mode != "Off":
            tool_definition_block = self.build_tool_definition(active_config)
            if "{TOOL_DEFINITION}" in base_sys_prompt:
                base_sys_prompt = base_sys_prompt.replace("{TOOL_DEFINITION}", tool_definition_block)
            else:
                base_sys_prompt += tool_definition_block
                
            if tool_mode == "Forced":
                base_sys_prompt += "\n\nCRITICAL SYSTEM OVERRIDE: You MUST use an available tool tag or API tool to answer this prompt."
        else:
            if "{TOOL_DEFINITION}" in base_sys_prompt:
                base_sys_prompt = base_sys_prompt.replace("{TOOL_DEFINITION}", "")

        if "Server Emojis" not in disc_tools:
            base_sys_prompt += "\n\nCRITICAL EMOJI RESTRICTION: You are STRICTLY FORBIDDEN from generating or writing any custom server emojis (do not use the <:name:id> or <a:name:id> format). Write using standard text or unicode emojis only."
        if "Unicode Emojis" not in disc_tools:
            base_sys_prompt += "\n\nCRITICAL EMOJI RESTRICTION: You are STRICTLY FORBIDDEN from outputting standard unicode emojis (e.g. 🙂, 🔥, 😂, 👀, 💀) in your messages. Express yourself purely through text."

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

        active_tools = []
        if tool_mode != "Off":
            if "Code Execution" in active_config.get("system_tools", []):
                active_tools.append({"code_execution": {}})
            if "Google Search" in active_config.get("system_tools", []):
                if tool_mode == "Forced" or self._should_use_search(message_content, channel_history):
                    active_tools.append({"google_search": {}})

        thinking_level_for_stream = thinking_level if thinking_level in ("HIGH", "MINIMAL") else "MINIMAL"
        
        config = types.GenerateContentConfig(
            system_instruction=base_sys_prompt,
            tools=active_tools if active_tools else None,
            temperature=0.7,
            thinking_config=types.ThinkingConfig(
                thinking_level=thinking_level_for_stream,
                include_thoughts=True
            )
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
                        system_instruction=base_sys_prompt, tools=active_tools if active_tools else None, temperature=0.7
                    )
                    return await self.client.aio.models.generate_content_stream(
                        model=self.fallback_model, contents=parts, config=fallback_config
                    )
            raise e