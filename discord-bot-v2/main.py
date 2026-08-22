import sys
import asyncio
import logging
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from src.core.config import config
from src.core.bot import PriestyBot

console = Console()

def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
                tracebacks_show_locals=False
            )
        ]
    )
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)

def print_banner() -> None:
    table = Table(title="✨ PriestyAI v2 Engine Initialized ✨", border_style="cyan")
    table.add_column("Parameter", style="bold green")
    table.add_column("Value", style="bold white")
    table.add_row("Gemini API Keys Loaded", str(len(config.gemini_keys)))
    table.add_row("Database", config.database_path)
    table.add_row("Owner ID", str(config.owner_id))
    table.add_row("Intents", "discord.Intents.all()")
    console.print(table)

async def main() -> None:
    setup_logging()
    print_banner()
    logger = logging.getLogger("PriestyAI.Main")
    logger.info("[bold cyan]Starting PriestyAI discord v2 gateway...[/bold cyan]", extra={"markup": True})

    bot = PriestyBot()

    async with bot:
        await bot.start(config.discord_token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]PriestyAI shutdown requested by user. Exiting cleanly.[/bold yellow]")