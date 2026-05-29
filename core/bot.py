import discord
import io
import os
import re
import logging
import asyncio
import random
import datetime
from datetime import datetime as dt_class, timezone, timedelta
from discord import app_commands
from core.memory import ChatHistoryTracker
import core.memory as memory
from core.chat_handler import ChatHandler
from core.image_gen import ImageGenerator
from core.link_reader import LinkReader
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
        self.link_reader = LinkReader()
        
        self.active_channels = set()
        
        self.last_activity_time = dt_class.now(timezone.utc)
        self.dnd_until = None

        self.rerun_cache = {}
        self.rerun_indexes = {}
        
        self.voice_sessions = {}
        
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.loop.create_task(self._presence_monitor_loop())
        self.loop.create_task(self._spontaneous_checkin_loop())
        
        self.tree.add_command(app_commands.ContextMenu(
            name="Generate Image",
            callback=self.context_generate_image
        ))
        self.tree.add_command(app_commands.ContextMenu(
            name="Re-run",
            callback=self.context_rerun_pagination
        ))
        self.tree.add_command(app_commands.ContextMenu(
            name="Branch",
            callback=self.context_branch
        ))
        self.tree.add_command(app_commands.ContextMenu(
            name="Delete Bot Message",
            callback=self.context_delete
        ))
        self.tree.add_command(app_commands.ContextMenu(
            name="Reset AI Memory",
            callback=self.context_reset_memory
        ))
        
        await self.tree.sync()
        logger.info("Discord application context menus registered and synced globally.")

    def _compile_user_activity(self, member) -> str:
        """Parses a member's active games, custom status text, Spotify listening track, or streams."""
        if not isinstance(member, discord.Member):
            return "No active status (Direct Messages)."
            
        activities = member.activities
        if not activities:
            return "No active status (Offline / Idle / No current activity)."
            
        status_lines = []
        for act in activities:
            if isinstance(act, discord.CustomActivity):
                status_lines.append(f"Custom Status: \"{act.name if act.name else act.state}\"")
            elif isinstance(act, discord.Spotify):
                status_lines.append(f"Listening to Spotify: \"{act.title}\" by {act.artist}")
            elif isinstance(act, discord.Game):
                status_lines.append(f"Playing Game: {act.name}")
            elif isinstance(act, discord.Streaming):
                status_lines.append(f"Streaming on {act.platform if act.platform else 'stream'}: \"{act.name}\"")
            else:
                status_lines.append(f"Activity: {act.name}")
                
        return " | ".join(status_lines)

    async def _monitor_poll_end(self, poll_msg: discord.Message, delay_seconds: int):
        """Async background task that sleeps for a poll's duration and triggers an AI reaction once finished."""
        await asyncio.sleep(delay_seconds)
        try:
            channel = poll_msg.channel
            refetched_msg = await channel.fetch_message(poll_msg.id)
            poll = refetched_msg.poll
            if not poll:
                return
                
            results_summary = []
            for answer in poll.answers:
                results_summary.append(f"- {answer.text}: {answer.vote_count} votes")
                
            results_text = "\n".join(results_summary)
            
            prompt = (
                f"[System Prompt: The native Discord poll you posted \"{poll.question.text}\" has just closed. "
                f"Here are the final compiled results:\n{results_text}\n\n"
                f"Generate a natural, casual, and highly human reaction to this outcome. Celebrate the winners or roast your friends' choices!]"
            )
            
            async with channel.typing():
                history = self.history_tracker.get_formatted_history(channel.id)
                members = [m for m in channel.members if not m.bot] if hasattr(channel, 'members') else []
                target_user = random.choice(members) if members else self.user
                
                memories = await self._compile_memories_for_ai(target_user, channel)
                server_context = self._compile_server_context(channel.guild, target_user) if hasattr(channel, 'guild') else "Environment: Direct Messages."
                
                await self._execute_ai_with_retries(
                    prompt=prompt, 
                    history=history, 
                    attachments=[], 
                    display_name=target_user.display_name, 
                    memory_dict=memories, 
                    context=server_context, 
                    channel=channel, 
                    author=target_user, 
                    is_dm=isinstance(channel, discord.DMChannel), 
                    original_message=refetched_msg,
                    scraped_pages=None,
                    user_status=None
                )
        except Exception as e:
            logger.error(f"Error handling poll results callback: {e}")

    async def context_generate_image(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer(ephemeral=True)

        prompt = message.clean_content.strip()
        if not prompt:
            await interaction.followup.send("Cannot generate an image from empty message text.", ephemeral=True)
            return

        placeholder_msg = await message.reply(content="🎨 *Generating Image...*")
        await interaction.followup.send("🎨 Sparking generation from target message prompt...", ephemeral=True)

        try:
            generated_img_bytes = await self.image_generator.generate(prompt)
            file = discord.File(fp=io.BytesIO(generated_img_bytes), filename="context_image.png")
            
            await placeholder_msg.edit(
                content=f"🎨 *Image Generated from context prompt:* \"{prompt[:120]}...\"",
                attachments=[file]
            )
        except Exception as e:
            logger.error(f"Context menu image generation failed: {e}")
            await placeholder_msg.edit(content=f"❌ *Image generation failed:* {e}")

    async def context_rerun_pagination(self, interaction: discord.Interaction, message: discord.Message):
        await interaction.response.defer(ephemeral=True)

        if message.author.id != self.user.id:
            await interaction.followup.send("I can only generate alternate versions for my own messages.", ephemeral=True)
            return

        original_user_msg = None
        if message.reference:
            resolved = message.reference.resolved
            if isinstance(resolved, discord.Message):
                original_user_msg = resolved
            elif message.reference.message_id:
                try:
                    original_user_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception:
                    pass

        if not original_user_msg:
            try:
                history = [msg async for msg in message.channel.history(limit=5, before=message)]
                for hist_msg in history:
                    if not hist_msg.author.bot:
                        original_user_msg = hist_msg
                        break
            except Exception as hist_err:
                logger.debug(f"Unable to trace timeline for rerun matching: {hist_err}")

        if not original_user_msg:
            await interaction.followup.send("Could not identify the user prompt associated with this message context.", ephemeral=True)
            return

        await interaction.followup.send("🔄 Generating an alternative response option...", ephemeral=True)

        channel = message.channel
        async with channel.typing():
            self.last_activity_time = dt_class.now(timezone.utc)
            history = self.history_tracker.get_formatted_history(channel.id)
            display_name = original_user_msg.author.nick if isinstance(original_user_msg.author, discord.Member) and original_user_msg.author.nick else original_user_msg.author.display_name
            server_context = self._compile_server_context(channel.guild, original_user_msg.author)

            memories_task = self._compile_memories_for_ai(original_user_msg.author, channel)
            urls = self.link_reader.extract_urls(original_user_msg.clean_content)
            url_tasks = [self.link_reader.fetch_and_clean(url) for url in urls[:2]]

            if url_tasks:
                results = await asyncio.gather(memories_task, *url_tasks)
                memories = results[0]
                scraped_pages = results[1:]
            else:
                memories = await memories_task
                scraped_pages = []

            all_attachments = list(original_user_msg.attachments)
            is_dm = isinstance(channel, discord.DMChannel)
            user_status = self._compile_user_activity(original_user_msg.author) if isinstance(original_user_msg.author, discord.Member) else None

            if message.id not in self.rerun_cache:
                self.rerun_cache[message.id] = [
                    {
                        "content": message.content,
                        "attachments": list(message.attachments)
                    }
                ]
                self.rerun_indexes[message.id] = 0

            await self._execute_ai_with_retries(
                prompt=original_user_msg.clean_content,
                history=history,
                attachments=all_attachments,
                display_name=display_name,
                memory_dict=memories,
                context=server_context,
                channel=channel,
                author=original_user_msg.author,
                is_dm=is_dm,
                original_message=original_user_msg,
                scraped_pages=scraped_pages,
                user_status=user_status,
                edit_target=message
            )
            await interaction.followup.send("Alternative version applied successfully!", ephemeral=True)

    async def context_branch(self, interaction: discord.Interaction, message: discord.Message):
        if interaction.guild is None or isinstance(interaction.channel, discord.DMChannel):
            await interaction.response.send_message("The 'Branch' action cannot be used inside Direct Messages.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            thread_name = f"branch-{message.author.display_name[:15].lower()}-{dt_class.now(timezone.utc).strftime('%H%M')}"
            thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
            
            self.active_channels.add(thread.id)
            await interaction.followup.send(f"🌱 Conversational branch created successfully: {thread.mention}", ephemeral=True)

            preceding_history = []
            try:
                async for hist_msg in message.channel.history(limit=10, before=message):
                    preceding_history.append(hist_msg)
            except Exception as err:
                logger.warning(f"Could not scan branch precursor context: {err}")

            preceding_history.reverse()
            preceding_history.append(message)

            formatted_history_lines = []
            for h_msg in preceding_history:
                author_name = h_msg.author.nick if isinstance(h_msg.author, discord.Member) and h_msg.author.nick else h_msg.author.display_name
                formatted_history_lines.append(f"[{h_msg.created_at.strftime('%H:%M:%S')}] {author_name} (@{h_msg.author.name}): {h_msg.clean_content}")

            history_str = "\n".join(formatted_history_lines)

            async with thread.typing():
                self.last_activity_time = dt_class.now(timezone.utc)
                display_name = message.author.nick if isinstance(message.author, discord.Member) and message.author.nick else message.author.display_name
                server_context = self._compile_server_context(interaction.guild, message.author)
                memories = await self._compile_memories_for_ai(message.author, thread)

                branch_prompt = (
                    "[System Prompt: The user has created an isolated conversation branch from this message. "
                    "Analyze the conversation transcript provided and reply naturally to continue the conversation in this thread.]"
                )

                await self._execute_ai_with_retries(
                    prompt=branch_prompt,
                    history=history_str,
                    attachments=[],
                    display_name=display_name,
                    memory_dict=memories,
                    context=server_context,
                    channel=thread,
                    author=message.author,
                    is_dm=False,
                    original_message=None
                )

        except Exception as e:
            logger.error(f"Failed to create conversation branch: {e}")
            await interaction.followup.send(f"Could not construct a branch: {e}", ephemeral=True)

    async def context_delete(self, interaction: discord.Interaction, message: discord.Message):
        if message.author.id != self.user.id:
            await interaction.response.send_message("I can only delete messages sent by me!", ephemeral=True)
            return
            
        try:
            await message.delete()
            await interaction.response.send_message("Message removed successfully.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Could not delete message: {e}", ephemeral=True)

    async def context_reset_memory(self, interaction: discord.Interaction, user: discord.Member):
        await interaction.response.defer(ephemeral=True)
        
        guild = self.get_guild(self.brain_server_id)
        if not guild:
            await interaction.followup.send("Failed to reach brain storage. Verify BRAIN_SERVER_ID is correct.", ephemeral=True)
            return
            
        channel_name = f"{user.name}-memory".lower().replace(" ", "-")
        category = discord.utils.get(guild.categories, name="🧠 User Memories")
        
        if category:
            channel = discord.utils.get(category.text_channels, name=channel_name)
            if channel:
                try:
                    await channel.delete(reason=f"Memory reset initiated by {interaction.user.display_name}")
                    await interaction.followup.send(f"Success! All saved memory metrics for **{user.display_name}** have been wiped.", ephemeral=True)
                    return
                except Exception as e:
                    await interaction.followup.send(f"Failed to delete channel record: {e}", ephemeral=True)
                    return
                    
        await interaction.followup.send(f"No existing long-term memory records found for **{user.display_name}**.", ephemeral=True)

    async def _presence_monitor_loop(self):
        await self.wait_until_ready()
        while not self.is_closed():
            now = dt_class.now(timezone.utc)
            
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

    async def _spontaneous_checkin_loop(self):
        """Silently monitors server activity. If quiet for > 1 hour, there is a 30% chance to spontaneously check in!"""
        await self.wait_until_ready()
        while not self.is_closed():
            await asyncio.sleep(1800)
            
            now = dt_class.now(timezone.utc)
            elapsed_seconds = (now - self.last_activity_time).total_seconds()
            
            if elapsed_seconds >= 3600 and self.active_channels:
                if random.random() < 0.30:
                    target_channel_id = list(self.active_channels)[0]
                    channel = self.get_channel(target_channel_id) or await self.fetch_channel(target_channel_id)
                    if channel:
                        async with channel.typing():
                            members = [m for m in channel.members if not m.bot]
                            target_user = random.choice(members) if members else self.user
                            
                            history = self.history_tracker.get_formatted_history(channel.id)
                            server_context = self._compile_server_context(channel.guild, target_user)
                            memories = await self._compile_memories_for_ai(target_user, channel)
                            
                            spontaneous_prompt = (
                                "[System Prompt: You haven't spoken to your friends in an hour. Spontaneously initiate a conversation. "
                                "Ask a question, share a shower thought, say a meme reference, or check in based on your user memories. "
                                "DO NOT use UI components, button menus, or Google Search. Keep it short and extremely casual.]"
                            )
                            logger.info(f"Triggering spontaneous check-in inside #{channel.name}")
                            await self._execute_ai_with_retries(spontaneous_prompt, history, [], target_user.display_name, memories, server_context, channel, target_user, False, None)

    async def _compile_memories_for_ai(self, author, channel) -> dict:
        """Retrieves user, server, and global memory logs in parallel to optimize lookup times."""
        user_channel = f"{author.name}-memory".lower().replace(" ", "-")
        
        user_task = memory.fetch_memory_block(self, self.brain_server_id, "🧠 User Memories", user_channel)
        
        if channel.guild:
            server_channel = f"{channel.guild.name}-lore".lower().replace(" ", "-")
            server_task = memory.fetch_memory_block(self, self.brain_server_id, "🌍 Server Lore", server_channel)
        else:
            async def get_empty(): return ""
            server_task = get_empty()
            
        global_task = memory.fetch_memory_block(self, self.brain_server_id, "🌐 Global Database", "global-memory")
        
        user_mem, server_mem, global_mem = await asyncio.gather(user_task, server_task, global_task)
        
        return {
            "user_memories": user_mem if user_mem else "No memories saved yet.",
            "server_lore": server_mem if server_mem else "No server lore saved yet.",
            "global_database": global_mem if global_mem else "No global knowledge saved yet."
        }

    async def trigger_ai_reply(self, channel, user: discord.Member):
        async with channel.typing():
            self.last_activity_time = dt_class.now(timezone.utc)
            history = self.history_tracker.get_formatted_history(channel.id)
            server_context = self._compile_server_context(channel.guild, user)
            memories = await self._compile_memories_for_ai(user, channel)
            
            prompt = "[System Prompt: The user just interacted with a UI component. Read the transcript action and respond naturally to their choice.]"
            await self._execute_ai_with_retries(prompt, history, [], user.display_name, memories, server_context, channel, user, False, None)

    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.user.id:
            return
            
        self.last_activity_time = dt_class.now(timezone.utc)
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
        self.last_activity_time = dt_class.now(timezone.utc)
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

        ref_msg = None
        is_reply_to_bot = False
        if message.reference:
            ref_msg = message.reference.resolved
            if not ref_msg and message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                except Exception as e:
                    logger.debug(f"Failed to fetch referenced reply message: {e}")
            
            if isinstance(ref_msg, discord.Message) and ref_msg.author.id == self.user.id:
                is_reply_to_bot = True

        is_mentioned = self.user in message.mentions
        is_watched = message.channel.id in self.active_channels
        is_dm = isinstance(message.channel, discord.DMChannel)

        if is_mentioned or is_reply_to_bot or is_watched or is_dm:
            self.last_activity_time = dt_class.now(timezone.utc)
            async with message.channel.typing():
                history = self.history_tracker.get_formatted_history(message.channel.id)
                display_name = message.author.nick if isinstance(message.author, discord.Member) and message.author.nick else message.author.display_name
                server_context = self._compile_server_context(message.guild, message.author)
                
                memories_task = self._compile_memories_for_ai(message.author, message.channel)
                
                raw_text_to_scan = message.clean_content
                ref_msg_context = ""
                all_attachments = list(message.attachments)
                
                if isinstance(ref_msg, discord.Message):
                    all_attachments.extend(ref_msg.attachments)
                    ref_author = ref_msg.author.nick if isinstance(ref_msg.author, discord.Member) and ref_msg.author.nick else ref_msg.author.display_name
                    ref_msg_context = f"[Replying to {ref_author}: \"{ref_msg.clean_content}\"]\n"
                    raw_text_to_scan += " " + ref_msg.clean_content
                
                urls = self.link_reader.extract_urls(raw_text_to_scan)
                url_tasks = [self.link_reader.fetch_and_clean(url) for url in urls[:2]]
                
                if url_tasks:
                    results = await asyncio.gather(memories_task, *url_tasks)
                    memories = results[0]
                    scraped_pages = results[1:]
                else:
                    memories = await memories_task
                    scraped_pages = []
                
                prompt = f"{ref_msg_context}{message.clean_content}"
                
                user_status = self._compile_user_activity(message.author) if isinstance(message.author, discord.Member) else None
                
                await self._execute_ai_with_retries(
                    prompt=prompt, 
                    history=history, 
                    attachments=all_attachments, 
                    display_name=display_name, 
                    memory_dict=memories, 
                    context=server_context, 
                    channel=message.channel, 
                    author=message.author, 
                    is_dm=is_dm, 
                    original_message=message,
                    scraped_pages=scraped_pages,
                    user_status=user_status
                )

    async def _execute_ai_with_retries(self, prompt, history, attachments, display_name, memory_dict, context, channel, author, is_dm, original_message, scraped_pages=None, user_status=None, edit_target=None):
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = await self.chat_handler.generate_reply(
                    message_content=prompt, channel_history=history, attachments=attachments,
                    user_display_name=display_name, user_memory=memory_dict, server_context=context,
                    scraped_pages=scraped_pages, user_status=user_status
                )
                await self._process_and_send(response, channel, author, is_dm, original_message, edit_target)
                return
                
            except Exception as e:
                error_str = str(e)
                logger.warning(f"Generation Attempt {attempt + 1} Failed: {error_str}")
                
                if "429" in error_str:
                    logger.warning("Rate limit hit! Changing status to DND.")
                    self.dnd_until = dt_class.now(timezone.utc) + timedelta(minutes=5)
                    await self.change_presence(status=discord.Status.dnd)
                
                if attempt == max_retries - 1:
                    excuses = [
                        "wait my discord is lagging so bad rn, hold on",
                        "bruh my internet is literally dying, brb",
                        "sorry my connection is acting up lol, what did u say again?",
                        "hold on my brain is literally lagging rn, brb"
                    ]
                    excuse_msg = random.choice(excuses)
                    
                    if edit_target:
                        await edit_target.reply(excuse_msg)
                    elif original_message:
                        await original_message.reply(excuse_msg)
                    else:
                        await channel.send(excuse_msg)
                    return
                await asyncio.sleep(2)

    def _extract_clean_text_only(self, response) -> str:
        text_parts = []
        if response.candidates:
            for candidate in response.candidates:
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if getattr(part, 'thought', False):
                            continue
                        if getattr(part, 'text', None):
                            text_parts.append(part.text)
                            
        full_text = "".join(text_parts)
        
        full_text = re.sub(r'(?i)<thought>.*?</thought>', '', full_text, flags=re.DOTALL)
        full_text = re.sub(r'(?i)\[thought\].*?\[/thought\]', '', full_text, flags=re.DOTALL)
        full_text = re.sub(r'(?i)^THOUGHT:.*?\n\n', '', full_text, flags=re.DOTALL)
        full_text = re.sub(r'(?i)THOUGHT:.*?(?=\n|$)', '', full_text)
        
        return full_text.strip()

    async def _process_and_send(self, response_obj, channel, author, is_dm: bool, original_message: discord.Message = None, edit_target: discord.Message = None):
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

        poll_data = None
        for match in re.finditer(r'\[POLL:\s*(.+?)\s*\|\s*(.+?)\s*(?:\|\s*(\d+))?\]', response_text):
            question = match.group(1).strip()
            options = [opt.strip() for opt in match.group(2).split(",") if opt.strip()]
            duration_hrs = int(match.group(3)) if match.group(3) else 24
            poll_data = (question, options, duration_hrs)
            response_text = response_text.replace(match.group(0), "")

        response_text = response_text.strip()
        has_ui = len(view.children) > 0
        
        display_text = response_text
        if image_prompts:
            if display_text:
                display_text += "\n\n*(🎨 Generating Image...)*"
            else:
                display_text = "*(🎨 Generating Image...)*"

        if len(display_text) > 2000:
            logger.warning(f"AI response exceeded Discord limit ({len(display_text)} chars). Safely truncating.")
            display_text = display_text[:1990] + "\n..."

        if edit_target and edit_target.id in self.rerun_cache:
            view.add_rerun_pagination(self, edit_target.id)

        sent_msg = None
        if display_text or has_ui:
            if edit_target:
                sent_msg = await edit_target.edit(content=display_text, view=view if has_ui else None, attachments=[])
            else:
                if original_message and (self.user in original_message.mentions or original_message.reference) and target_channel == channel:
                    sent_msg = await original_message.reply(content=display_text, view=view if has_ui else None)
                else:
                    sent_msg = await target_channel.send(content=display_text, view=view if has_ui else None)

        files = []
        generated_img_bytes = None
        if image_prompts:
            for prompt in image_prompts:
                try:
                    generated_img_bytes = await self.image_generator.generate(prompt)
                    files.append(discord.File(fp=io.BytesIO(generated_img_bytes), filename="generated.png"))
                except Exception as img_err:
                    logger.error(f"Image generation failed for prompt '{prompt}': {img_err}")

            final_text = response_text
            if files:
                if final_text:
                    final_text += "\n\n*(🎨 Image Generated)*"
                else:
                    final_text = "*(🎨 Image Generated)*"
            else:
                if final_text:
                    final_text += "\n\n*(❌ Image Generation Failed)*"
                else:
                    final_text = "*(❌ Image Generation Failed)*"

            if sent_msg:
                sent_msg = await sent_msg.edit(content=final_text, attachments=files)

        if edit_target and edit_target.id in self.rerun_cache:
            self.rerun_cache[edit_target.id].append({
                "content": final_text if image_prompts else display_text,
                "attachments": list(sent_msg.attachments) if sent_msg else []
            })
            self.rerun_indexes[edit_target.id] = len(self.rerun_cache[edit_target.id]) - 1

            view = DynamicView(self, channel, search_queries=search_queries, code_blocks=code_blocks)
            view.add_rerun_pagination(self, edit_target.id)
            if sent_msg:
                sent_msg = await sent_msg.edit(view=view)

        if sent_msg and emoji:
            try: await sent_msg.add_reaction(emoji)
            except Exception: pass

        if typo_edit_data and sent_msg:
            self.loop.create_task(self._apply_typo_edit(sent_msg, typo_edit_data))

        if learn_image_prompt and generated_img_bytes:
            await memory.save_image_bytes_fact(self, self.brain_server_id, author, learn_image_prompt, generated_img_bytes, "generated.png")

        if learn_image_prompt and not image_prompts and original_message and original_message.attachments:
            await memory.save_image_fact(self, self.brain_server_id, author, learn_image_prompt, original_message.attachments[0])

        if poll_data and sent_msg:
            question, options, duration_hrs = poll_data
            try:
                import datetime
                poll = discord.Poll(
                    question=question[:300], 
                    duration=datetime.timedelta(hours=duration_hrs)
                )
                for opt in options[:10]:
                    poll.add_answer(text=opt[:55])
                
                poll_msg = await target_channel.send(poll=poll)
                self.loop.create_task(self._monitor_poll_end(poll_msg, duration_hrs * 3600))
            except Exception as e:
                logger.error(f"Failed to deploy native Discord poll: {e}")

        if followups:
            for text in followups:
                await asyncio.sleep(2.0)
                async with target_channel.typing():
                    await asyncio.sleep(1.5)
                    await target_channel.send(content=text)

    async def _apply_typo_edit(self, message: discord.Message, typo_edit_data: tuple[str, str]):
        await asyncio.sleep(2.5)
        try:
            corrected_text = message.content.replace(typo_edit_data[0], typo_edit_data[1])
            await message.edit(content=corrected_text)
        except Exception as e:
            logger.warning(f"Failed to apply typo self-edit correction: {e}")

    def _compile_server_context(self, guild: discord.Guild, member: discord.Member = None) -> str:
        if not guild:
            return "Environment: Direct Messages."
        
        context = f"Current Server: {guild.name}\n"
        
        context += "Available Text Channels to Mention:\n"
        for channel in guild.text_channels:
            context += f"- #{channel.name}: Use mention tag <#{channel.id}>\n"
            
        if member and member.voice and member.voice.channel:
            vc = member.voice.channel
            context += f"\nActive User Voice Status:\n"
            context += f"- User is currently inside Voice Channel: #{vc.name}\n"
            context += f"- To mention this Voice Channel in your reply, use this EXACT click-to-join mention syntax: <#{vc.id}>\n"
        else:
            context += f"\nActive User Voice Status:\n"
            context += f"- User is not currently connected to any Voice Channel in this server.\n"

        context += "\nAvailable Custom Emojis in this Server (You MUST use this EXACT syntax to display them):\n"
        if guild.emojis:
            for emoji in guild.emojis:
                if emoji.animated:
                    syntax = f"<a:{emoji.name}:{emoji.id}>"
                else:
                    syntax = f"<:{emoji.name}:{emoji.id}>"
                context += f"- Name: :{emoji.name}: | Syntax: {syntax}\n"
        else:
            context += "- No custom emojis available in this server.\n"
            
        return context

    def _get_clean_user_id(self, user: discord.User) -> str:
        clean_name = re.sub(r'[^a-zA-Z0-9]', '', user.name).lower()
        return f"{clean_name}-{user.id}"

    def run_bot(self):
        token = os.getenv("DISCORD_TOKEN")
        self.run(token)