import re

class LiveThoughtStandardizer:
    TRANSITIONS = [
        (r"(?i)^(okay|first|to start|let's begin|let me understand|the user is asking|understanding)", "Understanding Request"),
        (r"(?i)^(now|next|looking at|let's check|evaluating|considering|analyzing)", "Analyzing Context"),
        (r"(?i)^(calculating|computing|writing code|debugging|verifying|processing)", "Processing Logic"),
        (r"(?i)^(finally|in summary|now i can answer|formulating the response|in conclusion)", "Formulating Final Answer")
    ]

    def __init__(self):
        self.buffer = ""
        self.current_heading = None

    def process_chunk(self, text_chunk: str) -> str:
        if "**" in text_chunk:
            return text_chunk

        self.buffer += text_chunk
        sentences = re.split(r'(\. |\n)', self.buffer)

        if len(sentences) > 1:
            complete_sentence = sentences[0] + sentences[1]
            self.buffer = "".join(sentences[2:])

            cleaned = complete_sentence.strip()
            for pattern, title in self.TRANSITIONS:
                if re.search(pattern, cleaned) and self.current_heading != title:
                    self.current_heading = title
                    return f"\n\n**{title}**\n{cleaned} "

            return complete_sentence

        return ""

    def flush(self) -> str:
        remaining = self.buffer
        self.buffer = ""
        return remaining