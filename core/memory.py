
import discord
import io
import json
import logging
import re
from collections import deque
from datetime import datetime, timezone

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


async def get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if not category:
        category = await guild.create_category(name)
    return category

async def get_or_create_channel(guild: discord.Guild, category_name: str, channel_name: str) -> discord.TextChannel:
    category = await get_or_create_category(guild, category_name)
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel:
        channel = await guild.create_text_channel(channel_name, category=category)
    return channel


def should_preserve_message(message: discord.Message) -> bool:
    if message.attachments:
        return True
    
    content = message.content
    if content.startswith("**Image Upload:") or content.startswith("**AI Generated Image Saved:"):
        return True
    if "http" in content and (".png" in content or ".jpg" in content or "discordapp" in content):
        return True
    return False


async def consolidate_memories_if_needed(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str, threshold: int = 25):
    guild = client.get_guild(brain_server_id)
    if not guild:
        return
        
    category = discord.utils.get(guild.categories, name=category_name)
    if not category:
        return
        
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel:
        return

    messages = []
    async for msg in channel.history(limit=100):
        messages.append(msg)

    text_memories = [m for m in messages if not should_preserve_message(m)]
    
    if len(text_memories) < threshold:
        return

    logger.info(f"Triggering memory consolidation for #{channel_name} (found {len(text_memories)} text facts)")

    text_memories.reverse()
    raw_facts = [msg.content for msg in text_memories if msg.content.strip()]
    
    if not raw_facts:
        return

    facts_input = "\n".join(raw_facts)

    prompt = (
        "You are an active memory consolidation assistant for a Discord companion bot.\n"
        "Your task is to review the following chronological list of saved memories, facts, and observations "
        "about a user or server, and consolidate them into a clean, summarized list.\n\n"
        "Rules:\n"
        "1. Eliminate exact or semantic duplicates.\n"
        "2. Resolve any direct contradictions by prioritizing information that appears later (as it is more recent).\n"
        "3. Remove highly temporary, trivial, or fleeting notes that no longer have long-term value.\n"
        "4. Output the consolidated facts as concise, individual lines of text.\n"
        "5. Do NOT write any conversational intro, outro, headers, or markdown bullet points (like * or -). "
        "Just output each consolidated fact as a plain line of text.\n\n"
        f"Raw Memories to Consolidate:\n{facts_input}"
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

        await channel.purge(limit=100, check=lambda m: not should_preserve_message(m))

        lines = [line.strip().lstrip("*-• ").strip() for line in consolidated_text.split("\n") if line.strip()]
        for line in lines:
            if line:
                await channel.send(line)
        
        logger.info(f"Memory consolidation completed successfully for #{channel_name}")

    except Exception as e:
        logger.error(f"Failed to consolidate memories for #{channel_name}: {e}")


async def save_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel_name = f"{user.name}-memory".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🧠 User Memories", channel_name)
    await channel.send(fact)
    
    await consolidate_memories_if_needed(client, brain_server_id, "🧠 User Memories", channel_name)
    return True

async def save_image_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, description: str, attachment: discord.Attachment) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel_name = f"{user.name}-memory".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🧠 User Memories", channel_name)
    
    img_bytes = await attachment.read()
    file = discord.File(fp=io.BytesIO(img_bytes), filename=attachment.filename)
    
    msg = await channel.send(content=f"**Image Upload: {description}**", file=file)
    if msg.attachments:
        url = msg.attachments[0].url
        await msg.edit(content=f"**Image Upload: {description}**\n{url}")
    return True

async def save_image_bytes_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, description: str, img_bytes: bytes, filename: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel_name = f"{user.name}-memory".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🧠 User Memories", channel_name)
    
    file = discord.File(fp=io.BytesIO(img_bytes), filename=filename)
    msg = await channel.send(content=f"**AI Generated Image Saved: {description}**", file=file)
    if msg.attachments:
        url = msg.attachments[0].url
        await msg.edit(content=f"**AI Generated Image Saved: {description}**\n{url}")
    return True

async def save_server_fact(client: discord.Client, brain_server_id: int, server: discord.Guild, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild or not server: return False
    channel_name = f"{server.name}-lore".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🌍 Server Lore", channel_name)
    await channel.send(fact)
    
    await consolidate_memories_if_needed(client, brain_server_id, "🌍 Server Lore", channel_name)
    return True

async def save_global_fact(client: discord.Client, brain_server_id: int, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel = await get_or_create_channel(guild, "🌐 Global Database", "global-memory")
    await channel.send(fact)
    
    await consolidate_memories_if_needed(client, brain_server_id, "🌐 Global Database", "global-memory")
    return True

async def forget_fact(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    category = discord.utils.get(guild.categories, name=category_name)
    if not category: return False
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel: return False
    
    async for msg in channel.history(limit=100):
        if fact.lower().strip() in msg.content.lower().strip():
            await msg.delete()
            return True
    return False

async def fetch_memory_block(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str) -> str:
    guild = client.get_guild(brain_server_id)
    if not guild: return ""
    
    category = discord.utils.get(guild.categories, name=category_name)
    if not category: return ""
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel: return ""
    
    facts = []
    async for msg in channel.history(limit=30):
        facts.append(msg.content)
        
    facts.reverse()
    if not facts: return ""
    return "\n".join([f"{f}" for f in facts])

async def save_config(client: discord.Client, brain_server_id: int, target_id: int, is_dm: bool, config_dict: dict) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    
    prefix = "user" if is_dm else "channel"
    channel_name = f"{prefix}-{target_id}-config"
    
    channel = await get_or_create_channel(guild, "⚙️ Configurations", channel_name)
    
    await channel.purge(limit=10)
    
    config_str = json.dumps(config_dict, indent=2)
    await channel.send(f"```json\n{config_str}\n```")
    return True

async def load_config(client: discord.Client, brain_server_id: int, target_id: int, is_dm: bool) -> dict:
    guild = client.get_guild(brain_server_id)
    if not guild: return None
    
    category = discord.utils.get(guild.categories, name="⚙️ Configurations")
    if not category: return None
    
    prefix = "user" if is_dm else "channel"
    channel_name = f"{prefix}-{target_id}-config"
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel: return None
    
    async for msg in channel.history(limit=5):
        if "```json" in msg.content:
            match = re.search(r'```json\s*(.*?)\s*```', msg.content, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except Exception:
                    pass
    return None