import time
import logging
import asyncio
from google import genai
from google.genai.errors import APIError
from config.settings import (
    GEMINI_API_KEYS,
    FLAGSHIP_MODELS,
    LITE_MODELS,
    WORKHORSE_MODEL
)

logger = logging.getLogger("PriestyAI.ClientManager")

class KeyModelManager:
    def __init__(self):
        self.clients: list[genai.Client] = [genai.Client(api_key=k) for k in GEMINI_API_KEYS]
        self.key_count = len(self.clients)
        self.cooldowns: dict[tuple[int, str], float] = {}
        self.current_key_indices: dict[str, int] = {}

    def _is_available(self, key_idx: int, model_name: str) -> bool:
        cooldown_until = self.cooldowns.get((key_idx, model_name), 0)
        return time.time() >= cooldown_until

    def get_client(self, model_name: str) -> tuple[genai.Client, int, str]:
        model_candidates = self._get_cascade_list(model_name)

        for candidate_model in model_candidates:
            start_idx = self.current_key_indices.get(candidate_model, 0)
            for offset in range(self.key_count):
                idx = (start_idx + offset) % self.key_count
                if self._is_available(idx, candidate_model):
                    self.current_key_indices[candidate_model] = (idx + 1) % self.key_count
                    return self.clients[idx], idx, candidate_model

        logger.warning(f"All keys/models exhausted for {model_name}. Forcing fallback to workhorse {WORKHORSE_MODEL}.")
        return self.clients[0], 0, WORKHORSE_MODEL

    def _get_cascade_list(self, model_name: str) -> list[str]:
        if model_name in FLAGSHIP_MODELS:
            idx = FLAGSHIP_MODELS.index(model_name)
            return FLAGSHIP_MODELS[idx:] + LITE_MODELS + [WORKHORSE_MODEL]
        elif model_name in LITE_MODELS:
            idx = LITE_MODELS.index(model_name)
            return LITE_MODELS[idx:] + [WORKHORSE_MODEL]
        return [WORKHORSE_MODEL]

    def report_error(self, key_idx: int, model_name: str, error: Exception):
        now = time.time()
        err_msg = str(error).lower()

        if "429" in err_msg or "resource_exhausted" in err_msg:
            if "quota" in err_msg or "daily" in err_msg:
                cooldown_time = 43200
                logger.error(f"[Quota Exceeded] Key #{key_idx} for model '{model_name}'. Cooldown: 12 hours.")
            else:
                cooldown_time = 15
                logger.warning(f"[Rate Limit 429] Key #{key_idx} for model '{model_name}'. Cooldown: 15 seconds.")
            self.cooldowns[(key_idx, model_name)] = now + cooldown_time

        elif "503" in err_msg or "unavailable" in err_msg or "overloaded" in err_msg:
            logger.warning(f"[Service Overloaded 503] Key #{key_idx} for model '{model_name}'. Cooldown: 5 seconds.")
            self.cooldowns[(key_idx, model_name)] = now + 5
        else:
            logger.error(f"[API Error] Key #{key_idx} with model '{model_name}': {error}")

client_manager = KeyModelManager()