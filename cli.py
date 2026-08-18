
import argparse
import asyncio
from datetime import datetime
import os
from pathlib import Path
import re
import sys
import time
from typing import List, Optional, Tuple

from rich.text import Text
from watchfiles import Change, awatch

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

ROOT_DIR = Path(__file__).parent.resolve()
GITHUB_DIR = ROOT_DIR / "github-app"
DISCORD_DIR = ROOT_DIR / "discord-bot"
LOGS_DIR = ROOT_DIR / "logs"


def find_python_executable(project_dir: Path) -> str:
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


def get_process_stats(pid: Optional[int]) -> Tuple[float, float]:
    if not pid or not PSUTIL_AVAILABLE:
        return 0.0, 0.0
    try:
        proc = psutil.Process(pid)
        cpu = proc.cpu_percent(interval=None)
        mem = proc.memory_info().rss / (1024 * 1024)
        for child in proc.children(recursive=True):
            try:
                cpu += child.cpu_percent(interval=None)
                mem += child.memory_info().rss / (1024 * 1024)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return round(cpu, 1), round(mem, 1)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0.0, 0.0


async def terminate_process_tree(
    process: Optional[asyncio.subprocess.Process], timeout: float = 3.0
):
    if not process or process.returncode is not None:
        return

    pid = process.pid
    if PSUTIL_AVAILABLE and pid:
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for ch in children:
                try:
                    ch.terminate()
                except psutil.NoSuchProcess:
                    pass
            parent.terminate()
            _, alive = psutil.wait_procs(children + [parent], timeout=timeout)
            for p in alive:
                try:
                    p.kill()
                except psutil.NoSuchProcess:
                    pass
            return
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), timeout=timeout)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def colorize_log(line: str, service: str = "github", highlight_query: str = "") -> Text:
    text = Text(line)

    text.highlight_regex(
        r"\b\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2}(?:,\d+)?\b|\b\d{2}:\d{2}:\d{2}\b",
        style="dim #6272a4",
    )

    text.highlight_regex(
        r"\b(ERROR|CRITICAL|FATAL|Exception|Traceback)\b",
        style="bold white on #ff5555",
    )
    text.highlight_regex(
        r"\b(WARN|WARNING|rejected|cooldown|rate limit|429)\b",
        style="bold black on #ffb86c",
    )
    text.highlight_regex(r"\b(INFO)\b", style="bold cyan")
    text.highlight_regex(r"\b(DEBUG)\b", style="dim magenta")

    text.highlight_regex(
        r"\b(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\b", style="bold #f8f8f2"
    )
    text.highlight_regex(
        r"\b200 OK\b|\b201 Created\b|\b204 No Content\b|\b304 Not Modified\b|\bstatus=2\d{2}\b",
        style="bold #50fa7b",
    )
    text.highlight_regex(
        r"\b400\b|\b401\b|\b403\b|\b404\b|\b422\b|\bstatus=4\d{2}\b",
        style="bold #ffb86c",
    )
    text.highlight_regex(
        r"\b500\b|\b502\b|\b503\b|\b504\b|\bstatus=5\d{2}\b", style="bold #ff5555"
    )

    text.highlight_regex(r"https?://[^\s]+", style="underline dim cyan")
    text.highlight_regex(r"/(?:webhook|health|api|graphql)[^\s]*", style="bold cyan")

    if service == "github":

        text.highlight_regex(r"#\d+\b", style="bold #f1fa8c")
        text.highlight_regex(r"@[\w-]+", style="bold #8be9fd")
        text.highlight_regex(
            r"\b(?:feature|fix|chore|docs|refactor|test)/[\w-]+", style="green"
        )
        text.highlight_regex(r"\b[0-9a-f]{7,40}\b", style="dim #bd93f9")
        text.highlight_regex(r"\[priesty\.[^\]]+\]", style="bold #bd93f9")
        text.highlight_regex(r"\[smee\]", style="bold #8be9fd")
        text.highlight_regex(
            r"\b(python:\S+|node:\S+|golang:\S+|rust:\S+)\b", style="bold #50fa7b"
        )
        text.highlight_regex(
            r"\b(pytest|cargo test|go test|npm test)\b", style="italic #f8f8f2"
        )
        text.highlight_regex(
            r"\b(PASSED|COMPLETED|VERIFIED|SUCCESS)\b", style="bold #50fa7b"
        )
        text.highlight_regex(r"\b(FAILED|FAILURE)\b", style="bold #ff5555")

    elif service == "discord":

        text.highlight_regex(
            r"\[chat\]|\[news\]|\[voice\]|\[memory\]|\[core\]", style="bold #ff79c6"
        )
        text.highlight_regex(
            r"\b(Memory Gatekeeper|Learned User Fact|Learned Server Fact)\b",
            style="bold #50fa7b",
        )
        text.highlight_regex(
            r"\b(Server News|NewsScraper|video_generator|FFmpeg|Streamable|Edge-TTS)\b",
            style="bold #bd93f9",
        )
        text.highlight_regex(
            r"\b(Reasoning Tier|Routing Tier|Thinking|Deduction)\b",
            style="bold #ff79c6",
        )
        text.highlight_regex(
            r"\b(LayoutView|Components V2|ActionRow|Button)\b", style="bold #8be9fd"
        )
        text.highlight_regex(
            r"\b(Logged in as|Shard ready|synced|Connected to Gateway)\b",
            style="bold #50fa7b",
        )

    if highlight_query:
        text.highlight_words(
            [highlight_query], style="black on #f1fa8c", case_sensitive=False
        )

    return text


