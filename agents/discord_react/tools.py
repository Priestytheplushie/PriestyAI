
import discord
import logging
import asyncio
import json
import uuid
import urllib.parse
import aiohttp
from bs4 import BeautifulSoup
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("ReactTools")

async def run_local_agent_tool(bot, session, tool_name: str, arguments: dict) -> str:
    t_name = tool_name.lower().strip()
    
    try:
        if t_name == "read_channel_history":
            channel_id = int(arguments.get("channel_id", session.thread_id))
            limit = min(int(arguments.get("limit", 25)), 50)
            before_id = arguments.get("before_msg_id")
            before_id = int(before_id) if before_id else None
            
            return await tool_read_channel_history(bot, session, channel_id, limit, before_id)
            
        elif t_name == "search_server_messages":
            query = arguments.get("query", "")
            author_id = arguments.get("author_id")
            author_id = int(author_id) if author_id else None
            limit = min(int(arguments.get("limit", 15)), 20)
            
            return await tool_search_server_messages(bot, session, query, author_id, limit)
            
        elif t_name == "fetch_user_profile":
            user_id = int(arguments.get("user_id", 0))
            return await tool_fetch_user_profile(bot, session, user_id)
            
        elif t_name == "custom_web_search":
            query = arguments.get("query", "")
            return await tool_custom_web_search(bot, query)
            
        elif t_name == "custom_web_scrape":
            url = arguments.get("url", "")
            return await tool_custom_web_scrape(bot, url)
            
        elif t_name == "ask_user_question":
            question = arguments.get("question_text", "I require your assistance to proceed.")
            component_type = arguments.get("component_type", "Button").strip()
            options = arguments.get("suggested_options", [])
            return await tool_ask_user_question(bot, session, question, component_type, options)

        elif t_name == "compare_user_activity":
            user_id_1 = int(arguments.get("user_id_1", 0))
            user_id_2 = int(arguments.get("user_id_2", 0))
            timeframe_days = int(arguments.get("timeframe_days", 7))

            return await tool_compare_user_activity(bot, session, user_id_1, user_id_2, timeframe_days)

        elif t_name == "list_server_channels":
            return await tool_list_server_channels(bot, session)
            
        elif t_name == "get_channel_metadata":
            channel_id = int(arguments.get("channel_id", 0))
            return await tool_get_channel_metadata(bot, session, channel_id)

        elif t_name == "list_active_threads":
            return await tool_list_active_threads(bot, session)

        elif t_name == "read_message_attachment":
            channel_id = int(arguments.get("channel_id", 0))
            message_id = int(arguments.get("message_id", 0))
            attachment_url = arguments.get("attachment_url", "")
            return await tool_read_message_attachment(bot, session, channel_id, message_id, attachment_url)

        elif t_name == "update_context_snapshot":
            alias = arguments.get("alias", "")
            updated_data = arguments.get("updated_data", {})
            notes = arguments.get("notes", "")
            return await tool_update_context_snapshot(bot, session, alias, updated_data, notes)
            
        else:
            return f"[Error: Selected tool '{tool_name}' does not match any registered schema functions.]"
            
    except Exception as err:
        return f"[Error: Tool execution failed due to an uncaught crash: {err}]"


async def tool_read_channel_history(bot, session, channel_id: int, limit: int, before_msg_id: Optional[int]) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    try:
        channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    except Exception:
        return f"[Error: Could not locate channel ID {channel_id} inside target guild '{guild.name}']"

    if not channel:
        return f"[Error: Could not locate channel ID {channel_id} inside target guild '{guild.name}']"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    user_perms = channel.permissions_for(user_member)
    if not user_perms.read_messages or not user_perms.read_message_history:
        return f"[Error: Access Denied. You do not have permissions to view channel #{channel.name} inside '{guild.name}']"
        
    try:
        before_msg = None
        if before_msg_id:
            before_msg = await channel.fetch_message(before_msg_id)
            
        lines = []
        async for msg in channel.history(limit=limit, before=before_msg):
            author_name = f"{msg.author.display_name} (@{msg.author.name})"
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"[{timestamp}] MessageID: {msg.id} | {author_name}: {msg.clean_content}")
            
        if not lines:
            return "No message history was found inside the selected channel bounds."
            
        lines.reverse()
        return "\n".join(lines)
    except Exception as e:
        return f"[Failed to read history from channel {channel_id}: {e}]"


