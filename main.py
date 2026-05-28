import discord
import os
import asyncio
from dotenv import load_dotenv
from engine import GeminiEngine

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
MEMORY_CHANNEL_ID = int(os.getenv('MEMORY_CHANNEL_ID', 0))

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

client = discord.Client(intents=intents)
ai_engine = GeminiEngine()

@client.event
async def on_ready():
    print(f'✅ Logged in as {client.user}')
    print(f'🧠 Connected to memory channel: {MEMORY_CHANNEL_ID}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return
        
    is_dm = isinstance(message.channel, discord.DMChannel)
    if not client.user.mentioned_in(message) and not is_dm:
        return

    async with message.channel.typing():
        history = []
        async for msg in message.channel.history(limit=10):
            role = "model" if msg.author == client.user else "user"
            history.append({"role": role, "parts": [msg.clean_content]})
        
        history.reverse()

        response_text, reactions, components = await ai_engine.process_message(
            history=history, 
            user_message=message.clean_content
        )
        
        sent_message = await message.reply(response_text)
        
        for emoji in reactions:
            await sent_message.add_reaction(emoji)

client.run(TOKEN)