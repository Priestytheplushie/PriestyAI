import os
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PriestyAI.Config")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is missing from .env")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
if GITHUB_TOKEN:
    logger.info("GitHub API authentication loaded via GITHUB_TOKEN (5,000 req/hr).")
else:
    logger.warning("GITHUB_TOKEN not found in .env. GitHub tool will run in unauthenticated mode (60 req/hr).")

GEMINI_API_KEYS: list[str] = []
primary_key = os.getenv("GEMINI_API_KEY")
if primary_key:
    GEMINI_API_KEYS.append(primary_key)

for i in range(2, 10):
    k = os.getenv(f"GEMINI_API_KEY_{i}")
    if k:
        GEMINI_API_KEYS.append(k)

if not GEMINI_API_KEYS:
    raise ValueError("At least one GEMINI_API_KEY must be provided in .env")

logger.info(f"Loaded {len(GEMINI_API_KEYS)} Gemini API key(s) into active rotation pool.")

FLAGSHIP_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-2.5-flash"
]

LITE_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite"
]

WORKHORSE_DENSE_MODEL = "gemma-4-31b-it"
WORKHORSE_MOE_MODEL = "gemma-4-26b-a4b-it"
WORKHORSE_MODEL = WORKHORSE_DENSE_MODEL
GEMMA_MODELS = [WORKHORSE_DENSE_MODEL, WORKHORSE_MOE_MODEL]

ROUTER_PRIMARY = "gemini-3.5-flash-lite"
ROUTER_FALLBACK = "gemini-3.5-flash-lite"

SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")

LOADING_EMOJI = "<a:loading:1540750535093919906>"
THINKING_EMOJI = "<:thinking:1540750574851723385>"

STREAM_DEBOUNCE_INTERVAL = 1.2
MAX_MESSAGE_CHUNK_SIZE = 1900