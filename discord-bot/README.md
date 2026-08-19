# PriestyAI for Discord

[![Add to Discord](https://img.shields.io/badge/Discord-Add_to_Server-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)
[![User App](https://img.shields.io/badge/User_App-Install_to_Account-eb459e?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1509364708476452894)

PriestyAI is an intelligent, multi-modal Discord AI companion and server automation system. Designed to integrate naturally into communities, PriestyAI features transparent reasoning, long-term semantic memory, declarative UI layout generation (Components V2), autonomous research agents, and an automated daily broadcast news video engine.

---

## QuickStart (Using the Hosted Discord Bot)

Get up and running in three steps:

1. **Invite PriestyAI:** Add [PriestyAI](https://discord.com/oauth2/authorize?client_id=1509364708476452894) to your Discord server or install it directly to your personal account as a **User App**.
2. **Configure Settings:** Run `/config` in any channel to configure active tool pipelines, custom system instructions, reasoning depth, or Server News settings.
3. **Start Chatting:** Mention `@PriestyAI` in any public channel, reply to its messages, or run `/chat` to start an interactive conversation session.

---

## Using PriestyAI

PriestyAI interacts natively across your server through standard chat messages, interactive UI components, context menus, and automated background tasks.

### 1. Natural Companion with Transparent Reasoning

> [!TIP]
> Click the **`Thought for Xs`** button on any message to inspect PriestyAI's internal logic, chain of thought, and planning steps in an ephemeral popup.

* **Conversational Flow:** Understands multi-user banter, reacts organically with emojis, supports double-texting (`[FOLLOW_UP]`), and performs natural typo self-edits.
* **Multi-Modal Understanding:** Reads and analyzes attached images, PDFs, Word documents (`.docx`), Excel spreadsheets (`.xlsx`), audio notes, and video files.
* **LaTeX Formula Rendering:** Automatically renders math and physics equations into transparent 150 DPI graphics for clear readability.

---

### 2. Automated Daily Server Newsroom

PriestyAI features a broadcast production pipeline that generates fully voiced, compiled MP4 news videos recapping your server's daily highlights:

```
Scrape Logs & Presences ──▶ Director & Editor Scripting ──▶ Voice & Video Render ──▶ Broadcast & Q&A
```

* **Two Daily Editions:** Produces a data-driven **Morning Show** (with schedules, server velocity charts, and daily polls) and a satirical **Late-Night Talk Show** (featuring couch guest interviews, award plaques, and community roasts).
* **High-Fidelity Production:** Combines Edge-TTS voice narration, dynamic Matplotlib activity graphs, interactive user quote boards, Pollinations AI vibe art, animated host avatars, ducked background music, and FFmpeg video compositing.
* **Interactive Wrap-Up:** Automatically posts the Streamable video link, writes highlight summaries, creates a morning Q&A thread, and launches native Discord Question of the Day (QOTD) polls.

---

### 3. Modern Components V2 Message Builder

PriestyAI can compile rich, interactive Discord interfaces using modern Components V2:

* **Declarative Python DSL:** Compiles Python layout specifications into borderless containers, multi-column sections with accessories, visual dividers, and interactive dropdowns.
* **Interactive Modal Popups:** Generates Modals with support for text fields, multi-select checkboxes, radio buttons, entity selectors, and file upload targets.
* **Safe Sandbox Validation:** Verifies layout code against AST security policies and Discord API limitations before rendering.

---

### 4. Autonomous Diagnostic & Research Agents

Run `/agent` to launch an autonomous multi-step reasoning agent inside a private, dedicated thread:

1. **Plan Formulation:** PriestyAI analyzes your prompt, formulates an execution plan, and presents a pre-start checklist.
2. **Context Snapshots:** Attach saved message transcripts, user profile snapshots, or server lore to ground the agent's research.
3. **Execution Loop:** The agent uses tools, investigates logs, performs web queries, and delivers modular diagnostic reports.
4. **Deep Web Research:** Recursively crawls web search indexes, reconciles data discrepancies, and outputs comprehensive Word (`.docx`) or Markdown (`.md`) reports.

---

### 5. Semantic Memory Journals

PriestyAI maintains a long-term memory database backed by dense vector embeddings:

* **Factual Durability Gatekeeper:** Audits conversations in the background to extract meaningful facts while ignoring temporary slang, noise, or short-term banter.
* **Semantic Recall:** Performs cosine similarity lookups to retrieve relevant background context across user traits, server lore, and global knowledge bases.
* **Memory Categorization:** Automatically organizes saved facts under Profile & Identity, Technical Environment, and Relationship & Vibe headings.

---

## Command & Context Menu Cheatsheet

### Slash Commands

| Command | Description | Context |
| :--- | :--- | :--- |
| `/chat` | Launches an isolated conversational session with tool configuration | Server / DM / User App |
| `/config` | Configures channel settings, bot server identity, or Server News | Server / DM |
| `/generate` | Forces generation of an Image, Message Builder Layout, or Legacy UI | Server / DM |
| `/agent` | Initiates an autonomous research and diagnostic agent thread | Servers Only |
| `/context delete` | Permanently deletes a saved user or message context snapshot | Server / DM |
| `/start` | *(Owner-Only)* Triggers an immediate Server News pre-generation run | Servers Only |

### Context Menus (Right-Click Apps)

| Action | Target | Description |
| :--- | :--- | :--- |
| **Re-run** | Bot Message | Generates an alternative response version with pagination controls |
| **Branch** | Any Message | Spins off an isolated conversation thread from that specific message |
| **Save Message as Context** | Message | Archives message metadata and transcript as a reusable agent profile |
| **Save User as Context** | Member | Archives user roles, status, and profile info as an agent snapshot |
| **Reset AI Memory** | Member | Clears all saved long-term memory records for the selected user |
| **Delete Bot Message** | Bot Message | Removes a message sent by PriestyAI |

---

## Local Development & Setup

If you are developing, testing, or hosting the Discord Bot service yourself:

### 1. Prerequisites
* **Python 3.11+**
* **FFmpeg** installed and accessible in your system `PATH` (required for video/audio rendering).
* A **Discord Bot Application** with all **Privileged Gateway Intents** enabled (Server Members, Presence, Message Content) in the [Discord Developer Portal](https://discord.com/developers/applications).
* A private **Discord Brain Server** (used by the bot to store long-term vector memories and forum configurations).
* A **Google Gemini API Key** from [Google AI Studio](https://aistudio.google.com/).

### 2. Environment Configuration
Copy the template inside `discord-bot/` and fill in your credentials:
```bash
cp discord-bot/.env.example discord-bot/.env
```

Key environment variables:
* `DISCORD_TOKEN`: Discord Bot token from the Developer Portal.
* `BRAIN_SERVER_ID`: Guild ID of your private Discord storage server.
* `GEMINI_API_KEY`: Primary Google Gemini API key.
* `OWNER_ID`: Your personal Discord User ID (for admin commands like `/start`).
* `PEXELS_KEY`: *(Optional)* Pexels API key for HD news video backgrounds.
* `STREAMABLE_EMAIL` / `STREAMABLE_PASSWORD`: *(Optional)* Streamable credentials for hosting news broadcast clips.
* `PIXAZO_API_KEY`: *(Optional)* Pixazo Gateway key for Flux/SDXL image generation fallback.

### 3. Running the Service
```bash
# Run standalone from within discord-bot/
python run.py

# Or run via the PriestyAI CLI from the repository root
priestyai --discord-only
```

---

## Running Unit Tests

Run the test suite from the repository root or inside the `discord-bot` directory using `pytest`:

```bash
# Run all Discord Bot unit tests
pytest discord-bot/tests

# Run with verbose output and test coverage report
pytest -v --cov=discord-bot discord-bot/tests
```

---

## Privacy & Security

* **Dedicated Brain Server Isolation:** All long-term memories, user context snapshots, and configuration parameters are stored within an isolated, private Discord Brain Server.
* **Ephemeral Reasoning Visibility:** Inner thinking blocks are served through private ephemeral interactions to keep public channels clean and secure.
* **Strict Memory Gating:** Background evaluators use threshold scoring to prevent temporary chat logs or private user chatter from polluting persistent memory stores.
* **Sandboxed Layout Execution:** Layout scripts execute through a strict AST-validated compiler that strips imports, loops, and unapproved component definitions.

## License

This subproject is part of the PriestyAI monorepo and is licensed under the [GNU AGPLv3](../LICENSE). For trademark, persona, and self-hosting branding rules, see the [Root README](../README.md#license--brand-guidelines).