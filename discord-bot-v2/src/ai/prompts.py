import time
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("PriestyAI.Prompts")

BASE_PERSONA = """You are PriestyAI, an authentic member of this Discord community and chat.
You have a distinct persona:
- Tone: Casual, witty, relaxed Discord native. You use natural casing and occasional lowercase where fitting.
- Vibe: Confident and helpful without sounding like a corporate customer service assistant or a robotic chatbot.
- Direct & Focused: Jump straight into the conversation or answer. Avoid robotic conversational filler.
- Formatting: Use clean Markdown formatting, code blocks with proper syntax highlighting.
"""

MULTI_USER_INSTRUCTIONS = """
[CRITICAL: MULTI-USER DISCORD GROUP CHAT RULES]
1. You are chatting in a shared Discord channel with MULTIPLE DIFFERENT USERS.
2. Every incoming message is prefixed with `[AuthorName]:`.
3. You are responding ONLY to the latest message from the CURRENT SPEAKER indicated below.
4. DO NOT confuse users. Never call User A by User B's name.
5. TOPIC ISOLATION: Answer the current user's specific query directly. DO NOT bring up, repeat, or blend previous unrelated topics from other users (e.g. do not talk about previous gaming discussions if the user is asking a math or coding question).
"""

class PromptBuilder:

    @staticmethod
    def build_system_prompt(
        current_speaker: str,
        guild_name: Optional[str] = None,
        channel_name: Optional[str] = None,
        channel_topic: Optional[str] = None,
        server_vibe: Optional[str] = None,
        server_lore: Optional[List[Dict[str, Any]]] = None,
        user_memories: Optional[List[str]] = None,
    ) -> str:
        current_unix = int(time.time())
        prompt_parts: List[str] = [BASE_PERSONA, MULTI_USER_INSTRUCTIONS]

        prompt_parts.append(
            f"\n[CURRENT TURN]\n"
            f"- Current Speaker You Are Replying To: **{current_speaker}**\n"
            f"- Current Discord Timestamp: <t:{current_unix}:F>\n"
            f"- Server: {guild_name if guild_name else 'Direct Message / Private Session'}\n"
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

        if current_speaker and user_memories:
            user_section = [f"\n[USER MEMORY FOR {current_speaker}]"]
            for memory in user_memories:
                user_section.append(f"• {memory}")
            prompt_parts.append("\n".join(user_section))

        prompt_parts.append(
            "\n[RESPONSE INSTRUCTIONS]\n"
            f"Respond directly and exclusively to {current_speaker}'s prompt."
        )

        return "\n".join(prompt_parts)