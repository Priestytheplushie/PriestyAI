import os
import re
import asyncio
import logging
from typing import Any, Callable
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    TextDisplay,
    Separator,
    ActionRow,
    Button
)
from core.config_manager import config_manager
from core.memory_manager import memory_manager
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.Onboarding")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TERMS_FILE_PATH = os.path.join(BASE_DIR, "TERMS.md")
PRIVACY_FILE_PATH = os.path.join(BASE_DIR, "PRIVACY.md")

GITHUB_REPO_URL = "https://github.com/Priestytheplushie/PriestyAI"
GITHUB_TERMS_URL = f"{GITHUB_REPO_URL}/blob/main/TERMS.md"
GITHUB_PRIVACY_URL = f"{GITHUB_REPO_URL}/blob/main/PRIVACY.md"

DISCORD_COMMAND_MENTION_MAP = {
    "/ask": "</ask:1540889817980731543>",
    "/chat": "</chat:1540889817980731543>",
    "/config": "</config:1541093516078485646>",
    "/data": "</data:1541122763044163665>",
    "/agent": "</agent:1542280617515950221>",
    "/generate": "</generate:1542698067982164088>",
    "/feedback": "</feedback:1541122763044163665>",
    "/terms": "</terms:1540889817980731543>",
    "/privacy": "</privacy:1540889817980731543>"
}

DEFAULT_TERMS_FALLBACK = """# Terms of Service & Safety Guidelines

### 1. Overview & Service Scope
PriestyAI ("the Service") is an open-source autonomous AI assistant, code reasoning engine, and workspace agent designed for Discord. By accessing, invoking, or interacting with the Service, you agree to comply with these Terms of Service, Discord's Community Guidelines, and applicable laws.

**Scope of Agreement (Official Hosted Instance vs. Self-Hosting):**
These Terms of Service govern your interaction with the **official, hosted instance** of PriestyAI on Discord. If you are self-hosting an independent instance from our open-source codebase, you operate on your own infrastructure and API credentials; the project authors assume zero liability, warranty, or operational responsibility for third-party self-hosted deployments.

### 2. Service Availability & Zero SLA Disclaimer
The Service is provided strictly on an "AS IS" and "AS AVAILABLE" basis without any Service Level Agreement (Zero SLA) or warranty of uninterrupted 24/7 availability.

### 3. Acceptable Use & Prohibited Conduct
You agree not to submit, generate, or solicit sexually explicit, harassing, malicious, or adversarial content.

### 4. Data Storage, Encryption & User Rights
Sensitive personal facts, chat session logs, and personal credentials stored in our database are cryptographically encrypted at rest. Full self-service data management is available via `/data` and `/config`."""

DEFAULT_PRIVACY_FALLBACK = """# Privacy Policy

### 1. Overview
This Privacy Policy describes how the **official, hosted instance** of PriestyAI collects, processes, and manages data when you interact with our Discord bot, slash commands, and autonomous workspace tools.

*Note for Self-Hosters:* Independent self-hosted instances run on your own hardware and infrastructure. Data storage, retention policies, and compliance obligations are entirely managed by the self-hoster.

### 2. Information Collected
We collect only the minimum data required to facilitate conversational continuity: User ID, Channel ID, prompts, and user-authorized memories.

### 3. Data Security & Encryption
Sensitive database fields are encrypted at rest using AES-256 (Fernet with PBKDF2-HMAC-SHA256). You can inspect or delete your stored data at any time via `/data`."""


def _transform_commands_for_discord(markdown_text: str) -> str:
    if not markdown_text:
        return ""

    result = markdown_text
    for clean_cmd, mention in DISCORD_COMMAND_MENTION_MAP.items():
        escaped_cmd = re.escape(clean_cmd)
        result = re.sub(rf'`{escaped_cmd}`', mention, result)
        result = re.sub(rf'(?<![</a-zA-Z0-9_`]){escaped_cmd}(?![a-zA-Z0-9_:>`])', mention, result)

    return result


