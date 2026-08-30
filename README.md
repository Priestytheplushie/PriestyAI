# PriestyAI

[![Invite PriestyAI](https://img.shields.io/badge/Discord-Add_to_Server-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

PriestyAI is an autonomous AI companion, code reasoning engine, and workspace agent for Discord built on Python, `discord.py`, and the Google GenAI SDK.

Unlike standard conversational bots, PriestyAI features multi-turn workspace threads with Docker sandbox execution, Discord Components V2 interactive streaming, local SearXNG metasearch, and direct GitHub App integration with verified commit signing.

---

## Quickstart

### Prerequisites
* **Discord Bot Token** (with `Message Content Intent` enabled in the [Discord Developer Portal](https://discord.com/developers/applications))
* **Google Gemini API Key** (from [Google AI Studio](https://aistudio.google.com/))
* **Docker & Docker Compose** (for Docker Container) or **Python 3.11+** (for local setup)

---

### Option A: Docker Container

Spins up both **PriestyAI** and the local **SearXNG** metasearch service in isolated containers with zero manual environment management.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Priestytheplushie/PriestyAI.git
   cd PriestyAI
   ```

2. **Configure your environment:**
   ```bash
   cp .env.example .env
   ```
   *Edit `.env` and paste your `DISCORD_TOKEN` and `GEMINI_API_KEY`.*

3. **Start the services:**
   ```bash
   docker compose up -d --build
   ```

4. **View live logs:**
   ```bash
   docker compose logs -f priestyai
   ```

---

### Option B: Local Setup

If you prefer running the Python process directly on your host machine:

1. **Clone and create a virtual environment:**
   ```bash
   git clone https://github.com/Priestytheplushie/PriestyAI.git
   cd PriestyAI

   python -m venv .venv

   # Linux / macOS
   source .venv/bin/activate

   # Windows (PowerShell)
   .venv\Scripts\Activate.ps1
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure `.env`:**
   ```bash
   cp .env.example .env
   ```
   *Fill in your `DISCORD_TOKEN` and `GEMINI_API_KEY`.*

4. **Start local SearXNG search (Docker):**
   ```bash
   docker compose up -d searxng
   ```

5. **Start the bot:**
   ```bash
   python bot.py
   ```

---

## Key Architecture & Features

### 1. Autonomous Agent Mode (`/agent`)
* **Dedicated Workspace Threads**: Spawns isolated private threads with dedicated workspace directories.
* **Polyglot Docker Sandbox**: Safely executes Python, Node.js/TypeScript, Rust, Go, C/C++, and Bash commands in isolated containers.
* **Human-in-the-Loop Planning**: Two-phase execution loop with plan review, clarification questions, and interactive approval cards.
* **Verified GitHub Commits**: Stages code changes, applies co-author attribution, and publishes branches/PRs via GitHub's Git Data API with verified commit badges.

### 2. Intelligent Routing & Resilience
* **Complexity-Based Routing**: Dynamically classifies query complexity, routing lightweight requests to high-speed utility models and complex engineering tasks to deep reasoning models.
* **Automated Fallback Cascades**: Gracefully recovers from provider rate limits, network timeouts, and upstream outages via automated model fallback chains.
* **Instant Fast-Answer Interrupt**: Users can click `Answer Now` to abort reasoning scratchpads and receive immediate streaming responses.

### 3. Rich Discord Components V2 UI
* **Live Code Canvas & Playground**: Generates single-file scripts and multi-file `.zip` archives with interactive preview modals, unified diff calculation, and live web previews.
* **Interactive Knowledge Quizzes**: 1-by-1 stepper views with anti-spoiler thought process gates, instant scoring diagnostics, and dynamic study guide generation.
* **Thought Process Inspection**: Inspectable scratchpads, tool execution traces, and timings.

### 4. Privacy, Security & Local Tooling
* **SearXNG Metasearch**: Privacy-preserving web search and high-resolution image discovery without third-party tracking.
* **Field-Level Encryption at Rest**: Sensitive user facts, configuration credentials, and conversation sessions are encrypted using AES-256 (Fernet with PBKDF2 key derivation).
* **Automated Safety Guardrails**: Pre-flight moderation filtering with zero-tolerance policy enforcement and GDPR self-service data erasure via `/data`.

---

## Slash Commands & Context Menus

| Command | Scope | Description |
| :--- | :--- | :--- |
| `/ask` | Global / Guild | Ask a quick question with optional ephemeral visibility. |
| `/chat` | Global / Guild | Start or continue a multi-turn conversation with persistent channel memory. |
| `/agent` | Server Text | Launch an autonomous research or engineering agent in a private thread. |
| `/config` | Multi-Scope | Configure system prompts, persona, reasoning depth, AI channels, and permissions. |
| `/data` | Global / Guild | Self-service database browser to inspect, edit, or permanently delete stored facts. |
| `/generate` | Global / Guild | Direct generation across text, local image diffusion, or native voice messages. |
| `/feedback` | Global / Guild | Submit bug reports, feature suggestions, or tickets to the developer. |
| `/terms` & `/privacy` | Global / Guild | Review safety policies, data retention terms, and third-party sub-processors. |

### Message Context Menus
* **`Run as Prompt`**: Re-evaluates any selected message or attachment as a fresh prompt.
* **`Branch`**: Creates an exploratory branch thread preserving prior conversation context.
* **`Retry`**: Generates a new alternative version of any response with version swapper controls (`◀ 1/3 ▶`).
* **`View Prompt`** & **`Edit`**: Inspect the prompt that generated a response or edit bot messages in-place.

---

## Project Structure

```
PriestyAI/
├── agent/                  # Autonomous agent engine, session manager, git automation
├── commands/               # Application slash commands and context menus
├── config/                 # Settings and environment configuration
├── core/                   # Engine, client manager, encryption, router, memory manager
├── handlers/               # Chat handler, Components V2 streaming dispatcher
├── parsers/                # Discord markdown (DFM), math, emoji, mention, timestamp parsers
├── tools/                  # Registry and native tools (Docker sandbox, search, GitHub, math)
├── ui/                     # Components V2 layout views, modals, thought container, quizzes
├── searxng/                # Local SearXNG configuration
├── web/                    # Live canvas playground frontend template
├── bot.py                  # Discord client entrypoint & presence loop
├── requirements.txt        # Python package dependencies
├── Dockerfile              # Container definition for PriestyAI
└── docker-compose.yml      # Multi-service container orchestration
```

---

## Terms & Privacy (Official Hosted Instance)

The policies outlined below apply exclusively to users interacting with the **official, hosted instance** of PriestyAI on Discord:

* **[Terms of Service](TERMS.md)**: Governs service availability (Zero SLA disclaimer), acceptable use policies, safety filtering, and moderation enforcement.
* **[Privacy Policy](PRIVACY.md)**: Details data collection, at-rest database encryption, third-party inference sub-processors (Google Gemini, Groq, OpenRouter), and user rights (GDPR right-to-erasure via `/data`).

> [!NOTE]
> **Note for Self-Hosters:** If you are self-hosting your own independent instance of PriestyAI from this open-source repository, you are running on your own infrastructure and API credentials. You are solely responsible for managing your own data retention, privacy compliance, security, and user policies.

---

## License

This project is licensed under the [GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE).
