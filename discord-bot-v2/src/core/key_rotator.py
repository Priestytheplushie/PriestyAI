import time
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime, timezone
from src.core.config import MODEL_TIERS, TIER_QUOTAS_PER_KEY

logger = logging.getLogger("PriestyAI.KeyRotator")

@dataclass
class KeyState:
    api_key: str
    key_index: int
    cooldown_until: float = 0.0
    tier_usage: Dict[str, int] = field(default_factory=lambda: {"flash": 0, "flash_lite": 0, "gemma": 0})
    total_requests: int = 0
    last_error: Optional[str] = None

    def is_cooling_down(self) -> bool:
        return time.time() < self.cooldown_until

    def remaining_quota(self, tier: str) -> int:
        max_quota = TIER_QUOTAS_PER_KEY.get(tier, 100)
        return max(0, max_quota - self.tier_usage.get(tier, 0))


class KeyRotator:
    def __init__(self, api_keys: List[str]):
        self.keys: List[KeyState] = [
            KeyState(api_key=key, key_index=i + 1)
            for i, key in enumerate(api_keys)
        ]
        self._lock = asyncio.Lock()
        self._current_day: int = datetime.now(timezone.utc).day

    async def _check_daily_reset(self) -> None:
        today = datetime.now(timezone.utc).day
        if today != self._current_day:
            async with self._lock:
                if today != self._current_day:
                    logger.info("New UTC day detected. Resetting daily API usage counters.")
                    for state in self.keys:
                        state.tier_usage = {"flash": 0, "flash_lite": 0, "gemma": 0}
                    self._current_day = today

    async def get_key_for_model(self, model_name: str) -> str:
        await self._check_daily_reset()
        tier = MODEL_TIERS.get(model_name, "flash_lite")

        async with self._lock:
            available_keys = [k for k in self.keys if not k.is_cooling_down()]

            if not available_keys:
                earliest = min(self.keys, key=lambda k: k.cooldown_until)
                wait_time = max(0.0, earliest.cooldown_until - time.time())
                logger.warning(f"All keys are in cooldown. Key #{earliest.key_index} expires in {wait_time:.1f}s.")
                if wait_time > 0:
                    await asyncio.sleep(min(wait_time, 5.0))
                return earliest.api_key

            best_key = max(available_keys, key=lambda k: k.remaining_quota(tier))
            
            if best_key.remaining_quota(tier) <= 2:
                logger.warning(
                    f"Key #{best_key.key_index} is nearly depleted for tier '{tier}' "
                    f"({best_key.tier_usage.get(tier, 0)}/{TIER_QUOTAS_PER_KEY.get(tier, 0)} used)."
                )

            return best_key.api_key

    async def report_success(self, api_key: str, model_name: str) -> None:
        tier = MODEL_TIERS.get(model_name, "flash_lite")
        async with self._lock:
            for state in self.keys:
                if state.api_key == api_key:
                    state.tier_usage[tier] = state.tier_usage.get(tier, 0) + 1
                    state.total_requests += 1
                    state.last_error = None
                    break

    async def report_error(self, api_key: str, error: Exception, status_code: Optional[int] = None) -> None:
        error_str = str(error)
        cooldown_seconds = 60.0

        if status_code == 429 or "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
            cooldown_seconds = 120.0
        elif status_code in (500, 503) or "503" in error_str or "UNAVAILABLE" in error_str:
            cooldown_seconds = 45.0

        async with self._lock:
            for state in self.keys:
                if state.api_key == api_key:
                    state.cooldown_until = time.time() + cooldown_seconds
                    state.last_error = error_str
                    logger.warning(
                        f"Key #{state.key_index} placed on {cooldown_seconds}s cooldown. "
                        f"Reason: {error_str[:120]}"
                    )
                    break