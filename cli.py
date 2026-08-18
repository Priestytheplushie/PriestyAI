"""
PriestyAI CLI
An interactive terminal dashboard and headless process supervisor for running
and hot-reloading both the GitHub App and Discord Bot concurrently.
"""

import argparse
import asyncio
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.text import Text
from watchfiles import Change, awatch

ROOT_DIR = Path(__file__).parent.resolve()
GITHUB_DIR = ROOT_DIR / "github-app"
DISCORD_DIR = ROOT_DIR / "discord-bot"


def find_python_executable(project_dir: Path) -> str:
    """Finds the root or subfolder .venv python binary."""
    if sys.platform == "win32":
        sub_venv = project_dir / ".venv" / "Scripts" / "python.exe"
        root_venv = ROOT_DIR / ".venv" / "Scripts" / "python.exe"
    else:
        sub_venv = project_dir / ".venv" / "bin" / "python"
        root_venv = ROOT_DIR / ".venv" / "bin" / "python"

    if sub_venv.exists():
        return str(sub_venv)
    if root_venv.exists():
        return str(root_venv)
    return sys.executable


def colorize_github_log(line: str, highlight_query: str = "") -> Text:
    text = Text(line)
    low = line.lower()

    if any(
        kw in low for kw in ["error", "exception", "failed", "traceback", "status=5"]
    ):
        text.stylize("bold red")
    elif any(kw in low for kw in ["warning", "429", "rate limit", "rejected"]):
        text.stylize("bold yellow")
    elif "smee" in low:
        text.stylize("cyan")
    elif any(
        event in line
        for event in ["issues.", "pull_request", "discussion", "reaction."]
    ):
        text.stylize("bold magenta")
    elif any(
        kw in line for kw in ["200 OK", "completed successfully", "Created", "Triaged"]
    ):
        text.stylize("bold green")
    elif "INFO:" in line or "Uvicorn running" in line:
        text.stylize("dim white")

    if highlight_query:
        text.highlight_words(
            [highlight_query], style="black on bright_yellow", case_sensitive=False
        )

    return text


def colorize_discord_log(line: str, highlight_query: str = "") -> Text:
    text = Text(line)
    low = line.lower()

    if any(kw in low for kw in ["error", "exception", "failed", "crash", "traceback"]):
        text.stylize("bold red")
    elif any(kw in low for kw in ["warning", "429", "rate limit", "cooldown"]):
        text.stylize("bold yellow")
    elif any(
        kw in line for kw in ["Logged in as", "Shard ready", "synced", "Successfully"]
    ):
        text.stylize("bold green")
    elif any(kw in line for kw in ["[chat]", "Message from", "Spontaneous check-in"]):
        text.stylize("cyan")
    elif any(
        kw in line for kw in ["Thinking", "Reasoning", "Deduction", "generate_reply"]
    ):
        text.stylize("bold magenta")
    elif any(
        kw in line
        for kw in [
            "Server News",
            "NewsScraper",
            "video_generator",
            "FFmpeg",
            "Streamable",
        ]
    ):
        text.stylize("bold blue")
    elif any(
        kw in line
        for kw in ["Learned User Fact", "Learned Server Fact", "Memory Gatekeeper"]
    ):
        text.stylize("bright_green")
    elif any(kw in line for kw in ["LayoutView", "BUILD_MESSAGE", "Component"]):
        text.stylize("bright_cyan")

    if highlight_query:
        text.highlight_words(
            [highlight_query], style="black on bright_yellow", case_sensitive=False
        )

    return text