def load_terms_document(for_discord: bool = True) -> str:
    content = ""
    if os.path.exists(TERMS_FILE_PATH):
        try:
            with open(TERMS_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception as e:
            logger.warning(f"[Onboarding] Failed to read {TERMS_FILE_PATH}: {e}")

    if not content:
        content = DEFAULT_TERMS_FALLBACK

    return _transform_commands_for_discord(content) if for_discord else content


def load_privacy_document(for_discord: bool = True) -> str:
    content = ""
    if os.path.exists(PRIVACY_FILE_PATH):
        try:
            with open(PRIVACY_FILE_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception as e:
            logger.warning(f"[Onboarding] Failed to read {PRIVACY_FILE_PATH}: {e}")

    if not content:
        content = DEFAULT_PRIVACY_FALLBACK

    return _transform_commands_for_discord(content) if for_discord else content


def load_onboarding_summary_document() -> str:
    p_mention = DISCORD_COMMAND_MENTION_MAP.get('/privacy', '/privacy')
    t_mention = DISCORD_COMMAND_MENTION_MAP.get('/terms', '/terms')
    d_mention = DISCORD_COMMAND_MENTION_MAP.get('/data', '/data')
    c_mention = DISCORD_COMMAND_MENTION_MAP.get('/config', '/config')

    summary = f"""# Welcome to PriestyAI
Please review and agree to our **[Terms of Service]({GITHUB_TERMS_URL})** and **[Privacy Policy]({GITHUB_PRIVACY_URL})** to proceed.

### 1. Service Scope & Zero SLA Disclaimer
PriestyAI is an autonomous AI companion and reasoning engine for Discord provided on an "AS IS" and "AS AVAILABLE" basis without uptime warranties (Zero SLA). Self-hosted instances operate independently.

### 2. Acceptable Use & Automated Moderation
You agree not to generate sexually explicit, harassing, malicious, or adversarial content. Critical zero-tolerance violations result in permanent account suspension.

### 3. Data Encryption & Privacy
- **Encryption at Rest:** Sensitive database records (memories, chat logs, credentials) are cryptographically encrypted at rest using AES-256 (Fernet / PBKDF2-HMAC-SHA256).
- **Passive Chat:** Unrelated background messages in channels are never stored.

### 4. Third-Party Inference Sub-Processors
Prompts are transmitted over encrypted TLS to external inference APIs (Google Gemini API, and optionally Groq, OpenRouter, or Pollinations when explicitly invoked).

### 5. User Control & Data Erasure (GDPR)
- Inspect, export JSON, or permanently delete personal data at any time via {d_mention}.
- Adjust or disable memory banks via {c_mention}.

-# Review complete documents via {t_mention} and {p_mention}, or inspect on GitHub: [TERMS.md]({GITHUB_TERMS_URL}) • [PRIVACY.md]({GITHUB_PRIVACY_URL})."""
    return summary


def build_welcome_terms_modal(on_agree_callback: Callable[[discord.Interaction], Any]) -> DynamicModalV2:
    modal_content = load_onboarding_summary_document()

    fields = [
        {
            "type": "text_display",
            "content": modal_content
        },
        {
            "type": "checkbox_group",
            "custom_id": "terms_agreement_checkbox",
            "label": "Acknowledgment & Consent",
            "description": "Required to interact with PriestyAI",
            "required": True,
            "options": [
                {
                    "label": "I agree to the Terms of Service, Safety Guidelines, Zero SLA, and Privacy Policy",
                    "value": "agreed",
                    "description": "I understand the service terms, data handling policies, and safety guardrails.",
                    "default": False
                }
            ]
        }
    ]

    async def on_submit(interaction: discord.Interaction, data: dict[str, Any]):
        selected = data.get("terms_agreement_checkbox", [])
        is_agreed = False
        if isinstance(selected, list):
            is_agreed = "agreed" in selected
        elif isinstance(selected, str):
            is_agreed = (selected == "agreed")

        if is_agreed:
            config_manager.record_user_agreement(interaction.user.id)
            logger.info(f"[Onboarding] User {interaction.user} ({interaction.user.id}) accepted Terms & Privacy Policy.")
            await on_agree_callback(interaction)
        else:
            await interaction.response.send_message(
                content="❌ You must check the agreement box to use PriestyAI.",
                ephemeral=True
            )

    return DynamicModalV2(
        title="Welcome to PriestyAI",
        custom_id="modal_onboarding_terms",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


class LegalDocumentViewerLayoutView(LayoutView):
    def __init__(
        self,
        doc_type: str = "terms",
        user: discord.User | discord.Member | None = None,
        page: int = 0
    ):
        super().__init__(timeout=600)
        self.doc_type = doc_type.lower().strip()
        self.user = user
        self.current_page = page
        self.pages: list[str] = []
        self._prepare_pages()
        self._build_layout()

    def _prepare_pages(self):
        if self.doc_type == "privacy":
            raw_text = load_privacy_document(for_discord=True)
        else:
            raw_text = load_terms_document(for_discord=True)

        sections = re.split(r'(?m)(?=^###\s+\d+\.)', raw_text)
        pages = []
        current_chunk = []
        current_len = 0

        for sec in sections:
            sec_clean = sec.strip()
            if not sec_clean:
                continue

            sec_len = len(sec_clean)
            if (current_len + sec_len > 1750 or len(current_chunk) >= 3) and current_chunk:
                pages.append("\n\n".join(current_chunk))
                current_chunk = [sec_clean]
                current_len = sec_len
            else:
                current_chunk.append(sec_clean)
                current_len += sec_len

        if current_chunk:
            pages.append("\n\n".join(current_chunk))

        self.pages = pages if pages else [raw_text[:1800]]

    def _build_layout(self):
        self.clear_items()
        container = Container()

        total_pages = max(1, len(self.pages))
        self.current_page = max(0, min(self.current_page, total_pages - 1))
        page_content = self.pages[self.current_page]

        doc_title = "Privacy Policy" if self.doc_type == "privacy" else "Terms of Service & Safety Guidelines"
        github_url = GITHUB_PRIVACY_URL if self.doc_type == "privacy" else GITHUB_TERMS_URL

        header_str = f"# {doc_title} (Page {self.current_page + 1}/{total_pages})\n\n{page_content}"
        container.add_item(TextDisplay(header_str[:3600]))
        container.add_item(Separator(visible=True))

        footer_text = (
            "-# Note: You are bound by the Terms of Service and Privacy Policy while interacting with PriestyAI."
        )
        container.add_item(TextDisplay(footer_text))

        user_id = self.user.id if self.user else 0
        has_agreed = config_manager.has_user_agreed(user_id) if user_id else True

        row_items = []
        if total_pages > 1:
            prev_btn = Button(
                label="◀",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page == 0),
                custom_id="btn_doc_prev"
            )
            ind_btn = Button(
                label=f"{self.current_page + 1} / {total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
                custom_id="btn_doc_ind"
            )
            next_btn = Button(
                label="▶",
                style=discord.ButtonStyle.secondary,
                disabled=(self.current_page >= total_pages - 1),
                custom_id="btn_doc_next"
            )

            async def on_prev(inter: discord.Interaction):
                self.current_page -= 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            async def on_next(inter: discord.Interaction):
                self.current_page += 1
                self._build_layout()
                await inter.response.edit_message(view=self)

            prev_btn.callback = on_prev
            next_btn.callback = on_next
            row_items.extend([prev_btn, ind_btn, next_btn])

        if not has_agreed:
            accept_btn = Button(
                label="Accept & Agree",
                style=discord.ButtonStyle.primary,
                custom_id="btn_doc_accept"
            )

            async def on_accept_clicked(inter: discord.Interaction):
                config_manager.record_user_agreement(inter.user.id)
                self._build_layout()
                await inter.response.edit_message(view=self)
                await inter.followup.send(
                    content="✅ **Terms & Privacy Accepted!** You are now authorized to use all PriestyAI commands.",
                    ephemeral=True
                )

            accept_btn.callback = on_accept_clicked
            row_items.append(accept_btn)

        gh_btn = Button(
            label="View on GitHub",
            style=discord.ButtonStyle.link,
            url=github_url
        )
        row_items.append(gh_btn)

        container.add_item(ActionRow(*row_items))
        self.add_item(container)


class WelcomeOnboardingCardView(LayoutView):
    def __init__(
        self,
        author: discord.User | discord.Member,
        on_accepted_callback: Callable[[discord.Interaction, discord.Message], Any],
        timeout: float = 90.0
    ):
        super().__init__(timeout=timeout)
        self.author = author
        self.on_accepted_callback = on_accepted_callback
        self.message: discord.Message | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._build_card()

    def _build_card(self):
        self.clear_items()
        container = Container()

        privacy_mention = DISCORD_COMMAND_MENTION_MAP.get("/privacy", "/privacy")
        terms_mention = DISCORD_COMMAND_MENTION_MAP.get("/terms", "/terms")
        welcome_text = (
            f"### Welcome to PriestyAI\n"
            f"Hello {self.author.mention}. Before we begin, please review our "
            f"**[Terms of Service]({GITHUB_TERMS_URL})** ({terms_mention}), "
            f"Zero SLA Service Policy, and **[Privacy Policy]({GITHUB_PRIVACY_URL})** ({privacy_mention}).\n\n"
            f"-# This prompt will automatically expire in 90 seconds."
        )
        container.add_item(TextDisplay(welcome_text))

        review_btn = Button(
            label="Review & Accept",
            style=discord.ButtonStyle.primary,
            custom_id="btn_onboard_review"
        )
        review_btn.callback = self._on_review_clicked

        privacy_btn = Button(
            label="Privacy Policy",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_onboard_privacy"
        )
        privacy_btn.callback = self._on_privacy_clicked

        dismiss_btn = Button(
            label="Dismiss",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_onboard_dismiss"
        )
        dismiss_btn.callback = self._on_dismiss_clicked

        container.add_item(ActionRow(review_btn, privacy_btn, dismiss_btn))
        self.add_item(container)

    def start_cleanup_timer(self):
        if not self._cleanup_task or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._auto_delete_after(90.0))

    async def _auto_delete_after(self, delay: float):
        try:
            await asyncio.sleep(delay)
            if self.message:
                await self.message.delete()
        except Exception:
            pass

    async def _on_review_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content=f"This welcome prompt is intended for {self.author.mention}.",
                ephemeral=True
            )
            return

        async def after_agreed(sub_inter: discord.Interaction):
            if self._cleanup_task and not self._cleanup_task.done():
                self._cleanup_task.cancel()
            if self.message:
                await self.on_accepted_callback(sub_inter, self.message)

        modal = build_welcome_terms_modal(on_agree_callback=after_agreed)
        await interaction.response.send_modal(modal)

    async def _on_privacy_clicked(self, interaction: discord.Interaction):
        viewer = LegalDocumentViewerLayoutView(doc_type="privacy", user=interaction.user, page=0)
        await interaction.response.send_message(view=viewer, ephemeral=True)

    async def _on_dismiss_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content=f"This welcome prompt is intended for {self.author.mention}.",
                ephemeral=True
            )
            return

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()

        try:
            await interaction.response.defer()
            if self.message:
                await self.message.delete()
        except Exception as e:
            logger.debug(f"Dismiss delete error: {e}")

    async def on_timeout(self):
        try:
            if self.message:
                await self.message.delete()
        except Exception:
            pass


