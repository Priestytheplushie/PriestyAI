import time
import uuid
import asyncio
import logging
from typing import List, Optional
import discord

import config
from core.normalizer import BidirectionalNormalizer
from ui.thoughts_view import (
    THOUGHT_SESSIONS,
    ThoughtSession,
    PersistentThinkingButtonView
)

logger = logging.getLogger("PriestyAI.Streamer")

class LiveResponseStreamer:
    def __init__(
        self,
        message: discord.Message,
        normalizer: BidirectionalNormalizer,
        initial_status: str = "Analyzing query..."
    ):
        self.message = message
        self.normalizer = normalizer
        self.status_cycle: List[str] = [initial_status]
        self.session_id = str(uuid.uuid4())
        
        self.thought_session = ThoughtSession(session_id=self.session_id)
        THOUGHT_SESSIONS[self.session_id] = self.thought_session

        self.sent_message: Optional[discord.Message] = None
        self.view: Optional[PersistentThinkingButtonView] = None
        self.start_time: float = time.time()
        self.last_edit_time: float = 0.0
        self.last_ephemeral_edit_time: float = 0.0
        self.current_text: str = ""
        
        self.grace_task: Optional[asyncio.Task] = None
        self.status_task: Optional[asyncio.Task] = None
        self.placeholder_sent: bool = False
        self.is_streaming: bool = True

    def set_status_cycle(self, status_cycle: List[str]):
        if status_cycle and isinstance(status_cycle, list):
            self.status_cycle = status_cycle

    async def start_stream(self):
        self.start_time = time.time()
        self.grace_task = asyncio.create_task(self._grace_period_watcher())

    async def _grace_period_watcher(self):
        await asyncio.sleep(0.7)
        if self.is_streaming and not self.current_text and not self.placeholder_sent:
            await self._send_placeholder()

    async def _send_placeholder(self):
        if self.placeholder_sent or self.sent_message:
            return

        self.placeholder_sent = True
        initial_status = self.status_cycle[0] if self.status_cycle else "Synthesizing..."
        content = f"{config.LOADING_EMOJI} *{initial_status}*"

        self.view = PersistentThinkingButtonView(
            session_id=self.session_id,
            initial_label="Thinking for 0.0s"
        )

        try:
            self.sent_message = await self.message.reply(
                content=content,
                view=self.view,
                mention_author=True
            )
            self.status_task = asyncio.create_task(self._cycle_statuses())
        except Exception as e:
            logger.error(f"Failed to post placeholder message: {e}")

    async def _cycle_statuses(self):
        idx = 0
        while self.is_streaming and not self.current_text:
            await asyncio.sleep(1.2)
            if not self.is_streaming or self.current_text or not self.sent_message:
                break

            status = self.status_cycle[idx % len(self.status_cycle)]
            idx += 1
            elapsed = time.time() - self.start_time

            self.thought_session.elapsed_seconds = elapsed
            self.thought_session.is_complete = False
            self.view.set_label(f"Thinking for {elapsed:.1f}s")
            try:
                await self.sent_message.edit(
                    content=f"{config.LOADING_EMOJI} *{status}*",
                    view=self.view
                )
                self.last_edit_time = time.time()
            except Exception as e:
                logger.debug(f"Status cycle edit skipped: {e}")

            await self._update_live_ephemeral(force=False)

    async def push_chunk(self, text_chunk: str, thought_chunk: str = ""):
        if thought_chunk:
            self.thought_session.append_thought(thought_chunk)

        if text_chunk:
            if not self.current_text and self.status_task:
                self.status_task.cancel()
            self.current_text += text_chunk

            now = time.time()
            self.thought_session.elapsed_seconds = now - self.start_time

            if self.sent_message and (now - self.last_edit_time) >= 1.2:
                await self._perform_live_edit()

        await self._update_live_ephemeral(force=False)

    async def _update_live_ephemeral(self, force: bool = False):
        now = time.time()
        if not force and (now - self.last_ephemeral_edit_time) < 1.5:
            return

        if not self.thought_session.active_listeners:
            return

        self.last_ephemeral_edit_time = now
        for inter, view in list(self.thought_session.active_listeners):
            try:
                view.update_content()
                await inter.edit_original_response(content=None, view=view)
            except Exception as e:
                logger.debug(f"Removing inactive ephemeral listener: {e}")
                try:
                    self.thought_session.active_listeners.remove((inter, view))
                except ValueError:
                    pass

    async def _perform_live_edit(self):
        if not self.sent_message or not self.current_text:
            return

        elapsed = time.time() - self.start_time
        has_thoughts = len(self.thought_session.chronological_stream.strip()) > 0 or self.thought_session.tool_count > 0

        view_to_attach = self.view if has_thoughts else None
        if view_to_attach:
            self.view.set_label(f"Thinking for {elapsed:.1f}s")

        preview = self.current_text[:1900]
        try:
            await self.sent_message.edit(
                content=preview,
                view=view_to_attach
            )
            self.last_edit_time = time.time()
        except Exception as e:
            logger.debug(f"Live throttled edit failed: {e}")

    async def finalize(self, model_name: str) -> str:
        self.is_streaming = False
        if self.grace_task and not self.grace_task.done():
            self.grace_task.cancel()
        if self.status_task and not self.status_task.done():
            self.status_task.cancel()

        total_elapsed = time.time() - self.start_time
        has_thoughts = len(self.thought_session.chronological_stream.strip()) > 0 or self.thought_session.tool_count > 0
        
        self.thought_session.elapsed_seconds = total_elapsed
        self.thought_session.is_complete = True

        clean_text = self.normalizer.outbound_normalize(self.current_text, self.message.guild)
        if not clean_text.strip():
            clean_text = "*(Response complete)*"

        chunks = self._split_chunks(clean_text)
        first_chunk = chunks[0] if chunks else clean_text

        final_view: Optional[PersistentThinkingButtonView] = None
        if has_thoughts:
            final_label = f"Thought for {total_elapsed:.1f}s" if total_elapsed < 60 else f"Thought for {int(total_elapsed)}s"
            if not self.view:
                self.view = PersistentThinkingButtonView(
                    session_id=self.session_id,
                    initial_label=final_label
                )
            else:
                self.view.set_label(final_label)
            final_view = self.view

        if not self.sent_message:
            self.sent_message = await self.message.reply(
                content=first_chunk,
                view=final_view,
                mention_author=True
            )
        else:
            try:
                await self.sent_message.edit(
                    content=first_chunk,
                    view=final_view
                )
            except Exception as e:
                logger.error(f"Final edit on message failed: {e}")

        if len(chunks) > 1:
            for extra_chunk in chunks[1:]:
                await self.message.channel.send(extra_chunk)

        await self._update_live_ephemeral(force=True)
        return clean_text

    def _split_chunks(self, text: str, max_chars: int = 1950) -> List[str]:
        if len(text) <= max_chars:
            return [text]

        chunks = []
        lines = text.split("\n")
        current_chunk = ""
        in_codeblock = False
        codeblock_lang = ""

        for line in lines:
            if line.strip().startswith("```"):
                if not in_codeblock:
                    in_codeblock = True
                    codeblock_lang = line.strip()[3:].strip()
                else:
                    in_codeblock = False
                    codeblock_lang = ""

            if len(current_chunk) + len(line) + 1 > max_chars:
                if in_codeblock:
                    current_chunk += "\n```"
                    chunks.append(current_chunk.strip())
                    current_chunk = f"```{codeblock_lang}\n" + line
                else:
                    chunks.append(current_chunk.strip())
                    current_chunk = line
            else:
                current_chunk = f"{current_chunk}\n{line}" if current_chunk else line

        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks