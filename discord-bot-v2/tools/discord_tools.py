import logging
from typing import Any
import discord
from discord.ui import (
    Button,
    Select,
    UserSelect,
    RoleSelect,
    ChannelSelect,
    MentionableSelect,
    Item
)
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.DiscordTools")

async def _resolve_channel(channel_id: str | None, context: ToolExecutionContext | None) -> discord.abc.Messageable | None:
    if not context:
        return None

    if channel_id and isinstance(channel_id, str) and channel_id.strip().isdigit():
        try:
            c_id = int(channel_id.strip())
            if context.bot:
                return context.bot.get_channel(c_id) or await context.bot.fetch_channel(c_id)
        except Exception:
            pass

    return context.channel

def create_ui_component(comp_data: dict[str, Any]) -> Item:
    c_type = comp_data.get("type", "").lower()
    c_id = comp_data.get("custom_id")
    placeholder = comp_data.get("placeholder") or None
    min_v = comp_data.get("min_values", 1)
    max_v = comp_data.get("max_values", 1)
    disabled = comp_data.get("disabled", False)

    if c_type == "user_select":
        return UserSelect(custom_id=c_id, placeholder=placeholder, min_values=min_v, max_values=max_v, disabled=disabled)
    elif c_type == "role_select":
        return RoleSelect(custom_id=c_id, placeholder=placeholder, min_values=min_v, max_values=max_v, disabled=disabled)
    elif c_type == "channel_select":
        ch_types = []
        if "channel_types" in comp_data and isinstance(comp_data["channel_types"], list):
            for ct in comp_data["channel_types"]:
                if hasattr(discord.ChannelType, ct.lower()):
                    ch_types.append(getattr(discord.ChannelType, ct.lower()))
        return ChannelSelect(
            custom_id=c_id,
            placeholder=placeholder,
            min_values=min_v,
            max_values=max_v,
            disabled=disabled,
            channel_types=ch_types or None
        )
    elif c_type == "mentionable_select":
        return MentionableSelect(custom_id=c_id, placeholder=placeholder, min_values=min_v, max_values=max_v, disabled=disabled)
    elif c_type in ["select", "string_select"]:
        opts = [discord.SelectOption(**opt) for opt in comp_data.get("options", [])]
        return Select(custom_id=c_id, placeholder=placeholder, options=opts, min_values=min_v, max_values=max_v, disabled=disabled)
    else:
        style_val = getattr(discord.ButtonStyle, comp_data.get("style", "secondary").lower(), discord.ButtonStyle.secondary)
        return Button(label=comp_data.get("label", "Button"), custom_id=c_id, style=style_val, disabled=disabled)

