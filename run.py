
import os
import logging
import asyncio
from dotenv import load_dotenv
from core.bot import FriendBot
from core.web_server import CanvasWebServer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

async def main():
    load_dotenv()
    
    web_server = CanvasWebServer(port=8080)
    
    bot = FriendBot()
    
    asyncio.create_task(web_server.start_server_task())
    
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logging.critical("No DISCORD_TOKEN found inside your environmental config.")
        return
        
    await bot.start(token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Application shut down cleanly by user request.")