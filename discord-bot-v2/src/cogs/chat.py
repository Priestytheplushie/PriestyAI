import io
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional
import discord
from discord.ext import commands
from google.genai import types

from src.core.bot import PriestyBot
from src.ai.router import ModelRouter, RouteDecision
from src.ai.gemini_client import GeminiEngine
from src.ai.prompts import PromptBuilder
from src.database.memory_manager import memory_manager
from src.tools.manager import ToolManager, ToolContext
from src.utils.sanitizer import Sanitizer
from src.utils.message_splitter import MessageSplitter
from src.utils.timestamps import TimestampParser
from src.utils.media import MediaProcessor
from src.ui.views import ThinkingTraceView

logger = logging.getLogger("PriestyAI.ChatCog")

LOADING_EMOTE = "<a:loading:1540750535093919906>"

class ChatCog(commands.Cog):
    def __init__(self, bot: PriestyBot):
        self.bot = bot
        self.router = ModelRouter(bot.key_rotator)
        self.gemini_engine = GeminiEngine(bot.key_rotator)

    async def _should_respond(self, message: discord.Message) -> bool:
        if message.author.bot or not self.bot.user:
            return False

        if self.bot.user in message.mentions:
            return True

        if message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg.author.id == self.bot.user.id:
                    return True
            except Exception:
                pass

        if isinstance(message.channel, discord.DMChannel):
            return True

        watched = await memory_manager.get_active_watched_channels()
        watched_channel_ids = [w["channel_id"] for w in watched]
        if str(message.channel.id) in watched_channel_ids:
            return True

        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not await self._should_respond(message):
            return

        asyncio.create_task(self.handle_chat_pipeline(message))

    async def handle_chat_pipeline(self, message: discord.Message) -> None:
        channel = message.channel
        guild = message.guild
        author = message.author

        cleaned_prompt = Sanitizer.clean_incoming_content(message, self.bot.user)

        media_parts: List[types.Part] = []
        for attachment in message.attachments:
            part = await MediaProcessor.attachment_to_part(attachment)
            if part:
                media_parts.append(part)

        if not cleaned_prompt and not media_parts:
            return

        channel_history = await memory_manager.get_channel_history(str(channel.id), limit=10)
        user_memories = await memory_manager.get_user_memories(str(author.id), limit=5)
        server_lore = await memory_manager.get_server_lore(str(guild.id), limit=6) if guild else []
        server_vibe = await memory_manager.get_server_vibe(str(guild.id)) if guild else None

        channel_topic = getattr(channel, "topic", None)
        system_instruction = PromptBuilder.build_system_prompt(
            current_speaker=author.display_name,
            guild_name=guild.name if guild else None,
            channel_name=channel.name if hasattr(channel, "name") else "dm",
            channel_topic=channel_topic,
            server_vibe=server_vibe,
            server_lore=server_lore,
            user_memories=user_memories
        )

        route: RouteDecision = await self.router.route(cleaned_prompt or "Describe this attachment.")

        formatted_history: List[Dict[str, Any]] = []
        for h in channel_history:
            if h["role"] == "user":
                formatted_history.append({
                    "role": "user",
                    "content": f"[{h['author_name']}]: {h['content']}"
                })
            else:
                formatted_history.append({
                    "role": "assistant",
                    "content": h["content"]
                })

        formatted_history.append({
            "role": "user",
            "content": f"[{author.display_name}]: {cleaned_prompt if cleaned_prompt else 'Attached media file.'}"
        })

        placeholder_msg: Optional[discord.Message] = None
        status_task: Optional[asyncio.Task] = None
        needs_heavy_thinking = (route.thinking_level in ("medium", "high"))

        if needs_heavy_thinking:
            try:
                placeholder_msg = await channel.send(f"{LOADING_EMOTE} PriestyAI is thinking...")
            except Exception as e:
                logger.error(f"Failed to send placeholder message: {e}")

            thinking_steps = [
                f"{LOADING_EMOTE} PriestyAI is analyzing the request...",
                f"{LOADING_EMOTE} PriestyAI is processing data...",
                f"{LOADING_EMOTE} PriestyAI is formatting the answer..."
            ]

            async def animate_status():
                step_idx = 0
                while True:
                    await asyncio.sleep(2.5)
                    if placeholder_msg:
                        try:
                            await placeholder_msg.edit(content=thinking_steps[step_idx % len(thinking_steps)])
                            step_idx += 1
                        except Exception:
                            break

            if placeholder_msg:
                status_task = asyncio.create_task(animate_status())

        tools = ToolManager.get_tool_declarations()
        context = ToolContext(guild=guild, channel=channel, author=author, message=message)

        attachment_files: List[discord.File] = []
        dynamic_view: Optional[discord.ui.View] = None

        async def execute_tool_wrapper(func_call: types.FunctionCall) -> Dict[str, Any]:
            nonlocal dynamic_view
            tool_result = await ToolManager.dispatch_tool_call(func_call, context)

            if "_latex_bytes" in tool_result and tool_result["_latex_bytes"]:
                attachment_files.append(
                    discord.File(fp=io.BytesIO(tool_result["_latex_bytes"]), filename="latex_formula.png")
                )

            if "_modal_view" in tool_result and tool_result["_modal_view"]:
                dynamic_view = tool_result["_modal_view"]

            return tool_result

        try:
            if not placeholder_msg:
                await channel.typing()

            response = await self.gemini_engine.generate_response(
                model_name=route.target_model,
                system_instruction=system_instruction,
                conversation_history=formatted_history,
                tools=tools,
                thinking_level=route.thinking_level,
                tool_dispatcher=execute_tool_wrapper
            )

            if status_task:
                status_task.cancel()

            final_text = response.content.strip()
            if not final_text:
                if attachment_files:
                    final_text = "Here is the rendered formula:"
                elif dynamic_view:
                    final_text = "Click the button below to open the form:"
                else:
                    final_text = "Action completed!"

            final_text = TimestampParser.convert_relative_dates(final_text)
            final_text = Sanitizer.sanitize_outgoing_content(final_text)
            chunks = MessageSplitter.split(final_text, max_length=1900)

            has_real_thoughts = bool(response.thought_content and len(response.thought_content.strip()) > 0)
            has_tools = bool(response.tools_executed)

            trace_view = None
            if has_real_thoughts or has_tools:
                trace_view = ThinkingTraceView(
                    model_used=response.model_used,
                    duration=response.duration,
                    thought_content=response.thought_content if has_real_thoughts else None,
                    tools_executed=response.tools_executed
                )

            active_view = dynamic_view or trace_view

            if placeholder_msg:
                await placeholder_msg.edit(
                    content=chunks[0],
                    attachments=attachment_files,
                    view=active_view if len(chunks) == 1 else None
                )
                for i, chunk in enumerate(chunks[1:], start=2):
                    is_last = (i == len(chunks))
                    await channel.send(content=chunk, view=active_view if is_last else None)
            else:
                for i, chunk in enumerate(chunks, start=1):
                    is_last = (i == len(chunks))
                    await channel.send(
                        content=chunk,
                        files=attachment_files if i == 1 else [],
                        view=active_view if is_last else None
                    )

            await memory_manager.save_message(
                message_id=str(message.id),
                channel_id=str(channel.id),
                guild_id=str(guild.id) if guild else None,
                author_id=str(author.id),
                author_name=author.display_name,
                content=cleaned_prompt,
                role="user"
            )

            bot_msg_id = str(placeholder_msg.id) if placeholder_msg else str(message.id)
            await memory_manager.save_message(
                message_id=bot_msg_id,
                channel_id=str(channel.id),
                guild_id=str(guild.id) if guild else None,
                author_id=str(self.bot.user.id),
                author_name=self.bot.user.display_name,
                content=final_text,
                role="assistant"
            )

        except Exception as e:
            logger.error(f"Error in chat pipeline: {e}", exc_info=True)
            if status_task:
                status_task.cancel()
            if placeholder_msg:
                try:
                    await placeholder_msg.edit(content="Sorry, I ran into an error processing that. Please try again!")
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type == discord.InteractionType.modal_submit and interaction.data:
            from src.ui.modals import DynamicModalV2Handler
            parsed_data = DynamicModalV2Handler.parse_modal_submission(interaction.data)
            logger.info(f"Received Modal V2 submission: {parsed_data}")

            formatted_fields = "\n".join([f"• **{k}**: `{v}`" for k, v in parsed_data.items()])
            await interaction.response.send_message(
                f"✅ **Form Received!**\n{formatted_fields}",
                ephemeral=True
            )

            prompt_context = f"[Modal Submission: {json.dumps(parsed_data)}]"
            await memory_manager.save_message(
                message_id=str(interaction.id),
                channel_id=str(interaction.channel_id),
                guild_id=str(interaction.guild_id) if interaction.guild else None,
                author_id=str(interaction.user.id),
                author_name=interaction.user.display_name,
                content=prompt_context,
                role="user"
            )


async def setup(bot: PriestyBot) -> None:
    await bot.add_cog(ChatCog(bot))