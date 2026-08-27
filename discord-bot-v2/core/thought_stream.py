import re
import asyncio
import logging
from google.genai import types
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.ThoughtStream")

THOUGHT_FORMATTER_SYSTEM_INSTRUCTION = """You are the internal reasoning formatter for PriestyAI.
Your task is to take the model's raw scratchpad monologue and structure it into authentic, first-person reasoning steps matching the exact voice, depth, and cadence of Gemini 3.7 Flash.

STRICT FORMATTING & VOICE DIRECTIVES:
1. FIRST-PERSON ACTIVE VOICE ("I", "I'm", "Let me"):
   - Write from the internal perspective of the AI actively thinking and solving the problem.
   - Example style:
     "I'm analyzing the user's requirement for an LRU cache with TTL eviction. I need to make sure both capacity limits and time expiration work in O(1) time without blocking lookups. Let me check if Python's OrderedDict is sufficient or if a custom doubly-linked list is necessary..."
   - NEVER write in the third-person or sound like an explanatory essay/textbook (e.g., avoid "Designing an LRU cache requires harmonizing two strategies...").

2. DYNAMIC TITLES: Group the thoughts into logical conceptual phases. Prepend each phase with a bold semantic title on its own line: **Title**
   Examples:
   - **Analyzing Architecture & TTL Eviction Constraints**
   - **Evaluating Data Structures & Lock Mechanics**
   - **Tracing Edge Cases & Expiration Boundaries**
   - **Formulating Final Implementation**

3. PARAGRAPH PROSE: Write in complete, analytical sentences inside full prose paragraphs under each bold title. Do NOT reduce reasoning to bulleted checklists.

4. COMPLETE FIDELITY: Preserve 100% of all technical logic, algorithms, equations, variable names, and code snippets. Do NOT invent new decisions or omit complex steps.

5. CLEAN SLOP: Remove conversational filler words (e.g. "Okay so let's see", "Hmm wait", "Let me check", "Well...").

6. Output ONLY the formatted markdown thoughts with zero meta-commentary.
"""

def standardize_thoughts_text(raw_thoughts: str) -> str:
    if not raw_thoughts or not raw_thoughts.strip():
        return "No intermediate reasoning steps recorded."

    cleaned_raw = raw_thoughts.strip()
    paragraphs = [p.strip() for p in cleaned_raw.split("\n\n") if p.strip()]
    return "\n\n".join(paragraphs) if paragraphs else cleaned_raw

async def format_thoughts_with_llm(raw_thoughts: str) -> str:
    if not raw_thoughts or not raw_thoughts.strip():
        return ""

    client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
    if not client:
        return standardize_thoughts_text(raw_thoughts)

    try:
        config = types.GenerateContentConfig(
            system_instruction=THOUGHT_FORMATTER_SYSTEM_INSTRUCTION,
            temperature=0.2
        )
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=active_model,
                contents=f"Raw Model Thinking:\n{raw_thoughts[:7500]}",
                config=config
            ),
            timeout=4.5
        )
        if response.text and response.text.strip():
            return response.text.strip()
    except Exception as e:
        logger.warning(f"[ThoughtStream] JIT thought formatting failed or timed out: {e}")

    return standardize_thoughts_text(raw_thoughts)