async def tool_search_server_messages(bot, session, query: str, author_id: Optional[int], limit: int) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    lines = []
    found_count = 0
    
    for channel in guild.text_channels:
        if found_count >= limit:
            break
            
        try:
            bot_perms = channel.permissions_for(guild.me)
            if not bot_perms.read_message_history or not bot_perms.read_messages:
                continue
                
            user_perms = channel.permissions_for(user_member)
            if not user_perms.read_messages or not user_perms.read_message_history:
                continue
                
            async for msg in channel.history(limit=100):
                if found_count >= limit:
                    break
                    
                if author_id and msg.author.id != author_id:
                    continue
                    
                if query.lower() in msg.content.lower():
                    author_name = f"{msg.author.display_name} (@{msg.author.name})"
                    lines.append(f"Channel: #{channel.name} | MessageID: {msg.id} | {author_name}: {msg.clean_content}")
                    found_count += 1
        except Exception:
            continue
            
    if not lines:
        return f"No server messages matching query parameter '{query}' were identified."
    return "\n".join(lines)


async def tool_fetch_user_profile(bot, session, user_id: int) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    try:
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
    except Exception:
        return f"[Error: User with ID {user_id} is not a member of guild '{guild.name}']"

    if not member:
        return f"[Error: User with ID {user_id} is not a member of guild '{guild.name}']"
        
    try:
        profile = {
            "user_id": member.id,
            "username": member.name,
            "display_name": member.display_name,
            "nickname": member.nick,
            "joined_at": member.joined_at.strftime("%Y-%m-%d %H:%M:%S") if member.joined_at else "Unknown",
            "created_at": member.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "bot_status": member.bot,
            "server_roles": [role.name for role in member.roles if not role.is_default()],
            "presence_activities": bot._compile_user_activity(member)
        }
        return json.dumps(profile, indent=2)
    except Exception as e:
        return f"[Failed to compile user profile for ID {user_id}: {e}]"


async def tool_custom_web_search(bot, query: str) -> str:
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(url, timeout=12) as response:
                if response.status != 200:
                    return f"[Error: Search scraper failed with HTTP status {response.status}]"
                html_data = await response.text()
                
        soup = BeautifulSoup(html_data, "html.parser")
        results = []
        
        for node in soup.find_all("a", class_="result__snippet")[:5]:
            title_node = node.find_previous("a", class_="result__url")
            title = title_node.get_text().strip() if title_node else "Result Title"
            link = title_node["href"] if title_node else "No link found"
            snippet = node.get_text().strip()
            
            results.append(f"Title: {title}\nLink: {link}\nSnippet: {snippet}\n")
            
        if not results:
            return f"DuckDuckGo search returned no active results for query: '{query}'."
            
        return "\n".join(results)
    except Exception as e:
        return f"[Failed to process local web search: {e}]"


async def tool_custom_web_scrape(bot, url: str) -> str:
    if not url:
        return "[Error: URL argument is empty]"
    try:
        scraped_markdown = await bot.link_reader.fetch_and_clean(url)
        return scraped_markdown
    except Exception as e:
        return f"[Failed to scrape webpage: {e}]"


