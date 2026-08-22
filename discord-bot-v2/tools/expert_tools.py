import logging
from typing import Dict, Any
from google.genai import types

import config

logger = logging.getLogger("PriestyAI.ExpertTools")

async def execute_ask_expert(key_pool: Any, prompt: str) -> Dict[str, Any]:
    try:
        key_state = key_pool._get_next_available_key()
        if not key_state:
            key_state = key_pool.keys[0]

        gen_config = types.GenerateContentConfig(
            system_instruction="You are an expert reasoning engine. Provide the rigorous mathematical or logical solution directly.",
            temperature=0.3,
            max_output_tokens=8192
        )

        response = await key_state.client.aio.models.generate_content(
            model="gemini-3.7-flash",
            contents=[prompt],
            config=gen_config
        )

        return {
            "status": "success",
            "expert_model": "gemini-3.7-flash",
            "solution": response.text or "*(No output)*"
        }
    except Exception as e:
        logger.error(f"Ask expert tool failed: {e}")
        return {"status": "error", "error": str(e)}