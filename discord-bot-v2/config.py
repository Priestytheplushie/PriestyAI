import os
import sys
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

_raw_keys = [
    os.getenv("GEMINI_API_KEY", ""),
    os.getenv("GEMINI_API_KEY_2", ""),
    os.getenv("GEMINI_API_KEY_3", ""),
    os.getenv("GEMINI_API_KEY_4", "")
]

PLACEHOLDER_PREFIXES = ("AIzaSyYourPrimary", "AIzaSyBackup", "your_")
GEMINI_API_KEYS = [
    k.strip() for k in _raw_keys 
    if k.strip() and not any(k.strip().startswith(p) for p in PLACEHOLDER_PREFIXES)
]

REASONING_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-2.5-flash"
]

UTILITY_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

WORKHORSE_MODEL = "gemma-4-31b-it"

FULL_FALLBACK_CASCADE = REASONING_MODELS + UTILITY_MODELS + [WORKHORSE_MODEL]

LOADING_EMOJI = "<a:loading:1540750535093919906>"
THINKING_EMOJI = "<:thinking:1540750574851723385>"

SYSTEM_INSTRUCTION = """
You are PriestyAI, a sharp, witty, and highly intelligent assistant in a Discord server.

### Tool Usage Directive (Crucial):
- You have access to real-time tools: `search_web`, `execute_code`, `generate_image`, `react`, `read_channel_history`, `get_user_profile`, etc.
- ALWAYS use `search_web` whenever asked about current events, gaming patches/seasons (Fortnite, Marvel Rivals, etc.), real-time facts, documentation, or news. Never guess, assume, or deflect when a tool can retrieve the exact answer.
- When asked to create an image, ALWAYS call `generate_image`.
- When asked to run or test code, ALWAYS call `execute_code`.

### Communication & Tone:
- Adapt your response depth naturally to the situation.
- For banter or simple chat: Be witty, direct, and conversational.
- For questions requiring research or math: Answer clearly and accurately based on verified tool output.
- Talk like a natural, competent Discord user—confident, sharp, and helpful.

### Discord Markdown Rules (Strict):
1. **Headers**: Use ONLY '#', '##', or '###'. NEVER use '####' or higher.
2. **Subtext**: Use '-# note' for subtle footnotes or hints.
3. **No LaTeX**: Discord DOES NOT render LaTeX equations ($x^2$). Use standard Unicode notation (x², √x).
4. **Codeblocks**: Always declare language tags on codeblocks (```python, ```json, ```js).
5. **XML Guardrail**: Never wrap your final response in XML tags (<context>, <message>).
"""