def matches_filter_level(line: str, level: str) -> bool:
    if level == "ALL":
        return True
    low = line.lower()
    is_error = any(
        kw in low
        for kw in [
            "error",
            "exception",
            "failed",
            "traceback",
            "critical",
            "crash",
            "status=5",
        ]
    )
    if level == "ERROR":
        return is_error
    if level == "WARN+":
        is_warn = any(
            kw in low
            for kw in ["warning", "warn", "429", "rate limit", "rejected", "cooldown"]
        )
        return is_error or is_warn
    return True


class ProcessManager:
    def __init__(self, name: str, cwd: Path, command: List[str]):
        self.name = name
        self.cwd = cwd
        self.command = command
        self.process: Optional[asyncio.subprocess.Process] = None
        self.is_running = False

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

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
            await terminate_process_tree(self.process)
            self.process = None


async def run_headless_supervisor(start_github: bool, start_discord: bool, watch: bool):
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
            colored = colorize_log(line, service="github")
            console.print(f"[dim]{ts}[/] [bold cyan][GITHUB][/]  ", end="")
            console.print(colored)

    def log_dc(line: str, is_markup: bool = False):
        ts = datetime.now().strftime("%H:%M:%S")
        if is_markup:
            console.print(f"[dim]{ts}[/] [bold magenta][DISCORD][/] {line}")
        else:
            colored = colorize_log(line, service="discord")
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
            try:
                async for _ in awatch(str(GITHUB_DIR / "app"), debounce=1000):
                    log_gh(
                        "[bold yellow]⚡ File change in app/. Reloading GitHub App...[/]",
                        is_markup=True,
                    )
                    await github_proc.stop(log_gh)
                    asyncio.create_task(github_proc.start(log_gh))
            except asyncio.CancelledError:
                pass

        await watch_github()

    watch_task = asyncio.create_task(watch_loop()) if watch else None

    console.print(
        "[bold green]PriestyAI Headless Runner Active[/] (Press Ctrl+C to cleanly stop all services)"
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
            color: #f8f8f2;
            overflow: hidden;
        }

        * {
            scrollbar-size-vertical: 1;
            scrollbar-background: #000000;
            scrollbar-color: #44475a;
            scrollbar-color-hover: #6272a4;
            scrollbar-color-active: #bd93f9;
        }
        
        #top-bar {
            dock: top;
            width: 100%;
            height: 3;
            background: #0a0a0f;
            border-bottom: solid #282a36;
            padding: 0 1;
            align-vertical: middle;
        }

        #top-bar-label {
            width: 1fr;
            padding: 0 1;
        }

        #quit-btn {
            background: #282a36;
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
            border: solid #1e1f29;
            background: #050508;
            margin: 0;
            padding: 0 1;
        }

        .pane-box:focus-within {
            border: solid #6272a4;
        }

        .pane-title {
            text-style: bold;
            padding: 0 1;
            background: #0f1016;
            color: #bd93f9;
            margin-bottom: 0;
        }

        RichLog {
            height: 1fr;
            background: #050508;
            padding: 0;
        }

        #unified-pane {
            display: none;
            width: 100%;
            height: 100%;
            border: solid #1e1f29;
            background: #050508;
            padding: 0 1;
        }

        #unified-pane:focus-within {
            border: solid #6272a4;
        }

        #filter-container {
            dock: bottom;
            width: 100%;
            height: 3;
            background: #0a0a0f;
            border-top: solid #6272a4;
            padding: 0 1;
            display: none;
        }

        #filter-container.visible {
            display: block;
        }

        Input {
            background: #050508;
            border: none;
            color: #f8f8f2;
            height: 1;
        }

        Footer {
            background: #0a0a0f;
            color: #6272a4;
        }
        """

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
            Binding("q", "quit", "Quit", show=False),
            Binding("g", "toggle_github", "GitHub (G)"),
            Binding("d", "toggle_discord", "Discord (D)"),
            Binding("u", "toggle_unified", "Unified (U)"),
            Binding("r", "restart_all", "Restart (R)"),
            Binding("space", "toggle_autoscroll", "Scroll (Space)", show=True),
            Binding("1", "filter_errors", "Errors (1)"),
            Binding("2", "filter_warnings", "Warns (2)"),
            Binding("3", "filter_all", "All (3)"),
            Binding("e", "export_logs", "Export (E)", show=True),
            Binding("c", "clear_logs", "Clear (C)"),
            Binding("slash", "open_filter", "Search (/)", show=True),
            Binding("escape", "handle_escape", "Esc", show=False),
        ]

        github_status = reactive("[bold red]STOPPED[/]")
        discord_status = reactive("[bold red]STOPPED[/]")
        filter_query = reactive("")
        is_unified_mode = reactive(False)
        auto_scroll = reactive(True)
        log_level_filter = reactive("ALL")

        github_metrics = reactive((0.0, 0.0))
        discord_metrics = reactive((0.0, 0.0))

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

            self.unified_logs: List[Tuple[float, str, str, bool]] = []

        def compose(self) -> ComposeResult:
            with Horizontal(id="top-bar"):
                yield Label("", id="top-bar-label")
                yield Button("✕ Quit", id="quit-btn", variant="error")

            with Horizontal(id="main-container"):
                with Vertical(classes="pane-box", id="github-pane"):
                    yield Label("GitHub App (:8000)", classes="pane-title")
                    yield RichLog(
                        id="github-log", highlight=False, markup=True, wrap=True
                    )

                with Vertical(classes="pane-box", id="discord-pane"):
                    yield Label("Discord Companion Bot", classes="pane-title")
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
                    placeholder="/search keyword (Enter to save, Esc to clear)...",
                    id="filter-input",
                )

            yield Footer()

        async def on_mount(self):
            self.update_top_bar()
            self.set_interval(1.5, self.update_telemetry)
            if start_github:
                self.run_github_app()
            if start_discord:
                self.run_discord_bot()
            if watch:
                self.start_file_watchers()

        def update_telemetry(self):
            if self.github_proc.is_running:
                self.github_metrics = get_process_stats(self.github_proc.pid)
            else:
                self.github_metrics = (0.0, 0.0)

            if self.discord_proc.is_running:
                self.discord_metrics = get_process_stats(self.discord_proc.pid)
            else:
                self.discord_metrics = (0.0, 0.0)

            self.update_top_bar()

        def update_top_bar(self):
            bar = self.query_one("#top-bar-label", Label)
            mode_label = (
                "[#ff79c6]UNIFIED[/]" if self.is_unified_mode else "[dim]SPLIT[/]"
            )
            scroll_label = (
                "[#50fa7b]AUTO[/]"
                if self.auto_scroll
                else "[bold #ff5555 on #282a36]PAUSED[/]"
            )

            filter_badge = ""
            if self.log_level_filter != "ALL":
                filter_badge += f"  │  Level: [#f1fa8c]{self.log_level_filter}[/]"
            if self.filter_query:
                filter_badge += f"  │  Query: [#f1fa8c]'{self.filter_query}'[/]"

            gh_stats = ""
            if self.github_proc.is_running and PSUTIL_AVAILABLE:
                gh_stats = f" [dim]({self.github_metrics[1]:.0f}MB|{self.github_metrics[0]:.0f}%)[/]"

            dc_stats = ""
            if self.discord_proc.is_running and PSUTIL_AVAILABLE:
                dc_stats = f" [dim]({self.discord_metrics[1]:.0f}MB|{self.discord_metrics[0]:.0f}%)[/]"

            bar.update(
                f"[bold #ffffff]PriestyAI[/]  │  "
                f"GH: {self.github_status}{gh_stats}  │  "
                f"DC: {self.discord_status}{dc_stats}  │  "
                f"Scroll: {scroll_label}  │  "
                f"View: {mode_label}"
                f"{filter_badge}"
            )

        def log_github(self, line: str, is_markup: bool = False):
            ts = time.time()
            self.unified_logs.append((ts, "github", line, is_markup))

            if matches_filter_level(line, self.log_level_filter):
                if not self.filter_query or self.filter_query.lower() in line.lower():
                    self.query_one("#github-log", RichLog).write(
                        (
                            Text.from_markup(line)
                            if is_markup
                            else colorize_log(
                                line,
                                service="github",
                                highlight_query=self.filter_query,
                            )
                        ),
                        scroll_end=self.auto_scroll,
                    )
                    if self.is_unified_mode:
                        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                        unified_line = f"[dim]{time_str}[/] [cyan][GITHUB][/]  {line}"
                        self.query_one("#unified-log", RichLog).write(
                            colorize_log(
                                unified_line,
                                service="github",
                                highlight_query=self.filter_query,
                            ),
                            scroll_end=self.auto_scroll,
                        )

        def log_discord(self, line: str, is_markup: bool = False):
            ts = time.time()
            self.unified_logs.append((ts, "discord", line, is_markup))

            if matches_filter_level(line, self.log_level_filter):
                if not self.filter_query or self.filter_query.lower() in line.lower():
                    self.query_one("#discord-log", RichLog).write(
                        (
                            Text.from_markup(line)
                            if is_markup
                            else colorize_log(
                                line,
                                service="discord",
                                highlight_query=self.filter_query,
                            )
                        ),
                        scroll_end=self.auto_scroll,
                    )
                    if self.is_unified_mode:
                        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                        unified_line = (
                            f"[dim]{time_str}[/] [magenta][DISCORD][/] {line}"
                        )
                        self.query_one("#unified-log", RichLog).write(
                            colorize_log(
                                unified_line,
                                service="discord",
                                highlight_query=self.filter_query,
                            ),
                            scroll_end=self.auto_scroll,
                        )

        @work(exclusive=True, group="github_worker")
        async def run_github_app(self):
            self.github_status = "[bold #50fa7b]RUNNING[/]"
            self.update_top_bar()
            await self.github_proc.start(self.log_github)
            self.github_status = "[bold #ff5555]STOPPED[/]"
            self.update_top_bar()

        @work(exclusive=True, group="discord_worker")
        async def run_discord_bot(self):
            self.discord_status = "[bold #50fa7b]RUNNING[/]"
            self.update_top_bar()
            await self.discord_proc.start(self.log_discord)
            self.discord_status = "[bold #ff5555]STOPPED[/]"
            self.update_top_bar()

        @work(exclusive=True, group="watchers")
        async def start_file_watchers(self):
            try:
                async for _ in awatch(str(GITHUB_DIR / "app"), debounce=1000):
                    self.log_github(
                        "[bold #f1fa8c]⚡ File change in app/. Reloading GitHub App...[/]",
                        is_markup=True,
                    )
                    await self.github_proc.stop(self.log_github)
                    self.run_github_app()
            except asyncio.CancelledError:
                pass

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

        def action_toggle_autoscroll(self):
            self.auto_scroll = not self.auto_scroll
            status = "RESUMED" if self.auto_scroll else "PAUSED"
            self.notify(f"Auto-scroll {status}")
            self.update_top_bar()

        def action_filter_errors(self):
            self.log_level_filter = "ERROR"
            self.notify("Filtering: ERRORS ONLY")
            self.repopulate_filtered_logs()
            self.update_top_bar()

        def action_filter_warnings(self):
            self.log_level_filter = "WARN+"
            self.notify("Filtering: WARNINGS & ERRORS")
            self.repopulate_filtered_logs()
            self.update_top_bar()

        def action_filter_all(self):
            self.log_level_filter = "ALL"
            self.notify("Filtering: ALL LOGS")
            self.repopulate_filtered_logs()
            self.update_top_bar()

        def action_export_logs(self):
            os.makedirs(LOGS_DIR, exist_ok=True)
            export_path = (
                LOGS_DIR
                / f"priestyai_dump_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
            )
            with open(export_path, "w", encoding="utf-8") as f:
                for ts, source, line, _ in self.unified_logs:
                    t_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{t_str}] [{source.upper()}] {line}\n")
            self.notify(f"Exported logs to {export_path.name}")

        @work(exclusive=True, group="restart_worker")
        async def action_restart_all(self):
            self.log_github(
                "[bold #f1fa8c]🔄 Manual restart triggered...[/]", is_markup=True
            )
            self.log_discord(
                "[bold #f1fa8c]🔄 Manual restart triggered...[/]", is_markup=True
            )

            stop_tasks = []
            if self.github_proc.is_running:
                stop_tasks.append(self.github_proc.stop(self.log_github))
            if self.discord_proc.is_running:
                stop_tasks.append(self.discord_proc.stop(self.log_discord))

            if stop_tasks:
                await asyncio.gather(*stop_tasks)

            await asyncio.sleep(0.5)

            if start_github:
                self.run_github_app()
            if start_discord:
                self.run_discord_bot()

        def action_clear_logs(self):
            self.unified_logs.clear()
            self.query_one("#github-log", RichLog).clear()
            self.query_one("#discord-log", RichLog).clear()
            self.query_one("#unified-log", RichLog).clear()
            self.notify("Logs Cleared")

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
                if matches_filter_level(line, self.log_level_filter):
                    if not q or q in line.lower():
                        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")

                        if source == "github":
                            if is_markup:
                                gh_widget.write(
                                    Text.from_markup(line), scroll_end=self.auto_scroll
                                )
                            else:
                                gh_widget.write(
                                    colorize_log(
                                        line,
                                        service="github",
                                        highlight_query=self.filter_query,
                                    ),
                                    scroll_end=self.auto_scroll,
                                )

                            if self.is_unified_mode:
                                unified_line = (
                                    f"[dim]{time_str}[/] [cyan][GITHUB][/]  {line}"
                                )
                                un_widget.write(
                                    colorize_log(
                                        unified_line,
                                        service="github",
                                        highlight_query=self.filter_query,
                                    ),
                                    scroll_end=self.auto_scroll,
                                )

                        elif source == "discord":
                            if is_markup:
                                dc_widget.write(
                                    Text.from_markup(line), scroll_end=self.auto_scroll
                                )
                            else:
                                dc_widget.write(
                                    colorize_log(
                                        line,
                                        service="discord",
                                        highlight_query=self.filter_query,
                                    ),
                                    scroll_end=self.auto_scroll,
                                )

                            if self.is_unified_mode:
                                unified_line = (
                                    f"[dim]{time_str}[/] [magenta][DISCORD][/] {line}"
                                )
                                un_widget.write(
                                    colorize_log(
                                        unified_line,
                                        service="discord",
                                        highlight_query=self.filter_query,
                                    ),
                                    scroll_end=self.auto_scroll,
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
  [Space]         Pause / Resume Auto-Scroll (Freeze view to inspect stack traces)
  [1]             Show Errors Only
  [2]             Show Warnings + Errors
  [3]             Show All Logs
  [e]             Export Current Log Stream to logs/priestyai_dump_<time>.log
  [c]             Clear Screen Logs
  [/]             Open Keyword Search Bar (Highlights matches in bright yellow)
  [Esc]           Clear Active Search / Unfocus Bar
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
