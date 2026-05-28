import discord
import io

async def get_or_create_category(guild: discord.Guild, name: str) -> discord.CategoryChannel:
    category = discord.utils.get(guild.categories, name=name)
    if not category:
        category = await guild.create_category(name)
    return category

async def get_or_create_channel(guild: discord.Guild, category_name: str, channel_name: str) -> discord.TextChannel:
    category = await get_or_create_category(guild, category_name)
    channel = discord.utils.get(category.channels, name=channel_name)
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
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if not channel: return False
    
    async for msg in channel.history(limit=100):
        if fact.lower().strip() in msg.content.lower().strip():
            await msg.delete()
            return True
    return False

async def fetch_memory_block(client: discord.Client, brain_server_id: int, category_name: str, channel_name: str) -> str:
    guild = client.get_guild(brain_server_id)
    if not guild: return ""
    
    channel = discord.utils.get(guild.text_channels, name=channel_name)
    if not channel: return ""
    
    facts = []
    async for msg in channel.history(limit=30):
        facts.append(msg.content)
        
    facts.reverse()
    if not facts: return ""
    return "\n".join([f"- {f}" for f in facts])