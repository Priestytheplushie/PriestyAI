
import discord
import io
import json
import logging
import re
import numpy as np
from collections import deque
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger("Memory")


class ChatHistoryTracker:
    def __init__(self, limit: int = 30):
        self.limit = limit
        self.histories = {} 

    def add_message(self, message: discord.Message):
        channel_id = message.channel.id
        if channel_id not in self.histories:
            self.histories[channel_id] = deque(maxlen=self.limit)
        
        if isinstance(message.author, discord.Member) and message.author.nick:
            display_name = message.author.nick
        else:
            display_name = message.author.display_name or message.author.name

        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        
        message_struct = {
            "timestamp": timestamp,
            "username": message.author.name,
            "display_name": display_name,
            "content": message.clean_content,
            "has_attachments": len(message.attachments) > 0
        }
        
        self.histories[channel_id].append(message_struct)

    def add_system_action(self, channel_id: int, action_text: str):
        if channel_id not in self.histories:
            self.histories[channel_id] = deque(maxlen=self.limit)
            
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        message_struct = {
            "timestamp": timestamp,
            "username": "SYSTEM",
            "display_name": "SYSTEM",
            "content": f"[ACTION RECORDED: {action_text}]",
            "has_attachments": False
        }
        self.histories[channel_id].append(message_struct)

    def get_formatted_history(self, channel_id: int) -> str:
        if channel_id not in self.histories or not self.histories[channel_id]:
            return "No previous conversations recorded in this channel."
        
        lines = []
        for msg in self.histories[channel_id]:
            attachment_note = " [Sent an Attachment]" if msg['has_attachments'] else ""
            lines.append(f"[{msg['timestamp']}] {msg['display_name']} (@{msg['username']}): {msg['content']}{attachment_note}")
            
        return "\n".join(lines)



async def get_or_create_forum_channel(guild: discord.Guild, name: str) -> Optional[discord.ForumChannel]:
    forum = discord.utils.get(guild.channels, name=name, type=discord.ChannelType.forum)
    if not forum:
        try:
            forum = await guild.create_forum(name=name)
            logger.info(f"Database Forum Channel '#{name}' created in Brain Server '{guild.name}' (ID: {guild.id})")
        except Exception as e:
            logger.error(f"Failed to instantiate Forum Channel '{name}': {e}")
            return None
    return forum


async def get_or_create_db_thread(guild: discord.Guild, forum_name: str, thread_name: str, initial_content: str = "Database Thread") -> Optional[discord.Thread]:
    forum = await get_or_create_forum_channel(guild, forum_name)
    if not forum:
        return None
        
    thread = discord.utils.get(forum.threads, name=str(thread_name))
    
    if not thread:
        try:
            async for arch_thread in forum.archived_threads(limit=100):
                if arch_thread.name == str(thread_name):
                    thread = arch_thread
                    await thread.edit(archived=False)
                    logger.info(f"Unarchived Database Thread '{thread_name}' in forum '#{forum_name}'")
                    break
        except Exception as e:
            logger.warning(f"Error fetching archived threads in forum '#{forum_name}': {e}")
            
    if not thread:
        try:
            thread_with_msg = await forum.create_thread(
                name=str(thread_name),
                content=initial_content
            )
            thread = thread_with_msg.thread
            logger.info(f"Created fresh Database Thread '{thread_name}' in forum '#{forum_name}'")
        except Exception as e:
            logger.error(f"Failed to create Database Thread '{thread_name}' in forum '#{forum_name}': {e}")
            return None
            
    return thread


def should_preserve_message(message: discord.Message) -> bool:
    if message.attachments:
        return True
    
    content = message.content
    if content.startswith("[FACT_VEC]") or content.startswith("[VISUAL MEMORY]"):
        return True
    if content.startswith("**Image Upload:") or content.startswith("**AI Generated Image Saved:"):
        return True
    if "http" in content and (".png" in content or ".jpg" in content or "discordapp" in content):
        return True
    return bool(re.search(r'```json', content))


