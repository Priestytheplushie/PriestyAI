import sys
import logging
import asyncio
from typing import List
import discord
from discord.ext import commands

import config
from core.key_pool import KeyPoolManager
from core.router import FastRouter
from core.normalizer import BidirectionalNormalizer
from core.multimodal import MultimodalProcessor
from tools.discord_tools import DiscordToolsContext
from ui.streamer import LiveResponseStreamer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.ERROR)
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

logger = logging.getLogger("PriestyAI.Main")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

key_pool = KeyPoolManager(config.GEMINI_API_KEYS)
router = FastRouter(key_pool)
normalizer = BidirectionalNormalizer()
multimodal = MultimodalProcessor()


@bot.event
async def on_ready():
    logger.info("=" * 60)
    logger.info(f"PriestyAI (SubPhase 3: Tools Active) Online: {bot.user} (ID: {bot.user.id})")
    logger.info(f"Connected Guilds: {len(bot.guilds)}")
    logger.info(f"Active Key Projects: {len(config.GEMINI_API_KEYS)}")
    logger.info("=" * 60)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    is_mentioned = bot.user in message.mentions
    is_reply_to_bot = False

    if message.reference and message.reference.message_id:
        try:
            parent = await message.channel.fetch_message(message.reference.message_id)
            if parent.author.id == bot.user.id:
                is_reply_to_bot = True
        except Exception:
            pass

    if not (is_mentioned or is_reply_to_bot):
        await bot.process_commands(message)
        return

    async with message.channel.typing():
        try:
            streamer = LiveResponseStreamer(
                message=message,
                normalizer=normalizer,
                initial_status="Synthesizing..."
            )
            await streamer.start_stream()

            history_messages = []
            async for hist_msg in message.channel.history(limit=10, before=message):
                history_messages.append(hist_msg)
            history_messages.reverse()
            history_messages.append(message)

            xml_context = normalizer.inbound_normalize(
                current_message=message,
                history=history_messages,
                bot_user=bot.user
            )

            media_parts = await multimodal.process_attachments(message)
            parent_media_parts = await multimodal.extract_parent_attachments(message)
            all_media = media_parts + parent_media_parts
            has_media = len(all_media) > 0

            decision = await router.route(xml_context, has_media=has_media)
            streamer.set_status_cycle(decision.status_cycle)

            contents = [xml_context] + all_media
            discord_context = DiscordToolsContext(bot, message)
            model_used = decision.target_model

            async for text_chunk, thought_chunk, active_model in key_pool.generate_with_tools_stream(
                contents=contents,
                target_model=decision.target_model,
                discord_context=discord_context,
                thought_session=streamer.thought_session,
                system_instruction=config.SYSTEM_INSTRUCTION
            ):
                model_used = active_model
                await streamer.push_chunk(text_chunk=text_chunk, thought_chunk=thought_chunk)

            await streamer.finalize(model_name=model_used)

        except Exception as e:
            logger.error(f"Error handling message {message.id}: {e}", exc_info=True)
            try:
                await message.reply(f"⚠️ **Error:** `{str(e)}`", mention_author=True)
            except Exception:
                pass


@bot.event
async def on_close():
    await multimodal.close()


if __name__ == "__main__":
    if not config.DISCORD_TOKEN:
        logger.critical("DISCORD_TOKEN is missing in .env! Please configure your bot token.")
        sys.exit(1)

    bot.run(config.DISCORD_TOKEN)