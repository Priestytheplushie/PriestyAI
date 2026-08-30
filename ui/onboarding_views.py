import asyncio
import logging
from typing import Any, Callable
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    TextDisplay,
    ActionRow,
    Button
)
from core.config_manager import config_manager
from core.memory_manager import memory_manager
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.Onboarding")

TERMS_DOCUMENT_TEXT = """# Terms of Service & Safety Guidelines

### 1. Overview & Service Scope
PriestyAI ("the Service") is an open-source autonomous AI assistant, code reasoning engine, and workspace agent designed for Discord. By accessing, invoking, or interacting with the Service, you agree to comply with these Terms of Service, Discord's Community Guidelines, and applicable laws.

### 2. Service Availability & Zero SLA Disclaimer
The Service is provided strictly on an "AS IS" and "AS AVAILABLE" basis without any Service Level Agreement (Zero SLA) or warranty of uninterrupted 24/7 availability:
- Uptime & Maintenance: The Service may experience unexpected outages, API rate limit delays, maintenance downtime, or functional modifications at any time without notice or liability.
- Discontinuation & Deprecation: Hosted instances of the Service may be modified, suspended, or discontinued at any point in the future.
- Open-Source Availability: PriestyAI is an open-source project. Its complete codebase, documentation, and self-hosting instructions remain publicly accessible at:
  https://github.com/Priestytheplushie/PriestyAI

### 3. Incorporation of Privacy Policy
By agreeing to these Terms of Service, you acknowledge and agree that our Privacy Policy applies directly to your interaction with the Service:
- Privacy Policy Access: Review the complete Privacy Policy at any time via /privacy.
- Third-Party Inference Disclosures: You understand and consent to how prompts, files, and contextual data are processed, cryptographically encrypted at rest, and transmitted to third-party inference sub-processors (including Google Gemini API, Groq, OpenRouter, and Pollinations) as detailed in /privacy.

### 4. Acceptable Use & Prohibited Conduct
You agree not to submit, generate, or solicit any of the following:
- Sexually explicit, adult, NSFW, or non-consensual content.
- Targeted harassment, hate speech, direct threats, bullying, or defamation.
- Malicious code, exploits, denial-of-service scripts, keyloggers, or unauthorized penetration testing.
- Adversarial attempts to bypass safety filters or force illegal output.

### 5. Automated Moderation & Enforcement
All interactions are subject to real-time automated safety filtering:
- Standard Policy Refusals: Requests conflicting with routine safety guidelines receive a standard refusal without penalty to your account standing.
- Zero-Tolerance Violations: Critical or illegal violations (including child exploitation, severe threats, or cyber attacks) result in immediate, permanent account suspension across all bot instances.

### 6. Data Storage, Encryption & User Rights
To maintain conversational continuity and workspace features, the Service stores prompts, generated deliverables, and factual preferences:
- Data Encryption: Sensitive personal facts, chat session logs, and personal credentials stored in our database are cryptographically encrypted at rest.
- Passive Chat: Background chat from third parties who are not interacting with the bot is never stored.
- Privacy & Self-Service: Review our full Privacy Policy via /privacy. Full self-service data management is available via </data:1541122763044163665> (inspect/delete) and </config:1541093516078485646> (memory opt-out).

### 7. Disclaimer of Output Accuracy
The Service generates outputs using probabilistic machine learning models. Generated code, mathematical proofs, and technical explanations may occasionally contain bugs or inaccuracies. You are responsible for independently validating all outputs prior to execution."""

