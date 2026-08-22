import discord
from typing import Optional, List, Dict, Any

class ThinkingTraceModal(discord.ui.Modal, title="🧠 AI Reasoning & Thinking Trace"):
    def __init__(self, model_name: str, duration: float, thought_content: str):
        super().__init__()
        
        self.stats = discord.ui.TextInput(
            label="Execution Summary",
            default=f"Model: {model_name} | Duration: {duration:.2f}s",
            required=False,
            style=discord.TextStyle.short
        )
        self.add_item(self.stats)

        self.trace = discord.ui.TextInput(
            label="Internal Thought Process",
            default=thought_content[:3900] if thought_content else "No internal thinking trace recorded for this request.",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.trace)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()


class ThinkingTraceView(discord.ui.View):
    def __init__(self, model_used: str, duration: float, thought_content: Optional[str] = None):
        super().__init__(timeout=300)
        self.model_used = model_used
        self.duration = duration
        self.thought_content = thought_content

        button_label = f"Thought for {duration:.1f}s"
        button_emoji = "<:thinking:1540750574851723385>"

        self.inspect_button = discord.ui.Button(
            label=button_label,
            emoji=button_emoji,
            style=discord.ButtonStyle.secondary,
            custom_id="inspect_thinking_trace"
        )
        self.inspect_button.callback = self._on_inspect_click
        self.add_item(self.inspect_button)

    async def _on_inspect_click(self, interaction: discord.Interaction):
        modal = ThinkingTraceModal(
            model_name=self.model_used,
            duration=self.duration,
            thought_content=self.thought_content or "No thinking trace captured for this model generation."
        )
        await interaction.response.send_modal(modal)


class DynamicActionButtonsView(discord.ui.View):
    def __init__(self, buttons: List[Dict[str, Any]], timeout: float = 180):
        super().__init__(timeout=timeout)
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger
        }

        for btn in buttons[:5]:
            style = style_map.get(btn.get("style", "secondary"), discord.ButtonStyle.secondary)
            button = discord.ui.Button(
                label=btn.get("label", "Action"),
                custom_id=btn.get("custom_id", f"dynamic_btn_{btn.get('label')}"),
                style=style,
                emoji=btn.get("emoji")
            )
            self.add_item(button)