async def consolidate_memories_if_needed(client: discord.Client, brain_server_id: int, forum_name: str, thread_name: str, threshold: int = 35):
    guild = client.get_guild(brain_server_id)
    if not guild:
        return
        
    thread = await get_or_create_db_thread(guild, forum_name, thread_name)
    if not thread:
        return

    messages = []
    async for msg in thread.history(limit=100):
        messages.append(msg)

    text_memories = [m for m in messages if not should_preserve_message(m)]
    
    if len(text_memories) < threshold:
        return

    logger.info(f"Triggering structured memory consolidation for thread '{thread_name}' in '#{forum_name}'")

    text_memories.reverse()
    raw_facts = [msg.content for msg in text_memories if msg.content.strip()]
    
    if not raw_facts:
        return

    facts_input = "\n".join(raw_facts)

    prompt = (
        "You are an active memory consolidation assistant for a Discord companion bot.\n"
        "Your task is to review the following chronological list of saved memories, facts, and observations "
        "about a user or server, and consolidate them into a structured, categorized markdown schema.\n\n"
        "Eliminate duplicate facts, resolve direct contradictions by prioritizing the information that appeared later, "
        "and clean up stale or minor temporary chatter. Group the consolidated facts strictly under these headings:\n"
        "### 🧠 PROFILE & IDENTITY\n"
        "### 💻 TECHNICAL ENVIRONMENT\n"
        "### ✨ RELATIONSHIP & VIBE\n\n"
        "Do not write any conversational intro or outro. Output only the structured headings with plain consolidated bullet points.\n\n"
        f"Raw Memories:\n{facts_input}"
    )

    try:
        response = await client.chat_handler.client.aio.models.generate_content(
            model=client.chat_handler.premium_model,
            contents=prompt
        )

        consolidated_text = ""
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            consolidated_text = "".join(
                part.text for part in response.candidates[0].content.parts if getattr(part, 'text', None)
            )

        if not consolidated_text.strip():
            logger.warning("Consolidation prompt returned empty results. Aborting rewrite to prevent loss.")
            return

        for msg in text_memories:
            try:
                await msg.delete()
            except Exception:
                pass

        await thread.send(consolidated_text.strip())
        logger.info(f"Structured memory consolidation completed successfully for thread '{thread_name}'")

    except Exception as e:
        logger.error(f"Failed to consolidate memories for thread '{thread_name}': {e}")



async def generate_embedding(client: discord.Client, text: str) -> List[float]:
    try:
        result = await client.chat_handler.client.aio.models.embed_content(
            model="text-embedding-004",
            contents=text
        )
        if result and result.embeddings:
            return result.embeddings[0].values
    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
    return [0.0] * 768



