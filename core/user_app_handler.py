
import discord
from discord import app_commands
import asyncio
import time
import re
import random
import uuid
import logging

logger = logging.getLogger("UserAppHandler")

USER_APP_SESSIONS = {}

class UserAppSession:
    def __init__(self, channel_id: int, config: dict):
        self.channel_id = channel_id
        self.config = config
        self.history = []

def get_chat_placeholders():
    return random.choice([
        "e.g., 'Help me write a Python script for a discord bot'...",
        "e.g., 'Draw a cyberpunk city in the rain'...",
        "e.g., 'Explain quantum physics like I am five'...",
        "e.g., 'What are the latest news headlines today?'...",
        "e.g., 'Let's play a text adventure game'..."
    ])


class UserAppReplyView(discord.ui.View):
    def __init__(self, bot, session_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.session_id = session_id
        
        reply_btn = discord.ui.Button(
            style=discord.ButtonStyle.primary, 
            label="Reply", 
            emoji="💬", 
            custom_id=f"user_app_reply_{session_id}_{uuid.uuid4().hex[:8]}"
        )
        reply_btn.callback = self.reply_callback
        self.add_item(reply_btn)

    async def reply_callback(self, interaction: discord.Interaction):
        if self.session_id not in USER_APP_SESSIONS:
            await interaction.response.send_message("❌ This chat session has expired. Please run `/chat` to start a new one.", ephemeral=True)
            return
            
        session = USER_APP_SESSIONS[self.session_id]
        last_msg = session.history[-1] if session.history else "Type your response..."
        snippet = last_msg.split(":")[-1].strip()[:40] + "..." if ":" in last_msg else "Continue the chat..."
        
        await interaction.response.send_modal(ReplyModal(self.bot, self.session_id, session.config, snippet))

class ReplyModal(discord.ui.Modal):
    def __init__(self, bot, session_id, base_config, placeholder_tip):
        super().__init__(title="Reply to AI", timeout=None)
        self.bot = bot
        self.session_id = session_id
        self.base_config = base_config

        self.prompt = discord.ui.TextInput(
            label="💬 Your Message",
            style=discord.TextStyle.paragraph,
            placeholder=f"Context: {placeholder_tip}",
            required=True
        )
        self.add_item(self.prompt)

        self.attachment = discord.ui.FileUpload(
            label="📂 Attach File (Optional)",
            required=False
        )
        self.add_item(self.attachment)

        self.tools_override = discord.ui.Select(
            placeholder="🛠️ Override Active Tools (Optional)",
            min_values=0,
            max_values=5,
            options=[
                discord.SelectOption(label="Google Search", value="Google Search"),
                discord.SelectOption(label="Code Execution", value="Code Execution"),
                discord.SelectOption(label="URL Content", value="URL Content"),
                discord.SelectOption(label="Generate Images", value="Generate Images"),
                discord.SelectOption(label="Memory Journals", value="Memory Journals")
            ]
        )
        self.add_item(self.tools_override)

        self.thinking_override = discord.ui.Select(
            placeholder="🧠 Override Reasoning (Optional)",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Use Session Settings", value="Inherit", default=True),
                discord.SelectOption(label="Auto", value="Auto"),
                discord.SelectOption(label="Forced", value="High"),
                discord.SelectOption(label="Off", value="None")
            ]
        )
        self.add_item(self.thinking_override)

        self.mode_override = discord.ui.Select(
            placeholder="⚙️ Override Tool Mode (Optional)",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Use Session Settings", value="Inherit", default=True),
                discord.SelectOption(label="Auto", value="Auto"),
                discord.SelectOption(label="Forced", value="Forced"),
                discord.SelectOption(label="Off", value="Off")
            ]
        )
        self.add_item(self.mode_override)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        active_config = self.base_config.copy()
        if self.tools_override.values:
            active_config["system_tools"] = self.tools_override.values
        if self.thinking_override.values[0] != "Inherit":
            active_config["thinking_level"] = self.thinking_override.values[0]
        if self.mode_override.values[0] != "Inherit":
            active_config["tool_mode"] = self.mode_override.values[0]

        await process_user_app_turn(self.bot, interaction, self.session_id, self.prompt.value, self.attachment.value, active_config)


