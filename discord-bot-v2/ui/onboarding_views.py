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
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.Onboarding")

TERMS_DOCUMENT_TEXT = """# Terms of Service & Safety Guidelines

### 1. Overview & Service Scope
PriestyAI is an intelligent server companion, reasoning assistant, and autonomous software development tool designed for Discord. By accessing or interacting with PriestyAI, you agree to comply with these terms, Discord's Community Guidelines, and applicable laws.

### 2. Acceptable Use & Prohibited Conduct
You agree not to engage in or attempt any of the following activities:
• Jailbreaking, prompt injection, or attempting to bypass model safety constraints.
• Generating, requesting, or distributing NSFW, sexually explicit, graphic violent, or non-consensual content.
• Harassment, hate speech, threats, defamation, or malicious exploitation.
• Executing malicious, destructive, or unauthorized code within sandbox environments.

### 3. Automated Moderation & Enforcement
All interactions and prompts are actively monitored by automated safety guardrails and moderation filters. Engaging in policy violations, abuse, or attempts to circumvent safety mechanisms will result in immediate and permanent revocation of your access without appeal.

### 4. Data Storage & Privacy Rights
To provide conversational continuity, memory features, and version history, PriestyAI stores your direct prompts, generated deliverables, and factual preferences in an isolated local database. 
• Passive channel chat from third parties is never permanently stored.
• You retain full self-service control over your data. You may inspect, modify, or permanently wipe all stored data at any time using the </data:1541122763044163665> command or opt out of memory via </config:1541093516078485646>.

### 5. AI Disclaimer & Limitation of Liability
PriestyAI generates responses based on probabilistic AI models. Outputs may occasionally contain factual errors, hallucinated claims, or code bugs. You are solely responsible for independently reviewing and verifying any code, calculations, or advice before execution."""

def build_welcome_terms_modal(on_agree_callback: Callable[[discord.Interaction], Any]) -> DynamicModalV2:
    fields = [
        {
            "type": "text_display",
            "content": TERMS_DOCUMENT_TEXT
        },
        {
            "type": "checkbox_group",
            "custom_id": "terms_agreement_checkbox",
            "label": "Acknowledgment & Consent",
            "description": "Required to interact with PriestyAI",
            "required": True,
            "options": [
                {
                    "label": "I agree to the Terms of Service, Safety Rules, and Moderation Policies",
                    "value": "agreed",
                    "description": "I accept the guidelines and understand automated moderation is active.",
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
            logger.info(f"[Onboarding] User {interaction.user} ({interaction.user.id}) accepted Terms of Service.")
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
            f"Hello {self.author.mention}! Before we begin, please review our terms of service, "
            f"safety guidelines, and automated moderation policies.\n\n"
            f"-# This message will auto-delete in 90 seconds."
        )
        container.add_item(TextDisplay(welcome_text))

        review_btn = Button(
            label="Review & Accept",
            style=discord.ButtonStyle.primary,
            custom_id="btn_onboard_review"
        )
        review_btn.callback = self._on_review_clicked

        dismiss_btn = Button(
            label="Dismiss",
            style=discord.ButtonStyle.secondary,
            custom_id="btn_onboard_dismiss"
        )
        dismiss_btn.callback = self._on_dismiss_clicked

        container.add_item(ActionRow(review_btn, dismiss_btn))
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
                content=f"❌ This welcome card is for {self.author.mention}.",
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

    async def _on_dismiss_clicked(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                content=f"❌ This welcome card is for {self.author.mention}.",
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