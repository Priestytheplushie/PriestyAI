import re
import time
import logging
from typing import Optional
from google import genai
from config.settings import (
    GEMINI_API_KEYS,
    FLAGSHIP_MODELS,
    LITE_MODELS,
    WORKHORSE_MODEL
)

logger = logging.getLogger("PriestyAI.ClientManager")

def parse_retry_delay(error_str: str) -> float:
    match = re.search(r'retry in\s*([0-9\.]+)\s*s', error_str, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 15.0

class KeyModelManager:
    def __init__(self):
        self.clients: list[genai.Client] = [genai.Client(api_key=k) for k in GEMINI_API_KEYS]
        self.key_count = len(self.clients)
        self.cooldowns: dict[tuple[int, str], float] = {}
        self.model_cooldowns: dict[str, float] = {}
        self.current_key_indices: dict[str, int] = {}

    def is_available(self, key_idx: int, model_name: str) -> bool:
        now = time.time()
        if now < self.model_cooldowns.get(model_name, 0):
            return False
        return now >= self.cooldowns.get((key_idx, model_name), 0)

    def is_completely_exhausted(self) -> bool:
        all_models = FLAGSHIP_MODELS + LITE_MODELS + [WORKHORSE_MODEL]
        for m in all_models:
            for idx in range(self.key_count):
                if self.is_available(idx, m):
                    return False
        return True

    def get_client_for_model(
        self, 
        model_name: str, 
        exclude_keys: Optional[set[int]] = None
    ) -> tuple[Optional[genai.Client], Optional[int], str]:
        exclude_keys = exclude_keys or set()
        cascade = self._get_cascade_list(model_name)
        
        for cand_model in cascade:
            start_idx = self.current_key_indices.get(cand_model, 0)
            for offset in range(self.key_count):
                idx = (start_idx + offset) % self.key_count
                
                if idx in exclude_keys:
                    continue
                    
                if self.is_available(idx, cand_model):
                    self.current_key_indices[cand_model] = (idx + 1) % self.key_count
                    return self.clients[idx], idx, cand_model

        logger.warning(f"All keys busy or excluded across cascade for '{model_name}'.")
        return None, None, model_name

    def _get_cascade_list(self, model_name: str) -> list[str]:
        if model_name in FLAGSHIP_MODELS:
            idx = FLAGSHIP_MODELS.index(model_name)
            return FLAGSHIP_MODELS[idx:] + [WORKHORSE_MODEL] + LITE_MODELS
        elif model_name == WORKHORSE_MODEL:
            return [WORKHORSE_MODEL] + LITE_MODELS + FLAGSHIP_MODELS
        elif model_name in LITE_MODELS:
            idx = LITE_MODELS.index(model_name)
            return LITE_MODELS[idx:] + [WORKHORSE_MODEL]
        return [WORKHORSE_MODEL]

    def report_error(self, key_idx: int, model_name: str, error: Exception):
        now = time.time()
        err_msg = str(error)
        err_lower = err_msg.lower()

        if "503" in err_lower or "unavailable" in err_lower or "overloaded" in err_lower:
            logger.warning(f"[Model Overloaded 503] Cooldown model '{model_name}' globally for 20s.")
            self.model_cooldowns[model_name] = now + 20
            self.cooldowns[(key_idx, model_name)] = now + 20

        elif "429" in err_lower or "resource_exhausted" in err_lower:
            if "perday" in err_lower or "requestsperday" in err_lower or "daily" in err_lower:
                logger.error(f"[Daily Quota Hit] Key #{key_idx} for '{model_name}'. Cooldown: 12 hours.")
                self.cooldowns[(key_idx, model_name)] = now + 43200
            else:
                delay = parse_retry_delay(err_msg)
                logger.warning(f"[Rate Limit 429 TPM/RPM] Key #{key_idx} for '{model_name}'. Cooldown: {delay:.1f}s. Switching key...")
                self.cooldowns[(key_idx, model_name)] = now + delay
        elif "timeout" in err_lower:
            logger.warning(f"[Timeout] Key #{key_idx} for '{model_name}': {err_msg}. Brief 5s cooldown.")
            self.cooldowns[(key_idx, model_name)] = now + 5
        else:
            logger.error(f"[API Error] Key #{key_idx} '{model_name}': {err_msg}")

client_manager = KeyModelManager()