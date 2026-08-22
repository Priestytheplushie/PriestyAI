import os
import logging
from dataclasses import dataclass, field
from typing import List, Dict
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("PriestyAI.Config")

MODEL_TIERS: Dict[str, str] = {
    "gemini-2.5-flash": "flash",
    "gemini-3-flash": "flash",
    "gemini-3.5-flash": "flash",
    "gemini-3.6-flash": "flash",
    "gemini-3.7-flash": "flash",
    "gemini-3.1-flash-lite": "flash_lite",
    "gemini-3.5-flash-lite": "flash_lite",
    "gemma-4-31b-it": "gemma",
}

TIER_QUOTAS_PER_KEY: Dict[str, int] = {
    "flash": 20,
    "flash_lite": 500,
    "gemma": 14400,
}

@dataclass
class Config:
    discord_token: str
    owner_id: int
    gemini_keys: List[str] = field(default_factory=list)
    database_path: str = "data/priesty.db"

    @classmethod
    def load(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("CRITICAL: DISCORD_TOKEN is missing from environment or .env file.")

        owner_id_raw = os.getenv("OWNER_ID", "0").strip()
        try:
            owner_id = int(owner_id_raw)
        except ValueError:
            logger.warning("OWNER_ID is not a valid integer. Defaulting to 0.")
            owner_id = 0

        keys: List[str] = []
        primary_key = os.getenv("GEMINI_API_KEY", "").strip()
        if primary_key:
            keys.append(primary_key)

        for i in range(2, 10):
            extra_key = os.getenv(f"GEMINI_API_KEY_{i}", "").strip()
            if extra_key and extra_key not in keys:
                keys.append(extra_key)

        if not keys:
            raise ValueError("CRITICAL: No Gemini API keys found. Supply at least GEMINI_API_KEY.")

        logger.info(f"Loaded {len(keys)} Gemini API key(s) into key rotation pool.")

        return cls(
            discord_token=token,
            owner_id=owner_id,
            gemini_keys=keys,
            database_path="data/priesty.db"
        )

config = Config.load()