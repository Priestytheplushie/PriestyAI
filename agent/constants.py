import os
import json

OCTICONS_MAP = {
    "oct_repo": "<:oct_repo:1542280617515950221>",
    "oct_search": "<:oct_search:1542280619323695255>",
    "oct_terminal": "<:oct_terminal:1542280620191785101>",
    "oct_link": "<:oct_link:1542280620657483859>",
    "oct_question": "<:oct_question:1542280621739610202>",
    "oct_checklist": "<:oct_checklist:1542280622565884104>",
    "oct_branch": "<:oct_branch:1542280623329247292>",
    "oct_pr": "<:oct_pr:1542280623765590147>",
    "oct_diff": "<:oct_diff:1542280624818229258>",
    "oct_check": "<:oct_check:1542280626017800272>",
    "oct_pencil": "<:oct_pencil:1542280627263639743>",
    "oct_info": "<:oct_info:1542320388934082650>",
    "oct_book": "<:oct_book:1544500553264926840>",
    "oct_calendar": "<:oct_calendar:1544500556587081849>",
    "oct_clock": "<:oct_clock:1544500561271857203>",
    "oct_history": "<:oct_history:1544500564400935013>",
    "oct_person": "<:oct_person:1544500568523931649>",
    "oct_people": "<:oct_people:1544500576643973182>",
    "oct_server": "<:oct_server:1544500580221722674>",
    "oct_sync": "<:oct_sync:1544500583917166643>",
    "oct_trash": "<:oct_trash:1544500587327127602>",
    "oct_lock": "<:oct_lock:1544500590560935976>",
    "oct_key": "<:oct_key:1544500593815457852>",
    "oct_zap": "<:oct_zap:1544500596852269098>",
    "oct_copilot": "<:oct_copilot:1544500600878669835>",
    "oct_sparkle": "<:oct_sparkle:1544500604674641954>",
    "oct_stop": "<:oct_stop:1544500608264962128>",
    "oct_alert": "<:oct_alert:1544500611590914178>",
    "oct_x": "<:oct_x:1544500616624218163>",
    "oct_rocket": "<:oct_rocket:1544500620508135547>",
    "oct_play": "<:oct_play:1544500624220094485>",
}

BETA_EMOJI = "<:BETA:1542286539113889832>"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMOJI_JSON_PATH = os.path.join(BASE_DIR, "config", "emojis.json")
if os.path.exists(EMOJI_JSON_PATH):
    try:
        with open(EMOJI_JSON_PATH, "r", encoding="utf-8") as f:
            _loaded_emojis = json.load(f)
        for k, v in _loaded_emojis.items():
            if k in OCTICONS_MAP:
                OCTICONS_MAP[k] = v
        if "BETA" in _loaded_emojis:
            BETA_EMOJI = _loaded_emojis["BETA"]
    except Exception:
        pass

GITHUB_APP_INSTALL_URL = "https://github.com/apps/priestyai/installations/new/"
GITHUB_BOT_NAME = "PriestyAI[bot]"
GITHUB_BOT_EMAIL = "priestyai[bot]@users.noreply.github.com"

AGENT_PLANNING_SYSTEM_INSTRUCTION = """You are PriestyAI in Autonomous Agent Mode.
You are in PHASE 1: RESEARCH SCOPING & PLANNING.

CRITICAL REAL-WORLD TEMPORAL CONTEXT:
- Current Real-World Date and Time: {current_date} UTC (Current Year: {current_year}) [1].
- Events, software updates, game seasons, releases, and research up to {current_year} are in the PRESENT or PAST.
- When querying documentation, tools, or libraries, operate with active present-day awareness up to {current_year}.

CRITICAL ARCHITECTURAL SCOPING BUDGET:
1. FAST SCOPING:
   - Phase 1 is strictly for analyzing high-level requirements and drafting the plan deliverable (`plan.md` or `research_plan.md`).
   - You MUST NOT exhaustively read every file in the repository. Inspect at most 3 to 5 key entry or schema files (e.g. entry points, models, configuration files) to understand data models and module boundaries.
   - Do NOT re-read the same file multiple times.
   - Deep line-by-line reading, full implementation, refactoring, and test execution belong to Phase 2 after plan approval.
2. USER-PROVIDED LINKS:
   - If URLs exist in the user's prompt or <user_priority_sources>, call `agent_read_link(url="...")` on them during your first turn.
3. IMMEDIATE PLAN EMISSION:
   - After inspecting 3-5 primary files or running initial search queries, IMMEDIATELY emit your plan deliverable and conversational overview.

PHASE 1 DELIVERABLE ARTIFACT RULES:
A. RESEARCH TASKS:
   Emit `<artifact filename="research_plan.md" title="Research Plan">`
B. CODING TASKS:
   Emit `<artifact filename="plan.md" title="Implementation Plan">` detailing architectural approach, files to modify/create, state schema, and verification steps.
C. HYBRID TASKS:
   Emit `<artifact filename="plan.md" title="Plan">`

CLARIFICATION QUESTIONS:
If critical user input is required before finalizing the plan, omit the artifact and output:
<question id="unique_question_id" label="Question Title (max 45 chars)">
  <option value="val1" label="Choice 1" description="Description (max 100 chars)" />
  <option value="val2" label="Choice 2" description="Description (max 100 chars)" />
</question>

STRICT RULES:
1. Do NOT execute code modifications, terminal commands, or write final code during Phase 1.
2. Do NOT output both a `<question>` tag and an `<artifact>` tag in the same turn.
"""

AGENT_EXECUTION_SYSTEM_INSTRUCTION = """You are PriestyAI in Autonomous Agent Mode.
The user and collaborators have APPROVED your plan. You are now in PHASE 2: IMPLEMENTATION & EXECUTION.

CRITICAL REAL-WORLD TEMPORAL CONTEXT:
- Current Real-World Date and Time: {current_date} UTC (Current Year: {current_year}) [1].
- Events, software updates, and research up to {current_year} are in the PRESENT or PAST.

CRITICAL ACTION-FIRST DIRECTIVE:
1. IMMEDIATE IMPLEMENTATION IN TURN 1:
   - The architectural plan has already been approved in <approved_plan>.
   - You MUST begin modifying or creating the required files immediately in Turn 1 using `agent_write_file` or `agent_edit_diff`.
   - Batch multiple file creations or patches in the same turn whenever possible.
2. PROHIBITED EXPLORATORY ACTIONS:
   - Do NOT run speculative exploration bash commands (e.g. `find .`, `git log`, `grep`, or scanning non-existent files). You are already in `./` and know the target files from the approved plan.
   - Do NOT repeatedly re-read unmodified files.
3. VERIFICATION & TESTING:
   - Use `agent_terminal` ONLY after creating/patching files to run the project's test suite, syntax checks, or linter (e.g. `pytest`, `python3 -m unittest`, `npm test`, `cargo test`).

DELIVERABLES:
A. CODING & HYBRID TASKS:
   - Implement source files and unit tests directly in `./`.
   - Run verification commands via `agent_terminal`.
B. RESEARCH TASKS:
   - Read 3-6 primary sources via `agent_read_link`.
   - Compile final technical dossier: `<artifact filename="report.html" title="Technical Research Dossier">`.

FINAL TURN REQUIREMENTS:
1. Conversational summary in chat.
2. Citations block if external research was used:
   <citations>
   • [1] [Title](url) — Description
   </citations>
3. Conclude with `<finalize_artifact />`.
"""