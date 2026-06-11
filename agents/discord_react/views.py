
import discord
import json
import uuid
import logging
from typing import List, Dict, Any

logger = logging.getLogger("ReactViews")

class AgentStepDiagnosticsView(discord.ui.View):
    def __init__(self, step_index: int, thought: str, tool: str, args: dict, observation: str):
        super().__init__(timeout=900.0)
        self.step_index = step_index
        self.thought = thought
        self.tool = tool
        self.args_str = json.dumps(args, indent=2)
        self.observation = observation
        
        self.active_tab = "thoughts"
        self._update_buttons()

    def get_content(self) -> str:
        if self.active_tab == "thoughts":
            trimmed_thought = self.thought[:1800] + ("..." if len(self.thought) > 1800 else "")
            return (
                f"🧠 **Step {self.step_index + 1} - Internal Thoughts**\n"
                f"----------------------------------------\n"
                f"> {trimmed_thought}"
            )
        elif self.active_tab == "tool":
            trimmed_args = self.args_str[:1600] + ("..." if len(self.args_str) > 1600 else "")
            return (
                f"🛠️ **Step {self.step_index + 1} - Task Details**\n"
                f"----------------------------------------\n"
                f"• **Task Name:** `{self.tool}`\n"
                f"• **Arguments Payload:**\n"
                f"```json\n"
                f"{trimmed_args}\n"
                f"```"
            )
        else:
            trimmed_obs = self.observation[:1700] + ("..." if len(self.observation) > 1700 else "")
            return (
                f"📋 **Step {self.step_index + 1} - Gathered Findings**\n"
                f"----------------------------------------\n"
                f"```text\n"
                f"{trimmed_obs}\n"
                f"```"
            )

    def _update_buttons(self) -> None:
        self.thoughts_tab.style = discord.ButtonStyle.primary if self.active_tab == "thoughts" else discord.ButtonStyle.secondary
        self.tool_tab.style = discord.ButtonStyle.primary if self.active_tab == "tool" else discord.ButtonStyle.secondary
        self.observation_tab.style = discord.ButtonStyle.primary if self.active_tab == "observation" else discord.ButtonStyle.secondary

    @discord.ui.button(label="Thoughts", style=discord.ButtonStyle.primary, emoji="🧠", row=0)
    async def thoughts_tab(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.active_tab = "thoughts"
        self._update_buttons()
        await interaction.response.edit_message(content=self.get_content(), view=self)

    @discord.ui.button(label="Tool Call", style=discord.ButtonStyle.secondary, emoji="🛠️", row=0)
    async def tool_tab(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.active_tab = "tool"
        self._update_buttons()
        await interaction.response.edit_message(content=self.get_content(), view=self)

    @discord.ui.button(label="Observation", style=discord.ButtonStyle.secondary, emoji="📋", row=0)
    async def observation_tab(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.active_tab = "observation"
        self._update_buttons()
        await interaction.response.edit_message(content=self.get_content(), view=self)


class AgentStepButton(discord.ui.Button):
    def __init__(self, step_index: int, session_id: int):
        super().__init__(style=discord.ButtonStyle.secondary, label="View Step", custom_id=f"agent_step_{session_id}_{step_index}")
        self.step_index = step_index
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session or self.step_index >= len(session.react_history):
            await interaction.response.send_message("❌ Step details no longer available in runtime memory.", ephemeral=True)
            return
            
        step_data = session.react_history[self.step_index]
        thought = step_data.get("thought", "No thought details compiled.")
        tool = step_data.get("tool", "None")
        args = step_data.get("args", {})
        observation = step_data.get("observation", "No observation returned.")
        
        tabbed_view = AgentStepDiagnosticsView(
            step_index=self.step_index,
            thought=thought,
            tool=tool,
            args=args,
            observation=observation
        )
        await interaction.response.send_message(content=tabbed_view.get_content(), view=tabbed_view, ephemeral=True)


class AgentContinuationView(discord.ui.View):
    def __init__(self, session_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id

    @discord.ui.button(label="Continue 15 Steps", style=discord.ButtonStyle.success, emoji="✅")
    async def continue_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.max_steps += 15
        session.status = "running"
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        interaction.client.loop.create_task(session.execute_tick(interaction.client))

    @discord.ui.button(label="Stop & Report Findings", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.status = "completed"
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        await session.finalize_report(interaction.client)


class AgentErrorView(discord.ui.View):
    def __init__(self, session_id: int, error_text: str):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.error_text = error_text

    @discord.ui.button(label="Retry Step", style=discord.ButtonStyle.success, emoji="🔄")
    async def retry_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
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

    @discord.ui.button(label="Stop & Report Findings", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = interaction.client.active_agent_sessions.get(self.session_id)
        if not session:
            await interaction.response.send_message("❌ Agent session expired.", ephemeral=True)
            return
            
        await interaction.response.defer()
        session.status = "completed"
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        await session.finalize_report(interaction.client)