import discord
import os
import asyncio
import random
from datetime import datetime
from dotenv import load_dotenv
from google.genai import types 
from components import DynamicAIView
from engine import GeminiEngine
import memory 

from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
BRAIN_SERVER_ID = int(os.getenv('BRAIN_SERVER_ID', 0))

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True 

client = discord.Client(intents=intents)
ai_engine = GeminiEngine()

class KeepAliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        self.wfile.write(b"PriestyAI is alive and healthy!")

    def log_message(self, format, *args):
        return

def run_keep_alive_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), KeepAliveHandler)
    print(f"📡 Keep-alive web server listening on port {port}")
    server.serve_forever()


def split_message(text: str, limit: int = 1900) -> list[str]:
    if len(text) <= limit:
        return [text]
        
    chunks = []
    lines = text.split("\n")
    current_chunk = ""
    in_code_block = False
    
    for line in lines:
        if "```" in line:
            in_code_block = not in_code_block
            
        if len(current_chunk) + len(line) + 1 > limit:
            if in_code_block:
                current_chunk += "\n```"
            chunks.append(current_chunk.strip())
            
            if in_code_block:
                current_chunk = "```\n" + line
            else:
                current_chunk = line
        else:
            current_chunk += "\n" + line if current_chunk else line
            
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks

def count_turns(history: list) -> int:
    if not history:
        return 0
    turns = 1
    last_role = history[0]["role"]
    for msg in history[1:]:
        if msg["role"] != last_role:
            turns += 1
            last_role = msg["role"]
    return turns

async def handle_ai_response(channel, history, user_content, trigger_author, attachments_data=None, context_data="", trigger_message=None, system_note=None, reply_to_message=None):
    
    await asyncio.sleep(random.uniform(1.0, 2.5))
    
    async with channel.typing():
        await asyncio.sleep(random.uniform(0.5, 2.0))
        
        try:
            clean_text, actions = await ai_engine.process_message(
                history=history, 
                user_message=user_content,
                attachments_data=attachments_data,
                context_data=context_data,
                system_note=system_note
            )
        except Exception as e:
            print(f"❌ Critical Gemini Error: {e}")
            clean_text = "my brain is literally fried right now. give me a second and ask again lol"
            actions = {
                "reactions_self": ["😵"], "reactions_user": [], "buttons": [],
                "thread_title": None, "close_thread": False, "follow_up": False,
                "learn_facts": [], "forget_facts": [], "learn_images": [],
                "learn_server": [], "forget_server": [],
                "learn_global": [], "forget_global": [],
                "modal_buttons": [], "string_selects": [], "user_selects": [], "role_selects": []
            }
        
        for fact in actions["learn_facts"]:
            await memory.save_fact(client, BRAIN_SERVER_ID, trigger_author, fact)
            print(f"🧠 Learned (User) about {trigger_author.name}: {fact}")
            
        for fact in actions["forget_facts"]:
            channel_name = f"{trigger_author.name}-memory".lower().replace(" ", "-")
            await memory.forget_fact(client, BRAIN_SERVER_ID, "🧠 User Memories", channel_name, fact)
            print(f"🧠 Forgot (User) about {trigger_author.name}: {fact}")
            
        if actions["learn_images"] and trigger_message and trigger_message.attachments:
            image_att = trigger_message.attachments[0]
            for desc in actions["learn_images"]:
                await memory.save_image_fact(client, BRAIN_SERVER_ID, trigger_author, desc, image_att)
                print(f"📸 Learned Image about {trigger_author.name}: {desc}")
        
        is_dm = isinstance(channel, discord.DMChannel)
        if not is_dm and hasattr(channel, "guild"):
            for fact in actions["learn_server"]:
                await memory.save_server_fact(client, BRAIN_SERVER_ID, channel.guild, fact)
                print(f"🌍 Learned (Server) about {channel.guild.name}: {fact}")
                
            for fact in actions["forget_server"]:
                channel_name = f"{channel.guild.name}-lore".lower().replace(" ", "-")
                await memory.forget_fact(client, BRAIN_SERVER_ID, "🌍 Server Lore", channel_name, fact)
                print(f"🌍 Forgot (Server) about {channel.guild.name}: {fact}")

        for fact in actions["learn_global"]:
            await memory.save_global_fact(client, BRAIN_SERVER_ID, fact)
            print(f"🌐 Learned (Global): {fact}")
            
        for fact in actions["forget_global"]:
            await memory.forget_fact(client, BRAIN_SERVER_ID, "🌐 Global Database", "global-memory", fact)
            print(f"🌐 Forgot (Global): {fact}")
        
        view = None
        has_ui = any([actions["buttons"], actions["modal_buttons"], actions["string_selects"], actions["user_selects"], actions["role_selects"]])
        
        if has_ui:
            async def ui_interaction_callback(interaction: discord.Interaction, system_note_str: str):
                click_history = history + [{"role": "model", "content": clean_text}]
                
                target_channel = interaction.channel
                if interaction.guild:
                    thread = interaction.guild.get_thread(interaction.message.id)
                    if thread:
                        target_channel = thread
                
                await handle_ai_response(
                    channel=target_channel,
                    history=click_history,
                    user_content="",
                    trigger_author=interaction.user,
                    attachments_data=None,
                    context_data=context_data,
                    trigger_message=None, 
                    system_note=system_note_str
                )
                
            view = DynamicAIView(actions=actions, interaction_callback=ui_interaction_callback)

        message_chunks = split_message(clean_text)
        
        for idx, chunk in enumerate(message_chunks):
            chunk_view = view if idx == len(message_chunks) - 1 else None
            
            if reply_to_message and idx == 0:
                sent_message = await reply_to_message.reply(chunk, view=chunk_view)
            else:
                sent_message = await channel.send(chunk, view=chunk_view)
        
        for emoji in actions["reactions_self"]:
            try: await sent_message.add_reaction(emoji)
            except Exception: pass
                
        if trigger_message:
            for emoji in actions["reactions_user"]:
                try: await trigger_message.add_reaction(emoji)
                except Exception: pass
                
        if actions["thread_title"] and not is_dm:
            try:
                await sent_message.create_thread(name=actions["thread_title"][:100], auto_archive_duration=60)
            except Exception: pass
                
        if actions["close_thread"] and isinstance(channel, discord.Thread):
            try:
                await channel.send("🔒 *Thread closed by AI.*")
                await channel.edit(archived=True, locked=True) 
            except Exception: pass

        if actions["follow_up"] and not actions["close_thread"]:
            print(f"💬 {trigger_author.name}'s chat triggered a consecutive follow-up message.")
            follow_up_history = history + [{"role": "model", "content": clean_text}]
            
            await handle_ai_response(
                channel=channel,
                history=follow_up_history,
                user_content="",
                trigger_author=trigger_author,
                attachments_data=None,
                context_data=context_data,
                trigger_message=None,
                system_note="You chose to send a consecutive follow-up message. Continue your thought, add context, or send your secondary thought now."
            )

