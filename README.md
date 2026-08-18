# PriestyAI

[![Install GitHub App](https://img.shields.io/badge/GitHub_App-Install-blue?logo=github)](https://github.com/apps/priestyai)
[![Add Discord Bot](https://img.shields.io/badge/Discord-Add_to_Server-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)
[![User App](https://img.shields.io/badge/User_App-Install_to_Account-eb459e?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)

PriestyAI is an ecosystem of autonomous developer and community AI agents. This monorepo serves as the centralized codebase housing both the **GitHub Automation App** and the **Discord AI Companion**.

---

## Projects

| Project | Description | Tech Stack | Documentation |
| :--- | :--- | :--- | :--- |
| [**GitHub App**](./github-app) | Autonomous AI pair programmer that handles issue-to-PR workflows, code reviews, and sandboxed test execution. | FastAPI, Docker, Gemini, PyJWT | [View README](./github-app/README.md) |
| [**Discord Bot**](./discord-bot) | Multi-modal AI companion featuring long-term vector memory, Components V2 UI generation, and daily broadcast news video generation. | Discord.py, MoviePy, Edge-TTS, Gemini | [View README](./discord-bot/README.md) |

---

## QuickStart (Local Development CLI)

Get both services running simultaneously in less than two minutes using the built-in **PriestyAI CLI**:

### 1. Clone & Set Up Environment

```powershell
# Clone the repository
git clone https://github.com/YourUsername/PriestyAI.git
cd PriestyAI

# Create and activate unified virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1  # On Linux/macOS: source .venv/bin/activate

# Install all dependencies and register the CLI command
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
# Launch full interactive TUI dashboard with hot-reloading
priestyai

# Alternatively, run without editable install:
python cli.py
```

---

## PriestyAI CLI Usage & Cheatsheet

The **PriestyAI CLI** is an interactive, terminal-native supervisor built with [Textual](https://textual.textualize.io/) that runs both bots concurrently with isolated subprocesses, debounced file watchers, and real-time log search.

```text
┌─ PriestyAI CLI ──────────────────────────────────────────────────────────────────────────────────┐
│ GitHub: RUNNING (:8000)  │  Discord: RUNNING  │  Watchfiles: ACTIVE  │  View: SPLIT              │
├─────────────────────────────────────┬────────────────────────────────────────────────────────────┤
│ 📂 GitHub App                       │ 🤖 Discord Bot                                             │
│                                     │                                                            │
│ 20:32:59 [api] Uvicorn running      │ 20:33:00 [bot] Logged in as PriestyAI#1234                 │
│ 20:32:59 [smee] Connected to proxy  │ 20:33:01 [bot] Shard ID None connected to Gateway          │
│ 20:33:05 [api] POST /webhook 200 OK │ 20:33:03 [news] Automated Server News loop started         │
│                                     │                                                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [g] Toggle GitHub  [d] Toggle Discord  [u] Unified Stream  [r] Restart Both  [/] Search  [q] Quit │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### CLI Command Flags

| Command | Description |
| :--- | :--- |
| `priestyai` | Launches the interactive side-by-side TUI dashboard with auto-reloading. |
| `priestyai --headless` | Runs both services in raw scrolling terminal mode without the TUI (ideal for SSH / VPS). |
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
| **`c`** | Clears the current terminal scrollback buffer. |
| **`/`** | Opens the Vim-style search bar (highlights keyword matches in **bright yellow** as you type). |
| **`Esc`** | Closes search input / clears active search filter. |
| **`Ctrl + C`** / **`q`** | Gracefully terminates all background subprocesses and exits. |

---

## Directory Structure

```text
PriestyAI/
├── .gitignore               # Unified root secret and build artifact exclusion rules
├── cli.py                   # PriestyAI CLI & subprocess supervisor
├── pyproject.toml           # Monorepo packaging & 'priestyai' console entrypoint
├── requirements.txt         # Consolidated dependency manifest
├── README.md                # Monorepo overview and developer guide
│
├── github-app/              # GitHub App (FastAPI / Smee / Docker)
│   ├── app/                 # Workflows, triage, reviews, and git clients
│   ├── .env.example         # GitHub App environment template
│   ├── github_app.pem       # GitHub App private key (git-ignored)
│   ├── README.md            # GitHub App documentation
│   └── requirements.txt     # Service-specific requirements
│
└── discord-bot/             # Discord Bot (Discord.py / MoviePy / Agents)
    ├── agents/              # Autonomous research and diagnostic agent workspaces
    ├── assets/              # Host sprites, UI cards, and background audio
    ├── config/              # Core Markdown system prompts and guidelines
    ├── core/                # Bot lifecycle, chat handler, memory, and UI components
    ├── tools/               # Components V2 message builder and Server News pipeline
    ├── .env.example         # Discord Bot environment template
    ├── README.md            # Discord Bot documentation
    ├── requirements.txt     # Service-specific requirements
    └── run.py               # Bot entrypoint
```

---