
import discord
from typing import Optional, List, Dict, Any

class BaseAgentSession:
    def __init__(self, thread_id: int, user_id: int, prompt: str, loaded_contexts: str, channel: discord.Thread):
        self.thread_id = thread_id
        self.user_id = user_id
        self.primary_task = prompt
        self.loaded_contexts = loaded_contexts
        self.channel = channel
        
        self.current_step: int = 0
        self.max_steps: int = 15
        self.status: str = "running"
        self.additional_instructions: str = ""
        self.target_guild_id: Optional[int] = None

    def compile_react_transcript(self) -> str:
        raise NotImplementedError("Subclasses must implement compile_react_transcript()")

    async def execute_tick(self, bot) -> None:
        raise NotImplementedError("Subclasses must implement execute_tick()")

    async def finalize_report(self, bot, raw_model_output: str = "") -> None:
        raise NotImplementedError("Subclasses must implement finalize_report()")