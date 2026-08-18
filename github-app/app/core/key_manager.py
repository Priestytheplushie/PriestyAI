import asyncio
import logging
import time
from typing import Dict, List, Optional, Set
from app.config import settings

logger = logging.getLogger("priesty.key_manager")


class KeyManager:

    def __init__(self) -> None:
        raw_keys = [
            settings.GEMINI_API_KEY,
            settings.GEMINI_API_KEY_2,
            settings.GEMINI_API_KEY_3,
            settings.GEMINI_API_KEY_4,
        ]
        self.keys: List[str] = [k.strip() for k in raw_keys if k and k.strip()]
        self._current_index: int = 0

        self._tpm_cooldowns: Dict[str, float] = {}

        self._rpd_exhausted: Dict[str, Set[str]] = {k: set() for k in self.keys}

        if not self.keys:
            logger.warning("No GEMINI_API_KEY found in configuration!")

    def get_available_key(self, model: str) -> Optional[str]:
        if not self.keys:
            return None

        now = time.time()
        num_keys = len(self.keys)

        for _ in range(num_keys):
            key = self.keys[self._current_index]
            self._current_index = (self._current_index + 1) % num_keys

            if key in self._tpm_cooldowns and now < self._tpm_cooldowns[key]:
                continue

            if model in self._rpd_exhausted.get(key, set()):
                continue

            return key

        return None

    def mark_tpm_limit(self, key: str, cooldown_seconds: int = 180) -> None:
        logger.warning(
            f"Key (...{key[-6:]}) hit TPM rate limit. Cooldown for {cooldown_seconds}s."
        )
        self._tpm_cooldowns[key] = time.time() + cooldown_seconds

    def mark_rpd_limit(self, key: str, model: str) -> None:
        logger.warning(f"Key (...{key[-6:]}) exhausted daily RPD for model '{model}'.")
        if key not in self._rpd_exhausted:
            self._rpd_exhausted[key] = set()
        self._rpd_exhausted[key].add(model)


key_manager = KeyManager()