class StartChatModal(discord.ui.Modal):
    def __init__(self, bot, channel_id):
        super().__init__(title="Start a New Chat", timeout=None)
        self.bot = bot
        self.channel_id = channel_id

        self.prompt = discord.ui.TextInput(
            label="✍️ Start Conversation",
            style=discord.TextStyle.paragraph,
            placeholder=get_chat_placeholders(),
            required=True
        )
        self.add_item(self.prompt)

        self.attachment = discord.ui.FileUpload(
            label="📂 Upload File (Optional)",
            required=False
        )
        self.add_item(self.attachment)

        self.tools = discord.ui.Select(
            placeholder="🛠️ Active System Features",
            min_values=0, max_values=6,
            options=[
                discord.SelectOption(label="Google Search", value="Google Search", default=True),
                discord.SelectOption(label="Code Execution", value="Code Execution", default=True),
                discord.SelectOption(label="URL Content", value="URL Content", default=True),
                discord.SelectOption(label="Generate Images", value="Generate Images", default=True),
                discord.SelectOption(label="Memory Journals", value="Memory Journals", default=True),
                discord.SelectOption(label="Discord Components", value="Discord Components", default=True)
            ]
        )
        self.add_item(self.tools)
        
        self.thinking = discord.ui.Select(
            placeholder="🧠 Reasoning Depth",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Auto", value="Auto", default=True),
                discord.SelectOption(label="Forced", value="High"),
                discord.SelectOption(label="Off", value="None")
            ]
        )
        self.add_item(self.thinking)

        self.tool_mode = discord.ui.Select(
            placeholder="⚙️ Tool Invocation Mode",
            min_values=1, max_values=1,
            options=[
                discord.SelectOption(label="Auto", value="Auto", default=True),
                discord.SelectOption(label="Forced", value="Forced"),
                discord.SelectOption(label="Off", value="Off")
            ]
        )
        self.add_item(self.tool_mode)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        config = {
            "system_tools": self.tools.values,
            "thinking_level": self.thinking.values[0],
            "tool_mode": self.tool_mode.values[0],
            "discord_tools": ["Buttons", "Modals"] if "Discord Components" in self.tools.values else []
        }
        
        USER_APP_SESSIONS[self.channel_id] = UserAppSession(self.channel_id, config)
        await process_user_app_turn(self.bot, interaction, self.channel_id, self.prompt.value, self.attachment.value, config)



async def process_user_app_turn(bot, interaction: discord.Interaction, session_id: int, prompt_text: str, attachment, config: dict):
    session = USER_APP_SESSIONS[session_id]
    
    display_name = interaction.user.display_name
    session.history.append(f"{display_name}: {prompt_text}")
    
    history_str = "\n".join(session.history[-15:])
    
    discord_attachments = [attachment] if attachment else []
    
    current_thinking = config.get("thinking_level", "Auto")
    if current_thinking == "Auto":
        current_thinking = bot.chat_handler._select_thinking_level(prompt_text, history_str)

    placeholder_msg = await interaction.followup.send(content="💭 *Thinking...*", wait=True)
    
    try:
        response_stream = await bot.chat_handler.generate_reply_stream(
            message_content=prompt_text,
            channel_history=history_str,
            attachments=discord_attachments,
            user_display_name=display_name,
            user_memory={"Notice": "Memory works normally for User profiles, but Server Lore is disabled in User Apps."},
            server_context="Environment: User-Installed App Sandbox (No Server Gateway).",
            thinking_level=current_thinking,
            is_dm=True,
            active_config=config
        )
        
        accumulated_text = []
        last_edit_time = time.time()
        
        response_aiter = response_stream.__aiter__()
        while True:
            try:
                chunk = await asyncio.wait_for(response_aiter.__anext__(), timeout=30.0)
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                break
                
            if chunk and chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    if getattr(part, 'text', None) and not getattr(part, 'thought', False):
                        accumulated_text.append(part.text)
                        
            now = time.time()
            if now - last_edit_time >= 2.0:
                last_edit_time = now
                current_text = "".join(accumulated_text).strip()
                if current_text:
                    try:
                        await interaction.edit_original_response(content=current_text + " ✍️")
                    except Exception:
                        pass

        final_text = "".join(accumulated_text).strip()
        
        final_text = re.sub(r'\[REACT_USER:.*?\]', '', final_text)
        final_text = re.sub(r'\[POLL:.*?\]', '', final_text)
        
        if not final_text:
            final_text = "*(Silent generation - No text returned)*"
            
        session.history.append(f"AI: {final_text}")
        
        view = UserAppReplyView(bot, session_id)
        await interaction.edit_original_response(content=final_text, view=view)
        
    except Exception as e:
        logger.error(f"User App Processing Error: {e}")
        await interaction.edit_original_response(content=f"❌ **An error occurred processing your request:** {e}")


def register_user_app_commands(bot: discord.Client):
    @app_commands.command(name="chat", description="Start an isolated, collaborative AI conversation (User App)")
    @app_commands.allowed_installs(guilds=False, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def user_chat_command(interaction: discord.Interaction):
        modal = StartChatModal(bot, interaction.channel_id)
        await interaction.response.send_modal(modal)

    bot.tree.add_command(user_chat_command)
    logger.info("Isolated User-Installed App commands loaded successfully.")