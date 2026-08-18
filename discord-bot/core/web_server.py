from aiohttp import web
import logging
import os

logger = logging.getLogger("WebServer")


async def handle_ping(request):
    """Simple health check endpoint for Render to ping."""
    return web.Response(text="Bot is alive and running 24/7!", status=200)


async def start_web_server():
    """Initializes and starts the lightweight web server inside the bot's event loop."""
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)

    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()

    try:

        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(
            f"Render keep-alive web server successfully bound to 0.0.0.0:{port}"
        )
    except Exception as e:
        logger.error(f"Failed to start keep-alive web server on port {port}: {e}")