class ProcessManager:
    def __init__(self, name: str, cwd: Path, command: list[str]):
        self.name = name
        self.cwd = cwd
        self.command = command
        self.process: Optional[asyncio.subprocess.Process] = None
        self.is_running = False

    async def start(self, log_callback):
        if self.is_running:
            return

        self.is_running = True
        log_callback(f"[bold cyan]▶ Starting {self.name}...[/]", is_markup=True)

        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.command,
                cwd=str(self.cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )

            while self.is_running and self.process and self.process.stdout:
                line = await self.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    log_callback(decoded, is_markup=False)

        except asyncio.CancelledError:
            pass
        except Exception as err:
            log_callback(f"[bold red]❌ {self.name} crashed: {err}[/]", is_markup=True)
        finally:
            await self.stop(log_callback)

    async def stop(self, log_callback=None):
        self.is_running = False
        if self.process:
            if log_callback:
                log_callback(
                    f"[bold yellow]⏹ Stopping {self.name}...[/]", is_markup=True
                )
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
            self.process = None


async def run_headless_supervisor(start_github: bool, start_discord: bool, watch: bool):
    """Runs processes directly in standard terminal with clean SIGINT handling."""
    from rich.console import Console

    console = Console()
    gh_python = find_python_executable(GITHUB_DIR)
    dc_python = find_python_executable(DISCORD_DIR)

    github_proc = ProcessManager(
        name="GitHub App",
        cwd=GITHUB_DIR,
        command=[
            gh_python,
            "-m",
            "uvicorn",
            "app.main:app",
            "--port",
            "8000",
            "--host",
            "0.0.0.0",
        ],
    )

    discord_proc = ProcessManager(
        name="Discord Bot",
        cwd=DISCORD_DIR,
        command=[dc_python, "run.py"],
    )

    def log_gh(line: str, is_markup: bool = False):
        ts = datetime.now().strftime("%H:%M:%S")
        if is_markup:
            console.print(f"[dim]{ts}[/] [bold cyan][GITHUB][/]  {line}")
        else:
            colored = colorize_github_log(line)
            console.print(f"[dim]{ts}[/] [bold cyan][GITHUB][/]  ", end="")
            console.print(colored)

    def log_dc(line: str, is_markup: bool = False):
        ts = datetime.now().strftime("%H:%M:%S")
        if is_markup:
            console.print(f"[dim]{ts}[/] [bold magenta][DISCORD][/] {line}")
        else:
            colored = colorize_discord_log(line)
            console.print(f"[dim]{ts}[/] [bold magenta][DISCORD][/] ", end="")
            console.print(colored)

    tasks = []
    if start_github:
        tasks.append(asyncio.create_task(github_proc.start(log_gh)))
    if start_discord:
        tasks.append(asyncio.create_task(discord_proc.start(log_dc)))

    async def watch_loop():
        if not watch:
            return

        async def watch_github():
            async for changes in awatch(str(GITHUB_DIR / "app")):
                log_gh(
                    "[bold yellow]⚡ File change detected in app/. Reloading GitHub App...[/]",
                    is_markup=True,
                )
                await github_proc.stop(log_gh)
                asyncio.create_task(github_proc.start(log_gh))

        async def watch_discord():
            def watch_filter(change: Change, path: str) -> bool:
                p = path.replace("\\", "/").lower()
                if any(
                    x in p
                    for x in [
                        "/temp/",
                        "/temp_",
                        "__pycache__",
                        "/.git/",
                        ".venv",
                        ".mp4",
                        ".mp3",
                    ]
                ):
                    return False
                return p.endswith((".py", ".md", ".json"))

            async for changes in awatch(
                str(DISCORD_DIR), watch_filter=watch_filter, debounce=2500
            ):
                log_dc(
                    "[bold yellow]⚡ Code change detected. Debounce elapsed (2.5s). Restarting Discord Bot...[/]",
                    is_markup=True,
                )
                await discord_proc.stop(log_dc)
                asyncio.create_task(discord_proc.start(log_dc))

        await asyncio.gather(watch_github(), watch_discord())

    watch_task = asyncio.create_task(watch_loop()) if watch else None

    console.print(
        "[bold green]PriestyAI Headless Runner Active[/] (Press Ctrl+C to cleanly stop both services)"
    )

    try:
        await asyncio.gather(*tasks)
    except (asyncio.CancelledError, KeyboardInterrupt):
        console.print(
            "\n[bold yellow]🛑 Shutdown signal received. Stopping subprocesses...[/]"
        )
    finally:
        if watch_task:
            watch_task.cancel()
        await asyncio.gather(
            github_proc.stop(log_gh), discord_proc.stop(log_dc), return_exceptions=True
        )
        console.print("[bold green]✅ All services cleanly stopped.[/]")


