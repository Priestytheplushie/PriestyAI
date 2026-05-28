import discord
from collections import deque
from datetime import datetime
import logging

logger = logging.getLogger("MemorySystem")

class ChatHistoryTracker:
    def __init__(self, limit: int = 30):
        self.limit = limit
        self.histories = {} 

    def add_message(self, message: discord.Message):
        """Adds a Discord message to the channel's short-term history buffer."""
        channel_id = message.channel.id
        if channel_id not in self.histories:
            self.histories[channel_id] = deque(maxlen=self.limit)
        
        if isinstance(message.author, discord.Member) and message.author.nick:
            display_name = message.author.nick
        else:
            display_name = message.author.display_name or message.author.name

        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        message_struct = {
            "timestamp": timestamp,
            "author_name": message.author.name,
            "author_id": message.author.id,
            "display_name": display_name,
            "content": message.clean_content,
            "has_attachments": len(message.attachments) > 0
        }
        
        self.histories[channel_id].append(message_struct)

    def get_formatted_history(self, channel_id: int) -> str:
        """Returns the chronological transcript of the channel."""
        if channel_id not in self.histories or not self.histories[channel_id]:
            return "No previous conversations recorded in this channel."
        
        lines = []
        for msg in self.histories[channel_id]:
            attachment_note = " [Attached Media]" if msg['has_attachments'] else ""
            lines.append(f"[{msg['timestamp']}] [User: {msg['display_name']} (ID: {msg['author_id']})]: \"{msg['content']}\"{attachment_note}")
            
        return "\n".join(lines)