PRIVACY_DOCUMENT_TEXT = """# Privacy Policy

### 1. Overview
This Privacy Policy describes how PriestyAI ("the Service", "we", "our") collects, processes, and manages data when you interact with our Discord bot, commands, and autonomous workspace tools.

### 2. Information Collected
We collect only the minimum data required to facilitate conversational continuity and autonomous task execution:
- Account Identifiers: Discord User ID, Guild ID, and Channel ID.
- User Submissions: Prompts, command inputs, and files directly attached or referenced in conversation.
- Memory & Configurations: Custom personas, preferred names, Git author attribution, and user-authorized memory facts stored in the local database.
- Session History: Version trees of generated messages, research reports, and workspace deliverables.

We do not monitor, parse, or store passive channel messages from members who are not directly interacting with the Service.

### 3. Third-Party Inference Sub-Processors & Data Handling
To generate responses, user prompts and relevant context are transmitted to external AI providers via encrypted TLS connections:

A. Default Bot Operations (Chat, Reasoning, Autonomous Agents, Search):
- Google LLC (Gemini API & Google AI Studio): Handles general reasoning, embeddings, code analysis, and agent planning.
  * Notice regarding Unpaid/Free Tier API Usage: When operating on Google's unpaid API tiers, Google's terms specify that prompts and outputs may be processed and reviewed to develop and improve Google machine learning products and services. Do not submit unencrypted passwords, API secrets, or private personal credentials.
- Pollinations AI: Serves fallback AI image generation requests.

B. Multi-Model Generation Command (</generate:1542698067982164088>):
The following external providers are ONLY invoked when you explicitly run the </generate:1542698067982164088> command:
- Groq, Inc.: High-speed LPU inference when selecting Groq models via </generate:1542698067982164088>.
- OpenRouter: Multi-model API gateway when selecting OpenRouter free-tier models via </generate:1542698067982164088>.
- Microsoft Corporation (Edge Speech Services): Neural voice generation when selecting Audio via </generate:1542698067982164088>.
- Local Ollama Runtime: Processed entirely on local host infrastructure when selecting Local models via </generate:1542698067982164088>.

### 4. Data Security, Encryption & Retention
- Encryption in Transit: All data exchanged between Discord, the host server, and third-party inference APIs is transmitted over encrypted TLS connections.
- Encryption at Rest: Sensitive database fields—including personal memory facts, multi-turn chat session logs, and personal configuration credentials—are cryptographically encrypted at rest using authenticated symmetric encryption (Fernet / AES with PBKDF2-HMAC-SHA256 key derivation).
- Workspace Isolation: Temporary agent workspace directories and sandbox containers are automatically pruned after 24 hours of inactivity or upon session closure.

### 5. User Control & Data Deletion (Right to Erasure)
You maintain complete control over your stored data:
- Inspect Data: Run </data:1541122763044163665> at any time to inspect all stored personal facts, server lore, and configuration profiles.
- Permanently Erase Data: Select Delete in </data:1541122763044163665> to immediately purge all memories, chat sessions, and generation history from our database.
- Opt Out of Memory: Set your personal memory policy to Read-Only or Disabled in </config:1541093516078485646> to prevent the bot from recording future facts.

### 6. Inquiries & Source Code
For questions regarding data processing, encryption, or to inspect the open-source codebase, visit:
https://github.com/Priestytheplushie/PriestyAI"""


def build_welcome_terms_modal(on_agree_callback: Callable[[discord.Interaction], Any]) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": f"{TERMS_DOCUMENT_TEXT}\n\n---\n\n{PRIVACY_DOCUMENT_TEXT}"
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
                content="You must check the agreement box to use PriestyAI.",
                ephemeral=True
            )

    return DynamicModalV2(
        title="Welcome to PriestyAI",
        custom_id="modal_onboarding_terms",
        fields_schema=fields,
        on_submit_callback=on_submit
    )


def build_terms_review_modal() -> DynamicModalV2:
    review_content = f"{TERMS_DOCUMENT_TEXT}\n\n---\n-# Note: You agreed to these terms and the Privacy Policy (/privacy) while interacting with PriestyAI."
    fields = [
        {
            "type": "text_display",
            "content": review_content
        }
    ]

    async def on_review_submit(interaction: discord.Interaction, data: dict[str, Any]):
        if not interaction.response.is_done():
            await interaction.response.defer()

    return DynamicModalV2(
        title="Terms of Service",
        custom_id="modal_terms_review",
        fields_schema=fields,
        on_submit_callback=on_review_submit
    )


def build_privacy_modal() -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": PRIVACY_DOCUMENT_TEXT
        }
    ]

    async def on_privacy_submit(interaction: discord.Interaction, data: dict[str, Any]):
        if not interaction.response.is_done():
            await interaction.response.defer()

    return DynamicModalV2(
        title="Privacy Policy",
        custom_id="modal_privacy_review",
        fields_schema=fields,
        on_submit_callback=on_privacy_submit
    )


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

        welcome_text = (
            f"### Welcome to PriestyAI\n"
            f"Hello {self.author.mention}. Before we begin, please review our Terms of Service, "
            f"Zero SLA Service Policy, and Privacy Policy (/privacy).\n\n"
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
        modal = build_privacy_modal()
        await interaction.response.send_modal(modal)

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

        notice_text = (
            f"### Account Access Suspended\n"
            f"{self.author.mention}, your access to PriestyAI has been revoked "
            f"due to violations of our Safety Guidelines and Terms of Service.\n\n"
            f"Under our Privacy Policy and GDPR right-to-erasure guidelines, you may permanently "
            f"delete all personal data and memories stored about your account below.\n\n"
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