async def build_context_data(channel, author) -> str:
    is_dm = isinstance(channel, discord.DMChannel)
    is_thread = isinstance(channel, discord.Thread)
    
    context_lines = []
    current_time = datetime.now().strftime("%I:%M %p")
    current_day = datetime.now().strftime("%A, %B %d, %Y")
    context_lines.append(f"Current Date & Time: {current_day} at {current_time}")
    context_lines.append(f"Speaking to: {author.display_name} (To ping them, use <@{author.id}>)")
    
    if is_dm:
        context_lines.append("Environment: Private Direct Messages (DM).")
    else:
        guild = channel.guild if not is_thread else channel.parent.guild
        context_lines.append(f"Environment: Server '{guild.name}', Channel '#{channel.name}'")
        recent_channels = [c for c in guild.text_channels[:5]]
        channel_str = ", ".join([f"#{c.name} (<#{c.id}>)" for c in recent_channels])
        context_lines.append(f"Available Server Channels: {channel_str}")
        
        member = guild.get_member(author.id) if hasattr(guild, "get_member") else None
        if member:
            roles = [f"{r.name} (<@&{r.id}>)" for r in member.roles if r.name != "@everyone"]
            role_str = ", ".join(roles) if roles else "None"
            context_lines.append(f"User's Roles: {role_str}")

    if BRAIN_SERVER_ID:
        user_channel_name = f"{author.name}-memory".lower().replace(" ", "-")
        user_memory = await memory.fetch_memory_block(client, BRAIN_SERVER_ID, "🧠 User Memories", user_channel_name)
        if user_memory:
            context_lines.append(f"\n# YOUR RELATIONSHIP/MEMORY LOG ABOUT {author.display_name.upper()}\n{user_memory}")
            
        if not is_dm:
            guild = channel.guild if not is_thread else channel.parent.guild
            server_channel_name = f"{guild.name}-lore".lower().replace(" ", "-")
            server_memory = await memory.fetch_memory_block(client, BRAIN_SERVER_ID, "🌍 Server Lore", server_channel_name)
            if server_memory:
                context_lines.append(f"\n# SERVER LORE & STORIES FOR SERVER: '{guild.name.upper()}'\n{server_memory}")
                
        global_memory = await memory.fetch_memory_block(client, BRAIN_SERVER_ID, "🌐 Global Database", "global-memory")
        if global_memory:
            context_lines.append(f"\n# GLOBAL DATABASE & GENERAL KNOWLEDGE\n{global_memory}")

    return "\n".join(context_lines)

