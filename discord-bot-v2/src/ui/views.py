import time
import discord
from typing import Optional, List, Dict, Any

class ThinkingTraceView(discord.ui.View):
    def __init__(
        self,
        model_used: str,
        duration: float,
        thought_content: Optional[str] = None,
        tools_executed: Optional[List[Dict[str, Any]]] = None
    ):
        super().__init__(timeout=600)
        self.model_used = model_used
        self.duration = duration
        self.thought_content = thought_content
        self.tools_executed = tools_executed or []

        has_thoughts = bool(self.thought_content and len(self.thought_content.strip()) > 0)
        has_tools = bool(self.tools_executed)
        action_count = len(self.tools_executed)

        if has_thoughts and has_tools:
            button_label = f"Thought for {duration:.1f}s • {action_count} Action{'s' if action_count > 1 else ''}"
        elif has_thoughts:
            button_label = f"Thought for {duration:.1f}s"
        else:
            button_label = f"{action_count} Action{'s' if action_count > 1 else ''} Taken"

        button_emoji = "<:thinking:1540750574851723385>"

        self.inspect_button = discord.ui.Button(
            label=button_label,
            emoji=button_emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"inspect_trace_{id(self)}"
        )
        self.inspect_button.callback = self._on_inspect_click
        self.add_item(self.inspect_button)

    @staticmethod
    def _format_human_action(tool: Dict[str, Any]) -> str:
        name = tool.get("name", "Action")
        args = tool.get("args", {})
        res = tool.get("result", {})

        if name == "reset_channel_memory":
            count = res.get("cleared_messages", 0)
            return f"**Memory Cleared**: Purged `{count}` messages from active channel context."

        elif name == "run_sandbox_code":
            lang = args.get("language", "code")
            code = args.get("code", "")
            stdout = res.get("stdout", "").strip()
            stderr = res.get("stderr", "").strip()

            out_block = f"\n**Output:**\n```{stdout[:400]}\n```" if stdout else ""
            err_block = f"\n**Error:**\n```{stderr[:250]}\n```" if stderr else ""
            return f"**Executed {lang.capitalize()} Sandbox Code:**\n```{lang}\n{code[:300]}\n```{out_block}{err_block}"

        elif name == "generate_image":
            prompt = args.get("prompt", "image")
            return f"**Generated Image**: *\"{prompt[:200]}\"*"

        elif name == "render_latex_math":
            formula = args.get("latex_code", "")
            return f"**Rendered Formula**: `${formula[:150]}$`"

        elif name == "web_search":
            query = args.get("query", "")
            return f"**Searched Web**: *\"{query[:150]}\"*"

        elif name == "scrape_website":
            url = args.get("url", "")
            return f"**Read Webpage**: `{url[:100]}`"

        elif name == "get_user_profile":
            return f"**Looked Up User Profile**: ID `{args.get('user_id')}`"

        elif name == "read_channel_messages":
            return f"**Read Channel History**: Channel `{args.get('channel_id')}`"

        elif name == "react_to_message":
            return f"**Added Reaction**: {args.get('emoji', '👍')}"

        elif name == "watch_channel":
            return f"**Started Channel Watch**: `{args.get('duration_minutes', 30)}` minutes"

        return f"**Executed `{name}`**"

    async def _on_inspect_click(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Process Details",
            color=discord.Color.dark_embed()
        )

        embed.add_field(name="Model", value=f"`{self.model_used}`", inline=True)
        embed.add_field(name="Duration", value=f"`{self.duration:.2f}s`", inline=True)

        if self.tools_executed:
            actions_formatted = [self._format_human_action(t) for t in self.tools_executed]
            embed.add_field(
                name="Actions Taken",
                value="\n\n".join(actions_formatted)[:1024],
                inline=False
            )

        if self.thought_content and len(self.thought_content.strip()) > 0:
            cleaned_trace = self.thought_content.strip()
            if len(cleaned_trace) > 2000:
                cleaned_trace = cleaned_trace[:2000] + "\n\n...[trace truncated]"
            embed.add_field(
                name="Internal Reasoning",
                value=f"```markdown\n{cleaned_trace}\n```",
                inline=False
            )

        current_time = time.strftime("%I:%M %p")
        embed.set_footer(text=f"{self.model_used} • {self.duration:.2f}s • {current_time}")
        await interaction.response.send_message(embed=embed, ephemeral=True)


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