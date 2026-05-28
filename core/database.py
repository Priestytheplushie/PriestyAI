import discord
import json
import logging

logger = logging.getLogger("BrainDB")

class BrainDatabase:
    def __init__(self, bot: discord.Client, brain_guild_id: int):
        self.bot = bot
        self.brain_guild_id = brain_guild_id
        self.category_name = "Brain-Memory"

    async def _get_guild(self) -> discord.Guild:
        guild = self.bot.get_guild(self.brain_guild_id)
        if not guild:
            try:
                guild = await self.bot.fetch_guild(self.brain_guild_id)
            except Exception as e:
                logger.error(f"Could not fetch Brain Guild: {e}")
        return guild

    async def _get_or_create_category(self, guild: discord.Guild) -> discord.CategoryChannel:
        for category in guild.categories:
            if category.name.lower() == self.category_name.lower():
                return category
        return await guild.create_category(name=self.category_name)

    async def _get_or_create_channel(self, guild: discord.Guild, identifier: str) -> discord.TextChannel:
        category = await self._get_or_create_category(guild)
        formatted_name = identifier.lower().replace(" ", "-").replace("_", "-")
        
        for channel in category.text_channels:
            if channel.name == formatted_name:
                return channel
                
        try:
            live_channels = await guild.fetch_channels()
            for channel in live_channels:
                if isinstance(channel, discord.TextChannel) and channel.category_id == category.id and channel.name == formatted_name:
                    return channel
        except Exception as e:
            logger.warning(f"Failed to fetch live channels: {e}")

        new_channel = await guild.create_text_channel(name=formatted_name, category=category)
        
        default_memory = {"known_aliases": [], "preferences": [], "key_facts": [], "past_topics": []}
        await self._write_pin(new_channel, default_memory)
        return new_channel

    async def _write_pin(self, channel: discord.TextChannel, data: dict):
        json_data = json.dumps(data, indent=2, ensure_ascii=False)
        payload = f"```json\n{json_data}\n```"
        
        pinned_messages = []
        async for msg in channel.pins():
            pinned_messages.append(msg)
            
        if pinned_messages:
            await pinned_messages[0].edit(content=payload)
        else:
            msg = await channel.send(content=payload)
            await msg.pin()

    async def get_memory(self, identifier: str) -> dict:
        guild = await self._get_guild()
        if not guild:
            return {}
        
        channel = await self._get_or_create_channel(guild, identifier)
        pinned_messages = []
        async for msg in channel.pins():
            pinned_messages.append(msg)
        
        if pinned_messages:
            content = pinned_messages[0].content
            if content.startswith("```json") and content.endswith("```"):
                json_str = content[7:-3].strip()
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError as err:
                    logger.critical(f"JSON memory corruption: {err}")
        
        return {"known_aliases": [], "preferences": [], "key_facts": [], "past_topics": []}

    async def save_memory(self, identifier: str, data: dict):
        guild = await self._get_guild()
        if not guild:
            return
        channel = await self._get_or_create_channel(guild, identifier)
        await self._write_pin(channel, data)
        logger.info(f"Updated memory successfully in #{channel.name}")