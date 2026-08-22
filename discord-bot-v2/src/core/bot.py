import os
import logging
import discord
from discord.ext import commands
from src.core.config import config
from src.core.key_rotator import KeyRotator
from src.database.db import db

logger = logging.getLogger("PriestyAI.Bot")

class PriestyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()

        super().__init__(
            command_prefix="!priesty_disabled_",
            intents=intents,
            help_command=None,
            case_insensitive=True,
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=True,
                replied_user=True
            )
        )

        self.key_rotator = KeyRotator(config.gemini_keys)
        self.owner_id = config.owner_id

    async def setup_hook(self) -> None:
        logger.info("Initializing database connection and verifying schema...")
        await db.connect()

        os.makedirs("data", exist_ok=True)
        os.makedirs("sandbox", exist_ok=True)

        cogs_to_load = [
            "src.cogs.chat",
        ]

        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                logger.info(f"Loaded extension: {cog}")
            except Exception as e:
                logger.error(f"Failed to load extension {cog}: {e}", exc_info=True)

    async def close(self) -> None:
        logger.info("Bot shutting down. Closing database connection pool...")
        await db.close()
        await super().close()

    async def on_ready(self) -> None:
        if self.user:
            logger.info("=" * 50)
            logger.info("PriestyAI v2 is Online!")
            logger.info(f"Logged in as: {self.user.name} (ID: {self.user.id})")
            logger.info(f"Connected Guilds: {len(self.guilds)}")
            logger.info("Intents: All Enabled")
            logger.info("=" * 50)

            activity = discord.Activity(
                type=discord.ActivityType.custom,
                name="status",
                state="listening for @mentions"
            )
            await self.change_presence(status=discord.Status.online, activity=activity)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        logger.error(f"Command error in {ctx.command}: {error}", exc_info=True)

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.error(f"Unhandled gateway exception in event '{event_method}'", exc_info=True)