def run_tui(start_github: bool, start_discord: bool, watch: bool):
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.reactive import reactive
    from textual.widgets import Button, Footer, Input, Label, RichLog

    class PriestyAICLI(App):
        CSS = """
        Screen {
            background: #000000;
            color: #e0e0e0;
            overflow: hidden;
        }

        * {
            scrollbar-size-vertical: 1;
            scrollbar-background: #000000;
            scrollbar-color: #333338;
            scrollbar-color-hover: #55555e;
            scrollbar-color-active: #7aa2f7;
        }
        
        #top-bar {
            dock: top;
            width: 100%;
            height: 3;
            background: #0a0a0c;
            border-bottom: solid #1c1c22;
            padding: 0 1;
            align-vertical: middle;
        }

        #top-bar-label {
            width: 1fr;
            padding: 0 1;
        }

        #quit-btn {
            background: #1c1c22;
            color: #ff5555;
            border: none;
            height: 1;
            padding: 0 1;
            min-width: 8;
            margin-right: 1;
        }

        #quit-btn:hover {
            background: #ff5555;
            color: #000000;
            text-style: bold;
        }

        #main-container {
            width: 100%;
            height: 1fr;
            padding: 0;
        }

        .pane-box {
            width: 1fr;
            height: 100%;
            border: solid #1c1c22;
            background: #050507;
            margin: 0;
            padding: 0 1;
        }

        .pane-box:focus-within {
            border: solid #3d4455;
        }

        .pane-title {
            text-style: bold;
            padding: 0 1;
            background: #0f0f13;
            color: #888899;
            margin-bottom: 0;
        }

        RichLog {
            height: 1fr;
            background: #050507;
            padding: 0;
        }

        #unified-pane {
            display: none;
            width: 100%;
            height: 100%;
            border: solid #1c1c22;
            background: #050507;
            padding: 0 1;
        }

        #unified-pane:focus-within {
            border: solid #3d4455;
        }

        #filter-container {
            dock: bottom;
            width: 100%;
            height: 3;
            background: #0a0a0c;
            border-top: solid #3d4455;
            padding: 0 1;
            display: none;
        }

        #filter-container.visible {
            display: block;
        }

        Input {
            background: #050507;
            border: none;
            color: #f0f0f0;
            height: 1;
        }

        Footer {
            background: #0a0a0c;
            color: #777788;
        }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
            Binding("q", "quit", "Quit", show=False),
            Binding("g", "toggle_github", "Toggle GitHub"),
            Binding("d", "toggle_discord", "Toggle Discord"),
            Binding("u", "toggle_unified", "Unified Stream (U)"),
            Binding("r", "restart_all", "Restart Both"),
            Binding("c", "clear_logs", "Clear Logs"),
            Binding("slash", "open_filter", "Search (/)", show=True),
            Binding("escape", "handle_escape", "Clear / Close", show=False),
        ]

        github_status = reactive("[bold red]STOPPED[/]")
        discord_status = reactive("[bold red]STOPPED[/]")
        filter_query = reactive("")
        is_unified_mode = reactive(False)

        def __init__(self):
            super().__init__()
            gh_python = find_python_executable(GITHUB_DIR)
            dc_python = find_python_executable(DISCORD_DIR)

            self.github_proc = ProcessManager(
                name="GitHub App",
                cwd=GITHUB_DIR,
                command=[
                    gh_python,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--port",
                    "8000",
                    "--host",
                    "0.0.0.0",
                ],
            )

            self.discord_proc = ProcessManager(
                name="Discord Bot",
                cwd=DISCORD_DIR,
                command=[dc_python, "run.py"],
            )

            self.unified_logs: list[tuple[float, str, str, bool]] = []

        def compose(self) -> ComposeResult:
            with Horizontal(id="top-bar"):
                yield Label("", id="top-bar-label")
                yield Button("✕ Quit", id="quit-btn", variant="error")

            with Horizontal(id="main-container"):
                with Vertical(classes="pane-box", id="github-pane"):
                    yield Label("GitHub App", classes="pane-title")
                    yield RichLog(
                        id="github-log", highlight=False, markup=True, wrap=True
                    )

                with Vertical(classes="pane-box", id="discord-pane"):
                    yield Label("Discord Bot", classes="pane-title")
                    yield RichLog(
                        id="discord-log", highlight=False, markup=True, wrap=True
                    )

                with Vertical(id="unified-pane"):
                    yield Label(
                        "Unified Log Stream (Chronological)", classes="pane-title"
                    )
                    yield RichLog(
                        id="unified-log", highlight=False, markup=True, wrap=True
                    )

            with Horizontal(id="filter-container"):
                yield Input(
                    placeholder="/search query (Enter to lock, Esc to cancel)...",
                    id="filter-input",
                )

            yield Footer()

        async def on_mount(self):
            self.update_top_bar()
            if start_github:
                self.run_github_app()
            if start_discord:
                self.run_discord_bot()
            if watch:
                self.start_file_watchers()

        def update_top_bar(self):
            bar = self.query_one("#top-bar-label", Label)
            mode_label = (
                "[magenta]UNIFIED[/]" if self.is_unified_mode else "[dim]SPLIT[/]"
            )
            filter_status = (
                f"  │  Filter: [yellow]'{self.filter_query}'[/]"
                if self.filter_query
                else ""
            )
            bar.update(
                f"[bold #ffffff]PriestyAI CLI[/]  │  "
                f"GitHub: {self.github_status}  │  "
                f"Discord: {self.discord_status}  │  "
                f"Watchfiles: {'[green]ACTIVE[/]' if watch else '[dim]OFF[/]'}  │  "
                f"View: {mode_label}"
                f"{filter_status}"
            )

        def log_github(self, line: str, is_markup: bool = False):
            ts = time.time()
            self.unified_logs.append((ts, "github", line, is_markup))
            if not self.filter_query or self.filter_query.lower() in line.lower():
                self.query_one("#github-log", RichLog).write(
                    Text.from_markup(line)
                    if is_markup
                    else colorize_github_log(line, self.filter_query)
                )
                if self.is_unified_mode:
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                    unified_line = f"[dim]{time_str}[/] [cyan][GITHUB][/]  {line}"
                    self.query_one("#unified-log", RichLog).write(
                        colorize_github_log(unified_line, self.filter_query)
                    )

        def log_discord(self, line: str, is_markup: bool = False):
            ts = time.time()
            self.unified_logs.append((ts, "discord", line, is_markup))
            if not self.filter_query or self.filter_query.lower() in line.lower():
                self.query_one("#discord-log", RichLog).write(
                    Text.from_markup(line)
                    if is_markup
                    else colorize_discord_log(line, self.filter_query)
                )
                if self.is_unified_mode:
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                    unified_line = f"[dim]{time_str}[/] [magenta][DISCORD][/] {line}"
                    self.query_one("#unified-log", RichLog).write(
                        colorize_discord_log(unified_line, self.filter_query)
                    )

        @work(exclusive=True, group="github_worker")
        async def run_github_app(self):
            self.github_status = "[bold green]RUNNING (:8000)[/]"
            self.update_top_bar()
            await self.github_proc.start(self.log_github)
            self.github_status = "[bold red]STOPPED[/]"
            self.update_top_bar()

        @work(exclusive=True, group="discord_worker")
        async def run_discord_bot(self):
            self.discord_status = "[bold green]RUNNING[/]"
            self.update_top_bar()
            await self.discord_proc.start(self.log_discord)
            self.discord_status = "[bold red]STOPPED[/]"
            self.update_top_bar()

        @work(exclusive=True, group="watchers")
        async def start_file_watchers(self):
            async def watch_github():
                async for changes in awatch(str(GITHUB_DIR / "app")):
                    self.log_github(
                        "[bold yellow]⚡ File change detected in app/. Reloading GitHub App...[/]",
                        is_markup=True,
                    )
                    await self.github_proc.stop(self.log_github)
                    self.run_github_app()

            async def watch_discord():
                def watch_filter(change: Change, path: str) -> bool:
                    p = path.replace("\\", "/").lower()
                    if any(
                        x in p
                        for x in [
                            "/temp/",
                            "/temp_",
                            "__pycache__",
                            "/.git/",
                            ".venv",
                            ".mp4",
                            ".mp3",
                        ]
                    ):
                        return False
                    return p.endswith((".py", ".md", ".json"))

                async for changes in awatch(
                    str(DISCORD_DIR), watch_filter=watch_filter, debounce=2500
                ):
                    self.log_discord(
                        "[bold yellow]⚡ Code change detected. Debounce elapsed (2.5s). Restarting Discord Bot...[/]",
                        is_markup=True,
                    )
                    await self.discord_proc.stop(self.log_discord)
                    self.run_discord_bot()

            await asyncio.gather(watch_github(), watch_discord())

        @on(Button.Pressed, "#quit-btn")
        def on_quit_button_pressed(self):
            self.exit()

        def action_toggle_github(self):
            if self.github_proc.is_running:
                asyncio.create_task(self.github_proc.stop(self.log_github))
            else:
                self.run_github_app()

        def action_toggle_discord(self):
            if self.discord_proc.is_running:
                asyncio.create_task(self.discord_proc.stop(self.log_discord))
            else:
                self.run_discord_bot()

        def action_toggle_unified(self):
            self.is_unified_mode = not self.is_unified_mode
            gh_pane = self.query_one("#github-pane")
            dc_pane = self.query_one("#discord-pane")
            un_pane = self.query_one("#unified-pane")

            if self.is_unified_mode:
                gh_pane.styles.display = "none"
                dc_pane.styles.display = "none"
                un_pane.styles.display = "block"
            else:
                gh_pane.styles.display = "block"
                dc_pane.styles.display = "block"
                un_pane.styles.display = "none"

            self.repopulate_filtered_logs()
            self.update_top_bar()

        def action_restart_all(self):
            self.log_github(
                "[bold yellow]🔄 Manual restart triggered...[/]", is_markup=True
            )
            self.log_discord(
                "[bold yellow]🔄 Manual restart triggered...[/]", is_markup=True
            )
            if self.github_proc.is_running:
                asyncio.create_task(self.github_proc.stop(self.log_github))
                self.run_github_app()
            if self.discord_proc.is_running:
                asyncio.create_task(self.discord_proc.stop(self.log_discord))
                self.run_discord_bot()

        def action_clear_logs(self):
            self.unified_logs.clear()
            self.query_one("#github-log", RichLog).clear()
            self.query_one("#discord-log", RichLog).clear()
            self.query_one("#unified-log", RichLog).clear()

        def action_open_filter(self):
            filter_box = self.query_one("#filter-container")
            filter_box.add_class("visible")
            filter_input = self.query_one("#filter-input", Input)
            filter_input.focus()

        def action_handle_escape(self):
            filter_box = self.query_one("#filter-container")
            filter_input = self.query_one("#filter-input", Input)

            if filter_box.has_class("visible"):
                filter_box.remove_class("visible")
                if self.is_unified_mode:
                    self.query_one("#unified-pane").focus()
                else:
                    self.query_one("#github-pane").focus()
            elif self.filter_query:
                filter_input.value = ""
                self.filter_query = ""
                self.repopulate_filtered_logs()
                self.update_top_bar()

        @on(Input.Submitted, "#filter-input")
        def handle_filter_submit(self, event: Input.Submitted):
            self.filter_query = event.value.strip()
            self.repopulate_filtered_logs()
            self.update_top_bar()
            self.query_one("#filter-container").remove_class("visible")
            if self.is_unified_mode:
                self.query_one("#unified-pane").focus()
            else:
                self.query_one("#github-pane").focus()

        @on(Input.Changed, "#filter-input")
        def handle_filter_changed(self, event: Input.Changed):
            self.filter_query = event.value.strip()
            self.repopulate_filtered_logs()
            self.update_top_bar()

        def repopulate_filtered_logs(self):
            gh_widget = self.query_one("#github-log", RichLog)
            dc_widget = self.query_one("#discord-log", RichLog)
            un_widget = self.query_one("#unified-log", RichLog)

            gh_widget.clear()
            dc_widget.clear()
            un_widget.clear()

            q = self.filter_query.lower()

            for ts, source, line, is_markup in self.unified_logs:
                if not q or q in line.lower():
                    time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")

                    if source == "github":
                        if is_markup:
                            gh_widget.write(Text.from_markup(line))
                        else:
                            gh_widget.write(
                                colorize_github_log(line, self.filter_query)
                            )

                        if self.is_unified_mode:
                            unified_line = (
                                f"[dim]{time_str}[/] [cyan][GITHUB][/]  {line}"
                            )
                            un_widget.write(
                                colorize_github_log(unified_line, self.filter_query)
                            )

                    elif source == "discord":
                        if is_markup:
                            dc_widget.write(Text.from_markup(line))
                        else:
                            dc_widget.write(
                                colorize_discord_log(line, self.filter_query)
                            )

                        if self.is_unified_mode:
                            unified_line = (
                                f"[dim]{time_str}[/] [magenta][DISCORD][/] {line}"
                            )
                            un_widget.write(
                                colorize_discord_log(unified_line, self.filter_query)
                            )

        async def on_unmount(self):
            await asyncio.gather(
                self.github_proc.stop(),
                self.discord_proc.stop(),
                return_exceptions=True,
            )

    app = PriestyAICLI()
    app.run()


def main():
    help_epilog = """