async def tool_ask_user_question(bot, session, question: str, component_type: str = "Button", options: list = None) -> str:
    session.status = "paused_user_question"
    session.pending_question_text = question
    session.pending_options = [o.strip() for o in options if o.strip()][:25] if options else []
    
    logger.info(f"Agent session {session.thread_id} paused to prompt user question: '{question[:20]}...'")
    
    view = discord.ui.View(timeout=None)
    
    async def resume_with_choice(interaction: discord.Interaction, choice_text: str):
        for child in view.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=view)
        except Exception:
            pass
            
        session.status = "running"
        session.react_history[-1]["observation"] = choice_text
        interaction.client.loop.create_task(session.execute_tick(interaction.client))

    comp_clean = component_type.strip().lower()
    
    if comp_clean == "channelselect":
        select = discord.ui.ChannelSelect(placeholder="Select a channel...", custom_id=f"agent_q_chan_{session.thread_id}")
        async def chan_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            selected = select.values[0] if select.values else None
            summary = f"User selected channel: #{selected.name} [ID: {selected.id}]" if selected else "User submitted an empty channel selection."
            await resume_with_choice(interaction, summary)
        select.callback = chan_callback
        view.add_item(select)
        
    elif comp_clean == "userselect":
        select = discord.ui.UserSelect(placeholder="Select a member...", custom_id=f"agent_q_user_{session.thread_id}")
        async def user_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            selected = select.values[0] if select.values else None
            summary = f"User selected member: {selected.display_name} (@{selected.name}) [ID: {selected.id}]" if selected else "User submitted an empty user selection."
            await resume_with_choice(interaction, summary)
        select.callback = user_callback
        view.add_item(select)
        
    elif comp_clean == "roleselect":
        select = discord.ui.RoleSelect(placeholder="Select a role...", custom_id=f"agent_q_role_{session.thread_id}")
        async def role_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            selected = select.values[0] if select.values else None
            summary = f"User selected role: {selected.name} [ID: {selected.id}]" if selected else "User submitted an empty role selection."
            await resume_with_choice(interaction, summary)
        select.callback = role_callback
        view.add_item(select)
        
    elif comp_clean == "stringselect" and session.pending_options:
        opts = [discord.SelectOption(label=opt[:100], value=opt[:100]) for opt in session.pending_options]
        select = discord.ui.Select(placeholder="Choose an option...", options=opts, custom_id=f"agent_q_str_{session.thread_id}")
        async def str_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            selected = select.values[0] if select.values else "None"
            await resume_with_choice(interaction, f"User selected option: '{selected}'")
        select.callback = str_callback
        view.add_item(select)
        
    else:
        if session.pending_options:
            for opt in session.pending_options[:5]:
                btn = discord.ui.Button(
                    style=discord.ButtonStyle.primary, 
                    label=opt[:80], 
                    custom_id=f"agent_question_{session.thread_id}_{uuid.uuid4().hex[:6]}"
                )
                def make_btn_callback(selected_opt=opt):
                    async def btn_callback(interaction: discord.Interaction):
                        await interaction.response.defer()
                        await resume_with_choice(interaction, f"User selected option: '{selected_opt}'")
                    return btn_callback
                btn.callback = make_btn_callback()
                view.add_item(btn)

    cancel_btn = discord.ui.Button(style=discord.ButtonStyle.danger, label="Cancel Session", custom_id=f"agent_cancel_{session.thread_id}")
    async def cancel_callback(interaction: discord.Interaction):
        session = interaction.client.active_agent_sessions.get(interaction.channel_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.status = "completed"
        
        for child in view.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=view)
        except Exception:
            pass
        
        await session.finalize_report(interaction.client)
        
    cancel_btn.callback = cancel_callback
    view.add_item(cancel_btn)
    
    question_chunks = [question[i:i+1900] for i in range(0, len(question), 1900)] if question else [""]
    
    for chunk in question_chunks[:-1]:
        await session.channel.send(content=f"💬 {chunk}")
        
    last_chunk_content = f"💬 **Inquiry**\n----------------------------------------\n{question_chunks[-1]}"
    await session.channel.send(content=last_chunk_content, view=view)
    
    return "PAUSED: Awaiting user text reply or component choice dropdown/button interaction..."


