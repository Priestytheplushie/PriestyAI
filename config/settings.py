import os
import tempfile
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("PriestyAI.Config")

logging.getLogger("google_genai.models").setLevel(logging.ERROR)

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is missing from .env")

BOT_OWNER_ID = os.getenv("BOT_OWNER_ID", "").strip()

DATABASE_ENCRYPTION_KEY = os.getenv("DATABASE_ENCRYPTION_KEY", "").strip()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
if GITHUB_TOKEN:
    logger.info("GitHub API authentication loaded via GITHUB_TOKEN (5,000 req/hr).")
else:
    logger.warning("GITHUB_TOKEN not found in .env. GitHub tool will run in unauthenticated mode (60 req/hr).")

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID", "").strip()
GITHUB_APP_PRIVATE_KEY_PATH = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "github_app.pem").strip()
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").strip()
SMEE_URL = os.getenv("SMEE_URL", "").strip()

if GITHUB_APP_ID and os.path.exists(GITHUB_APP_PRIVATE_KEY_PATH):
    logger.info(f"GitHub App integration online (App ID: {GITHUB_APP_ID}, Key: {GITHUB_APP_PRIVATE_KEY_PATH}).")
else:
    logger.warning("GitHub App credentials not fully configured. Pull Request publishing will require App configuration.")

GITHUB_APP_BOT_NAME = "PriestyAI[bot]"
GITHUB_APP_BOT_EMAIL = f"{GITHUB_APP_ID}+priestyai[bot]@users.noreply.github.com" if GITHUB_APP_ID else "priestyai[bot]@users.noreply.github.com"

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

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")

if GROQ_API_KEY:
    logger.info("Groq Cloud integration online (300+ tok/s).")
if OPENROUTER_API_KEY:
    logger.info("OpenRouter free tier integration online.")

FLAGSHIP_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-3.8-flash"
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

AGENT_WORKSPACES_ROOT = os.getenv(
    "AGENT_WORKSPACES_PATH",
    os.path.join(tempfile.gettempdir(), "priesty_agent_workspaces")
)
os.makedirs(AGENT_WORKSPACES_ROOT, exist_ok=True)

LOADING_EMOJI = "<a:loading:1540750535093919906>"
THINKING_EMOJI = "<:thinking:1540750574851723385>"

STREAM_DEBOUNCE_INTERVAL = 1.2
MAX_MESSAGE_CHUNK_SIZE = 1900