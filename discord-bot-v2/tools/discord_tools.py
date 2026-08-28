import ast
import json
import uuid
import logging
from datetime import timedelta
import time
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
from core.poll_manager import poll_manager
from core.branch_manager import branch_manager

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

def normalize_component_type(component_type: str) -> str:
    cleaned = component_type.lower().strip().replace(" ", "_")
    mapping = {
        "button": "button",
        "btn": "button",
        "select": "string_select",
        "string_select": "string_select",
        "stringselect": "string_select",
        "user_select": "user_select",
        "userselect": "user_select",
        "role_select": "role_select",
        "roleselect": "role_select",
        "channel_select": "channel_select",
        "channelselect": "channel_select",
        "mentionable_select": "mentionable_select",
        "mentionableselect": "mentionable_select"
    }
    return mapping.get(cleaned, cleaned)

def parse_options_robust(raw_options: Any) -> list[dict[str, Any]]:
    if isinstance(raw_options, list):
        return [o for o in raw_options if isinstance(o, dict)]
    if isinstance(raw_options, str):
        raw_str = raw_options.strip()
        try:
            val = json.loads(raw_str)
            if isinstance(val, list):
                return [o for o in val if isinstance(o, dict)]
        except Exception:
            pass
        try:
            val = ast.literal_eval(raw_str)
            if isinstance(val, list):
                return [o for o in val if isinstance(o, dict)]
        except Exception:
            pass
    return []