async def save_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, fact: str, category: str = "PROFILE & IDENTITY", score: int = 10) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild, 
        forum_name="user-memories", 
        thread_name=str(user.id),
        initial_content=f"Memory ledger for user {user.display_name} (<@{user.id}>)"
    )
    if not thread:
        return False
        
    vector = await generate_embedding(client, fact)
    payload = {
        "fact": fact,
        "category": category,
        "score": score,
        "vector": vector,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    msg_content = f"[FACT_VEC] {json.dumps(payload, ensure_ascii=False)}"
    await thread.send(msg_content)
    await consolidate_memories_if_needed(client, brain_server_id, "user-memories", str(user.id))
    return True


async def save_visual_memory(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, prompt: str, style: str, ratio: str, seed: int, cdn_url: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild:
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild,
        forum_name="user-memories",
        thread_name=str(user.id),
        initial_content=f"Memory ledger for user {user.display_name} (<@{user.id}>)"
    )
    if not thread:
        return False
        
    payload = {
        "prompt": prompt,
        "style": style,
        "ratio": ratio,
        "seed": seed,
        "cdn_url": cdn_url,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    msg_content = f"[VISUAL MEMORY] ```json\n{json.dumps(payload, indent=2)}\n```"
    await thread.send(msg_content)
    return True


async def fetch_recent_visual_memories(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, limit: int = 5) -> List[dict]:
    guild = client.get_guild(brain_server_id)
    if not guild:
        return []
        
    forum = discord.utils.get(guild.channels, name="user-memories", type=discord.ChannelType.forum)
    if not forum:
        return []
        
    thread = discord.utils.get(forum.threads, name=str(user.id))
    if not thread:
        try:
            async for arch_thread in forum.archived_threads(limit=100):
                if arch_thread.name == str(user.id):
                    thread = arch_thread
                    break
        except Exception:
            pass
            
    if not thread:
        return []
        
    memories = []
    async for msg in thread.history(limit=50):
        if "[VISUAL MEMORY]" in msg.content:
            match = re.search(r'```json\s*(.*?)\s*```', msg.content, flags=re.DOTALL)
            if match:
                try:
                    mem_data = json.loads(match.group(1))
                    memories.append(mem_data)
                    if len(memories) >= limit:
                        break
                except Exception:
                    pass
    return memories


async def save_image_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, description: str, attachment: discord.Attachment) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild, 
        forum_name="user-memories", 
        thread_name=str(user.id),
        initial_content=f"Memory ledger for user {user.display_name} (<@{user.id}>)"
    )
    if not thread:
        return False
    
    img_bytes = await attachment.read()
    file = discord.File(fp=io.BytesIO(img_bytes), filename=attachment.filename)
    
    msg = await thread.send(content=f"**Image Upload: {description}**", file=file)
    if msg.attachments:
        url = msg.attachments[0].url
        await msg.edit(content=f"**Image Upload: {description}**\n{url}")
    return True


async def save_image_bytes_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, description: str, img_bytes: bytes, filename: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild, 
        forum_name="user-memories", 
        thread_name=str(user.id),
        initial_content=f"Memory ledger for user {user.display_name} (<@{user.id}>)"
    )
    if not thread:
        return False
    
    file = discord.File(fp=io.BytesIO(img_bytes), filename=filename)
    msg = await thread.send(content=f"**AI Generated Image Saved: {description}**", file=file)
    if msg.attachments:
        url = msg.attachments[0].url
        await msg.edit(content=f"**AI Generated Image Saved: {description}**\n{url}")
    return True