@client.event
async def on_ready():
    print(f'✅ Logged in as {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return
        
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_thread = isinstance(message.channel, discord.Thread)
    
    if not client.user.mentioned_in(message) and not is_dm and not is_thread:
        return

    attachments_data = []
    if message.attachments:
        for att in message.attachments:
            if att.content_type and att.content_type.startswith('image/'):
                img_bytes = await att.read()
                attachments_data.append({"bytes": img_bytes, "mime_type": att.content_type})

    context_data = await build_context_data(message.channel, message.author)

    raw_history = []
    async for msg in message.channel.history(limit=15):
        role = "model" if msg.author == client.user else "user"
        raw_history.append({"role": role, "content": msg.clean_content})
    
    raw_history.reverse() 
    
    if raw_history and raw_history[-1]["content"] == message.clean_content:
        raw_history.pop()

    if is_thread and len(raw_history) < 10:
        try:
            parent_msg = await message.channel.parent.fetch_message(message.channel.id)
            if parent_msg:
                exists = any(parent_msg.clean_content == h["content"] for h in raw_history)
                if not exists:
                    role = "model" if parent_msg.author == client.user else "user"
                    raw_history.insert(0, {"role": role, "content": parent_msg.clean_content})
        except Exception as e:
            print(f"Failed to fetch thread starter message: {e}")

    turns = count_turns(raw_history)
    system_note = None
    if turns >= 6 and not is_thread and not is_dm:
        system_note = "This conversation is getting long. Consider using [THREAD: topic]."

    await handle_ai_response(
        channel=message.channel,
        history=raw_history,
        user_content=message.clean_content,
        trigger_author=message.author,
        attachments_data=attachments_data,
        context_data=context_data,
        trigger_message=message, 
        system_note=system_note,
        reply_to_message=message if not is_thread else None
    )

@client.event
async def on_raw_reaction_add(payload):
    if payload.user_id == client.user.id:
        return
        
    channel = client.get_channel(payload.channel_id)
    if not channel:
        try: channel = await client.fetch_channel(payload.channel_id)
        except Exception: return
            
    try: message = await channel.fetch_message(payload.message_id)
    except Exception: return

    if message.author != client.user:
        return

    user = client.get_user(payload.user_id)
    if not user:
        try: user = await client.fetch_user(payload.user_id)
        except Exception: return

    context_data = await build_context_data(channel, user)

    raw_history = []
    async for msg in channel.history(limit=10):
        role = "model" if msg.author == client.user else "user"
        raw_history.append({"role": role, "content": msg.clean_content})
    raw_history.reverse()

    is_thread = isinstance(channel, discord.Thread)
    if is_thread and len(raw_history) < 10:
        try:
            parent_msg = await channel.parent.fetch_message(channel.id)
            if parent_msg:
                exists = any(parent_msg.clean_content == h["content"] for h in raw_history)
                if not exists:
                    role = "model" if parent_msg.author == client.user else "user"
                    raw_history.insert(0, {"role": role, "content": parent_msg.clean_content})
        except Exception as e:
            print(f"Failed to fetch thread starter message: {e}")

    system_note = f"The user {user.display_name} reacted with the emoji {payload.emoji} to your message: '{message.clean_content}'"

    await handle_ai_response(
        channel=channel,
        history=raw_history,
        user_content="",
        trigger_author=user,
        attachments_data=None,
        context_data=context_data,
        trigger_message=message,
        system_note=system_note,
        reply_to_message=message if not is_thread else None
    )

if __name__ == "__main__":
    threading.Thread(target=run_keep_alive_server, daemon=True).start()
    
    client.run(TOKEN)