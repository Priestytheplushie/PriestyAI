# Privacy Policy

### 1. Overview
This Privacy Policy describes how PriestyAI ("the Service", "we", "our") collects, processes, and manages data when you interact with our Discord bot, commands, and autonomous workspace tools.

### 2. Information Collected
We collect only the minimum data required to facilitate conversational continuity and autonomous task execution:
- **Account Identifiers:** Discord User ID, Guild ID, and Channel ID.
- **User Submissions:** Prompts, command inputs, and files directly attached or referenced in conversation.
- **Memory & Configurations:** Custom personas, preferred names, Git author attribution, and user-authorized memory facts stored in the local database.
- **Session History:** Version trees of generated messages, research reports, and workspace deliverables.

We do not monitor, parse, or store passive channel messages from members who are not directly interacting with the Service.

### 3. Third-Party Inference Sub-Processors & Data Handling
To generate responses, user prompts and relevant context are transmitted to external AI providers via encrypted TLS connections:

### A. Default Bot Operations (Chat, Reasoning, Autonomous Agents, Search):
- **Google LLC (Gemini API & Google AI Studio):** Handles general reasoning, embeddings, code analysis, and agent planning.
  - *Notice regarding Unpaid/Free Tier API Usage:* When operating on Google's unpaid API tiers, Google's terms specify that prompts and outputs may be processed and reviewed to develop and improve Google machine learning products and services. Do not submit unencrypted passwords, API secrets, or private personal credentials.
- **Pollinations AI:** Serves fallback AI image generation requests.

### B. Multi-Model Generation Command (`/generate`):
The following external providers are ONLY invoked when you explicitly run the `/generate` command:
- **Groq, Inc.:** High-speed LPU inference when selecting Groq models via `/generate`.
- **OpenRouter:** Multi-model API gateway when selecting OpenRouter free-tier models via `/generate`.
- **Microsoft Corporation (Edge Speech Services):** Neural voice generation when selecting Audio via `/generate`.
- **Local Ollama Runtime:** Processed entirely on local host infrastructure when selecting Local models via `/generate`.

### 4. Data Security, Encryption & Retention
- **Encryption in Transit:** All data exchanged between Discord, the host server, and third-party inference APIs is transmitted over encrypted TLS connections.
- **Encryption at Rest:** Sensitive database fields—including personal memory facts, multi-turn chat session logs, and personal configuration credentials—are cryptographically encrypted at rest using authenticated symmetric encryption (Fernet / AES with PBKDF2-HMAC-SHA256 key derivation).
- **Workspace Isolation:** Temporary agent workspace directories and sandbox containers are automatically pruned after 24 hours of inactivity or upon session closure.

### 5. User Control & Data Deletion (Right to Erasure)
You maintain complete control over your stored data:
- **Inspect Data:** Run `/data` at any time to inspect all stored personal facts, server lore, and configuration profiles.
- **Permanently Erase Data:** Select Delete in `/data` to immediately purge all memories, chat sessions, and generation history from our database.
- **Opt Out of Memory:** Set your personal memory policy to Read-Only or Disabled in `/config` to prevent the bot from recording future facts.

### 6. Inquiries & Source Code
For questions regarding data processing, encryption, or to inspect the open-source codebase, visit:
https://github.com/Priestytheplushie/PriestyAI