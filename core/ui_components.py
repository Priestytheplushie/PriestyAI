import discord
import logging

logger = logging.getLogger("UIComponents")

class SearchButton(discord.ui.Button):
    def __init__(self, queries: list[str]):
        super().__init__(style=discord.ButtonStyle.secondary, label="View Search Results", emoji="🔍")
        self.queries = queries

    async def callback(self, interaction: discord.Interaction):
        text = "**Google Search Queries used:**\n" + "\n".join(f"- `{q}`" for q in self.queries)
        await interaction.response.send_message(content=text, ephemeral=True)


class CodeButton(discord.ui.Button):
    def __init__(self, code_blocks: list[str]):
        super().__init__(style=discord.ButtonStyle.secondary, label="View Code Execution", emoji="💻")
        self.code_blocks = code_blocks

    async def callback(self, interaction: discord.Interaction):
        blocks = []
        for i, code in enumerate(self.code_blocks):
            blocks.append(f"**Execution Block {i+1}:**\n```python\n{code}\n```")
        text = "\n\n".join(blocks)
        
        if len(text) > 2000:
            text = text[:1990] + "\n..."
            
        await interaction.response.send_message(content=text, ephemeral=True)


class DynamicModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list[tuple[str, str]], bot_instance, channel):
        super().__init__(title=title[:45])
        self.bot = bot_instance
        self.channel = channel
        
        for f_name, f_style in fields:
            style = discord.TextStyle.paragraph if f_style.lower() == "long" else discord.TextStyle.short
            self.add_item(discord.ui.TextInput(
                label=f_name[:45],
                custom_id=f_name[:100],
                style=style,
                required=True
            ))

    async def on_submit(self, interaction: discord.Interaction):
        results = [f"{item.custom_id}: {item.value}" for item in self.children]
        summary = " | ".join(results)
        
        await interaction.response.defer()
        
        action_text = f"{interaction.user.display_name} submitted form '{self.title}' with data: {summary}"
        self.bot.history_tracker.add_system_action(self.channel.id, action_text)
        await self.bot.trigger_ai_reply(self.channel, interaction.user)


class DynamicView(discord.ui.View):
    def __init__(self, bot_instance, channel, search_queries=None, code_blocks=None):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.channel = channel

        if search_queries:
            self.add_item(SearchButton(search_queries))
        if code_blocks:
            self.add_item(CodeButton(code_blocks))

    async def _handle_interaction(self, interaction: discord.Interaction, action_text: str):
        for child in self.children:
            child.disabled = True
            
        await interaction.response.edit_message(view=self)
        
        self.bot.history_tracker.add_system_action(self.channel.id, action_text)
        await self.bot.trigger_ai_reply(self.channel, interaction.user)

    def add_dynamic_button(self, label: str, color_str: str, emoji_str: str = None):
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger
        }
        button = discord.ui.Button(
            style=style_map.get(color_str.lower(), discord.ButtonStyle.primary), 
            label=label, 
            emoji=emoji_str if emoji_str else None
        )

        async def button_callback(interaction: discord.Interaction):
            await self._handle_interaction(interaction, f"{interaction.user.display_name} clicked button: {label}")
        button.callback = button_callback
        self.add_item(button)

    def add_modal_trigger_button(self, button_label: str, fields: list[tuple[str, str]]):
        button = discord.ui.Button(style=discord.ButtonStyle.success, label=button_label, emoji="📝")
        async def modal_callback(interaction: discord.Interaction):
            await interaction.response.send_modal(DynamicModal(button_label, fields, self.bot, self.channel))
        button.callback = modal_callback
        self.add_item(button)

    def add_dropdown(self, placeholder: str, options: list[tuple[str, str, str]]):
        select_options = []
        for opt_label, opt_desc, opt_emoji in options:
            select_options.append(discord.SelectOption(
                label=opt_label,
                description=opt_desc if opt_desc else None,
                emoji=opt_emoji if opt_emoji else None
            ))
            
        select = discord.ui.Select(
            placeholder=placeholder, min_values=1, max_values=1,
            options=select_options
        )
        async def select_callback(interaction: discord.Interaction):
            await self._handle_interaction(interaction, f"{interaction.user.display_name} selected '{select.values[0]}' from dropdown")
        select.callback = select_callback
        self.add_item(select)

    def add_rerun_pagination(self, bot_instance, message_id: int):
        versions = bot_instance.rerun_cache.get(message_id, [])
        current_idx = bot_instance.rerun_indexes.get(message_id, 0)
        total_versions = len(versions)

        prev_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="◀",
            disabled=(current_idx <= 0),
            row=4
        )
        async def prev_callback(interaction: discord.Interaction):
            bot_instance.rerun_indexes[message_id] -= 1
            await self._show_version(interaction, bot_instance, message_id)
        prev_btn.callback = prev_callback
        self.add_item(prev_btn)

        indicator_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label=f"{current_idx + 1} / {total_versions}",
            disabled=True,
            row=4
        )
        self.add_item(indicator_btn)

        next_btn = discord.ui.Button(
            style=discord.ButtonStyle.secondary,
            label="▶",
            disabled=(current_idx >= total_versions - 1),
            row=4
        )
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

            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.label == "◀":
                        child.disabled = (current_idx <= 0)
                    elif " / " in child.label:
                        child.label = f"{current_idx + 1} / {total_versions}"
                    elif child.label == "▶":
                        child.disabled = (current_idx >= total_versions - 1)

            await interaction.response.edit_message(content=new_content, view=self, attachments=attachments)