async def tool_compare_user_activity(bot, session, user_id_1: int, user_id_2: int, timeframe_days: int) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    member1 = guild.get_member(user_id_1) or await guild.fetch_member(user_id_1)
    member2 = guild.get_member(user_id_2) or await guild.fetch_member(user_id_2)
    
    if not member1 or not member2:
        return f"[Error: One or both user IDs ({user_id_1}, {user_id_2}) could not be resolved in guild '{guild.name}']"
        
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=timeframe_days)
    
    m1_count = 0
    m2_count = 0
    scanned_channels = 0
    
    channels_to_scan = [ch for ch in guild.text_channels if ch.permissions_for(guild.me).read_message_history]
    
    for channel in channels_to_scan[:8]:
        try:
            user_perms = channel.permissions_for(user_member)
            if not user_perms.read_messages or not user_perms.read_message_history:
                continue
                
            async for msg in channel.history(limit=100, after=cutoff):
                if msg.author.id == user_id_1:
                    m1_count += 1
                elif msg.author.id == user_id_2:
                    m2_count += 1
            scanned_channels += 1
        except Exception:
            continue
            
    profile1 = {
        "username": member1.name,
        "display_name": member1.display_name,
        "joined_at": member1.joined_at.strftime("%Y-%m-%d") if member1.joined_at else "Unknown",
        "created_at": member1.created_at.strftime("%Y-%m-%d"),
        "roles_count": len(member1.roles) - 1,
        "scanned_message_count": m1_count
    }
    
    profile2 = {
        "username": member2.name,
        "display_name": member2.display_name,
        "joined_at": member2.joined_at.strftime("%Y-%m-%d") if member2.joined_at else "Unknown",
        "created_at": member2.created_at.strftime("%Y-%m-%d"),
        "roles_count": len(member2.roles) - 1,
        "scanned_message_count": m2_count
    }
    
    more_active = ""
    if m1_count > m2_count:
        more_active = f"{member1.display_name} is more active in the scanned timeline."
    elif m2_count > m1_count:
        more_active = f"{member2.display_name} is more active in the scanned timeline."
    else:
        more_active = "Both users show equal activity metrics in scanned areas."
        
    comparison_report = {
        "timeframe_days": timeframe_days,
        "channels_scanned": scanned_channels,
        "user_1": profile1,
        "user_2": profile2,
        "result_summary": more_active
    }
    
    return json.dumps(comparison_report, indent=2)


async def tool_list_server_channels(bot, session) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    try:
        lines = [f"Available Channels inside '{guild.name}':"]
        for category in guild.categories:
            category_lines = []
            for ch in category.channels:
                user_perms = ch.permissions_for(user_member)
                if not user_perms.read_messages:
                    continue
                    
                ch_type = "Text" if isinstance(ch, discord.TextChannel) else "Voice" if isinstance(ch, discord.VoiceChannel) else "Forum" if isinstance(ch, discord.ForumChannel) else "Channel"
                category_lines.append(f"  - #{ch.name} ({ch_type}) [ID: {ch.id}]")
                
            if category_lines:
                lines.append(f"📁 Category: {category.name} [ID: {category.id}]")
                lines.extend(category_lines)
        
        orphan_channels = [ch for ch in guild.channels if ch.category is None]
        if orphan_channels:
            lines.append("📁 Uncategorized Channels:")
            for ch in orphan_channels:
                user_perms = ch.permissions_for(user_member)
                if not user_perms.read_messages:
                    continue
                ch_type = "Text" if isinstance(ch, discord.TextChannel) else "Voice" if isinstance(ch, discord.VoiceChannel) else "Forum" if isinstance(ch, discord.ForumChannel) else "Channel"
                lines.append(f"  - #{ch.name} ({ch_type}) [ID: {ch.id}]")
                
        return "\n".join(lines)
    except Exception as e:
        return f"[Failed to list guild channels: {e}]"


