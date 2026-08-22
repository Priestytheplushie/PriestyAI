import discord
import logging
from typing import Dict, Any, List, Optional
from src.database.memory_manager import memory_manager

logger = logging.getLogger("PriestyAI.DiscordTools")

class DiscordTools:

    @staticmethod
    async def get_user_profile(guild: Optional[discord.Guild], user_id: int) -> Dict[str, Any]:
        if not guild:
            return {"error": "User profile lookup is only available inside servers (guilds)."}

        member = guild.get_member(user_id)
        if not member:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                return {"error": f"Member with ID {user_id} not found in this guild."}

        roles = [r.name for r in member.roles if r.name != "@everyone"]
        activities = []
        for act in member.activities:
            if isinstance(act, discord.CustomActivity):
                activities.append(f"Custom Status: {act.name}")
            elif isinstance(act, discord.Activity):
                activities.append(f"{act.type.name.capitalize()}: {act.name}")
            elif isinstance(act, discord.Game):
                activities.append(f"Playing: {act.name}")
            elif isinstance(act, discord.Streaming):
                activities.append(f"Streaming: {act.name} ({act.url})")

        return {
            "name": member.name,
            "display_name": member.display_name,
            "id": member.id,
            "bot": member.bot,
            "joined_server_at": member.joined_at.strftime("%Y-%m-%d %H:%M:%S UTC") if member.joined_at else "Unknown",
            "account_created_at": member.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "roles": roles,
            "top_role": member.top_role.name,
            "status": str(member.status),
            "activities": activities
        }

    @staticmethod
    async def read_channel_messages(
        guild: Optional[discord.Guild],
        channel_id: int,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        if not guild:
            return [{"error": "Channel reading is only available inside servers."}]

        channel = guild.get_channel(channel_id)
        if not channel or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return [{"error": f"Channel with ID {channel_id} not found or is not a text channel."}]

        messages = []
        try:
            async for msg in channel.history(limit=min(limit, 25)):
                messages.append({
                    "id": msg.id,
                    "author": msg.author.display_name,
                    "content": msg.clean_content,
                    "created_at": msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
                })
        except discord.Forbidden:
            return [{"error": f"Missing permissions to read channel #{channel.name}."}]
        except Exception as e:
            return [{"error": f"Failed to read messages: {str(e)}"}]

        return messages

    @staticmethod
    async def react_to_message(
        channel: discord.abc.Messageable,
        message_id: int,
        emoji: str
    ) -> Dict[str, Any]:
        try:
            msg = await channel.fetch_message(message_id)
            await msg.add_reaction(emoji)
            return {"success": True, "reacted_to": message_id, "emoji": emoji}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @staticmethod
    async def watch_channel(guild_id: str, channel_id: str, duration_minutes: int = 30, reason: Optional[str] = None) -> Dict[str, Any]:
        until = await memory_manager.add_watched_channel(channel_id, guild_id, duration_minutes, reason)
        return {
            "success": True,
            "channel_id": channel_id,
            "watching_until_unix": until,
            "duration_minutes": duration_minutes
        }

    @staticmethod
    async def reset_channel_memory(channel_id: str) -> Dict[str, Any]:
        count = await memory_manager.clear_channel_history(channel_id)
        return {"success": True, "cleared_messages": count, "message": "Channel conversation memory reset."}