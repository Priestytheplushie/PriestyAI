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
    return random.choice(
        [
            "e.g., 'Help me write a Python script for a discord bot'...",
            "e.g., 'Draw a cyberpunk city in the rain'...",
            "e.g., 'Explain quantum physics like I am five'...",
            "e.g., 'What are the latest news headlines today?'...",
            "e.g., 'Let's play a text adventure game'...",
        ]
    )


class UserAppReplyButton(discord.ui.Button):
    def __init__(self, bot, session_id, row=4):
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="Reply",
            emoji="💬",
            custom_id=f"user_app_reply_{session_id}_{uuid.uuid4().hex[:8]}",
            row=row,
        )
        self.bot = bot
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        if self.session_id not in USER_APP_SESSIONS:
            await interaction.response.send_message(
                "❌ This chat session has expired. Please run `/chat` to start a new one.",
                ephemeral=True,
            )
            return

        session = USER_APP_SESSIONS[self.session_id]
        last_msg = session.history[-1] if session.history else "Type your response..."
        snippet = (
            last_msg.split(":")[-1].strip()[:40] + "..."
            if ":" in last_msg
            else "Continue the chat..."
        )

        await interaction.response.send_modal(
            ReplyModal(self.bot, self.session_id, session.config, snippet)
        )


class ReplyModal(discord.ui.Modal):
    def __init__(self, bot, session_id, base_config, placeholder_tip):
        super().__init__(title="Reply to AI", timeout=None)
        self.bot = bot
        self.session_id = session_id
        self.base_config = base_config

        self.prompt_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder=f"Tip: {placeholder_tip}",
            required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="💬 Your Message",
                description="Continue your conversation back to the AI companion.",
                component=self.prompt_input,
            )
        )

        self.attachment_input = discord.ui.FileUpload(required=False)
        self.add_item(
            discord.ui.Label(
                text="📂 Attach File",
                description="Upload an optional new image or document to reference in this turn.",
                component=self.attachment_input,
            )
        )

        t_opts = [
            discord.CheckboxGroupOption(
                label="Google Search",
                value="Google Search",
                description="Live web search queries for current events",
            ),
            discord.CheckboxGroupOption(
                label="Code Sandbox",
                value="Code Execution",
                description="Evaluate math and algorithms in a Python sandbox",
            ),
            discord.CheckboxGroupOption(
                label="Web Reader",
                value="URL Content",
                description="Scrape clean text from shared webpage URLs",
            ),
            discord.CheckboxGroupOption(
                label="Image Generator",
                value="Generate Images",
                description="Generate and edit images directly in chat",
            ),
            discord.CheckboxGroupOption(
                label="Memory Journals",
                value="Memory Journals",
                description="Track long-term user facts in your database",
            ),
            discord.CheckboxGroupOption(
                label="Discord Components",
                value="Discord Components",
                description="Enables interactive menus, buttons, and tools",
            ),
        ]
        self.tools_override = discord.ui.CheckboxGroup(
            options=t_opts, min_values=0, max_values=6, required=False
        )
        self.add_item(
            discord.ui.Label(
                text="🛠️ Override Active Tools (Optional)",
                description="Adjust tools for this turn only. Leave blank to inherit session setup.",
                component=self.tools_override,
            )
        )

        th_opts = [
            discord.RadioGroupOption(
                label="Use Session Settings",
                value="Inherit",
                description="Match original chat visibility setting",
                default=True,
            ),
            discord.RadioGroupOption(
                label="Auto",
                value="Auto",
                description="Reasoning level scales dynamically based on complexity",
            ),
            discord.RadioGroupOption(
                label="Force High",
                value="High",
                description="Force detailed step-by-step thinking for every response",
            ),
            discord.RadioGroupOption(
                label="Off",
                value="None",
                description="Disable logical thinking frames entirely",
            ),
        ]
        self.thinking_override = discord.ui.RadioGroup(options=th_opts)
        self.add_item(
            discord.ui.Label(
                text="🧠 Override Reasoning (Optional)",
                description="Adjust logical depth for this turn only.",
                component=self.thinking_override,
            )
        )

        m_opts = [
            discord.RadioGroupOption(
                label="Use Session Settings",
                value="Inherit",
                description="Match original chat visibility setting",
                default=True,
            ),
            discord.RadioGroupOption(
                label="Auto",
                value="Auto",
                description="The AI dynamically chooses when to trigger tools",
            ),
            discord.RadioGroupOption(
                label="Forced",
                value="Forced",
                description="The AI is required to invoke an active tool",
            ),
            discord.RadioGroupOption(
                label="Off",
                value="Off",
                description="Strictly disable and strip out all tool pipelines",
            ),
        ]
        self.mode_override = discord.ui.RadioGroup(options=m_opts)
        self.add_item(
            discord.ui.Label(
                text="⚙️ Override Tool Mode (Optional)",
                description="Adjust tool invocation style for this turn only.",
                component=self.mode_override,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        active_config = self.base_config.copy()
        if self.tools_override.values:
            active_config["system_tools"] = self.tools_override.values
            active_config["discord_tools"] = (
                ["Buttons", "Modals", "Custom Dropdowns"]
                if "Discord Components" in self.tools_override.values
                else []
            )

        think_val = self.thinking_override.value
        if think_val and think_val != "Inherit":
            active_config["thinking_level"] = think_val

        mode_val = self.mode_override.value
        if mode_val and mode_val != "Inherit":
            active_config["tool_mode"] = mode_val

        attachment = (
            self.attachment_input.values[0] if self.attachment_input.values else None
        )
        await process_user_app_turn(
            self.bot,
            interaction,
            self.session_id,
            self.prompt_input.value,
            attachment,
            active_config,
        )


class StartChatModal(discord.ui.Modal):
    def __init__(self, bot, channel_id):
        super().__init__(title="Start a New Chat", timeout=None)
        self.bot = bot
        self.channel_id = channel_id

        self.prompt_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder=get_chat_placeholders(),
            required=True,
        )
        self.add_item(
            discord.ui.Label(
                text="✍️ Start Conversation",
                description="What would you like to ask or build?",
                component=self.prompt_input,
            )
        )

        self.attachment_input = discord.ui.FileUpload(required=False)
        self.add_item(
            discord.ui.Label(
                text="📂 Upload File",
                description="Share an optional image, PDF, or text document for context.",
                component=self.attachment_input,
            )
        )

        t_opts = [
            discord.CheckboxGroupOption(
                label="Google Search",
                value="Google Search",
                description="Live web search queries for current events",
                default=True,
            ),
            discord.CheckboxGroupOption(
                label="Code Sandbox",
                value="Code Execution",
                description="Evaluate math and algorithms in a Python sandbox",
                default=True,
            ),
            discord.CheckboxGroupOption(
                label="Web Reader",
                value="URL Content",
                description="Scrape clean text from shared webpage URLs",
                default=True,
            ),
            discord.CheckboxGroupOption(
                label="Image Generator",
                value="Generate Images",
                description="Generate and edit images directly in chat",
                default=True,
            ),
            discord.CheckboxGroupOption(
                label="Memory Journals",
                value="Memory Journals",
                description="Track long-term user facts in your database",
                default=True,
            ),
            discord.CheckboxGroupOption(
                label="Discord Components",
                value="Discord Components",
                description="Enables interactive menus, buttons, and tools",
                default=True,
            ),
        ]
        self.tools_input = discord.ui.CheckboxGroup(
            options=t_opts, min_values=0, max_values=6, required=False
        )
        self.add_item(
            discord.ui.Label(
                text="🛠️ Active System Features",
                description="Toggle available services.",
                component=self.tools_input,
            )
        )

        th_opts = [
            discord.RadioGroupOption(
                label="Auto",
                value="Auto",
                description="Reasoning level scales dynamically based on complexity",
                default=True,
            ),
            discord.RadioGroupOption(
                label="Force High",
                value="High",
                description="Force detailed step-by-step thinking for every response",
            ),
            discord.RadioGroupOption(
                label="Off",
                value="None",
                description="Disable logical thinking frames entirely",
            ),
        ]
        self.thinking_input = discord.ui.RadioGroup(options=th_opts)
        self.add_item(
            discord.ui.Label(
                text="🧠 Reasoning Depth",
                description="Set the reasoning level for the AI's internal thinking frames.",
                component=self.thinking_input,
            )
        )

        m_opts = [
            discord.RadioGroupOption(
                label="Auto",
                value="Auto",
                description="The AI dynamically chooses when to trigger tools",
                default=True,
            ),
            discord.RadioGroupOption(
                label="Forced",
                value="Forced",
                description="The AI is required to invoke an active tool",
            ),
            discord.RadioGroupOption(
                label="Off",
                value="Off",
                description="Strictly disable and strip out all tool pipelines",
            ),
        ]
        self.mode_input = discord.ui.RadioGroup(options=m_opts)
        self.add_item(
            discord.ui.Label(
                text="⚙️ Tool Invocation Mode",
                description="How strictly should the AI be required to run active tools?",
                component=self.mode_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()

        selected_tools = self.tools_input.values or []
        config = {
            "system_tools": selected_tools,
            "thinking_level": (
                self.thinking_input.value if self.thinking_input.value else "Auto"
            ),
            "tool_mode": self.mode_input.value if self.mode_input.value else "Auto",
            "discord_tools": (
                ["Buttons", "Modals", "Custom Dropdowns"]
                if "Discord Components" in selected_tools
                else []
            ),
        }

        USER_APP_SESSIONS[self.channel_id] = UserAppSession(self.channel_id, config)
        attachment = (
            self.attachment_input.values[0] if self.attachment_input.values else None
        )

        await process_user_app_turn(
            self.bot,
            interaction,
            self.channel_id,
            self.prompt_input.value,
            attachment,
            config,
        )


async def process_user_app_turn(
    bot,
    interaction: discord.Interaction,
    session_id: int,
    prompt_text: str,
    attachment,
    config: dict,
):
    session = USER_APP_SESSIONS.get(session_id)
    if not session:
        await interaction.followup.send(
            "❌ Session lost. Please start a new `/chat`.", ephemeral=True
        )
        return

    display_name = interaction.user.display_name
    session.history.append(f"{display_name}: {prompt_text}")
    history_str = "\n".join(session.history[-15:])

    discord_attachments = [attachment] if attachment else []

    bot_name = bot.user.display_name if bot.user else "Bot"
    sent_msg = await interaction.followup.send(
        content=f"💭 *{bot_name} is thinking...*", wait=True
    )

    base_prompt = config.get("system_prompt", "").strip()
    if not base_prompt:
        base_prompt = bot.chat_handler.system_instruction

    sandbox_guidelines = (
        "\n\n=== CRITICAL COMPONENT RULES (User App Sandbox) ===\n"
        "1. Standalone dropdown tags—specifically `[USER_SELECT: Prompt]`, `[CHANNEL_SELECT: Prompt]`, "
        "`[ROLE_SELECT: Prompt]`, and `[MENTIONABLE_SELECT: Prompt]`—work perfectly as direct, standalone attachments inside normal chat "
        "messages in ALL contexts (including DMs, GDMs, and Servers).\n"
        "2. Even in DMs and Group chats, you do NOT need to 'fake' these menus using `[SELECT_STRING]`. "
        "Use the native tags; the Discord client will automatically index and populate the dropdowns with "
        "the correct local participants of that GDM or channel on the user's screen."
    )

    app_config = config.copy()
    app_config["system_prompt"] = base_prompt + sandbox_guidelines
    app_config["user_app_session_id"] = session_id

    try:

        await bot._execute_ai_with_retries(
            prompt=prompt_text,
            history=history_str,
            attachments=discord_attachments,
            display_name=display_name,
            memory_dict={"Notice": "User App context active. Server Lore is disabled."},
            context="Environment: User-Installed App Sandbox.",
            channel=interaction.channel,
            author=interaction.user,
            is_dm=True,
            original_message=None,
            edit_target=sent_msg,
            config=app_config,
        )

        session.history.append(f"AI: [Response processed by main pipeline]")
    except Exception as e:
        logger.error(f"User App Pipeline Error: {e}")
        await sent_msg.edit(
            content=f"❌ **An error occurred processing your request:** {e}"
        )


async def handle_component_interaction(
    bot, interaction: discord.Interaction, action_text: str
):
    session_id = interaction.channel_id

    if session_id in USER_APP_SESSIONS:
        await interaction.response.defer()
        session = USER_APP_SESSIONS[session_id]
        await process_user_app_turn(
            bot, interaction, session_id, action_text, None, session.config
        )
    else:

        await interaction.response.defer()
        bot.history_tracker.add_system_action(
            interaction.channel_id,
            f"{interaction.user.display_name} interacted: {action_text}",
        )
        await bot.trigger_ai_reply(interaction.channel, interaction.user)


def register_user_app_commands(bot: discord.Client):
    @app_commands.command(
        name="chat",
        description="Start an isolated, collaborative AI conversation (User App)",
    )
    @app_commands.allowed_installs(guilds=False, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def user_chat_command(interaction: discord.Interaction):
        modal = StartChatModal(bot, interaction.channel_id)
        await interaction.response.send_modal(modal)

    bot.tree.add_command(user_chat_command)
    logger.info("Isolated User-Installed App commands loaded successfully.")