async def save_server_fact(client: discord.Client, brain_server_id: int, server: discord.Guild, fact: str, category: str = "PROFILE & IDENTITY", score: int = 10) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild or not server: 
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild, 
        forum_name="server-lore", 
        thread_name=str(server.id),
        initial_content=f"Lore index database for server '{server.name}'"
    )
    if not thread:
        return False
        
    vector = await generate_embedding(client, fact)
    payload = {
        "fact": fact,
        "category": category,
        "score": score,
        "vector": vector,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    msg_content = f"[FACT_VEC] {json.dumps(payload, ensure_ascii=False)}"
    await thread.send(msg_content)
    await consolidate_memories_if_needed(client, brain_server_id, "server-lore", str(server.id))
    return True


async def save_global_fact(client: discord.Client, brain_server_id: int, fact: str, category: str = "PROFILE & IDENTITY", score: int = 10) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild, 
        forum_name="global-memory", 
        thread_name="global-database",
        initial_content="Global shared database knowledge base."
    )
    if not thread:
        return False
        
    vector = await generate_embedding(client, fact)
    payload = {
        "fact": fact,
        "category": category,
        "score": score,
        "vector": vector,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    msg_content = f"[FACT_VEC] {json.dumps(payload, ensure_ascii=False)}"
    await thread.send(msg_content)
    await consolidate_memories_if_needed(client, brain_server_id, "global-memory", "global-database")
    return True


async def forget_fact(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
        
    forum_map = {
        "🧠 User Memories": "user-memories",
        "🌍 Server Lore": "server-lore",
        "🌐 Global Database": "global-memory"
    }
    forum_name = forum_map.get(category_name, "user-memories")
    
    thread_name = re.sub(r'[^a-zA-Z0-9\-]', '', channel_name).replace("-memory", "").replace("-lore", "")
    
    thread = await get_or_create_db_thread(guild, forum_name, thread_name)
    if not thread:
        return False
    
    async for msg in thread.history(limit=100):
        if msg.content.startswith("[FACT_VEC]"):
            try:
                payload = json.loads(msg.content[11:])
                if fact.lower().strip() in payload.get("fact", "").lower().strip():
                    await msg.delete()
                    return True
            except Exception:
                pass
        elif fact.lower().strip() in msg.content.lower().strip():
            await msg.delete()
            return True
    return False


async def fetch_memory_block(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str, query_text: str = "") -> str:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return ""
        
    forum_map = {
        "🧠 User Memories": "user-memories",
        "🌍 Server Lore": "server-lore",
        "🌐 Global Database": "global-memory"
    }
    forum_name = forum_map.get(category_name, "user-memories")
    
    thread_name = re.sub(r'[^a-zA-Z0-9\-]', '', channel_name).replace("-memory", "").replace("-lore", "")
    
    thread = await get_or_create_db_thread(guild, forum_name, thread_name)
    if not thread:
        return ""
    
    raw_messages = []
    async for msg in thread.history(limit=100):
        if msg.id == thread.id:
            continue
        raw_messages.append(msg)
        
    if not raw_messages:
        return ""

    structured_memories = []
    legacy_text_blocks = []

    for msg in raw_messages:
        content = msg.content.strip()
        if content.startswith("[FACT_VEC]"):
            try:
                payload = json.loads(content[11:])
                if "fact" in payload and "vector" in payload:
                    structured_memories.append(payload)
            except Exception:
                pass
        elif not should_preserve_message(msg):
            legacy_text_blocks.append(content)

    if query_text.strip() and (structured_memories or legacy_text_blocks):
        try:
            query_vector = np.array(await generate_embedding(client, query_text))
            query_norm = np.linalg.norm(query_vector)
            
            for text_block in legacy_text_blocks:
                block_vec = await generate_embedding(client, text_block)
                structured_memories.append({
                    "fact": text_block,
                    "category": "PROFILE & IDENTITY",
                    "score": 10,
                    "vector": block_vec
                })

            scored_memories = []
            for mem in structured_memories:
                fact_vector = np.array(mem["vector"])
                fact_norm = np.linalg.norm(fact_vector)
                
                if query_norm == 0.0 or fact_norm == 0.0:
                    similarity = 0.0
                else:
                    similarity = float(np.dot(query_vector, fact_vector) / (query_norm * fact_norm))
                
                scored_memories.append((similarity, mem))

            scored_memories.sort(key=lambda x: x[0], reverse=True)
            top_memories = scored_memories[:12]
            
            categories = {
                "PROFILE & IDENTITY": [],
                "TECHNICAL ENVIRONMENT": [],
                "RELATIONSHIP & VIBE": []
            }
            
            for sim, mem in top_memories:
                cat = mem.get("category", "PROFILE & IDENTITY")
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(f"• {mem['fact']}")
                
            formatted_blocks = []
            for cat_title, items in categories.items():
                if items:
                    formatted_blocks.append(f"### {cat_title}\n" + "\n".join(items))
                    
            return "\n\n".join(formatted_blocks).strip()
            
        except Exception as sim_err:
            logger.error(f"Semantic similarity retrieval failed: {sim_err}. Falling back to standard list dump...")

    fallback_lines = []
    for mem in structured_memories:
        fallback_lines.append(f"- {mem['fact']}")
    for text_block in legacy_text_blocks:
        fallback_lines.append(text_block)
        
    return "\n\n".join(fallback_lines).strip()


async def save_config(client: discord.Client, brain_server_id: int, target_id: int, is_dm: bool, config_dict: dict) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
    
    thread_name = f"config-{target_id}"
    thread = await get_or_create_db_thread(
        guild=guild,
        forum_name="configurations",
        thread_name=thread_name,
        initial_content=f"Configuration parameters for Target ID {target_id}"
    )
    if not thread:
        return False
    
    async for msg in thread.history(limit=10):
        if msg.id != thread.id:
            try:
                await msg.delete()
            except Exception:
                pass
    
    config_str = json.dumps(config_dict, indent=2)
    await thread.send(f"```json\n{config_str}\n```")
    return True


async def load_config(client: discord.Client, brain_server_id: int, target_id: int, is_dm: bool) -> Optional[dict]:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return None
    
    forum = discord.utils.get(guild.channels, name="configurations", type=discord.ChannelType.forum)
    if not forum:
        return None
        
    thread_name = f"config-{target_id}"
    thread = discord.utils.get(forum.threads, name=thread_name)
    
    if not thread:
        try:
            async for arch_thread in forum.archived_threads(limit=100):
                if arch_thread.name == thread_name:
                    thread = arch_thread
                    break
        except Exception:
            pass
            
    if not thread:
        return None
    
    async for msg in thread.history(limit=5):
        if "```json" in msg.content:
            match = re.search(r'```json\s*(.*?)\s*```', msg.content, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass
    return None



async def save_context_snippet(client, brain_server_id: int, user_id: int, alias: str, type_name: str, data_payload: dict, notes: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild:
        return False
        
    thread = await get_or_create_db_thread(
        guild=guild,
        forum_name="context-snippets",
        thread_name=str(user_id),
        initial_content=f"Context snapshot ledger for user <@{user_id}>"
    )
    if not thread:
        return False
    
    payload = {"alias": alias, "type": type_name, "data": data_payload, "notes": notes}
    msg_content = f"```json\n{json.dumps(payload, indent=2)}\n```"
    
    async for msg in thread.history(limit=50):
        if f'"alias": "{alias}"' in msg.content:
            await msg.edit(content=msg_content)
            return True
            
    await thread.send(content=msg_content)
    return True


async def fetch_all_contexts_for_user(client, brain_server_id: int, user_id: int) -> list:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return []
        
    forum = discord.utils.get(guild.channels, name="context-snippets", type=discord.ChannelType.forum)
    if not forum: 
        return []
        
    thread = discord.utils.get(forum.threads, name=str(user_id))
    if not thread:
        try:
            async for arch_thread in forum.archived_threads(limit=100):
                if arch_thread.name == str(user_id):
                    thread = arch_thread
                    break
        except Exception:
            pass
            
    if not thread: 
        return []
    
    snippets = []
    async for msg in thread.history(limit=50):
        match = re.search(r'```json\s*(.*?)\s*```', msg.content, flags=re.DOTALL)
        if match: 
            try:
                snippets.append(json.loads(match.group(1)))
            except Exception:
                pass
    return snippets


async def save_news_state(client: discord.Client, brain_server_id: int, guild_id: int, state_dict: dict) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return False
    
    thread_name = f"news-state-{guild_id}"
    thread = await get_or_create_db_thread(
        guild=guild,
        forum_name="configurations",
        thread_name=thread_name,
        initial_content=f"Persistent state tracking for Guild {guild_id} Server News"
    )
    if not thread:
        return False
    
    async for msg in thread.history(limit=10):
        if msg.id != thread.id:
            try:
                await msg.delete()
            except Exception:
                pass
    
    state_str = json.dumps(state_dict, indent=2)
    await thread.send(f"```json\n{state_str}\n```")
    return True


async def load_news_state(client: discord.Client, brain_server_id: int, guild_id: int) -> Optional[dict]:
    guild = client.get_guild(brain_server_id)
    if not guild: 
        return None
    
    forum = discord.utils.get(guild.channels, name="configurations", type=discord.ChannelType.forum)
    if not forum:
        return None
        
    thread_name = f"news-state-{guild_id}"
    thread = discord.utils.get(forum.threads, name=thread_name)
    
    if not thread:
        try:
            async for arch_thread in forum.archived_threads(limit=100):
                if arch_thread.name == thread_name:
                    thread = arch_thread
                    break
        except Exception:
            pass
            
    if not thread:
        return None
    
    async for msg in thread.history(limit=5):
        if "```json" in msg.content:
            match = re.search(r'```json\s*(.*?)\s*```', msg.content, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass
    return None