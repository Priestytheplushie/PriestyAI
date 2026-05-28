import discord
import io
import os
import re
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from core.memory import ChatHistoryTracker
import core.memory as memory
from core.chat_handler import ChatHandler
from core.image_gen import ImageGenerator
from core.ui_components import DynamicView

logger = logging.getLogger("DiscordFriend")

class FriendBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(intents=intents)
        
        self.gemini_key = os.getenv("GEMINI_API_KEY")
        self.brain_server_id = int(os.getenv("BRAIN_SERVER_ID", 0))
        self.text_model = os.getenv("GEMINI_MODEL_TEXT", "gemini-2.5-flash")
        
        prompt_path = os.path.join("config", "system_prompt.md")
        
        self.history_tracker = ChatHistoryTracker(limit=25)
        self.chat_handler = ChatHandler(api_key=self.gemini_key, system_prompt_path=prompt_path, model_name=self.text_model)
        self.image_generator = ImageGenerator()
        
        self.active_channels = set()
        
        self.last_activity_time = datetime.now(timezone.utc)
        self.dnd_until = None

    async def setup_hook(self):
        self.loop.create_task(self._presence_monitor_loop())

    async def _presence_monitor_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            now = datetime.now(timezone.utc)
            
            if self.dnd_until and now >= self.dnd_until:
                self.dnd_until = None
                await self.change_presence(status=discord.Status.online)
                logger.info("Rate limit cooled down. Status reset to Online.")
                
            if not self.dnd_until:
                elapsed_seconds = (now - self.last_activity_time).total_seconds()
                if elapsed_seconds >= 300:
                    await self.change_presence(status=discord.Status.idle)
                else:
                    await self.change_presence(status=discord.Status.online)
                    
            await asyncio.sleep(10)

    async def _compile_memories_for_ai(self, author, channel) -> dict:
        """Fetches memory text blocks dynamically from the Brain server on-the-fly."""
        user_channel = f"{author.name}-memory".lower().replace(" ", "-")
        user_mem = await memory.fetch_memory_block(self, self.brain_server_id, "🧠 User Memories", user_channel)
        
        server_mem = ""
        if channel.guild:
            server_channel = f"{channel.guild.name}-lore".lower().replace(" ", "-")
            server_mem = await memory.fetch_memory_block(self, self.brain_server_id, "🌍 Server Lore", server_channel)
            
        global_mem = await memory.fetch_memory_block(self, self.brain_server_id, "🌐 Global Database", "global-memory")
        
        return {
            "user_memories": user_mem if user_mem else "No memories saved yet.",
            "server_lore": server_mem if server_mem else "No server lore saved yet.",
            "global_database": global_mem if global_mem else "No global knowledge saved yet."
        }

    async def trigger_ai_reply(self, channel, user: discord.Member):
        async with channel.typing():
            self.last_activity_time = datetime.now(timezone.utc)
            history = self.history_tracker.get_formatted_history(channel.id)
            server_context = self._compile_server_context(channel.guild)
            memories = await self._compile_memories_for_ai(user, channel)
            
            prompt = "[System Prompt: The user just interacted with a UI component. Read the transcript action and respond naturally to their choice.]"
            await self._execute_ai_with_retries(prompt, history, [], user.display_name, memories, server_context, channel, user, False, None)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.user.id:
            return
            
        self.last_activity_time = datetime.now(timezone.utc)
        channel = self.get_channel(payload.channel_id) or await self.fetch_channel(payload.channel_id)
        if not channel:
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == self.user.id:
                async with channel.typing():
                    user = self.get_user(payload.user_id) or await self.fetch_user(payload.user_id)
                    display_name = payload.member.display_name if payload.member else user.display_name
                    
                    self.history_tracker.add_system_action(
                        channel.id, 
                        f"{display_name} reacted to my message with {payload.emoji.name}"
                    )
                    await self.trigger_ai_reply(channel, user)
        except Exception as e:
            logger.error(f"Error handling reaction: {e}")

    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        self.last_activity_time = datetime.now(timezone.utc)
        channel = self.get_channel(payload.channel_id) or await self.fetch_channel(payload.channel_id)
        if not channel:
            return
            
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.id == self.user.id:
                async with channel.typing():
                    user = self.get_user(payload.user_id) or await self.fetch_user(payload.user_id)
                    self.history_tracker.add_system_action(
                        channel.id, 
                        f"{user.display_name} removed their reaction {payload.emoji.name} from my message"
                    )
                    await self.trigger_ai_reply(channel, user)
        except Exception as e:
            logger.error(f"Error handling reaction removal: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.id == self.user.id:
            self.history_tracker.add_message(message)
            return

        self.history_tracker.add_message(message)

        is_mentioned = self.user in message.mentions
        is_reply_to_bot = False
        if message.reference and message.reference.resolved:
            if isinstance(message.reference.resolved, discord.Message):
                if message.reference.resolved.author.id == self.user.id:
                    is_reply_to_bot = True

        is_watched = message.channel.id in self.active_channels
        is_dm = isinstance(message.channel, discord.DMChannel)

        if is_mentioned or is_reply_to_bot or is_watched or is_dm:
            self.last_activity_time = datetime.now(timezone.utc)
            async with message.channel.typing():
                history = self.history_tracker.get_formatted_history(message.channel.id)
                display_name = message.author.nick if isinstance(message.author, discord.Member) and message.author.nick else message.author.display_name
                server_context = self._compile_server_context(message.guild)
                
                memories = await self._compile_memories_for_ai(message.author, message.channel)
                
                await self._execute_ai_with_retries(message.clean_content, history, message.attachments, display_name, memories, server_context, message.channel, message.author, is_dm, message)

    async def _execute_ai_with_retries(self, prompt, history, attachments, display_name, memory_dict, context, channel, author, is_dm, original_message):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.chat_handler.generate_reply(
                    message_content=prompt, channel_history=history, attachments=attachments,
                    user_display_name=display_name, user_memory=memory_dict, server_context=context
                )
                await self._process_and_send(response, channel, author, is_dm, original_message)
                return
                
            except Exception as e:
                error_str = str(e)
                logger.warning(f"Generation Attempt {attempt + 1} Failed: {error_str}")
                
                if "429" in error_str:
                    logger.warning("Rate limit hit! Changing status to DND.")
                    self.dnd_until = datetime.now(timezone.utc) + timedelta(minutes=5)
                    await self.change_presence(status=discord.Status.dnd)
                
                if attempt == max_retries - 1:
                    err_msg = f"*(My brain has short-circuited: {error_str})*"
                    if original_message:
                        await original_message.reply(err_msg)
                    else:
                        await channel.send(err_msg)
                    return
                await asyncio.sleep(2)

    def _extract_clean_text_only(self, response) -> str:
        text_parts = []
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if getattr(part, 'text', None):
                            text_parts.append(part.text)
        return "".join(text_parts)

    async def _process_and_send(self, response_obj, channel, author, is_dm: bool, original_message: discord.Message = None):
        response_text = self._extract_clean_text_only(response_obj)
        if not response_text and not response_obj.candidates:
            return
            
        target_channel = channel

        search_queries = []
        code_blocks = []
        
        if response_obj.candidates and response_obj.candidates[0].content and response_obj.candidates[0].content.parts:
            for part in response_obj.candidates[0].content.parts:
                if getattr(part, 'executable_code', None):
                    code_blocks.append(part.executable_code.code)
                    
        if response_obj.candidates and response_obj.candidates[0].grounding_metadata:
            meta = response_obj.candidates[0].grounding_metadata
            if getattr(meta, 'web_search_queries', None):
                search_queries.extend(meta.web_search_queries)

        view = DynamicView(self, channel, search_queries=search_queries, code_blocks=code_blocks)

        image_prompts = []
        for match in re.finditer(r'\[IMAGE:\s*(.+?)\]', response_text):
            image_prompts.append(match.group(1).strip())
            response_text = response_text.replace(match.group(0), "")

        emoji = None
        for match in re.finditer(r'\[REACT:\s*(.+?)\]', response_text):
            emoji = match.group(1).strip()
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[REACT_USER:\s*(.+?)\]', response_text):
            user_emoji = match.group(1).strip()
            if original_message:
                try: await original_message.add_reaction(user_emoji)
                except Exception: pass
            response_text = response_text.replace(match.group(0), "")

        typo_edit_data = None
        for match in re.finditer(r'\[TYPO_EDIT:\s*(.+?)\s*\|\s*(.+?)\]', response_text):
            typo_word, corrected_word = match.groups()
            typo_edit_data = (typo_word.strip(), corrected_word.strip())
            response_text = response_text.replace(match.group(0), "")

        followups = []
        if "[FOLLOW_UP]" in response_text:
            parts = response_text.split("[FOLLOW_UP]")
            response_text = parts[0].strip()
            for p in parts[1:]:
                if p.strip():
                    followups.append(p.strip())

        for match in re.finditer(r'\[LEARN:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            await memory.save_fact(self, self.brain_server_id, author, fact)
            response_text = response_text.replace(match.group(0), "")

        learn_image_prompt = None
        for match in re.finditer(r'\[LEARN_IMAGE:\s*(.+?)\]', response_text):
            learn_image_prompt = match.group(1).strip()
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[FORGET:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            channel_name = f"{author.name}-memory".lower().replace(" ", "-")
            await memory.forget_fact(self, self.brain_server_id, "🧠 User Memories", channel_name, fact)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[LEARN_SERVER:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            if channel.guild:
                await memory.save_server_fact(self, self.brain_server_id, channel.guild, fact)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[FORGET_SERVER:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            if channel.guild:
                channel_name = f"{channel.guild.name}-lore".lower().replace(" ", "-")
                await memory.forget_fact(self, self.brain_server_id, "🌍 Server Lore", channel_name, fact)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[LEARN_GLOBAL:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            await memory.save_global_fact(self, self.brain_server_id, fact)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[FORGET_GLOBAL:\s*(.+?)\]', response_text):
            fact = match.group(1).strip()
            await memory.forget_fact(self, self.brain_server_id, "🌐 Global Database", "global-memory", fact)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[THREAD:\s*(.+?)\]', response_text):
            title = match.group(1).strip()
            if not is_dm and isinstance(channel, discord.TextChannel):
                thread = await channel.create_thread(name=title, auto_archive_duration=1440)
                self.active_channels.add(thread.id)
                target_channel = thread
                response_text = f"Hey <@{author.id}>, let's talk here!\n" + response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[CLOSE_THREAD\]', response_text):
            if isinstance(channel, discord.Thread):
                await channel.edit(archived=True, locked=True)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[BUTTON:\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*(.+?))?\]', response_text):
            l, c, e = match.group(1), match.group(2), match.group(3)
            view.add_dynamic_button(l.strip(), c.strip(), e.strip() if e else None)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[SELECT_STRING:\s*(.+?)\s*\|\s*(.+?)\]', response_text):
            p, o = match.groups()
            options = []
            for opt in o.split(","):
                parts = opt.split(":")
                lbl = parts[0].strip()
                desc = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
                emj = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
                options.append((lbl, desc, emj))
            view.add_dropdown(p.strip(), options)
            response_text = response_text.replace(match.group(0), "")

        for match in re.finditer(r'\[MODAL_BUTTON:\s*(.+?)\s*\|\s*(.+?)\]', response_text):
            b, f = match.groups()
            fields = []
            for f_item in f.split(","):
                f_parts = f_item.split(":")
                f_name = f_parts[0].strip()
                f_style = f_parts[1].strip() if len(f_parts) > 1 else "long"
                fields.append((f_name, f_style))
            view.add_modal_trigger_button(b.strip(), fields)
            response_text = response_text.replace(match.group(0), "")

        response_text = response_text.strip()
        has_ui = len(view.children) > 0
        
        display_text = response_text
        if image_prompts:
            display_text += "\n\n*(🎨 Generating image...)*"

        sent_msg = None
        if display_text or has_ui:
            if original_message and (self.user in original_message.mentions or original_message.reference) and target_channel == channel:
                sent_msg = await original_message.reply(content=display_text, view=view if has_ui else None)
            else:
                sent_msg = await target_channel.send(content=display_text, view=view if has_ui else None)

        if sent_msg and emoji:
            try: await sent_msg.add_reaction(emoji)
            except Exception: pass

        if typo_edit_data and sent_msg:
            await asyncio.sleep(2.5)
            corrected_text = sent_msg.content.replace(typo_edit_data[0], typo_edit_data[1])
            await sent_msg.edit(content=corrected_text)

        if image_prompts and sent_msg:
            files = []
            generated_img_bytes = None
            for prompt in image_prompts:
                generated_img_bytes = await self.image_generator.generate(prompt)
                files.append(discord.File(fp=io.BytesIO(generated_img_bytes), filename="generated.png"))
            await sent_msg.edit(content=response_text, attachments=files)
            
            if learn_image_prompt and generated_img_bytes:
                await memory.save_image_bytes_fact(self, self.brain_server_id, author, learn_image_prompt, generated_img_bytes, "generated.png")

        if learn_image_prompt and not image_prompts and original_message and original_message.attachments:
            await memory.save_image_fact(self, self.brain_server_id, author, learn_image_prompt, original_message.attachments[0])

        if followups:
            for text in followups:
                await asyncio.sleep(2.0)
                async with target_channel.typing():
                    await asyncio.sleep(1.5)
                    await target_channel.send(content=text)

    def _compile_server_context(self, guild: discord.Guild) -> str:
        if not guild:
            return "Environment: Direct Messages."
        context = f"Current Server: {guild.name}\nAvailable Text Channels to Mention:\n"
        for channel in guild.text_channels:
            context += f"- #{channel.name}: Use mention tag <#{channel.id}>\n"
        return context

    def _get_clean_user_id(self, user: discord.User) -> str:
        clean_name = re.sub(r'[^a-zA-Z0-9]', '', user.name).lower()
        return f"{clean_name}-{user.id}"

    def run_bot(self):
        token = os.getenv("DISCORD_TOKEN")
        self.run(token)