@tool_registry.register(
    name="add_component",
    description=(
        "Stages interactive Discord UI components (Buttons, Select Menus, or Section Accessories) to attach to your response.\n"
        "Supported component_type values:\n"
        "- 'Button': Clickable button (set label, style: 'primary'|'secondary'|'success'|'danger', or modal_id)\n"
        "- 'StringSelect': Dropdown menu with custom text options (options: [{'label':'...','value':'...','description':'...'}]).\n"
        "- 'UserSelect': Native Discord user/member picker dropdown\n"
        "- 'RoleSelect': Native Discord server role picker dropdown\n"
        "- 'ChannelSelect': Native Discord channel picker dropdown\n"
        "- 'MentionableSelect': Native Discord user OR role picker dropdown\n"
        "Placement options: 'action_row' (default) or 'section' (side-by-side text with button on right)."
    )
)
async def add_component(
    component_type: str,
    custom_id: str = "",
    modal_id: str | None = None,
    label: str = "",
    placeholder: str = "",
    section_text: str = "",
    placement: str = "action_row",
    options: Any = None,
    channel_types: list[str] | None = None,
    min_values: int = 1,
    max_values: int = 1,
    style: str = "secondary",
    disabled: bool = False,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    c_type = normalize_component_type(component_type)
    valid_types = {
        "button", "string_select", "user_select",
        "role_select", "channel_select", "mentionable_select"
    }

    if c_type not in valid_types:
        return {
            "error": f"Invalid component type '{component_type}'. Valid options: {', '.join(sorted(valid_types))}"
        }

    comp_id = custom_id.strip() if custom_id.strip() else f"comp_{c_type}_{uuid.uuid4().hex[:8]}"
    target_modal_id = modal_id or (comp_id if c_type == "button" else None)
    clean_placement = "section" if (placement.lower().strip() == "section" or section_text.strip()) and c_type == "button" else "action_row"

    parsed_options = parse_options_robust(options)

    option_modals = {}
    if c_type == "string_select" and isinstance(parsed_options, list):
        for opt in parsed_options:
            if isinstance(opt, dict) and opt.get("modal_id"):
                val = opt.get("value", opt.get("label", ""))
                option_modals[val] = opt["modal_id"]

    component_payload = {
        "type": c_type,
        "custom_id": comp_id,
        "modal_id": target_modal_id,
        "option_modals": option_modals,
        "label": label,
        "placeholder": placeholder,
        "section_text": section_text.strip(),
        "placement": clean_placement,
        "min_values": max(0, int(min_values)),
        "max_values": max(1, min(25, int(max_values))),
        "style": style.lower().strip(),
        "disabled": disabled
    }

    if c_type == "string_select":
        component_payload["options"] = parsed_options
    elif c_type == "channel_select" and channel_types:
        component_payload["channel_types"] = channel_types

    if context:
        if not hasattr(context, "staged_components"):
            context.staged_components = []

        existing_idx = None
        for idx, existing_comp in enumerate(context.staged_components):
            if existing_comp.get("custom_id") == comp_id:
                existing_idx = idx
                break

        if existing_idx is not None:
            context.staged_components[existing_idx] = component_payload
            logger.info(f"[add_component] Updated existing '{c_type}' (ID: '{comp_id}')")
        else:
            context.staged_components.append(component_payload)
            logger.info(f"[add_component] Staged '{c_type}' (ID: '{comp_id}', options: {len(parsed_options)})")

    return {
        "status": "staged",
        "component_type": c_type,
        "custom_id": comp_id,
        "modal_id": target_modal_id,
        "placement": clean_placement,
        "options_count": len(parsed_options),
        "details": component_payload
    }

@tool_registry.register(
    name="create_poll",
    description=(
        "Creates a native Discord Poll with interactive vote buttons.\n"
        "- question: The poll topic or title\n"
        "- options: List of choices (2 to 10 options)\n"
        "- duration_hours: Duration before poll concludes (1 to 168 hours, default 24)"
    )
)
async def create_poll(
    question: str,
    options: list[str],
    duration_hours: int = 24,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not context or not context.channel:
        return {"error": "Channel context unavailable for poll creation."}

    clean_options = [str(opt).strip() for opt in options if str(opt).strip()]
    if len(clean_options) < 2:
        return {"error": "Polls require at least 2 distinct options."}
    if len(clean_options) > 10:
        clean_options = clean_options[:10]

    hours = max(1, min(int(duration_hours), 168))

    try:
        poll_obj = discord.Poll(
            question=question.strip()[:300],
            duration=timedelta(hours=hours),
            multiple=False
        )
        for opt_text in clean_options:
            poll_obj.add_answer(text=opt_text[:55])

        poll_message = await context.channel.send(poll=poll_obj)
        poll_id = f"poll_{int(time.time() * 1000)}"

        poll_manager.register_poll(
            poll_id=poll_id,
            message_id=poll_message.id,
            channel_id=context.channel.id,
            guild_id=context.guild.id if context.guild else None,
            question=question.strip(),
            options=clean_options,
            duration_hours=hours
        )

        logger.info(f"[create_poll] Created native poll #{poll_id} on message {poll_message.id}")
        return {
            "status": "created",
            "poll_id": poll_id,
            "message_id": str(poll_message.id),
            "question": question,
            "options_count": len(clean_options),
            "duration_hours": hours
        }

    except Exception as e:
        logger.error(f"[create_poll] Failed to create poll: {e}")
        return {"error": f"Failed to create Discord poll: {str(e)}"}

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
async def search_channel_history(query: str = "", limit: int = 25, channel_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    target_channel = await _resolve_channel(channel_id, context)
    if not target_channel or not isinstance(target_channel, (discord.TextChannel, discord.Thread)):
        return {"error": "Channel does not support search."}

    matched = []
    q_lower = query.lower().strip()
    try:
        async for m in target_channel.history(limit=min(limit, 50)):
            if not q_lower or q_lower in m.clean_content.lower():
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
    description="Creates a dedicated Discord thread to host an extended discussion or deep coding project."
)
async def create_thread(name: str, private: bool = False, message_id: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    if not context or not context.channel:
        return {"error": "Channel context unavailable."}

    if getattr(context, "active_thread", None) is not None:
        return {
            "status": "already_created",
            "thread_id": str(context.active_thread.id),
            "thread_name": context.active_thread.name,
            "message": "A thread has already been created for this turn."
        }

    if isinstance(context.channel, discord.Thread):
        return {"error": "Already inside a thread. Do not create nested threads."}

    if isinstance(context.channel, discord.DMChannel):
        return {"error": "Threads cannot be created in direct messages."}

    if not isinstance(context.channel, discord.TextChannel):
        return {"error": "Threads can only be created inside standard server text channels."}

    try:
        target_msg = None
        clean_msg_id = "".join([c for c in str(message_id) if c.isdigit()])
        if clean_msg_id:
            try:
                target_msg = await context.channel.fetch_message(int(clean_msg_id))
            except Exception:
                pass

        if not target_msg and getattr(context, "message", None):
            target_msg = context.message

        if target_msg:
            thread = await target_msg.create_thread(name=name[:100])
        else:
            thread_type = discord.ChannelType.private_thread if private else discord.ChannelType.public_thread
            thread = await context.channel.create_thread(name=name[:100], type=thread_type)

        if context.author and hasattr(thread, "add_user"):
            try:
                await thread.add_user(context.author)
            except Exception as ex:
                logger.debug(f"Failed to add author to thread: {ex}")

        branch_id = str(uuid.uuid4())[:8]
        branch_manager.create_branch(
            branch_id=branch_id,
            thread_id=thread.id,
            channel_id=context.channel.id,
            guild_id=context.guild.id if context.guild else None,
            creator_id=context.author.id if context.author else "0",
            title=name[:60],
            root_message_id=str(target_msg.id) if target_msg else "0",
            messages=[]
        )

        context.active_thread = thread
        logger.info(f"[create_thread] Created and registered thread '{thread.name}' (ID: {thread.id})")
        return {
            "status": "created",
            "thread_id": str(thread.id),
            "thread_name": thread.name,
            "jump_url": thread.jump_url
        }
    except Exception as e:
        logger.error(f"[create_thread] Failed to create thread: {e}")
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