import re

TRANSITION_TAXONOMY = [
    (
        r"(?i)^(okay|first|to start|let's begin|let me understand|the user is asking|understanding|i need to understand)",
        "Understanding Request"
    ),
    (
        r"(?i)^(now|next|looking at|let's check|evaluating|considering|analyzing|examining|regarding)",
        "Analyzing Context & Constraints"
    ),
    (
        r"(?i)^(let me search|searching for|calling tool|i need to find|querying|looking up|search_web|read_link)",
        "Executing Research & Verification"
    ),
    (
        r"(?i)^(calculating|computing|writing code|debugging|verifying|processing|deriving|proving|tracing)",
        "Processing Logic & Derivations"
    ),
    (
        r"(?i)^(in contrast|on the other hand|comparing|alternative|trade-offs|alternatively|however)",
        "Evaluating Architectural Trade-offs"
    ),
    (
        r"(?i)^(finally|in summary|now i can answer|formulating the response|in conclusion|synthesizing|to conclude)",
        "Formulating Final Response"
    )
]

def standardize_thoughts_text(raw_thoughts: str) -> str:
    if not raw_thoughts or not raw_thoughts.strip():
        return "No intermediate reasoning steps recorded."

    cleaned_raw = raw_thoughts.strip()

    if re.search(r"\*\*[A-Za-z\s&]+\*\*", cleaned_raw):
        return re.sub(r"\n{3,}", "\n\n", cleaned_raw)

    cleaned_raw = re.sub(r"(?i)^(okay,\s*so\s*|let me see,\s*|well,\s*|let's think,\s*)", "", cleaned_raw)

    sentences = re.split(r'(?<=[.!?])\s+', cleaned_raw)
    
    formatted_sections: list[tuple[str, list[str]]] = []
    current_title = "Understanding Request"
    current_sentences: list[str] = []

    for sentence in sentences:
        s_clean = sentence.strip()
        if not s_clean:
            continue

        matched_title = None
        for pattern, title in TRANSITION_TAXONOMY:
            if re.search(pattern, s_clean):
                matched_title = title
                break

        if matched_title and matched_title != current_title:
            if current_sentences:
                formatted_sections.append((current_title, current_sentences))
                current_sentences = []
            current_title = matched_title

        s_clean = re.sub(r"(?i)^(okay|first|now|next|finally|in summary|let me check)\s*,\s*", "", s_clean)
        if s_clean:
            s_clean = s_clean[0].upper() + s_clean[1:]
        current_sentences.append(s_clean)

    if current_sentences:
        formatted_sections.append((current_title, current_sentences))

    output_blocks = []
    for title, s_list in formatted_sections:
        paragraph = " ".join(s_list)
        output_blocks.append(f"**{title}**\n{paragraph}")

    return "\n\n".join(output_blocks)

class LiveThoughtStandardizer:
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
            for pattern, title in TRANSITION_TAXONOMY:
                if re.search(pattern, cleaned) and self.current_heading != title:
                    self.current_heading = title
                    return f"\n\n**{title}**\n{cleaned} "

            return complete_sentence

        return ""

    def flush(self) -> str:
        remaining = self.buffer
        self.buffer = ""
        return remaining