@tool_registry.register(
    name="add_component",
    description="Stages interactive Discord UI components (UserSelect, RoleSelect, ChannelSelect, MentionableSelect, StringSelect, or Button)."
)
async def add_component(
    component_type: str,
    custom_id: str = "",
    label: str = "",
    placeholder: str = "",
    options: list[dict[str, Any]] | None = None,
    channel_types: list[str] | None = None,
    min_values: int = 1,
    max_values: int = 1,
    disabled: bool = False,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    c_type = component_type.lower().strip()
    valid_types = {
        "button", "select", "string_select",
        "user_select", "role_select", "channel_select", "mentionable_select"
    }

    if c_type not in valid_types:
        return {
            "error": f"Invalid component type '{component_type}'. Valid options: {', '.join(sorted(valid_types))}"
        }

    comp_id = custom_id or f"comp_{c_type}_{id(context)}"

    component_payload = {
        "type": c_type,
        "custom_id": comp_id,
        "label": label,
        "placeholder": placeholder,
        "min_values": min_values,
        "max_values": max_values,
        "disabled": disabled
    }

    if c_type in ["select", "string_select"]:
        component_payload["options"] = options or []
    elif c_type == "channel_select" and channel_types:
        component_payload["channel_types"] = channel_types

    if context:
        if not hasattr(context, "staged_components"):
            context.staged_components = []
        context.staged_components.append(component_payload)

    return {
        "status": "staged",
        "component_type": c_type,
        "custom_id": comp_id,
        "details": component_payload
    }

@tool_registry.register(
    name="send_message",
    description="Sends a text message to a specific channel or the current channel."
)
async def send_message(content: str, channel_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    target_channel = await _resolve_channel(channel_id, context)
    if not target_channel:
        return {"error": "Target channel could not be resolved."}

    try:
        msg = await target_channel.send(content=content)
        return {
            "status": "sent",
            "message_id": str(msg.id),
            "channel": getattr(target_channel, "name", "DM"),
            "content": content[:100]
        }
    except Exception as e:
        return {"error": f"Failed to send message: {str(e)}"}

@tool_registry.register(
    name="react",
    description="Adds an emoji reaction to a specific message in the channel."
)
async def react(message_id: str, emoji: str, channel_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    target_channel = await _resolve_channel(channel_id, context)
    if not target_channel or not isinstance(target_channel, (discord.TextChannel, discord.DMChannel, discord.Thread)):
        return {"error": "Invalid channel type for reaction."}

    try:
        clean_msg_id = "".join([c for c in str(message_id) if c.isdigit()])
        if not clean_msg_id:
            return {"error": f"Invalid message ID: '{message_id}'"}

        msg = await target_channel.fetch_message(int(clean_msg_id))
        await msg.add_reaction(emoji)
        return {
            "status": "reacted",
            "emoji": emoji,
            "message_id": clean_msg_id,
            "target_author": msg.author.name
        }
    except Exception as e:
        return {"error": f"Failed to add reaction: {str(e)}"}

@tool_registry.register(
    name="read_message_history",
    description="Reads recent chat history from a channel with user IDs and timestamps."
)
async def read_message_history(limit: int = 10, channel_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    target_channel = await _resolve_channel(channel_id, context)
    if not target_channel or not isinstance(target_channel, (discord.TextChannel, discord.DMChannel, discord.Thread)):
        return {"error": "Channel does not support message history."}

    messages = []
    try:
        raw_msgs = [m async for m in target_channel.history(limit=min(limit, 30))]
        raw_msgs.reverse()
        for m in raw_msgs:
            messages.append({
                "id": str(m.id),
                "author": m.author.name,
                "author_id": str(m.author.id),
                "content": m.clean_content,
                "timestamp": m.created_at.isoformat()
            })
        return {
            "channel": getattr(target_channel, "name", "DM"),
            "count": len(messages),
            "messages": messages
        }
    except Exception as e:
        return {"error": f"Failed to read history: {str(e)}"}

@tool_registry.register(
    name="search_channel_history",
    description="Searches recent channel messages matching a specific keyword or query."
)
async def search_channel_history(query: str, limit: int = 25, channel_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    target_channel = await _resolve_channel(channel_id, context)
    if not target_channel or not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
        return {"error": "Channel does not support search."}

    matched = []
    q_lower = query.lower()
    try:
        async for m in target_channel.history(limit=min(limit, 50)):
            if q_lower in m.clean_content.lower():
                matched.append({
                    "id": str(m.id),
                    "author": m.author.name,
                    "content": m.clean_content,
                    "timestamp": m.created_at.isoformat()
                })
        return {
            "query": query,
            "channel": target_channel.name,
            "matches_found": len(matched),
            "results": matched
        }
    except Exception as e:
        return {"error": f"Search failed: {str(e)}"}

@tool_registry.register(
    name="create_thread",
    description="Creates a public or private thread in the current channel and routes conversation into it."
)
async def create_thread(name: str, private: bool = False, message_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    if not context or not isinstance(context.channel, discord.TextChannel):
        return {"error": "Threads can only be created inside standard server text channels."}

    try:
        clean_msg_id = "".join([c for c in str(message_id) if c.isdigit()])
        if clean_msg_id:
            msg = await context.channel.fetch_message(int(clean_msg_id))
            thread = await msg.create_thread(name=name[:100])
        else:
            thread_type = discord.ChannelType.private_thread if private else discord.ChannelType.public_thread
            thread = await context.channel.create_thread(name=name[:100], type=thread_type)

        context.active_thread = thread
        logger.info(f"[create_thread] Created thread '{thread.name}' (ID: {thread.id})")
        return {
            "status": "created",
            "thread_id": str(thread.id),
            "thread_name": thread.name
        }
    except Exception as e:
        return {"error": f"Failed to create thread: {str(e)}"}

@tool_registry.register(
    name="get_user_profile",
    description="Fetches user profile details including server roles, display name, and join dates."
)
async def get_user_profile(user_id: str, context: ToolExecutionContext = None) -> dict[str, Any]:
    if not context or not context.guild:
        return {"error": "Guild context unavailable."}

    try:
        clean_user_id = "".join([c for c in str(user_id) if c.isdigit()])
        if not clean_user_id:
            return {"error": f"Invalid user ID: '{user_id}'"}

        member = context.guild.get_member(int(clean_user_id)) or await context.guild.fetch_member(int(clean_user_id))
        if not member:
            return {"error": f"User ID {user_id} not found in this server."}

        return {
            "user_id": str(member.id),
            "name": member.name,
            "display_name": member.display_name,
            "roles": [r.name for r in member.roles if r.name != "@everyone"],
            "joined_server_at": member.joined_at.isoformat() if member.joined_at else "Unknown",
            "account_created_at": member.created_at.isoformat(),
            "is_bot": member.bot
        }
    except Exception as e:
        return {"error": f"Failed to fetch profile: {str(e)}"}

@tool_registry.register(
    name="get_server_info",
    description="Fetches detailed information about the current Discord server."
)
async def get_server_info(context: ToolExecutionContext = None) -> dict[str, Any]:
    if not context or not context.guild:
        return {"error": "Server information is only available inside servers, not DMs."}

    g = context.guild
    return {
        "server_name": g.name,
        "server_id": str(g.id),
        "owner": str(g.owner),
        "member_count": g.member_count,
        "text_channels": len(g.text_channels),
        "voice_channels": len(g.voice_channels),
        "roles_count": len(g.roles),
        "custom_emojis": [e.name for e in g.emojis[:30]],
        "created_at": g.created_at.isoformat()
    }