class BannedUserNoticeView(LayoutView):
    def __init__(self, author: discord.User | discord.Member):
        super().__init__(timeout=600)
        self.author = author
        self._build_card()

    def _build_card(self):
        self.clear_items()
        container = Container()

        d_mention = DISCORD_COMMAND_MENTION_MAP.get("/data", "/data")
        notice_text = (
            f"### Account Access Suspended\n"
            f"{self.author.mention}, your access to PriestyAI has been revoked "
            f"due to violations of our **[Safety Guidelines]({GITHUB_TERMS_URL})** and **[Terms of Service]({GITHUB_TERMS_URL})**.\n\n"
            f"Under our **[Privacy Policy]({GITHUB_PRIVACY_URL})** and GDPR right-to-erasure guidelines, you may permanently "
            f"delete all personal data and memories stored about your account via {d_mention} below.\n\n"
            f"-# Deleting stored data will not lift account suspension."
        )
        container.add_item(TextDisplay(notice_text))

        purge_btn = Button(
            label="Delete My Stored Data",
            style=discord.ButtonStyle.danger,
            custom_id="btn_ban_purge_data"
        )
        purge_btn.callback = self._on_purge_clicked

        container.add_item(ActionRow(purge_btn))
        self.add_item(container)

    async def _on_purge_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content="You cannot manage data for this account.",
                ephemeral=True
            )
            return

        res = memory_manager.purge_entire_user_data(self.author.id)
        logger.info(f"[GDPR Wipe on Ban] Purged data for banned user {self.author.id}: {res}")

        confirmation_text = (
            f"### Account Access Suspended\n"
            f"All personal data, memories, configurations, and chat sessions associated with your account "
            f"have been permanently erased from our database.\n\n"
            f"-# Account suspension remains in effect."
        )
        container = Container()
        container.add_item(TextDisplay(confirmation_text))
        self.clear_items()
        self.add_item(container)

        await interaction.response.edit_message(view=self)