# PriestyAI

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![Install GitHub App](https://img.shields.io/badge/GitHub_App-Install-blue?logo=github)](https://github.com/apps/priestyai)
[![Machine Account](https://img.shields.io/badge/Machine_User-@PriestyAI-181717?logo=github)](https://github.com/PriestyAI)
[![Add Discord Bot](https://img.shields.io/badge/Discord-Add_to_Server-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)
[![User App](https://img.shields.io/badge/User_App-Install_to_Account-eb459e?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)

PriestyAI is an ecosystem of autonomous developer and community AI agents. This monorepo houses both the **GitHub Automation App** and the **Discord AI Companion**, supervised by a terminal control plane.

> [!TIP]
> Just want to use the hosted bot without compiling code? Visit the [PriestyAI Machine Account](https://github.com/PriestyAI/PriestyAI) for 1-click installations.

---

## Projects

| Project | Description | Tech Stack | Documentation |
| :--- | :--- | :--- | :--- |
| [**GitHub App**](./github-app) | Autonomous AI pair programmer that handles issue-to-PR workflows, code reviews, and sandboxed test execution. | FastAPI, Docker, Gemini, PyJWT | [View README](./github-app/README.md) |
| [**Discord Bot**](./discord-bot) | Multi-modal AI companion featuring long-term vector memory, Components V2 UI generation, and daily broadcast news video generation. | Discord.py, MoviePy, Edge-TTS, Gemini | [View README](./discord-bot/README.md) |

---

## QuickStart (Local Development & Testing)

Get both services running simultaneously in less than two minutes using the built-in **PriestyAI CLI**:

### 1. Clone & Set Up Environment

```powershell
# Clone the repository
git clone https://github.com/Priestytheplushie/PriestyAI.git
cd PriestyAI

# Create and activate virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # On Linux/macOS: source .venv/bin/activate

# Install all dependencies and register the CLI
pip install -r requirements.txt
pip install -e .
```

### 2. Configure Environment Secrets

Copy the example configuration files and add your API credentials:

```powershell
# GitHub App Configuration
cp github-app/.env.example github-app/.env

# Discord Bot Configuration
cp discord-bot/.env.example discord-bot/.env
```

> [!IMPORTANT]
> Ensure your GitHub App private key (`github_app.pem`) is placed inside `github-app/` before booting the GitHub service.

### 3. Launch the PriestyAI CLI

```powershell
# Launch the interactive TUI dashboard with hot-reloading
priestyai

# Alternatively, run directly without editable install:
python cli.py
```

### 4. Running Automated Tests

Run the full automated test suite across all projects using `pytest`:

```powershell
# Run all tests across the monorepo
pytest

# Run tests with code coverage report
pytest --cov

# Run tests for a specific subproject
pytest github-app/tests
pytest discord-bot/tests
pytest tests/              # Root CLI tests
```

---

## PriestyAI CLI

The **PriestyAI CLI** is an interactive, terminal-native supervisor built with [Textual](https://textual.textualize.io/) and [Rich](https://rich.readthedocs.io/). It manages both bot subprocesses concurrently with debounced hot-reloading, live process telemetry, tokenized log syntax highlighting, and keyword log search.

```text
┌─ PriestyAI ──────────────────────────────────────────────────────────────────────────────────────┐
│ GH: RUNNING (48MB|0.4%) │ DC: RUNNING (132MB|1.1%) │ Scroll: AUTO │ View: SPLIT │ Level: ALL     │
├─────────────────────────────────────┬────────────────────────────────────────────────────────────┤
│ 📂 GitHub App (:8000)               │ 🤖 Discord Companion Bot                                   │
│                                     │                                                            │
│ 20:41:15 [INFO] POST /webhook 200 OK │ 20:41:16 [INFO] [bot] Shard ID 0 connected (42ms ping)     │
│ 20:41:15 [priesty.issue_to_pr]      │ 20:41:18 [chat] Message from @alex in #dev: "news"        │
│   #42 Draft PR opened on branch     │ 20:41:19 [news] [Server News] Video rendering complete     │
│   feature/cache-ttl [python:3.11]   │ 20:41:20 [memory] [Learned User Fact] Cached preference    │
│                                     │                                                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [g] GitHub  [d] Discord  [u] Unified  [Space] Scroll  [1-3] Level  [e] Export  [/] Search  [q] Quit│
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### Key CLI Features

* **Tokenized Syntax Highlighting:** Distinctly colorizes timestamps, log levels (`INFO`, `WARN`, `ERROR`), HTTP verbs & status codes, GitHub PR/Issue `#numbers`, `@mentions`, Git branches, Docker images, and Discord media events.
* **Live Process Telemetry HUD:** Displays real-time CPU % and RAM (MB) usage for both services and their child workers via `psutil`.
* **Auto-Scroll Freeze (`Space`):** Pause auto-scroll at any moment to inspect a traceback or error without incoming logs jumping your screen.
* **Log Level Filtering (`1`, `2`, `3`):** Instantly isolate errors (`1`), warnings & errors (`2`), or view the full stream (`3`).
* **One-Key Log Export (`e`):** Dumps the current buffer to timestamped log files in `logs/`.
* **Safe Process Tree Termination:** Prevents zombie processes by recursively killing child subprocesses (Uvicorn reloaders, FFmpeg workers, Docker tasks) upon exit.

---

## Command Cheatsheet & Keybindings

### CLI Command Flags

| Command | Description |
| :--- | :--- |
| `priestyai` | Launches the interactive side-by-side TUI dashboard with auto-reloading. |
| `priestyai --headless` | Runs both services in raw terminal stream mode (ideal for VPS / CI / SSH). |
| `priestyai --github-only` | Boots only the GitHub App service on `:8000`. |
| `priestyai --discord-only` | Boots only the Discord Bot gateway service. |
| `priestyai --no-watch` | Launches services without the `watchfiles` hot-reloaders. |
| `priestyai --help` | Displays the formatted CLI help menu and keyboard shortcuts. |

### Interactive TUI Keybindings

| Key | Action |
| :--- | :--- |
| **`g`** | Starts, stops, or restarts the **GitHub App** process independently. |
| **`d`** | Starts, stops, or restarts the **Discord Bot** process independently. |
| **`u`** | Toggles between **Split View** (side-by-side) and **Unified Stream** (chronological single feed). |
| **`r`** | Manually triggers a clean restart for both running services. |
| **`Space`** | **Pause / Resume Auto-Scroll** (freeze log viewport to inspect errors without interruption). |
| **`1`** | **Filter: Errors Only** (`ERROR`, `CRITICAL`, `TRACEBACK`, `FATAL`). |
| **`2`** | **Filter: Warnings + Errors** (`WARN`, `429`, `RATE LIMIT`, `COOLDOWN`). |
| **`3`** | **Filter: All Logs** (reset log filter to standard verbose output). |
| **`e`** | **Export Logs** (dumps active log buffer to `logs/priestyai_dump_<timestamp>.log`). |
| **`c`** | Clears the current terminal scrollback buffer. |
| **`/`** | Opens the keyword search bar (highlights matches in **bright yellow** as you type). |
| **`Esc`** | Closes search input / clears active search filter. |
| **`Ctrl + C`** / **`q`** | Gracefully terminates all background subprocess trees and exits. |

---

## Directory Structure

```text
PriestyAI/
├── .github/                 # CI workflows & issue templates
│   ├── workflows/ci.yml     # Automated tests, coverage & lint checks
│   └── ISSUE_TEMPLATE/      # Bug reports and feature request forms
├── .gitignore               # Unified secret & build artifact exclusion rules
├── cli.py                   # PriestyAI CLI, TUI dashboard & subprocess supervisor
├── pyproject.toml           # Monorepo packaging, entrypoints & pytest config
├── requirements.txt         # Consolidated dependency & test runner manifest
├── LICENSE                  # GNU Affero General Public License (AGPLv3)
├── README.md                # Monorepo overview, quickstart & developer guide
│
├── scripts/
│   └── clean_project.py     # Codebase hygiene & formatting validator
│
├── logs/                    # Exported log dumps from the CLI (git-ignored)
│
├── tests/                   # Root CLI & supervisor test suite
│   ├── conftest.py          # Global pytest configuration and fixtures
│   └── test_cli.py          # CLI log colorizer and filter unit tests
│
├── github-app/              # GitHub App (FastAPI / Smee / Docker)
│   ├── app/                 # Workflows, triage, reviews, and git clients
│   ├── tests/               # GitHub App unit & integration tests
│   ├── .env.example         # GitHub App environment template
│   ├── README.md            # GitHub App documentation & developer guide
│   └── requirements.txt     # Service-specific requirements
│
└── discord-bot/             # Discord Bot (Discord.py / MoviePy / Agents)
    ├── agents/              # Autonomous research and diagnostic agent workspaces
    ├── assets/              # Host sprites, UI cards, and background audio
    ├── config/              # Core Markdown system prompts and guidelines
    ├── core/                # Bot lifecycle, chat handler, memory, and UI components
    ├── tests/               # Discord Bot test suite
    ├── tools/               # Components V2 message builder and Server News pipeline
    ├── .env.example         # Discord Bot environment template
    ├── README.md            # Discord Bot documentation
    ├── requirements.txt     # Service-specific requirements
    └── run.py               # Bot entrypoint
```

---

## License & Brand Guidelines

### Software License
This repository is licensed under the **GNU Affero General Public License v3.0 (GNU AGPLv3)**. You are free to inspect, run, modify, and self-host this software. If you host or run a modified version of this software as a network service, you must make the complete corresponding source code available under the same AGPLv3 license. See [LICENSE](LICENSE) for full details.

### Trademark, Persona & Identity Policy
"PriestyAI", the character assets/sprites, and the `@PriestyAI` machine account represent the official identity and persona of the project creator.
* **Self-Hosting:** You are welcome to deploy your own instance of the bot for your team or organization.
* **Branding Requirement:** If you host or distribute your own public instance, you must configure your own bot credentials/app registrations and **must not** use the name "PriestyAI", use the official character sprites/avatars, or represent your instance as the official `@PriestyAI` service.