Interactive TUI Keybindings (Dashboard Mode):
  [g]             Toggle / Restart GitHub App
  [d]             Toggle / Restart Discord Bot
  [u]             Toggle Unified Stream vs Split View
  [r]             Restart Both Services
  [c]             Clear Screen Logs
  [/]             Open Vim-Style Search Bar (Highlights matches)
  [Esc]           Clear Filter / Unfocus Search
  [Ctrl+C] / [q]  Quit and cleanly stop all background processes

Quick Examples:
  priestyai                # Launch interactive split dashboard with hot-reloading
  priestyai --headless     # Run standard terminal stream without TUI
  priestyai --github-only  # Run only the GitHub App
  priestyai --discord-only # Run only the Discord Bot
    """

    parser = argparse.ArgumentParser(
        prog="priestyai",
        description="PriestyAI Monorepo Developer Suite\nRun and hot-reload the GitHub App and Discord Bot simultaneously.",
        epilog=help_epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--github-only",
        action="store_true",
        help="Launch only the GitHub App (FastAPI / Smee)",
    )
    parser.add_argument(
        "--discord-only", action="store_true", help="Launch only the Discord Bot"
    )
    parser.add_argument(
        "--no-watch", action="store_true", help="Disable auto-reloading file watchers"
    )
    parser.add_argument(
        "--headless", action="store_true", help="Run in headless stream mode (no TUI)"
    )

    args = parser.parse_args()

    start_gh = not args.discord_only
    start_dc = not args.github_only
    watch_enabled = not args.no_watch

    if args.headless:
        try:
            asyncio.run(run_headless_supervisor(start_gh, start_dc, watch_enabled))
        except KeyboardInterrupt:
            pass
    else:
        run_tui(start_gh, start_dc, watch_enabled)


if __name__ == "__main__":
    main()