async def tool_get_channel_metadata(bot, session, channel_id: int) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    try:
        channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    except Exception:
        return f"[Error: Channel with ID {channel_id} was not found inside target guild '{guild.name}']"

    if not channel:
        return f"[Error: Channel with ID {channel_id} was not found inside target guild '{guild.name}']"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    user_perms = channel.permissions_for(user_member)
    if not user_perms.read_messages:
        return f"[Error: Access Denied. You do not have permissions to view this target channel's parameters.]"
        
    try:
        topic = getattr(channel, 'topic', 'No topic defined')
        nsfw = getattr(channel, 'nsfw', False)
        slowmode = getattr(channel, 'slowmode_delay', 0)
        category_name = channel.category.name if channel.category else "Uncategorized"
        
        metadata = {
            "channel_id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
            "category": category_name,
            "topic": topic,
            "nsfw": nsfw,
            "slowmode_delay_seconds": slowmode,
            "created_at": channel.created_at.strftime("%Y-%m-%d %H:%M:%S")
        }
        return json.dumps(metadata, indent=2)
    except Exception as e:
        return f"[Failed to fetch metadata for channel ID {channel_id}: {e}]"


async def tool_list_active_threads(bot, session) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    try:
        lines = [f"Active Threads inside '{guild.name}':"]
        for thread in guild.threads:
            parent_channel = thread.parent
            if parent_channel:
                user_perms = parent_channel.permissions_for(user_member)
                if not user_perms.read_messages:
                    continue
                    
                thread_type = "Public Thread" if thread.type == discord.ChannelType.public_thread else "Private Thread"
                lines.append(f"  - #{thread.name} ({thread_type}) [ID: {thread.id}] under channel #{parent_channel.name}")
                
        if len(lines) == 1:
            return "No active threads were found in the channels you have permission to view."
            
        return "\n".join(lines)
    except Exception as e:
        return f"[Failed to list active threads: {e}]"


async def tool_read_message_attachment(bot, session, channel_id: int, message_id: int, attachment_url: str) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    try:
        channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
    except Exception:
        return f"[Error: Channel ID {channel_id} not found.]"

    if not channel:
        return f"[Error: Channel ID {channel_id} not found.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied.]"
        
    user_perms = channel.permissions_for(user_member)
    if not user_perms.read_messages or not user_perms.read_message_history:
        return f"[Error: Access Denied. You cannot view this channel's files.]"
        
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession(headers=headers) as http_session:
            async with http_session.get(attachment_url, timeout=15) as response:
                if response.status != 200:
                    return f"[Error: File download failed with HTTP status {response.status}]"
                
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/" in content_type or "json" in content_type or attachment_url.lower().endswith(('.txt', '.log', '.json', '.yaml', '.yml', '.py', '.csv')):
                    text_data = await response.text()
                    return text_data[:6000]
                else:
                    return f"[File is of type {content_type} and cannot be processed as raw text.]"
    except Exception as e:
        return f"[Failed to parse attachment: {e}]"


async def tool_update_context_snapshot(bot, session, alias: str, updated_data: dict, notes: str) -> str:
    guild = session.channel.guild
    if hasattr(session, 'target_guild_id') and session.target_guild_id:
        guild = bot.get_guild(session.target_guild_id)
        
    if not guild:
        return "[Error: Target guild context is missing or unreachable.]"
        
    user_member = guild.get_member(session.user_id) or await guild.fetch_member(session.user_id)
    if not user_member:
        return f"[Error: Access Denied. You are not a member of target guild '{guild.name}']"
        
    import core.memory as memory
    all_contexts = await memory.fetch_all_contexts_for_user(bot, bot.brain_server_id, session.user_id)
    target_snapshot = next((c for c in all_contexts if c.get("alias") == alias), None)
    
    if not target_snapshot:
        return f"[Error: Saved context snapshot with alias '{alias}' was not found in your profile.]"
        
    type_name = target_snapshot.get("type", "Custom Snapshot")
    
    success = await memory.save_context_snippet(
        bot, bot.brain_server_id, session.user_id,
        alias, type_name, updated_data, notes
    )
    
    if success:
        if session.user_id in bot.user_context_cache:
            del bot.user_context_cache[session.user_id]
        return f"✅ Successfully updated saved context snapshot '{alias}' with fresh values and flushed local cache index."
    else:
        return f"[Error: Failed to write context update back to database.]"