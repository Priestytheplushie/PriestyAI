import logging
from typing import Dict, Any, Optional, List
import discord

logger = logging.getLogger("PriestyAI.DiscordTools")

class DiscordToolsContext:
    def __init__(self, bot: discord.Client, current_message: discord.Message):
        self.bot = bot
        self.message = current_message
        self.guild = current_message.guild
        self.channel = current_message.channel


async def execute_react(context: DiscordToolsContext, emoji: str, message_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        target_msg = context.message
        if message_id and str(message_id).strip() != str(context.message.id):
            target_msg = await context.channel.fetch_message(int(message_id))

        await target_msg.add_reaction(emoji)
        return {"status": "success", "message": f"Successfully reacted with {emoji} to message {target_msg.id}"}
    except Exception as e:
        logger.error(f"React tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_send_message(context: DiscordToolsContext, content: str, channel_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        target_channel = context.channel
        if channel_id and context.guild:
            target_channel = context.guild.get_channel(int(channel_id)) or await context.bot.fetch_channel(int(channel_id))

        sent = await target_channel.send(content)
        return {"status": "success", "message_id": str(sent.id), "channel": getattr(target_channel, "name", "DM")}
    except Exception as e:
        logger.error(f"Send message tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_read_channel_history(context: DiscordToolsContext, channel_id: Optional[str] = None, limit: int = 15) -> Dict[str, Any]:
    try:
        target_channel = context.channel
        if channel_id and context.guild:
            target_channel = context.guild.get_channel(int(channel_id)) or await context.bot.fetch_channel(int(channel_id))

        messages = []
        async for msg in target_channel.history(limit=min(limit, 30)):
            if msg.content or msg.attachments:
                messages.append({
                    "id": str(msg.id),
                    "author": msg.author.display_name,
                    "content": msg.content,
                    "attachments": [a.filename for a in msg.attachments]
                })

        return {"status": "success", "channel": getattr(target_channel, "name", "DM"), "messages": messages}
    except Exception as e:
        logger.error(f"Read history tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_get_server_channels(context: DiscordToolsContext) -> Dict[str, Any]:
    try:
        if not context.guild:
            return {"status": "error", "error": "Not currently inside a server guild."}

        channels = [
            {"id": str(c.id), "name": c.name, "type": str(c.type)}
            for c in context.guild.channels
            if isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.ForumChannel))
        ]
        return {"status": "success", "guild": context.guild.name, "channels": channels[:40]}
    except Exception as e:
        logger.error(f"Get channels tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_get_user_profile(context: DiscordToolsContext, user_id: str) -> Dict[str, Any]:
    try:
        target_id = int(str(user_id).replace("<@", "").replace(">", "").strip())
        member = context.guild.get_member(target_id) if context.guild else None
        
        if not member:
            user = await context.bot.fetch_user(target_id)
            return {
                "status": "success",
                "id": str(user.id),
                "name": user.name,
                "display_name": user.display_name,
                "avatar_url": user.display_avatar.url,
                "bot": user.bot
            }

        return {
            "status": "success",
            "id": str(member.id),
            "name": member.name,
            "display_name": member.display_name,
            "roles": [r.name for r in member.roles if r.name != "@everyone"],
            "joined_at": member.joined_at.isoformat() if member.joined_at else None,
            "created_at": member.created_at.isoformat(),
            "avatar_url": member.display_avatar.url,
            "bot": member.bot
        }
    except Exception as e:
        logger.error(f"Get user profile tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_search_server(context: DiscordToolsContext, query: str, limit: int = 15) -> Dict[str, Any]:
    try:
        results = []
        query_lower = query.lower()
        async for msg in context.channel.history(limit=100):
            if query_lower in msg.content.lower():
                results.append({
                    "id": str(msg.id),
                    "author": msg.author.display_name,
                    "content": msg.content[:200]
                })
                if len(results) >= limit:
                    break

        return {"status": "success", "query": query, "matches_found": len(results), "results": results}
    except Exception as e:
        logger.error(f"Search server tool failed: {e}")
        return {"status": "error", "error": str(e)}


async def execute_create_thread(context: DiscordToolsContext, thread_name: str, message_id: Optional[str] = None) -> Dict[str, Any]:
    try:
        target_msg = context.message
        if message_id:
            target_msg = await context.channel.fetch_message(int(message_id))

        if isinstance(context.channel, discord.TextChannel):
            thread = await target_msg.create_thread(name=thread_name, auto_archive_duration=60)
            return {"status": "success", "thread_id": str(thread.id), "thread_name": thread.name}
        
        return {"status": "error", "error": "Threads can only be created in text channels."}
    except Exception as e:
        logger.error(f"Create thread tool failed: {e}")
        return {"status": "error", "error": str(e)}