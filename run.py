
import os
import logging
import subprocess
import sys
import atexit
from dotenv import load_dotenv
from core.bot import FriendBot

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger("Launcher")

node_process = None

def cleanup_node_process():
    """Ensures background Node.js process is cleanly killed on any shutdown."""
    global node_process
    if node_process and node_process.poll() is None:
        logger.info("Terminating headless Node.js voice companion service...")
        node_process.terminate()
        try:
            node_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            logger.warning("Node.js did not stop in time; forcing kill...")
            node_process.kill()
        logger.info("Node.js voice service terminated cleanly.")

atexit.register(cleanup_node_process)

if __name__ == "__main__":
    load_dotenv()
    
    project_root = os.path.dirname(os.path.abspath(__file__))
    voice_service_dir = os.path.join(project_root, "core", "voice_service")
    
    if os.path.exists(voice_service_dir):
        logger.info("Spawning headless Node.js voice companion service...")
        try:
            node_process = subprocess.Popen(
                ["node", "index.js"],
                cwd=voice_service_dir
            )
            logger.info("Headless Node.js voice companion successfully spawned in background.")
        except Exception as e:
            logger.error(f"Failed to automatically spawn Node.js voice service: {e}")
            logger.warning("You may need to run 'node index.js' manually inside 'core/voice_service/'.")
    else:
        logger.error(f"Could not locate voice_service folder at: {voice_service_dir}")

    try:
        bot = FriendBot()
        bot.run_bot()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received.")
    finally:
        cleanup_node_process()