
import discord
import logging
import re
import uuid
import json
import asyncio

logger = logging.getLogger("UIComponents")

def sanitize_thoughts(thoughts: str) -> str:
    banned_keywords = [
        "constraint check", "formatting:", "casual discord user", "not an ai", "ai assistant", 
        "no robotic", "system prompt", "bullet point", "markdown header", "remain in character",
        "exploratory mode", "no bet", "let me cook", "no shot", "sheesh", "hype", "dont talk like",
        "don't talk like", "character check"
    ]
    lines = thoughts.split("\n")
    cleaned_lines = []
    for line in lines:
        line_lower = line.lower()
        if any(kw in line_lower for kw in banned_keywords):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


class SearchButton(discord.ui.Button):
    def __init__(self, queries: list[str]):
        super().__init__(style=discord.ButtonStyle.secondary, label="View Search Results", emoji="🔍")
        self.queries = queries

    async def callback(self, interaction: discord.Interaction):
        text = "**Google Search Queries used:**\n" + "\n".join(f"- `{q}`" for q in self.queries)
        await interaction.response.send_message(content=text, ephemeral=True)


class CodeButton(discord.ui.Button):
    def __init__(self, code_blocks: list[dict]):
        super().__init__(style=discord.ButtonStyle.secondary, label="View Code Execution", emoji="💻")
        self.code_blocks = code_blocks

    async def callback(self, interaction: discord.Interaction):
        blocks = []
        for i, block in enumerate(self.code_blocks):
            code_input = block.get("code", "")
            code_output = block.get("output", "No execution output returned.")
            blocks.append(
                f"**Execution Block {i+1}:**\n"
                f"```python\n{code_input}\n```\n"
                f"**Sandbox Execution Output:**\n"
                f"```text\n{code_output}\n```"
            )
        text = "\n\n".join(blocks)
        if len(text) > 2000:
            text = text[:1990] + "\n..."
        await interaction.response.send_message(content=text, ephemeral=True)


class EphemeralThoughtsView(discord.ui.View):
    def __init__(self, thoughts: str, thinking_active: bool = False, current_level: str = "HIGH", page_index: int = 0, bot_instance = None):
        super().__init__(timeout=None)
        self.thoughts_raw = thoughts
        self.thinking_active = thinking_active
        self.current_level = current_level
        self.page_index = page_index
        self.bot = bot_instance
        
        self.pages = self._paginate_text(thoughts)
        if self.page_index >= len(self.pages):
            self.page_index = max(0, len(self.pages) - 1)
            
        if len(self.pages) > 1:
            prev_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="◀ Previous", disabled=(self.page_index <= 0), row=0)
            async def prev_callback(interaction: discord.Interaction):
                self.page_index -= 1
                await self._update_page(interaction)
            prev_btn.callback = prev_callback
            self.add_item(prev_btn)
            
            indicator = discord.ui.Button(style=discord.ButtonStyle.secondary, label=f"Page {self.page_index + 1} / {len(self.pages)}", disabled=True, row=0)
            self.add_item(indicator)
            
            next_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Next ▶", disabled=(self.page_index >= len(self.pages) - 1), row=0)
            async def next_callback(interaction: discord.Interaction):
                self.page_index += 1
                await self._update_page(interaction)
            next_btn.callback = next_callback
            self.add_item(next_btn)

    def _paginate_text(self, text: str, max_chars: int = 1500) -> list[str]:
        if not text.strip(): return []
        paragraphs = text.split("\n")
        pages = []
        current_page = []
        current_len = 0
        for p in paragraphs:
            p_strip = p.strip()
            if not p_strip: continue
            p_len = len(p_strip) + 1
            if current_len + p_len > max_chars:
                if current_page: pages.append("\n".join(current_page))
                current_page = [p_strip]
                current_len = p_len
            else:
                current_page.append(p_strip)
                current_len += p_len
        if current_page:
            pages.append("\n".join(current_page))
        return pages

    def get_content(self) -> str:
        current_page_text = self.pages[self.page_index] if self.pages else "*Thinking has just started...*"
        status_text = f"✨ **Deduction & Analysis (Thinking Level: {self.current_level})**\n*Thinking is currently in progress...*" if self.thinking_active else f"🧠 **Deduction & Analysis (Thinking Level: {self.current_level})**"
        
        quoted_lines = []
        for line in current_page_text.split("\n"):
            line_strip = line.strip()
            if line_strip:
                if line_strip.startswith(">") or line_strip.startswith("#") or line_strip.startswith("*"):
                    quoted_lines.append(line_strip)
                else:
                    quoted_lines.append(f"> {line_strip}")
            else:
                quoted_lines.append("")
                
        page_formatted = "\n".join(quoted_lines)
        return f"{status_text}\n\n{page_formatted}"

    async def _update_page(self, interaction: discord.Interaction):
        new_view = EphemeralThoughtsView(
            self.thoughts_raw, thinking_active=self.thinking_active, 
            current_level=self.current_level, page_index=self.page_index, bot_instance=self.bot
        )
        await interaction.response.edit_message(content=new_view.get_content(), view=new_view)
        
        if self.bot and self.thoughts_raw:
            try:
                ephemeral_msg = await interaction.original_response()
                for parent_id, listeners in self.bot.active_thought_listeners.items():
                    for listener_data in listeners:
                        if listener_data["message"].id == ephemeral_msg.id:
                            listener_data["page_index"] = self.page_index
                            break
            except Exception: pass


class ThoughtsButton(discord.ui.Button):
    def __init__(self, thoughts: str, elapsed: int = None, thinking_active: bool = False, message_id: int = None, bot_instance = None, thinking_level: str = "HIGH"):
        emoji = "💭" if thinking_active else "🧠"
        if elapsed is not None:
            label = f"Thinking for {elapsed}s" if thinking_active else f"Thought for {elapsed}s"
        else:
            label = "View Inner Thoughts"
        
        custom_id = f"thoughts_btn_{uuid.uuid4().hex[:12]}"
        super().__init__(style=discord.ButtonStyle.secondary, label=label, emoji=emoji, custom_id=custom_id)
        self.thoughts = thoughts
        self.thinking_active = thinking_active
        self.message_id = message_id
        self.bot = bot_instance
        self.thinking_level = thinking_level

    async def callback(self, interaction: discord.Interaction):
        sanitized = sanitize_thoughts(self.thoughts)
        ephemeral_view = EphemeralThoughtsView(sanitized, thinking_active=self.thinking_active, current_level=self.thinking_level, bot_instance=self.bot)
        await interaction.response.send_message(content=ephemeral_view.get_content(), view=ephemeral_view, ephemeral=True)
        
        if self.thinking_active and self.message_id and self.bot:
            try:
                ephemeral_msg = await interaction.original_response()
                if self.message_id not in self.bot.active_thought_listeners:
                    self.bot.active_thought_listeners[self.message_id] = []
                self.bot.active_thought_listeners[self.message_id].append({
                    "message": ephemeral_msg,
                    "page_index": 0
                })
            except Exception: pass


class DynamicModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list[tuple[str, str, str | None]], bot_instance, channel):
        super().__init__(title=title[:45])
        self.bot = bot_instance
        self.channel = channel
        
        for f_name, f_style, f_desc in fields:
            if len(self.children) >= 5:
                logger.warning("DynamicModal fields truncated to 5 to prevent Discord API crash.")
                break
                
            style_lower = f_style.lower().strip()
            unique_id = f"{f_name[:80].strip()}_{uuid.uuid4().hex[:8]}"
            
            if style_lower == "long":
                comp = discord.ui.TextInput(
                    custom_id=unique_id, style=discord.TextStyle.paragraph, required=True
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower == "short":
                comp = discord.ui.TextInput(
                    custom_id=unique_id, style=discord.TextStyle.short, required=True
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower == "user_select":
                comp = discord.ui.UserSelect(
                    custom_id=unique_id, min_values=1, max_values=1
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower == "role_select":
                comp = discord.ui.RoleSelect(
                    custom_id=unique_id, min_values=1, max_values=1
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower == "channel_select":
                comp = discord.ui.ChannelSelect(
                    custom_id=unique_id, min_values=1, max_values=1
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower == "mentionable_select":
                comp = discord.ui.MentionableSelect(
                    custom_id=unique_id, min_values=1, max_values=1
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            elif style_lower.startswith("select_string"):
                options = []
                match = re.search(r'\((.*?)\)', f_style)
                if match:
                    raw_opts = re.split(r',(?![^(]*\))', match.group(1))
                    for opt in raw_opts:
                        opt_strip = opt.strip()
                        if opt_strip:
                            opt_parts = opt_strip.split(":")
                            o_label = opt_parts[0].strip()
                            o_desc = opt_parts[1].strip() if len(opt_parts) > 1 and opt_parts[1].strip() else None
                            o_emoji = opt_parts[2].strip() if len(opt_parts) > 2 and opt_parts[2].strip() else None
                            options.append(discord.SelectOption(label=o_label, description=o_desc, emoji=o_emoji, value=o_label))
                if not options:
                    options = [discord.SelectOption(label="Default", value="Default")]
                    
                comp = discord.ui.Select(
                    custom_id=unique_id, options=options, min_values=1, max_values=1
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)
            else:
                comp = discord.ui.TextInput(
                    custom_id=unique_id, style=discord.TextStyle.short, required=True
                )
                lbl = discord.ui.Label(text=f_name[:45], description=f_desc, component=comp)
                self.add_item(lbl)

    async def on_submit(self, interaction: discord.Interaction):
        results = []
        for child in self.children:
            if isinstance(child, discord.ui.Label):
                comp = child.component
                label_text = getattr(child, 'label', '') or getattr(child, 'text', 'Input')
                
                if isinstance(comp, discord.ui.TextInput):
                    results.append(f"{label_text}: {comp.value}")
                elif isinstance(comp, (discord.ui.UserSelect, discord.ui.RoleSelect, discord.ui.ChannelSelect, discord.ui.MentionableSelect, discord.ui.Select)):
                    selected_vals = []
                    for val in comp.values:
                        if hasattr(val, 'mention'):
                            selected_vals.append(f"{val} [ID: {val.id}] [Mention: {val.mention}]")
                        elif hasattr(val, 'name'):
                            selected_vals.append(val.name)
                        else:
                            selected_vals.append(str(val))
                    summary_val = ", ".join(selected_vals) if selected_vals else "None"
                    results.append(f"{label_text}: {summary_val}")
                
        summary = " | ".join(results)
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, f"Submitted: {summary}")
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Submitted form '{self.title}': {summary}")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} submitted form '{self.title}' with data: {summary}"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class ConfigModal(discord.ui.Modal):
    def __init__(self, current_config: dict, target_id: int, is_dm: bool, bot_instance):
        super().__init__(title="Configure Channel Bot Settings")
        self.bot = bot_instance
        self.target_id = target_id
        self.is_dm = is_dm

        self.sys_prompt = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Enter custom rules... Use {TOOL_DEFINITION} to position tools. Leave blank for default prompt",
            default=current_config.get("system_prompt", ""),
            required=False,
            max_length=4000
        )
        lbl_prompt = discord.ui.Label(
            text="✍️ Core System Prompt",
            description="Write custom rules. Use {TOOL_DEFINITION} to position active tool parameter injections.",
            component=self.sys_prompt
        )
        self.add_item(lbl_prompt)

        mode_val = current_config.get("tool_mode", "Auto")
        mode_opts = [
            discord.RadioGroupOption(label="Auto", value="Auto", description="AI dynamically chooses when to trigger tools.", default=(mode_val == "Auto")),
            discord.RadioGroupOption(label="Forced", value="Forced", description="Requires the AI to trigger an active tool.", default=(mode_val == "Forced")),
            discord.RadioGroupOption(label="Off", value="Off", description="Entirely disables and strips tool execution pipelines.", default=(mode_val == "Off"))
        ]
        self.tool_mode = discord.ui.RadioGroup(options=mode_opts)
        lbl_mode = discord.ui.Label(
            text="⚙️ Tool Use Behavior", 
            description="Sets how strictly the model must execute active tool configurations during normal chats.", 
            component=self.tool_mode
        )
        self.add_item(lbl_mode)

        think_val = current_config.get("thinking_level", "Auto")
        think_opts = [
            discord.RadioGroupOption(label="Auto", value="Auto", description="Selects level automatically based on complexity.", default=(think_val == "Auto")),
            discord.RadioGroupOption(label="High", value="High", description="Forces detailed step-by-step thinking for every response.", default=(think_val == "High")),
            discord.RadioGroupOption(label="None", value="None", description="Disables logical thinking frames completely.", default=(think_val == "None"))
        ]
        self.thinking_level = discord.ui.RadioGroup(options=think_opts)
        lbl_think = discord.ui.Label(
            text="🧠 Reasoning Depth (Thinking)", 
            description="Controls active multi-pass logical thinking levels on the AI model.", 
            component=self.thinking_level
        )
        self.add_item(lbl_think)

        sys_tools = current_config.get("system_tools", [])
        sys_opts = [
            discord.CheckboxGroupOption(label="Google Search", value="Google Search", description="Allows live web grounding queries on current events.", default=("Google Search" in sys_tools)),
            discord.CheckboxGroupOption(label="Code Execution", value="Code Execution", description="Enables execution of Python code blocks in a sandbox.", default=("Code Execution" in sys_tools)),
            discord.CheckboxGroupOption(label="URL Content", value="URL Content", description="Permits extracting text contents from shared web URLs.", default=("URL Content" in sys_tools)),
            discord.CheckboxGroupOption(label="Generate Images", value="Generate Images", description="Permits visual drawing and img2img editing from text prompt lines.", default=("Generate Images" in sys_tools)),
            discord.CheckboxGroupOption(label="Memory Journals", value="Memory Journals", description="Maintains long-term fact tracking in the database.", default=("Memory Journals" in sys_tools)),
            discord.CheckboxGroupOption(label="Message Builder", value="Message Builder", description="Enables creating custom layouts with buttons, selectors, and modals using Components v2", default=("Message Builder" in sys_tools))
        ]
        self.sys_tools = discord.ui.CheckboxGroup(options=sys_opts, min_values=0, max_values=6, required=False)
        lbl_sys = discord.ui.Label(
            text="🛠️ Enabled System Tools", 
            description="Toggles active system-level tool architectures accessible to the AI.", 
            component=self.sys_tools
        )
        self.add_item(lbl_sys)

        disc_tools = current_config.get("discord_tools", [])
        disc_opts = [
            discord.CheckboxGroupOption(label="Buttons", value="Buttons", description="Allows AI to spawn clickable interaction buttons for choices.", default=("Buttons" in disc_tools)),
            discord.CheckboxGroupOption(label="Modals", value="Modals", description="Allows AI to trigger custom pop-up input forms/modals.", default=("Modals" in disc_tools)),
            discord.CheckboxGroupOption(label="Threads", value="Threads", description="Enables the AI to spin up dedicated threads on deep-dives.", default=("Threads" in disc_tools)),
            discord.CheckboxGroupOption(label="Entity Dropdowns", value="Entity Dropdowns", description="Enables user, channel, and role interactive selectors.", default=("Entity Dropdowns" in disc_tools)),
            discord.CheckboxGroupOption(label="Custom Dropdowns", value="Custom Dropdowns", description="Enables dropdown menus with custom strings and descriptions.", default=("Custom Dropdowns" in disc_tools)),
            discord.CheckboxGroupOption(label="Double-Texting", value="Double-Texting", description="Allows the AI to send organic consecutive follow-up messages.", default=("Double-Texting" in disc_tools)),
            discord.CheckboxGroupOption(label="Reactions", value="Reactions", description="Enables semantic emoji reactions on bot or user messages.", default=("Reactions" in disc_tools)),
            discord.CheckboxGroupOption(label="Native Polls", value="Native Polls", description="Allows the AI to launch native Discord vote polls.", default=("Native Polls" in disc_tools)),
            discord.CheckboxGroupOption(label="Unicode Emojis", value="Unicode Emojis", description="Enables Standard emojis (e.g. 🙂, 🔥) in bot outputs.", default=("Unicode Emojis" in disc_tools)),
            discord.CheckboxGroupOption(label="Server Emojis", value="Server Emojis", description="Enables Custom Guild emojis in bot outputs.", default=("Server Emojis" in disc_tools))
        ]
        self.disc_tools = discord.ui.CheckboxGroup(options=disc_opts, min_values=0, max_values=10, required=False)
        lbl_disc = discord.ui.Label(
            text="💬 Discord Interactive Features", 
            description="Enable or disable specific Discord component pipelines for AI outputs.", 
            component=self.disc_tools
        )
        self.add_item(lbl_disc)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        new_config = {
            "system_prompt": self.sys_prompt.value.strip(),
            "tool_mode": self.tool_mode.value if self.tool_mode.value else "Auto",
            "thinking_level": self.thinking_level.value if self.thinking_level.value else "Auto",
            "system_tools": self.sys_tools.values,
            "discord_tools": self.disc_tools.values
        }
        
        self.bot.configs[self.target_id] = new_config
        import core.memory as memory
        success = await memory.save_config(self.bot, self.bot.brain_server_id, self.target_id, self.is_dm, new_config)
        
        if success:
            await interaction.followup.send("✅ Configuration successfully saved and applied to this channel!", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Configuration updated temporarily in-memory, but failed to save permanently to the Brain Server.", ephemeral=True)


class UnifiedImageEditModal(discord.ui.Modal):
    def __init__(self, current_prompt: str, current_style: str, current_ratio: str, current_strength: str, current_is_edit_flow: bool, parent_msg_id: int, bot_instance):
        super().__init__(title="Edit Image Settings")
        self.bot = bot_instance
        self.parent_msg_id = parent_msg_id
        
        style_options = [
            discord.SelectOption(label="Cinematic Realism", value="photorealistic", emoji="📷", default=(current_style == "photorealistic")),
            discord.SelectOption(label="Anime/Studio Ghibli", value="anime", emoji="🌸", default=(current_style == "anime")),
            discord.SelectOption(label="Cyberpunk Neon", value="cyberpunk", emoji="🌃", default=(current_style == "cyberpunk")),
            discord.SelectOption(label="3D Claymation", value="clay", emoji="🧸", default=(current_style == "clay")),
            discord.SelectOption(label="Watercolor Painting", value="watercolor", emoji="🖌️", default=(current_style == "watercolor")),
            discord.SelectOption(label="Pixel Art", value="pixel", emoji="👾", default=(current_style == "pixel")),
            discord.SelectOption(label="Pencil Sketch", value="sketch", emoji="✏️", default=(current_style == "sketch")),
            discord.SelectOption(label="Origami Papercraft", value="origami", emoji="📄", default=(current_style == "origami")),
            discord.SelectOption(label="Glowing Neon Light", value="neon", emoji="🚨", default=(current_style == "neon")),
            discord.SelectOption(label="Oil Painting", value="oilpainting", emoji="🎨", default=(current_style == "oilpainting")),
            discord.SelectOption(label="Epic Fantasy Art", value="fantasy", emoji="✨", default=(current_style == "fantasy"))
        ]
        self.style_select = discord.ui.Select(custom_id="style_dropdown", options=style_options)
        self.labeled_style = discord.ui.Label(
            text="🎨 Artistic Preset",
            description="Select a visual style preset (e.g. realism, anime, watercolor).", 
            component=self.style_select
        )
        
        ratio_options = [
            discord.SelectOption(label="Square (1:1)", value="1:1", emoji="⏹️", default=(current_ratio == "1:1")),
            discord.SelectOption(label="Portrait (4:5)", value="4:5", emoji="📱", default=(current_ratio == "4:5")),
            discord.SelectOption(label="Story (9:16)", value="9:16", emoji="↕️", default=(current_ratio == "9:16")),
            discord.SelectOption(label="Landscape (3:2)", value="3:2", emoji="🌅", default=(current_ratio == "3:2")),
            discord.SelectOption(label="Widescreen (16:9)", value="16:9", emoji="🎬", default=(current_ratio == "16:9")),
        ]
        self.ratio_select = discord.ui.Select(custom_id="ratio_dropdown", options=ratio_options)
        self.labeled_ratio = discord.ui.Label(
            text="📐 Aspect Ratio",
            description="Determines the canvas dimensions and output framing.", 
            component=self.ratio_select
        )

        if not current_strength: current_strength = "0.6"
            
        strength_options = [
            discord.SelectOption(label="Slight Variation (20%)", value="0.2", emoji="🔍", default=(current_strength == "0.2")),
            discord.SelectOption(label="Mild Adjustments (40%)", value="0.4", emoji="✨", default=(current_strength == "0.4")),
            discord.SelectOption(label="Balanced Blend (60%)", value="0.6", emoji="⚖️", default=(current_strength == "0.6")),
            discord.SelectOption(label="Heavy Remake (80%)", value="0.8", emoji="🌀", default=(current_strength == "0.8")),
            discord.SelectOption(label="Complete Rewrite (100%)", value="1.0", emoji="💥", default=(current_strength == "1.0"))
        ]
        self.strength_select = discord.ui.Select(custom_id="strength_dropdown", options=strength_options)
        self.labeled_strength = discord.ui.Label(
            text="🖌️ Aesthetic Strength (Img2Img Only)", 
            description="How heavily the source is altered. Higher values change more. Ignored on Start Fresh.", 
            component=self.strength_select
        )

        mode_options = [
            discord.SelectOption(label="Modify Current Image", value="i2i", description="Uses the active image as a base (Cloudflare)", emoji="🖌️", default=current_is_edit_flow),
            discord.SelectOption(label="Start Fresh", value="t2i", description="Generates completely from scratch (Pollinations)", emoji="✨", default=not current_is_edit_flow)
        ]
        self.mode_select = discord.ui.Select(custom_id="mode_dropdown", options=mode_options)
        self.labeled_mode = discord.ui.Label(
            text="⚙️ Generation Mode",
            description="Choose whether to edit the current image or generate from scratch.", 
            component=self.mode_select
        )
        
        self.prompt_input = discord.ui.TextInput(
            custom_id="prompt_textbox", style=discord.TextStyle.long, default=current_prompt, placeholder="Describe what you want to draw..."
        )
        self.labeled_prompt = discord.ui.Label(
            text="✍️ Prompt / Edit Instructions", 
            description="New description for Start Fresh, or adjustments/additions for Modify Image.", 
            component=self.prompt_input
        )
        
        self.add_item(self.labeled_style)
        self.add_item(self.labeled_ratio)
        self.add_item(self.labeled_strength)
        self.add_item(self.labeled_mode)
        self.add_item(self.labeled_prompt)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await self.bot.process_image_generation_update(
            interaction=interaction,
            message_id=self.parent_msg_id,
            prompt=self.prompt_input.value,
            style=self.style_select.values[0],
            ratio=self.ratio_select.values[0],
            strength=self.strength_select.values[0],
            generation_mode=self.mode_select.values[0],
            regenerate=True
        )


class CustomButton(discord.ui.Button):
    def __init__(self, label: str, style_name: str, emoji: str = None, custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger
        }
        style = style_map.get(style_name.lower().strip(), discord.ButtonStyle.secondary)
        
        if custom_id is None:
            custom_id = f"custom_btn_{uuid.uuid4().hex[:12]}"
            
        super().__init__(style=style, label=label, emoji=emoji, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": f"Clicked Button: '{self.label}'"
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, f"Selection: '{self.label}'")
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Clicked button '{self.label}'")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} clicked button '{self.label}'"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomUserSelect(discord.ui.UserSelect):
    def __init__(self, placeholder: str, custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_user_select_{uuid.uuid4().hex[:12]}"
            
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected_user = self.values[0]
        summary = f"Selected User: {selected_user.display_name} (@{selected_user.name}) [ID: {selected_user.id}] [Mention: <@{selected_user.id}>]"
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, summary)
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Selected user: {selected_user.display_name} (@{selected_user.name}) [ID: {selected_user.id}] [Mention: <@{selected_user.id}>]")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} selected user: {selected_user.display_name} (@{selected_user.name}) [ID: {selected_user.id}] [Mention: <@{selected_user.id}>]"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomRoleSelect(discord.ui.RoleSelect):
    def __init__(self, placeholder: str, custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_role_select_{uuid.uuid4().hex[:12]}"
            
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        summary = f"Selected Role: {selected_role.name} [ID: {selected_role.id}] [Mention: <@&{selected_role.id}>]"
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, summary)
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Selected role: {selected_role.name} [ID: {selected_role.id}] [Mention: <@&{selected_role.id}>]")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} selected role: {selected_role.name} [ID: {selected_role.id}] [Mention: <@&{selected_role.id}>]"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, placeholder: str, custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_channel_select_{uuid.uuid4().hex[:12]}"
            
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected_channel = self.values[0]
        summary = f"Selected Channel: #{selected_channel.name} [ID: {selected_channel.id}] [Mention: <#{selected_channel.id}>]"
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, summary)
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Selected channel: #{selected_channel.name} [ID: {selected_channel.id}] [Mention: <#{selected_channel.id}>]")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} selected channel: #{selected_channel.name} [ID: {selected_channel.id}] [Mention: <#{selected_channel.id}>]"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomMentionableSelect(discord.ui.MentionableSelect):
    def __init__(self, placeholder: str, custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_mentionable_select_{uuid.uuid4().hex[:12]}"
            
        super().__init__(placeholder=placeholder, min_values=1, max_values=1, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if hasattr(selected, "mention"):
            summary = f"Selected Mentionable: {selected} [ID: {selected.id}] [Mention: {selected.mention}]"
        else:
            summary = f"Selected Mentionable: {selected} [ID: {selected.id}]"
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, summary)
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Selected mentionable: {selected} [ID: {selected.id}]")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} selected mentionable: {selected} [ID: {selected.id}]"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomStringSelect(discord.ui.Select):
    def __init__(self, placeholder: str, options_list: list[discord.SelectOption], custom_id: str = None, bot_instance = None, channel = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_string_select_{uuid.uuid4().hex[:12]}"
            
        super().__init__(placeholder=placeholder, options=options_list, min_values=1, max_values=1, custom_id=custom_id, row=row)
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        summary = f"Selected Option: '{selected_value}'"
        
        msg_id = interaction.message.id if interaction.message else None
        collector = self.bot.active_collectors.get(msg_id) if msg_id else None
        
        if collector:
            collector.submissions.append({
                "user_id": interaction.user.id,
                "username": interaction.user.name,
                "display_name": interaction.user.display_name,
                "data": summary
            })
            collector.participants.add(interaction.user.id)
            await self.bot.trigger_ephemeral_ai_reply(interaction, collector, summary)
        else:
            is_user_app = False
            if hasattr(interaction, "is_user_integration"):
                is_user_app = interaction.is_user_integration()
            else:
                is_user_app = (interaction.guild is None and interaction.guild_id is not None) or (interaction.channel is not None and not isinstance(interaction.channel, discord.DMChannel) and interaction.guild is None)

            if is_user_app:
                from core.user_app_handler import handle_component_interaction
                await handle_component_interaction(self.bot, interaction, f"Selected option: '{selected_value}'")
            else:
                await interaction.response.defer()
                action_text = f"{interaction.user.display_name} selected option: '{selected_value}'"
                self.bot.history_tracker.add_system_action(self.channel.id, action_text)
                await self.bot.trigger_ai_reply(self.channel, interaction.user)


class CustomModalButton(discord.ui.Button):
    def __init__(self, label: str, title: str, fields: list[tuple[str, str, str | None]], bot_instance, channel, custom_id: str = None, row: int = None):
        if custom_id is None:
            custom_id = f"custom_modal_btn_{uuid.uuid4().hex[:12]}"
            
        super().__init__(style=discord.ButtonStyle.secondary, label=label, custom_id=custom_id, row=row)
        self.title_text = title
        self.fields = fields
        self.bot = bot_instance
        self.channel = channel

    async def callback(self, interaction: discord.Interaction):
        modal = DynamicModal(self.title_text, self.fields, self.bot, self.channel)
        await interaction.response.send_modal(modal)


class DynamicView(discord.ui.View):
    def __init__(self, bot_instance, channel, bot_text=None, search_queries=None, code_blocks=None, image_filename=None, disabled_triggers=None, original_message=None, user_app_session_id=None):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.channel = channel
        self.bot_text = bot_text
        self.image_filename = image_filename
        self.disabled_triggers = disabled_triggers if disabled_triggers else {}
        self.original_message = original_message
        self.embeds = []
        self.user_app_session_id = user_app_session_id

        if search_queries:
            self.add_item(SearchButton(search_queries))
        if code_blocks:
            self.add_item(CodeButton(code_blocks))

        self._add_trigger_warnings()

    def finalize_layout(self):
        if self.user_app_session_id:
            from core.user_app_handler import USER_APP_SESSIONS
            session = USER_APP_SESSIONS.get(self.user_app_session_id)
            if session:
                from core.user_app_handler import UserAppReplyButton
                self.add_item(UserAppReplyButton(self.bot, self.user_app_session_id, row=4))

    def _add_trigger_warnings(self):
        for tool_name, triggered in self.disabled_triggers.items():
            if not triggered:
                continue
                
            desc_text = ""
            btn_label = ""
            if tool_name == "URL Content":
                desc_text = "You attached a URL but the bot cannot read it. Enable URL content to allow me to scrape web URLs."
                btn_label = "Enable URL Content"
            elif tool_name == "Google Search":
                desc_text = "Your prompt requires current events, but Google Search is disabled. Enable search to ground replies."
                btn_label = "Enable Google Search"
            elif tool_name == "Generate Images":
                desc_text = "Your prompt requested drawing generation, but Generate Images is disabled. Enable visual processing."
                btn_label = "Enable Generate Images"
                
            embed = discord.Embed(
                title=f"⚠️ {tool_name} Disabled",
                description=desc_text,
                color=discord.Color.gold()
            )
            self.embeds.append(embed)
            
            btn = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=btn_label,
                custom_id=f"enable_{tool_name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
            )
            
            def make_callback(t_name=tool_name):
                async def callback(interaction: discord.Interaction):
                    is_dm = isinstance(interaction.channel, discord.DMChannel)
                    is_author = self.original_message and (interaction.user.id == self.original_message.author.id)
                    has_perm = is_dm or is_author or (isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.manage_channels)
                    
                    if not has_perm:
                        await interaction.response.send_message("❌ You do not have permission to enable tool configurations for this channel.", ephemeral=True)
                        return
                        
                    await interaction.response.defer(ephemeral=True)
                    target_id = interaction.user.id if is_dm else interaction.channel.id
                    config = await self.bot.get_config(target_id, is_dm)
                    
                    if t_name not in config["system_tools"]:
                        config["system_tools"].append(t_name)
                        self.bot.configs[target_id] = config
                        import core.memory as memory
                        await memory.save_config(self.bot, self.bot.brain_server_id, target_id, is_dm, config)
                        
                    await interaction.followup.send(f"✅ **{t_name}** has been successfully enabled! Generating next version...", ephemeral=True)
                    
                    bot_msg = interaction.message
                    if bot_msg.id not in self.bot.rerun_cache:
                        self.bot.rerun_cache[bot_msg.id] = [
                            {"content": bot_msg.content, "attachments": list(bot_msg.attachments), "ui_state": {}}
                        ]
                        self.bot.rerun_indexes[bot_msg.id] = 0

                    if self.original_message:
                        history = self.bot.history_tracker.get_formatted_history(self.channel.id)
                        display_name = self.original_message.author.nick if isinstance(self.original_message.author, discord.Member) and self.original_message.author.nick else self.original_message.author.display_name
                        server_context = self.bot._compile_server_context(self.channel.guild, self.original_message.author) if hasattr(self.channel, 'guild') else ""
                        
                        if "Memory Journals" in config.get("system_tools", []):
                            memories = await self.bot._compile_memories_for_ai(self.original_message.author, self.channel)
                        else:
                            memories = {"user_memories": "", "server_lore": "", "global_database": ""}
                            
                        import asyncio
                        if "URL Content" in config.get("system_tools", []):
                            urls = self.bot.link_reader.extract_urls(self.original_message.clean_content)
                            scraped_pages = await asyncio.gather(*[self.bot.link_reader.fetch_and_clean(url) for url in urls[:2]])
                        else:
                            scraped_pages = []
                            
                        user_status = self.bot._compile_user_activity(self.original_message.author) if isinstance(self.original_message.author, discord.Member) else None
                        prompt = self.original_message.clean_content
                        
                        temp_text = f"*(🎨 Re-processing prompt with {t_name} enabled...)*"
                        temp_view = DynamicView(self.bot, self.channel, user_app_session_id=self.user_app_session_id)
                        temp_view.finalize_layout()
                        
                        temp_text = await self.bot._resolve_mentions(temp_text, self.channel)
                        await bot_msg.edit(content=temp_text, view=temp_view, attachments=[])
                        
                        updated_triggers = self.disabled_triggers.copy()
                        updated_triggers[t_name] = False
                        
                        await self.bot._execute_ai_with_retries(
                            prompt=prompt, history=history, attachments=list(self.original_message.attachments),
                            display_name=display_name, memory_dict=memories, context=server_context,
                            channel=self.channel, author=self.original_message.author, is_dm=is_dm,
                            original_message=self.original_message, scraped_pages=scraped_pages,
                            user_status=user_status, edit_target=bot_msg, config=config,
                            disabled_triggers=updated_triggers
                        )
                        
                return callback
                
            btn.callback = make_callback()
            self.add_item(btn)

    def add_image_controls(self, bot_instance, message_id: int, current_prompt: str, current_style: str, current_ratio: str, current_strength: str = "0.6", current_is_edit_flow: bool = False, show_actions: bool = True):
        image_versions = bot_instance.image_versions.get(message_id, [])
        active_idx = bot_instance.image_version_indexes.get(message_id, 0)
        total_versions = len(image_versions)
        
        if total_versions > 1:
            prev_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="◀ Previous Version", disabled=(not show_actions) or (active_idx <= 0), row=0)
            async def img_prev_callback(interaction: discord.Interaction):
                bot_instance.image_version_indexes[message_id] -= 1
                await bot_instance.render_saved_image_version(interaction, message_id)
            prev_btn.callback = img_prev_callback
            self.add_item(prev_btn)
            
            indicator = discord.ui.Button(style=discord.ButtonStyle.secondary, label=f"Version {active_idx + 1} / {total_versions}", disabled=True, row=0)
            self.add_item(indicator)
            
            next_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Next Version ▶", disabled=(not show_actions) or (active_idx >= total_versions - 1), row=0)
            async def img_next_callback(interaction: discord.Interaction):
                bot_instance.image_version_indexes[message_id] += 1
                await bot_instance.render_saved_image_version(interaction, message_id)
            next_btn.callback = img_next_callback
            self.add_item(next_btn)

        regen_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Re-generate", emoji="🔄", disabled=not show_actions, row=1)
        async def regen_callback(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            import random
            regen_mode = "i2i" if current_is_edit_flow else "t2i"
            await bot_instance.process_image_generation_update(
                interaction=interaction, message_id=message_id, prompt=current_prompt,
                style=current_style, ratio=current_ratio, strength=current_strength,
                generation_mode=regen_mode, seed=random.randint(1, 10000000), regenerate=True
            )
        regen_btn.callback = regen_callback
        self.add_item(regen_btn)

        edit_settings_btn = discord.ui.Button(style=discord.ButtonStyle.success, label="Edit Settings", emoji="🎨", disabled=not show_actions, row=1)
        async def edit_settings_callback(interaction: discord.Interaction):
            from core.ui_components import UnifiedImageEditModal
            await interaction.response.send_modal(UnifiedImageEditModal(
                current_prompt, current_style, current_ratio, current_strength, current_is_edit_flow, message_id, bot_instance
            ))
        edit_settings_btn.callback = edit_settings_callback
        self.add_item(edit_settings_btn)

    def add_rerun_pagination(self, bot_instance, message_id: int):
        versions = bot_instance.rerun_cache.get(message_id, [])
        current_idx = bot_instance.rerun_indexes.get(message_id, 0)
        total_versions = len(versions)

        prev_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="◀", disabled=(current_idx <= 0), row=2)
        async def prev_callback(interaction: discord.Interaction):
            bot_instance.rerun_indexes[message_id] -= 1
            await self._show_version(interaction, bot_instance, message_id)
        prev_btn.callback = prev_callback
        self.add_item(prev_btn)

        indicator_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label=f"{current_idx + 1} / {total_versions}", disabled=True, row=2)
        self.add_item(indicator_btn)

        next_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="▶", disabled=(current_idx >= total_versions - 1), row=2)
        async def next_callback(interaction: discord.Interaction):
            bot_instance.rerun_indexes[message_id] += 1
            await self._show_version(interaction, bot_instance, message_id)
        next_btn.callback = next_callback
        self.add_item(next_btn)

    async def _show_version(self, interaction: discord.Interaction, bot_instance, message_id: int):
        versions = bot_instance.rerun_cache.get(message_id, [])
        current_idx = bot_instance.rerun_indexes.get(message_id, 0)
        total_versions = len(versions)

        if 0 <= current_idx < total_versions:
            version_data = versions[current_idx]
            new_content = version_data["content"]
            attachments = version_data["attachments"]
            ui_state = version_data.get("ui_state", {})

            new_view = DynamicView(
                bot_instance, self.channel, bot_text=new_content,
                search_queries=ui_state.get("search_queries", []), code_blocks=ui_state.get("code_blocks", []),
                image_filename="generated.png" if ui_state.get("has_image") else None,
                user_app_session_id=self.user_app_session_id
            )
            if ui_state.get("thoughts"):
                from core.ui_components import ThoughtsButton
                new_view.add_item(ThoughtsButton(ui_state["thoughts"], elapsed=None, thinking_active=False, message_id=message_id, bot_instance=bot_instance))
                
            new_view.add_rerun_pagination(bot_instance, message_id)
            new_view.finalize_layout()

            embeds = getattr(new_view, "embeds", [])
            new_content = await bot_instance._resolve_mentions(new_content, self.channel)
            await interaction.response.edit_message(content=new_content, view=new_view, attachments=attachments, embeds=embeds)




class SaveContextModal(discord.ui.Modal):
    def __init__(self, target_alias: str, payload_type: str, prefilled_data: str, bot_instance):
        super().__init__(title="Save Context Profile")
        self.bot = bot_instance
        self.payload_type = payload_type
        
        self.explanation = discord.ui.TextDisplay(
            content="ℹ️ **How Saved Profiles Work:**\n"
                    "Enter a clean, lowercase name for the **Alias** (e.g., `annoying_user`) to uniquely identify this snapshot. "
                    "You can then dynamically load this context inside `/agent` by selecting its name in your profile options."
        )
        self.add_item(self.explanation)
        
        self.alias_input = discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder="e.g. annoying_user",
            default=target_alias[:100],
            required=True,
            max_length=100
        )
        self.alias_label = discord.ui.Label(
            text="✍️ Profile Alias (Unique)",
            description="Use only lowercase letters, numbers, and underscores (e.g. annoying_user).",
            component=self.alias_input
        )
        self.add_item(self.alias_label)
        
        self.notes_input = discord.ui.TextInput(
            style=discord.TextStyle.long,
            placeholder="Describe why this profile snapshot is relevant...",
            required=False,
            max_length=500
        )
        self.notes_label = discord.ui.Label(
            text="📝 Description Notes (Optional)",
            description="Add additional comments or qualitative observations to aid the analysis.",
            component=self.notes_input
        )
        self.add_item(self.notes_label)
        
        self.payload_input = discord.ui.TextInput(
            style=discord.TextStyle.long,
            default=prefilled_data[:3900],
            required=True,
            max_length=4000
        )
        self.payload_label = discord.ui.Label(
            text="📂 Profile Details",
            description="The details of your saved profile. You can freely edit, redact, or trim this data.",
            component=self.payload_input
        )
        self.add_item(self.payload_label)
        
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        raw_alias = self.alias_input.value.strip().lower()
        alias = re.sub(r'[^a-z0-9_]', '', raw_alias)
        
        if not alias:
            await interaction.followup.send("❌ Error: Invalid Alias. Use alphanumeric characters and underscores only.", ephemeral=True)
            return
            
        try:
            data_dict = json.loads(self.payload_input.value)
        except Exception as err:
            await interaction.followup.send(f"❌ Error: Invalid structure inside data: {err}", ephemeral=True)
            return
            
        import core.memory as memory
        success = await memory.save_context_snippet(
            self.bot, self.bot.brain_server_id, interaction.user.id,
            alias, self.payload_type, data_dict, self.notes_input.value.strip()
        )
        
        if success:
            await interaction.followup.send(
                f"✅ **Profile Saved Successfully!**\n"
                f"Your **{self.payload_type}** record is archived. "
                f"You can now reference this context inside the `/agent` setup popup.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Error: Failed to write profile to the server's archives.",
                ephemeral=True
            )


class AgentInstructionsModal(discord.ui.Modal):
    def __init__(self, session_id: int, parent_view):
        super().__init__(title="Add Agent Instructions")
        self.session_id = session_id
        self.parent_view = parent_view
        
        self.instructions_input = discord.ui.TextInput(
            style=discord.TextStyle.long,
            placeholder="e.g. Focus only on message contents. Ignore role structures.",
            required=True,
            max_length=1000
        )
        self.instructions_label = discord.ui.Label(
            text="📝 Extra Guidelines / Constraints",
            description="Add extra directives that the Agent will read prior to beginning its tasks.",
            component=self.instructions_input
        )
        self.add_item(self.instructions_label)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.followup.send("❌ Error: Active session has concluded or expired.", ephemeral=True)
            return
            
        session.additional_instructions = self.instructions_input.value.strip()
        await self.parent_view.update_checklist_message(interaction, session)


class AgentPreStartView(discord.ui.View):
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.checklist_msg = None

    async def update_checklist_message(self, interaction: discord.Interaction, session):
        contexts_log = "None"
        if session.loaded_contexts:
            aliases = re.findall(r'\[CONTEXT ALIAS:\s*(.*?)\s*\]', session.loaded_contexts)
            if aliases:
                contexts_log = ", ".join([f"`{a}`" for a in aliases])
                
        extra_note = f"\n📝 **Added Instructions:**\n> {session.additional_instructions}" if session.additional_instructions else ""
        
        new_content = (
            f"📋 **Agent Pre-Start Checklist**\n"
            f"----------------------------------------\n"
            f"📂 Loaded Contexts: {contexts_log}\n"
            f"🎯 Primary Task: \"{session.primary_task}\"\n"
            f"{extra_note}\n\n"
            f"Review the configuration above. You can add extra instructions or start execution below."
        )
        await interaction.message.edit(content=new_content, view=self)

    @discord.ui.button(label="Start Agent", style=discord.ButtonStyle.success, emoji="🚀")
    async def start_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.status = "running"
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        interaction.client.loop.create_task(session.execute_tick(interaction.client))

    @discord.ui.button(label="Add Instructions", style=discord.ButtonStyle.primary, emoji="📝")
    async def add_instructions_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AgentInstructionsModal(self.session_id, self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel Session", style=discord.ButtonStyle.danger, emoji="🛑")
    async def cancel_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.status = "completed"
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        if self.session_id in interaction.client.active_agent_sessions:
            del interaction.client.active_agent_sessions[self.session_id]
        await session.channel.send("💡 *Agent Workspace session cancelled by user prior to execution.*")

    class AgentLaunchModal(discord.ui.Modal):
        def __init__(self, bot_instance, prompt: str, user: discord.User, guild_list: list, user_contexts: list, message_contexts: list):
            super().__init__(title="Configure Agent Profiles")
            self.bot = bot_instance
            self.prompt = prompt
            self.user = user

            info_lines = [
                "📂 **Saved Profiles Index**",
                "Choose mutual servers or saved template snapshots below to attach to this workspace."
            ]
            
            all_aliases = [f"`{c.get('alias')}`" for c in user_contexts + message_contexts if c.get("alias")]
            if all_aliases:
                info_lines.append(f"Saved: {', '.join(all_aliases[:10])}" + ("..." if len(all_aliases) > 10 else ""))
            else:
                info_lines.append("No saved profiles exist yet. Use the message options menu to save snapshot templates!")
                
            self.info_box = discord.ui.TextDisplay(content="\n".join(info_lines)[:1500])
            self.add_item(self.info_box)

            server_options = []
            for guild in guild_list[:25]:
                server_options.append(discord.SelectOption(
                    label=guild.name[:100],
                    value=f"server_{guild.id}",
                    description=f"Server ID: {guild.id}"
                ))
            if not server_options:
                server_options.append(discord.SelectOption(label="No mutual servers", value="none"))
                
            self.server_select = discord.ui.Select(
                custom_id="agent_modal_server",
                placeholder="Select target server context...",
                options=server_options,
                min_values=0,
                max_values=1,
                required=False
            )
            self.add_item(discord.ui.Label(
                text="📁 Target Server",
                description="Select which server's channels and messages the AI should access.",
                component=self.server_select
            ))

            user_options = []
            for u_ctx in user_contexts[:25]:
                alias = u_ctx.get("alias", "unknown")
                desc = u_ctx.get("notes") or "User profile template snapshot"
                user_options.append(discord.SelectOption(
                    label=alias[:100],
                    value=alias,
                    description=desc[:100]
                ))
            if not user_options:
                user_options.append(discord.SelectOption(label="No saved user snapshots", value="none"))
                
            self.user_select = discord.ui.Select(
                custom_id="agent_modal_user_ctx",
                placeholder="Select user snapshot context...",
                options=user_options,
                min_values=0,
                max_values=min(25, len(user_options)),
                required=False
            )
            self.add_item(discord.ui.Label(
                text="👤 User Profiles",
                description="Attach saved user profile snapshots to guide the analysis.",
                component=self.user_select
            ))

            msg_options = []
            for m_ctx in message_contexts[:25]:
                alias = m_ctx.get("alias", "unknown")
                desc = m_ctx.get("notes") or "Message transcript snapshot template"
                msg_options.append(discord.SelectOption(
                    label=alias[:100],
                    value=alias,
                    description=desc[:100]
                ))
            if not msg_options:
                msg_options.append(discord.SelectOption(label="No saved transcripts", value="none"))
                
            self.message_select = discord.ui.Select(
                custom_id="agent_modal_msg_ctx",
                placeholder="Select message transcript context...",
                options=msg_options,
                min_values=0,
                max_values=min(25, len(msg_options)),
                required=False
            )
            self.add_item(discord.ui.Label(
                text="💬 Message Transcripts",
                description="Attach saved transcript snapshots to guide the analysis.",
                component=self.message_select
            ))

            self.additional_input = discord.ui.TextInput(
                custom_id="agent_modal_additional",
                style=discord.TextStyle.short,
                placeholder="e.g. custom_audit, debug_leak",
                required=False,
                max_length=500
            )
            self.add_item(discord.ui.Label(
                text="✍️ Additional Profiles",
                description="Type extra profile names separated by commas for overflow.",
                component=self.additional_input
            ))

        async def on_submit(self, interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            
            initial_msg = None

            raw_data = interaction.data if interaction.data else {}
            submitted_vals = {}
            
            def walk_components(comps):
                for c in comps:
                    c_id = c.get("custom_id")
                    if c_id:
                        if "value" in c:
                            submitted_vals[c_id] = [c["value"]]
                        elif "values" in c:
                            submitted_vals[c_id] = c["values"]
                    
                    sub = c.get("components")
                    if sub:
                        walk_components(sub)
                        
                    single = c.get("component")
                    if single:
                        walk_components([single])
                        
            walk_components(raw_data.get("components", []))

            initial_msg = await interaction.followup.send(
                "⏳ **Initializing your private Agent Workspace thread...**",
                ephemeral=True,
                wait=True
            )

            selected_server_id = None
            server_vals = submitted_vals.get("agent_modal_server", [])
            if server_vals and server_vals[0] != "none":
                srv_val = server_vals[0]
                if srv_val.startswith("server_"):
                    selected_server_id = int(srv_val.split("_", 1)[1])

            selected_user_aliases = [v for v in submitted_vals.get("agent_modal_user_ctx", []) if v != "none"]
            selected_msg_aliases = [v for v in submitted_vals.get("agent_modal_msg_ctx", []) if v != "none"]

            additional_val = ""
            additional_list = submitted_vals.get("agent_modal_additional", [])
            if additional_list:
                additional_val = additional_list[0]
                
            extra_aliases = []
            if additional_val:
                extra_aliases = [a.strip().lower() for a in additional_val.split(",") if a.strip()]

            all_aliases = list(dict.fromkeys(selected_user_aliases + selected_msg_aliases + extra_aliases))

            appended_context_data = ""
            context_summary_log = "None"
            if all_aliases:
                context_summary_log = ", ".join([f"`{a}`" for a in all_aliases])
                appended_context_data = await self.bot._compile_selected_context_payloads(self.user.id, ",".join(all_aliases))

            target_server_log = "Local Guild Only"
            if selected_server_id:
                guild_obj = self.bot.get_guild(selected_server_id)
                if guild_obj:
                    target_server_log = f"{guild_obj.name} (ID: {selected_server_id})"
                else:
                    target_server_log = f"Unknown Server (ID: {selected_server_id})"

            channel = interaction.channel
            thread = None
            if isinstance(channel, discord.Thread):
                thread = channel
                await thread.add_user(interaction.user)
            else:
                try:
                    from core.bot import generate_slug_from_prompt, sanitize_channel_name
                    user_part = sanitize_channel_name(interaction.user.display_name[:12])
                    slug = generate_slug_from_prompt(self.prompt)
                    thread_name = f"agent-{user_part}-{slug}"
                    thread = await channel.create_thread(
                        name=thread_name,
                        type=discord.ChannelType.private_thread,
                        auto_archive_duration=1440
                    )
                    await thread.add_user(interaction.user)
                except Exception as e:
                    if initial_msg:
                        await interaction.followup.edit_message(
                            message_id=initial_msg.id,
                            content=f"❌ **Error:** Failed to spawn private workspace thread: {e}. Ensure thread permissions are active."
                        )
                    return

            from agents.discord_react.agent import AgentSession
            session = AgentSession(
                thread_id=thread.id,
                user_id=interaction.user.id,
                prompt=self.prompt,
                loaded_contexts=appended_context_data,
                channel=thread
            )
            session.target_guild_id = selected_server_id
            self.bot.active_agent_sessions[thread.id] = session

            view = AgentPreStartView(thread.id)
            checklist_content = (
                f"📋 **Agent Pre-Start Checklist**\n"
                f"----------------------------------------\n"
                f"📁 Target Server: {target_server_log}\n"
                f"📂 Loaded Contexts: {context_summary_log}\n"
                f"🎯 Primary Task: \"{self.prompt}\"\n\n"
                f"Review the configuration above. You can add extra directions or start execution below."
            )
            checklist_msg = await thread.send(content=checklist_content, view=view)
            view.checklist_msg = checklist_msg

            if initial_msg:
                await interaction.followup.edit_message(
                    message_id=initial_msg.id,
                    content=f"✅ **Agent Workspace Initialized!** Proceed directly to your private thread: {thread.mention}"
                )