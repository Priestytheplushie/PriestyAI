import discord
import io
from collections import deque
from datetime import datetime, timezone

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
    """Finds or creates a channel category."""
    category = discord.utils.get(guild.categories, name=name)
    if not category:
        category = await guild.create_category(name)
    return category

async def get_or_create_channel(guild: discord.Guild, category_name: str, channel_name: str) -> discord.TextChannel:
    """Finds or creates a channel inside a specific category."""
    category = await get_or_create_category(guild, category_name)
    channel = discord.utils.get(category.text_channels, name=channel_name)
    if not channel:
        channel = await guild.create_text_channel(channel_name, category=category)
    return channel

async def save_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel_name = f"{user.name}-memory".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🧠 User Memories", channel_name)
    await channel.send(fact)
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
        await channel.send(f"{description}: {url}")
    return True

async def save_image_bytes_fact(client: discord.Client, brain_server_id: int, user: discord.User | discord.Member, description: str, img_bytes: bytes, filename: str) -> bool:
    """Saves AI generated image bytes directly to a user's memory channel."""
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel_name = f"{user.name}-memory".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🧠 User Memories", channel_name)
    
    file = discord.File(fp=io.BytesIO(img_bytes), filename=filename)
    msg = await channel.send(content=f"**AI Generated Image Saved: {description}**", file=file)
    if msg.attachments:
        url = msg.attachments[0].url
        await channel.send(f"{description}: {url}")
    return True

async def save_server_fact(client: discord.Client, brain_server_id: int, server: discord.Guild, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild or not server: return False
    channel_name = f"{server.name}-lore".lower().replace(" ", "-")
    channel = await get_or_create_channel(guild, "🌍 Server Lore", channel_name)
    await channel.send(fact)
    return True

async def save_global_fact(client: discord.Client, brain_server_id: int, fact: str) -> bool:
    guild = client.get_guild(brain_server_id)
    if not guild: return False
    channel = await get_or_create_channel(guild, "🌐 Global Database", "global-memory")
    await channel.send(fact)
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