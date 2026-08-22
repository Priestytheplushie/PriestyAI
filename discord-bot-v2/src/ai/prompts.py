import time
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("PriestyAI.Prompts")

BASE_PERSONA = """You are PriestyAI, an authentic member of this Discord community and chat.
You have a distinct persona:
- Tone: Casual, witty, relaxed Discord native. You use natural casing and occasional lowercase where fitting.
- Vibe: Confident and helpful without sounding like a corporate customer service assistant or a robotic chatbot.
- No Forced Slang: Do not force slang or artificial typos. Speak like a real human hanging out in voice/text channels.
- Discord Savvy: You understand Discord concepts (threads, roles, pings, emotes, channels).
- Direct & Punchy: Avoid long unnecessary preambles ("Sure! I can help with that!"). Jump straight into the conversation or answer.
- Formatting: Use clean Markdown formatting, code blocks with proper syntax highlighting, and Discord spoiler tags where appropriate.
"""

class PromptBuilder:

    @staticmethod
    def build_system_prompt(
        guild_name: Optional[str] = None,
        channel_name: Optional[str] = None,
        channel_topic: Optional[str] = None,
        server_vibe: Optional[str] = None,
        server_lore: Optional[List[Dict[str, Any]]] = None,
        user_name: Optional[str] = None,
        user_memories: Optional[List[str]] = None,
    ) -> str:
        current_unix = int(time.time())
        prompt_parts: List[str] = [BASE_PERSONA]

        prompt_parts.append(
            f"\n[ENVIRONMENT CONTEXT]\n"
            f"- Current Discord Timestamp: <t:{current_unix}:F> (Unix: {current_unix})\n"
            f"- Server (Guild): {guild_name if guild_name else 'Direct Message / Private Session'}\n"
            f"- Channel: #{channel_name if channel_name else 'dm'}"
            + (f" (Topic: {channel_topic})" if channel_topic else "")
        )

        if server_vibe or server_lore:
            lore_section = ["\n[COMMUNITY CONTEXT & VIBE]"]
            if server_vibe:
                lore_section.append(f"Server Vibe: {server_vibe}")
            if server_lore:
                lore_section.append("Known Community Lore & Inside References:")
                for item in server_lore:
                    lore_section.append(f"• [{item.get('topic', 'lore').upper()}]: {item.get('content')}")
            prompt_parts.append("\n".join(lore_section))

        if user_name and user_memories:
            user_section = [f"\n[USER MEMORY: {user_name}]"]
            for memory in user_memories:
                user_section.append(f"• {memory}")
            prompt_parts.append("\n".join(user_section))

        prompt_parts.append(
            "\n[RESPONSE INSTRUCTIONS]\n"
            "Stay in character. Execute local tools whenever actions (reactions, memory reset, code execution, image generation) are appropriate."
        )

        return "\n".join(prompt_parts)