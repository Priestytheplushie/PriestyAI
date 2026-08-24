import io
import re
import asyncio
import logging
import discord
from config.settings import STREAM_DEBOUNCE_INTERVAL, MAX_MESSAGE_CHUNK_SIZE
from parsers.mention_parser import parse_mentions
from parsers.timestamp_parser import parse_timestamps
from parsers.emoji_parser import parse_emojis
from parsers.math_parser import sanitize_latex

logger = logging.getLogger("PriestyAI.StreamHandler")

def clean_discord_markdown(text: str) -> str:
    text = re.sub(r'(?m)^(\s*[-*_]\s*){3,}\s*$', '', text)
    text = re.sub(r'(?m)^#{4,}\s+', '### ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def apply_message_parsers(text: str, guild: discord.Guild | None) -> str:
    text = clean_discord_markdown(text)
    text = sanitize_latex(text)
    text = parse_mentions(text, guild)
    text = parse_timestamps(text)
    text = parse_emojis(text, guild)
    return text

def split_markdown_message(text: str, max_limit: int = MAX_MESSAGE_CHUNK_SIZE) -> list[str]:
    if len(text) <= max_limit:
        return [text]

    chunks = []
    current_text = text
    active_code_lang = None

    while current_text:
        if active_code_lang:
            prefix = f"```{active_code_lang}\n"
            if not current_text.startswith("```"):
                current_text = prefix + current_text

        if len(current_text) <= max_limit:
            chunks.append(current_text)
            break

        split_idx = current_text.rfind("\n\n", 0, max_limit)
        if split_idx == -1:
            split_idx = current_text.rfind("\n", 0, max_limit)
        if split_idx == -1:
            split_idx = current_text.rfind(". ", 0, max_limit)
        if split_idx == -1 or split_idx < 600:
            split_idx = max_limit

        chunk = current_text[:split_idx].rstrip()
        remainder = current_text[split_idx:].lstrip("\r\n")

        fences = re.findall(r"```([a-zA-Z0-9_+-]*)", chunk)
        if len(fences) % 2 != 0:
            last_lang = fences[-1] or (active_code_lang or "")
            chunk += "\n```"
            active_code_lang = last_lang
        else:
            active_code_lang = None

        chunks.append(chunk)
        current_text = remainder

    return chunks

def merge_views(view_a: discord.ui.View | None, view_b: discord.ui.View | None) -> discord.ui.View | None:
    if not view_a and not view_b:
        return None
    if not view_a:
        return view_b
    if not view_b:
        return view_a

    merged = discord.ui.View(timeout=900)
    for item in view_a.children:
        merged.add_item(item)
    for item in view_b.children:
        merged.add_item(item)
    return merged

class DiscordStreamDispatcher:
    def __init__(
        self,
        origin_message: discord.Message | None = None,
        guild: discord.Guild | None = None,
        existing_response_msg: discord.Message | None = None,
        interaction: discord.Interaction | None = None,
        is_ephemeral: bool = False
    ):
        self.origin_message = origin_message
        self.guild = guild
        self.primary_message = existing_response_msg
        self.interaction = interaction
        self.is_ephemeral = is_ephemeral
        
        self.sent_messages: list[discord.Message] = [existing_response_msg] if existing_response_msg else []
        self.interaction_overflow_count = 1 if interaction else 0
        
        self.buffer = ""
        self.last_edit_time = 0.0
        self.is_flushing = False

    def apply_all_parsers(self, text: str) -> str:
        return apply_message_parsers(text, self.guild)

    async def append_text(self, delta: str, view: discord.ui.View | None = None):
        self.buffer += delta
        now = asyncio.get_event_loop().time()
        if (now - self.last_edit_time) >= STREAM_DEBOUNCE_INTERVAL:
            await self.flush(view=view)

    async def flush(
        self,
        view: discord.ui.View | None = None,
        file: discord.File | None = None
    ):
        if self.is_flushing or not self.buffer.strip():
            return
        self.is_flushing = True
        try:
            parsed_text = self.apply_all_parsers(self.buffer)
            chunks = split_markdown_message(parsed_text)

            for i, chunk in enumerate(chunks):
                is_last_chunk = (i == len(chunks) - 1)
                chunk_view = view if is_last_chunk else None
                chunk_attachments = [file] if (is_last_chunk and file is not None) else discord.utils.MISSING

                if self.interaction:
                    if i == 0:
                        if chunk_attachments is not discord.utils.MISSING:
                            await self.interaction.edit_original_response(content=chunk, view=chunk_view, attachments=chunk_attachments)
                        else:
                            await self.interaction.edit_original_response(content=chunk, view=chunk_view)
                    else:
                        if i >= self.interaction_overflow_count:
                            if chunk_attachments is not discord.utils.MISSING:
                                await self.interaction.followup.send(content=chunk, view=chunk_view, file=file, ephemeral=self.is_ephemeral)
                            else:
                                await self.interaction.followup.send(content=chunk, view=chunk_view, ephemeral=self.is_ephemeral)
                            self.interaction_overflow_count += 1
                else:
                    if i < len(self.sent_messages):
                        msg = self.sent_messages[i]
                        if msg.content != chunk or chunk_view is not None or file is not None:
                            if chunk_attachments is not discord.utils.MISSING:
                                await msg.edit(content=chunk, view=chunk_view, attachments=chunk_attachments)
                            else:
                                await msg.edit(content=chunk, view=chunk_view)
                    else:
                        if not self.sent_messages and self.origin_message:
                            try:
                                if chunk_attachments is not discord.utils.MISSING:
                                    new_msg = await self.origin_message.reply(content=chunk, view=chunk_view, file=file, mention_author=False)
                                else:
                                    new_msg = await self.origin_message.reply(content=chunk, view=chunk_view, mention_author=False)
                            except discord.HTTPException:
                                if chunk_attachments is not discord.utils.MISSING:
                                    new_msg = await self.origin_message.channel.send(content=chunk, view=chunk_view, file=file)
                                else:
                                    new_msg = await self.origin_message.channel.send(content=chunk, view=chunk_view)
                        else:
                            channel = self.origin_message.channel if self.origin_message else self.primary_message.channel
                            if chunk_attachments is not discord.utils.MISSING:
                                new_msg = await channel.send(content=chunk, view=chunk_view, file=file)
                            else:
                                new_msg = await channel.send(content=chunk, view=chunk_view)

                        self.sent_messages.append(new_msg)
                        if not self.primary_message:
                            self.primary_message = new_msg

            self.last_edit_time = asyncio.get_event_loop().time()
        except discord.DiscordServerError:
            pass
        except Exception as e:
            logger.warning(f"Error during stream flush: {e}")
        finally:
            self.is_flushing = False

    async def finalize(
        self,
        view: discord.ui.View | None = None,
        file: discord.File | None = None
    ):
        await self.flush(view=view, file=file)