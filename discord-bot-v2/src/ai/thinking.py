import random
import time
from dataclasses import dataclass
from typing import List, Optional

THINKING_MESSAGES: List[str] = [
    "Thinking...",
    "Cooking up an answer...",
    "Consulting the digital void...",
    "Analyzing the prompt...",
    "Taking out the trash...",
    "Deciphering your message...",
    "Formulating a masterpiece...",
    "Querying the mainframe...",
    "Connecting the neural synapses...",
    "Brewing a response...",
    "Checking with the elders...",
    "Untangling spaghetti logic...",
    "Calculating the meaning of your prompt...",
    "Gathering thoughts...",
    "Rummaging through memory banks..."
]

@dataclass
class ThinkingSession:
    start_time: float
    model_name: str
    thinking_level: str
    status_messages: List[str]
    end_time: Optional[float] = None
    thought_content: Optional[str] = None

    @classmethod
    def start(cls, model_name: str, thinking_level: str = "medium") -> "ThinkingSession":
        chosen = random.sample(THINKING_MESSAGES, min(6, len(THINKING_MESSAGES)))
        return cls(
            start_time=time.time(),
            model_name=model_name,
            thinking_level=thinking_level,
            status_messages=chosen
        )

    def finish(self, thought_content: Optional[str] = None) -> float:
        self.end_time = time.time()
        self.thought_content = thought_content
        return self.duration

    @property
    def duration(self) -> float:
        end = self.end_time or time.time()
        return max(0.1, round(end - self.start_time, 2))