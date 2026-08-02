import os
import logging
from dotenv import load_dotenv
from core.bot import FriendBot
from google import genai

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    load_dotenv()

    bot = FriendBot()
    bot.run_bot()
