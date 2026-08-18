import discord
from typing import Optional, List, Dict, Any


class BaseAgentSession:
<<<<<<< HEAD:agents/base_agent.py
    """
    Abstract blueprint representing an active Agent session.
    Guarantees all specialized Agent loops share common structural attributes.
    """
=======
>>>>>>> 982e394 (feat: initial monorepo setup for discord bot and github app):discord-bot/agents/base_agent.py

    def __init__(
        self,
        thread_id: int,
        user_id: int,
        prompt: str,
        loaded_contexts: str,
        channel: discord.Thread,
    ):
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
<<<<<<< HEAD:agents/base_agent.py
        """Converts step logs into clean historical timeline context."""
=======
>>>>>>> 982e394 (feat: initial monorepo setup for discord bot and github app):discord-bot/agents/base_agent.py
        raise NotImplementedError(
            "Subclasses must implement compile_react_transcript()"
        )

    async def execute_tick(self, bot) -> None:
        """Asynchronous execution controller tick driving the agent model forward."""
        raise NotImplementedError("Subclasses must implement execute_tick()")

    async def finalize_report(self, bot, raw_model_output: str = "") -> None:
<<<<<<< HEAD:agents/base_agent.py
        """Assembles and transmits the final output findings to the user channel."""
=======
>>>>>>> 982e394 (feat: initial monorepo setup for discord bot and github app):discord-bot/agents/base_agent.py
        raise NotImplementedError("Subclasses must implement finalize_report()")
