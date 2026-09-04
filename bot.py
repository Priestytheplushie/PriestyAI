import io
import re
import os
import time
import base64
import asyncio
import logging
import aiohttp
from typing import Any
import discord
from discord import app_commands
from config.settings import DISCORD_TOKEN
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.memory_manager import memory_manager, get_user_chat_session_id
from core.poll_manager import poll_manager
from core.schedule_manager import schedule_manager
from core.searxng_client import searxng_client
from core.playground_server import playground_server
from core.screenshot_service import screenshot_service
from core.smee_service import smee_service
from core.github_app_client import github_app_client
from agent.session_manager import session_manager, normalize_repo_url
from agent.git_manager import git_manager
from agent.engine import AgentEngine
from agent.views import (
    AgentStepInspectorView,
    AgentFinalDeliverableView,
    AgentReadyForReviewView,
    AgentSignOffStepView,
    AgentSignOffInspectorView,
    AgentPRPublishedView,
    AgentCIFailureView,
    AgentMergeConflictView,
    AgentPRMergedView,
    AgentPRClosedUnmergedView,
    build_agent_new_task_modal,
    build_agent_signoff_modal
)
from agent.constants import OCTICONS_MAP
from handlers.chat_handler import ChatHandler
from commands import setup_commands, build_retry_placeholder_layout
from commands.chat import build_user_chat_modal, execute_chat_turn
from commands.generate import model_catalog
from handlers.stream_handler import (
    build_v2_message_layout,
    apply_message_parsers,
    chunk_timeline,
    cleanup_sibling_messages,
    should_show_reply_button
)
from ui.modals import DynamicModalV2
from ui.thought_container import ThoughtContainerView
from ui.context_views import (
    BranchTranscriptView,
    BranchHeaderView,
    build_branch_settings_modal,
    build_branch_message_edit_modal
)
from ui.artifact_views import build_code_preview_modal, build_artifact_open_modal, prepare_artifact_download_payload
from ui.quiz_views import (
    QuizActiveStepperView,
    QuizScoreSummaryView,
    build_quiz_spoiler_warning_view
)
from ui.onboarding_views import build_welcome_terms_modal, BannedUserNoticeView
from core.moderation import is_user_banned
from google.genai import types

logger = logging.getLogger("PriestyAI.Main")

IDLE_TIMEOUT_SECONDS = 600

class PriestyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.last_activity_time = time.time()
        self.current_status = discord.Status.online
        self.presence_task: asyncio.Task | None = None
        self.poll_watchdog_task: asyncio.Task | None = None
        self.schedule_watchdog_task: asyncio.Task | None = None

    async def setup_hook(self):
        asyncio.create_task(searxng_client.ensure_running())
        asyncio.create_task(playground_server.start())
        asyncio.create_task(smee_service.start())
        asyncio.create_task(screenshot_service.start())
        asyncio.create_task(session_manager.prune_stale_workspaces())
        asyncio.create_task(model_catalog.ensure_initialized())
        asyncio.create_task(github_app_client.initialize_bot_identity())

        smee_service.register_listener(self._on_github_webhook_event)

        setup_commands(self.tree)
        try:
            synced = await self.tree.sync()
            logger.info(f"[CommandTree] Successfully synced {len(synced)} application command(s) & context menus globally!")
        except Exception as e:
            logger.error(f"[CommandTree] Failed to sync application commands: {e}")

    async def _on_github_webhook_event(self, event_type: str, payload: dict[str, Any]):
        try:
            if event_type in ["installation", "installation_repositories"]:
                action = payload.get("action", "")
                if action in ["created", "added"]:
                    repos = payload.get("repositories_added") or payload.get("repositories", [])
                    for repo_info in repos:
                        full_name = repo_info.get("full_name", "")
                        if not full_name:
                            continue
                        parts = full_name.split("/")
                        if len(parts) == 2:
                            owner, repo_name = parts
                            active_sessions = session_manager.get_active_sessions_for_repo(owner, repo_name)
                            for sess in active_sessions:
                                rev_mid = sess.get("review_message_id")
                                th_id = sess.get("thread_id")
                                pr_data = sess.get("github_pr_data", {})
                                if rev_mid and th_id and pr_data:
                                    try:
                                        channel = self.get_channel(int(th_id)) or await self.fetch_channel(int(th_id))
                                        if channel:
                                            msg = await channel.fetch_message(int(rev_mid))
                                            if msg:
                                                new_rev_view = AgentReadyForReviewView(session=sess, pr_data=pr_data, is_installed=True)
                                                await msg.edit(view=new_rev_view)
                                                logger.info(f"[GitHubWebhook] Automatically unlocked Review card #{rev_mid} on thread {th_id}")
                                    except Exception as ex:
                                        logger.debug(f"[GitHubWebhook] Failed to update review message: {ex}")

            elif event_type == "check_run":
                action = payload.get("action", "")
                check_run = payload.get("check_run", {})
                conclusion = check_run.get("conclusion")
                
                if action == "completed" and conclusion in ["failure", "timed_out"]:
                    repo_info = payload.get("repository", {})
                    owner = repo_info.get("owner", {}).get("login", "")
                    repo_name = repo_info.get("name", "")
                    head_sha = check_run.get("head_sha", "")
                    check_name = check_run.get("name", "CI Check")
                    check_run_id = check_run.get("id", "")

                    active_sessions = session_manager.get_active_sessions_for_repo(owner, repo_name)
                    for sess in active_sessions:
                        th_id = sess.get("thread_id")
                        if th_id:
                            channel = self.get_channel(int(th_id)) or await self.fetch_channel(int(th_id))
                            if channel:
                                ci_card = AgentCIFailureView(
                                    session_id=sess["session_id"],
                                    commit_sha=head_sha,
                                    check_name=check_name,
                                    failed_step="",
                                    check_run_id=check_run_id
                                )
                                await channel.send(view=ci_card)

            elif event_type == "pull_request":
                action = payload.get("action", "")
                pr_obj = payload.get("pull_request", {})
                repo_info = payload.get("repository", {})
                owner = repo_info.get("owner", {}).get("login", "")
                repo_name = repo_info.get("name", "")
                pr_num = pr_obj.get("number", 1)
                pr_url = pr_obj.get("html_url", "")
                base_ref = pr_obj.get("base", {}).get("ref", "main")
                head_ref = pr_obj.get("head", {}).get("ref", "")

                active_sessions = session_manager.get_active_sessions_for_repo(owner, repo_name)
                for sess in active_sessions:
                    th_id = sess.get("thread_id")
                    if not th_id:
                        continue
                    channel = self.get_channel(int(th_id)) or await self.fetch_channel(int(th_id))
                    if not channel:
                        continue

                    if action == "closed":
                        is_merged = pr_obj.get("merged", False)
                        if is_merged:
                            merged_card = AgentPRMergedView(
                                session_id=sess["session_id"],
                                pr_number=pr_num,
                                base_branch=base_ref
                            )
                            await channel.send(view=merged_card)
                        else:
                            unmerged_card = AgentPRClosedUnmergedView(
                                pr_number=pr_num,
                                base_branch=base_ref,
                                pr_url=pr_url
                            )
                            await channel.send(view=unmerged_card)

                    elif action in ["synchronize", "reopened"] and pr_obj.get("mergeable") is False:
                        conflict_card = AgentMergeConflictView(
                            pr_number=pr_num,
                            branch_name=head_ref,
                            base_branch=base_ref,
                            pr_url=pr_url
                        )
                        await channel.send(view=conflict_card)

        except Exception as e:
            logger.debug(f"[GitHubWebhook] Dispatch error: {e}")

    async def close(self):
        await smee_service.stop()
        await screenshot_service.stop()
        await playground_server.stop()
        await super().close()

    async def on_ready(self):
        logger.info("=" * 60)
        logger.info(f"PriestyAI logged in as: {self.user} (ID: {self.user.id}) | Connected to {len(self.guilds)} guild(s)")
        logger.info("=" * 60)

        try:
            self.application = await self.application_info()
            owner = getattr(self.application, "owner", None)
            if isinstance(owner, discord.Team):
                logger.info(f"[ApplicationInfo] Team: '{owner.name}' | Members: {len(owner.members)} | Owner ID: {owner.owner_user_id}")
            else:
                logger.info(f"[ApplicationInfo] Individual Owner: {owner}")
        except Exception as e:
            logger.warning(f"[ApplicationInfo] Could not fetch application info: {e}")

        await self.set_bot_presence(discord.Status.online)
        
        if not self.presence_task or self.presence_task.done():
            self.presence_task = asyncio.create_task(self.presence_watchdog_loop())

        if not self.poll_watchdog_task or self.poll_watchdog_task.done():
            self.poll_watchdog_task = asyncio.create_task(self.poll_watchdog_loop())

        if not self.schedule_watchdog_task or self.schedule_watchdog_task.done():
            self.schedule_watchdog_task = asyncio.create_task(self.schedule_watchdog_loop())

    async def set_bot_presence(self, status: discord.Status):
        self.current_status = status
        activity = discord.CustomActivity(name="Listening for @mentions")
        try:
            await self.change_presence(status=status, activity=activity)
            logger.debug(f"[Presence] Updated status to {status.name.upper()}")
        except Exception as e:
            logger.warning(f"Failed to update presence: {e}")

    def record_activity(self):
        self.last_activity_time = time.time()
        if self.current_status == discord.Status.idle:
            asyncio.create_task(self.set_bot_presence(discord.Status.online))

    async def poll_watchdog_loop(self):
        while not self.is_closed():
            try:
                await asyncio.sleep(30)
                await poll_manager.poll_watchdog_tick(self)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Poll watchdog loop error: {e}")

    async def schedule_watchdog_loop(self):
        while not self.is_closed():
            try:
                await asyncio.sleep(30)
                await schedule_manager.schedule_watchdog_tick(self)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Schedule watchdog loop error: {e}")

    async def presence_watchdog_loop(self):
        while not self.is_closed():
            try:
                await asyncio.sleep(30)
                
                if client_manager.is_completely_exhausted():
                    if self.current_status != discord.Status.dnd:
                        logger.warning("[Presence] All API keys exhausted. Setting status to DND.")
                        await self.set_bot_presence(discord.Status.dnd)
                    continue

                now = time.time()
                if (now - self.last_activity_time) >= IDLE_TIMEOUT_SECONDS:
                    if self.current_status == discord.Status.online:
                        logger.info("[Presence] Inactivity threshold reached (10m). Switching to Idle.")
                        await self.set_bot_presence(discord.Status.idle)
                else:
                    if self.current_status != discord.Status.online:
                        await self.set_bot_presence(discord.Status.online)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Presence watchdog loop error: {e}")

    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        self.record_activity()

        if isinstance(message.channel, discord.Thread):
            agent_session = session_manager.get_session_by_thread_id(message.channel.id)
            if agent_session:
                await AgentEngine.handle_thread_message(message, agent_session)
                return

            branch = branch_manager.get_branch_by_thread_id(message.channel.id)
            if branch:
                branch_manager.add_branch_message(
                    thread_id=message.channel.id,
                    role="user",
                    author_name=message.author.display_name,
                    author_id=message.author.id,
                    content=message.clean_content
                )
                auto_reply = bool(branch.get("auto_reply", 1))
                bot_id = self.user.id
                is_mentioned = (f"<@{bot_id}>" in message.content) or (f"<@!{bot_id}>" in message.content) or any(m.id == bot_id for m in message.mentions)
                if auto_reply or is_mentioned:
                    await ChatHandler.handle_message(self, message, force_respond=True)
                return

        is_ai_channel = False
        if message.guild and message.channel:
            is_ai_channel = config_manager.is_ai_channel(message.guild.id, message.channel.id)

        await ChatHandler.handle_message(self, message, force_respond=is_ai_channel)

    async def on_interaction(self, interaction: discord.Interaction):
        self.record_activity()
        
        if interaction.response.is_done():
            return

        custom_id = None
        if hasattr(interaction, "data") and interaction.data:
            if isinstance(interaction.data, dict):
                custom_id = interaction.data.get("custom_id")
            else:
                custom_id = getattr(interaction.data, "custom_id", None)

        if not custom_id:
            return

        async def resolve_attachment_url(filename: str, message_id_val: str | int) -> str | None:
            target_msg = interaction.message
            if not target_msg or not target_msg.attachments:
                mid_str = str(message_id_val).strip() if message_id_val is not None else ""
                if interaction.channel and mid_str.isdigit() and mid_str not in ("0", "temp"):
                    try:
                        target_msg = await interaction.channel.fetch_message(int(mid_str))
                    except Exception:
                        target_msg = None

            if target_msg and target_msg.attachments:
                for att in target_msg.attachments:
                    if att.filename == filename:
                        return att.url
                return target_msg.attachments[0].url
            return None

        if custom_id.startswith("comp_button_") or custom_id.startswith("btn_"):
            if interaction.response.is_done():
                return
            target_msg = interaction.message
            if target_msg:
                gen = branch_manager.get_generation(target_msg.id)
                if gen:
                    versions = gen.get("versions", [])
                    active_v = gen.get("active_version", 1)
                    if 1 <= active_v <= len(versions):
                        v_data = versions[active_v - 1]
                        staged_comps = v_data.get("staged_components", [])
                        staged_mods = {m["modal_id"]: m for m in v_data.get("staged_modals", [])}

                        target_comp = next((c for c in staged_comps if c.get("custom_id") == custom_id), None)
                        if target_comp and target_comp.get("modal_id") in staged_mods:
                            if interaction.response.is_done():
                                return
                            m_spec = staged_mods[target_comp["modal_id"]]

                            async def on_fallback_modal_submit(sub_inter: discord.Interaction, d: dict[str, Any]):
                                await ChatHandler.handle_interaction_event(
                                    self,
                                    sub_inter,
                                    "modal_submit",
                                    {"modal_id": target_comp["modal_id"], "values": d},
                                    modals_map=staged_mods
                                )

                            modal_obj = DynamicModalV2(
                                title=m_spec.get("title", "Form"),
                                custom_id=target_comp["modal_id"],
                                fields_schema=m_spec.get("fields", []),
                                on_submit_callback=on_fallback_modal_submit
                            )
                            try:
                                if not interaction.response.is_done():
                                    await interaction.response.send_modal(modal_obj)
                            except discord.HTTPException as ex:
                                if ex.code == 40060:
                                    return
                                raise
                            return

        if custom_id.startswith("clear_staged_ctx_"):
            chan_id = custom_id.replace("clear_staged_ctx_", "")
            memory_manager.clear_staged_chat_context(chan_id, interaction.user.id)
            await interaction.response.send_message("🗑️ **Queued context cleared for this channel.**", ephemeral=True)
            return

        if custom_id.startswith("chat_reply:") or custom_id == "btn_chat_reply":
            if is_user_banned(interaction.user.id):
                ban_view = BannedUserNoticeView(author=interaction.user)
                await interaction.response.send_message(view=ban_view, ephemeral=True)
                return

            if not config_manager.has_user_agreed(interaction.user.id):
                async def on_agreed(sub_inter: discord.Interaction):
                    await sub_inter.response.send_message("✅ Terms accepted! You can now use the Reply button.", ephemeral=True)

                modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
                await interaction.response.send_modal(modal)
                return

            async def on_reply_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                prompt_text = data.get("prompt", "").strip()
                if not prompt_text:
                    await sub_inter.response.send_message(content="❌ Message cannot be empty.", ephemeral=True)
                    return

                await sub_inter.response.defer(ephemeral=False)

                raw_data = getattr(sub_inter, "data", {})
                resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
                attachment_parts: list[types.Part] = []
                raw_image_bytes: list[bytes] = []

                if resolved_attachments:
                    async with aiohttp.ClientSession() as http_session:
                        for att_id, att_obj in resolved_attachments.items():
                            att_url = att_obj.get("url")
                            att_fname = att_obj.get("filename", "file.bin")
                            content_type = att_obj.get("content_type", "application/octet-stream")
                            if att_url:
                                try:
                                    async with http_session.get(att_url) as resp:
                                        if resp.status == 200:
                                            file_bytes = await resp.read()
                                            part = types.Part.from_bytes(data=file_bytes, mime_type=content_type)
                                            attachment_parts.append(part)
                                            if content_type.startswith("image/"):
                                                raw_image_bytes.append(file_bytes)
                                except Exception as dl_err:
                                    logger.warning(f"Failed to download modal attachment '{att_fname}': {dl_err}")

                raw_participants = data.get("channel_context", [])
                if isinstance(raw_participants, str):
                    raw_participants = [raw_participants] if raw_participants else []
                elif not isinstance(raw_participants, list):
                    raw_participants = []

                await execute_chat_turn(
                    interaction=sub_inter,
                    prompt_text=prompt_text,
                    raw_attachment_parts=attachment_parts,
                    raw_image_bytes=raw_image_bytes,
                    participant_entities=raw_participants,
                    is_ephemeral=False
                )

            modal = build_user_chat_modal(on_submit=on_reply_modal_submit)
            await interaction.response.send_modal(modal)
            return

        if custom_id.startswith("quizopen:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                msg_id = parts[1]
                quiz_id = parts[2]

                quiz_data = branch_manager.get_quiz(quiz_id)
                if not quiz_data:
                    gen = branch_manager.get_generation(msg_id)
                    if gen:
                        for block in gen.get("versions", [{}])[-1].get("timeline_blocks", []):
                            if block.get("type") == "quiz" and block.get("quiz", {}).get("quiz_id") == quiz_id:
                                quiz_data = block["quiz"]
                                break

                if quiz_data and quiz_data.get("questions"):
                    total_q = len(quiz_data["questions"])
                    past_attempt = branch_manager.get_quiz_attempt(quiz_id, interaction.user.id)

                    if past_attempt and past_attempt.get("is_completed", 0) == 1:
                        summary_view = QuizScoreSummaryView(
                            quiz_data=quiz_data,
                            user=interaction.user,
                            score=past_attempt.get("score", 0),
                            total_questions=past_attempt.get("total_questions", total_q),
                            skipped=past_attempt.get("skipped", 0),
                            answers=past_attempt.get("answers", {}),
                            headline=past_attempt.get("headline", "Solid progress! Keep up the good work."),
                            strengths=past_attempt.get("strengths", []),
                            focus_areas=past_attempt.get("focus_areas", []),
                            message_id=msg_id
                        )
                        await interaction.response.send_message(view=summary_view, ephemeral=True)
                        return

                    saved_answers = past_attempt.get("answers", {}) if past_attempt else {}
                    start_idx = past_attempt.get("current_idx", 0) if past_attempt else 0

                    async def on_progress_cb(cur_idx, ans):
                        branch_manager.save_quiz_attempt_progress(
                            quiz_id=quiz_id,
                            user_id=interaction.user.id,
                            current_idx=cur_idx,
                            answers=ans,
                            total_questions=total_q
                        )

                    async def on_finish_save_cb(sc, tot, sk, head, ans, st, foc):
                        branch_manager.finalize_quiz_attempt(
                            quiz_id=quiz_id,
                            user_id=interaction.user.id,
                            score=sc,
                            total_questions=tot,
                            skipped=sk,
                            headline=head,
                            answers=ans,
                            strengths=st,
                            focus_areas=foc
                        )

                    stepper_view = QuizActiveStepperView(
                        quiz_data=quiz_data,
                        user=interaction.user,
                        message_id=msg_id,
                        saved_answers=saved_answers,
                        initial_idx=start_idx,
                        on_progress_callback=on_progress_cb,
                        on_finish_callback=on_finish_save_cb
                    )
                    await interaction.response.send_message(view=stepper_view, ephemeral=True)
                    return

            await interaction.response.send_message(content="❌ Quiz record not found or expired.", ephemeral=True)
            return

        if custom_id.startswith("fup:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                msg_id = parts[1]
                fup_idx = int(parts[2]) if parts[2].isdigit() else 0

                gen = branch_manager.get_generation(msg_id)
                if gen and interaction.message:
                    root_id = gen["message_id"]
                    active_v = gen.get("active_version", 1)
                    versions = gen.get("versions", [])
                    if 1 <= active_v <= len(versions):
                        v_data = versions[active_v - 1]
                        fups = v_data.get("staged_followups", [])
                        if 0 <= fup_idx < len(fups):
                            fup_item = fups[fup_idx]
                            prompt_text = fup_item.get("prompt", "")

                            for f in fups:
                                f["disabled"] = True

                            v_data["staged_followups"] = fups
                            branch_manager.update_version_data(root_id, active_v, v_data)

                            show_reply = should_show_reply_button(
                                bot=self,
                                guild=interaction.guild,
                                channel=interaction.channel,
                                interaction=interaction
                            )

                            mod_map = {m["modal_id"]: m for m in v_data.get("staged_modals", [])}
                            disabled_view = build_v2_message_layout(
                                raw_text=v_data.get("content", "") if not v_data.get("timeline_blocks") else None,
                                timeline_blocks=v_data.get("timeline_blocks"),
                                guild=interaction.guild,
                                staged_components=v_data.get("staged_components", []),
                                staged_artifacts=v_data.get("staged_artifacts", []),
                                staged_followups=fups,
                                modals_map=mod_map,
                                thought_duration=max(1, v_data.get("duration_seconds", 1)),
                                has_thoughts=v_data.get("has_thoughts", True),
                                show_reply_button=show_reply,
                                active_version=active_v,
                                total_versions=len(versions),
                                message_id=root_id,
                                is_live_stream=False
                            )

                            try:
                                await interaction.response.edit_message(view=disabled_view)
                            except Exception:
                                try:
                                    await interaction.response.defer()
                                except Exception:
                                    pass

                            await ChatHandler.handle_followup_turn(
                                bot=self,
                                interaction=interaction,
                                prompt_text=prompt_text
                            )
                            return

            await interaction.response.send_message(content="❌ Follow-up action expired.", ephemeral=True)
            return

        if custom_id.startswith("artprev:"):
            parts = custom_id.split(":")
            if len(parts) >= 4:
                msg_id = parts[1]
                art_id = parts[2]
                target_v = int(parts[3]) if parts[3].isdigit() else 1
                chan_id = interaction.channel_id or 0

                art_db = branch_manager.get_artifact(art_id)
                if art_db:
                    versions = art_db.get("versions", [])
                    if 1 <= target_v <= len(versions):
                        v_entry = versions[target_v - 1]
                        filename = art_db.get("filename", "code.txt")
                        att_url = await resolve_attachment_url(filename, msg_id)

                        async def preview_submit(inter: discord.Interaction, data: dict[str, Any]):
                            msg_content, discord_files = prepare_artifact_download_payload(art_db, target_v)
                            if not inter.response.is_done():
                                await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                        adds = v_entry.get("additions", 0)
                        dels = v_entry.get("deletions", 0)
                        diff_tup = (adds, dels) if (adds > 0 or dels > 0) else None

                        modal = build_code_preview_modal(
                            filename=filename,
                            raw_code=v_entry.get("content", ""),
                            channel_id=chan_id,
                            message_id=msg_id,
                            attachment_url=att_url,
                            on_submit_callback=preview_submit,
                            version=target_v,
                            diff_stats=diff_tup,
                            artifact_id=art_id,
                            user=interaction.user
                        )
                        await interaction.response.send_modal(modal)
                        return

                gen = branch_manager.get_generation(msg_id)
                if gen:
                    active_v = gen.get("active_version", 1)
                    versions = gen.get("versions", [])
                    if 1 <= active_v <= len(versions):
                        v_data = versions[active_v - 1]
                        for art in v_data.get("staged_artifacts", []):
                            if art.get("artifact_id") == art_id:
                                art_versions = art.get("versions", [])
                                filename = art.get("filename", "code.txt")
                                att_url = await resolve_attachment_url(filename, msg_id)

                                target_art_obj = art
                                async def gen_preview_submit(inter: discord.Interaction, data: dict[str, Any]):
                                    msg_content, discord_files = prepare_artifact_download_payload(target_art_obj, target_v)
                                    if not inter.response.is_done():
                                        await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                                if art_versions and 1 <= target_v <= len(art_versions):
                                    v_entry = art_versions[target_v - 1]
                                    adds = v_entry.get("additions", 0)
                                    dels = v_entry.get("deletions", 0)
                                    diff_tup = (adds, dels) if (adds > 0 or dels > 0) else None

                                    modal = build_code_preview_modal(
                                        filename=filename,
                                        raw_code=v_entry.get("content", ""),
                                        channel_id=chan_id,
                                        message_id=msg_id,
                                        attachment_url=att_url,
                                        on_submit_callback=gen_preview_submit,
                                        version=target_v,
                                        diff_stats=diff_tup,
                                        artifact_id=art_id,
                                        user=interaction.user
                                    )
                                    await interaction.response.send_modal(modal)
                                    return

            await interaction.response.send_message(content="❌ File preview record expired.", ephemeral=True)
            return

        if custom_id.startswith("artopen:"):
            parts = custom_id.split(":")
            if len(parts) >= 4:
                msg_id = parts[1]
                art_id = parts[2]
                target_v = int(parts[3]) if parts[3].isdigit() else 1
                chan_id = interaction.channel_id or 0

                target_art = branch_manager.get_artifact(art_id)
                if not target_art:
                    gen = branch_manager.get_generation(msg_id)
                    if gen:
                        active_v = gen.get("active_version", 1)
                        versions = gen.get("versions", [])
                        if 1 <= active_v <= len(versions):
                            v_data = versions[active_v - 1]
                            for art in v_data.get("staged_artifacts", []):
                                if art.get("artifact_id") == art_id:
                                    target_art = art
                                    break

                if target_art:
                    filename = target_art.get("filename", "project.zip")
                    att_url = await resolve_attachment_url(filename, msg_id)

                    async def open_submit(inter: discord.Interaction, data: dict[str, Any]):
                        msg_content, discord_files = prepare_artifact_download_payload(target_art, target_v)
                        if not inter.response.is_done():
                            await inter.response.send_message(content=msg_content, files=discord_files, ephemeral=True)

                    modal = build_artifact_open_modal(
                        artifact=target_art,
                        target_version=target_v,
                        channel_id=chan_id,
                        message_id=msg_id,
                        attachment_url=att_url,
                        on_submit_callback=open_submit,
                        user=interaction.user
                    )
                    await interaction.response.send_modal(modal)
                    return

            await interaction.response.send_message(content="❌ Project record expired.", ephemeral=True)
            return

        if custom_id.startswith("arthist:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                msg_id = parts[1]
                art_id = parts[2]
                selected_v_str = interaction.data.get("values", [None])[0] if interaction.data else None
                if selected_v_str is not None:
                    chosen_v = int(selected_v_str)
                    gen = branch_manager.get_generation(msg_id)
                    if gen and interaction.message:
                        root_id = gen["message_id"]
                        active_v = gen.get("active_version", 1)
                        versions = gen.get("versions", [])
                        if 1 <= active_v <= len(versions):
                            v_data = versions[active_v - 1]
                            v_content = v_data.get("content", "")
                            timeline_blocks = v_data.get("timeline_blocks")
                            staged_comps = v_data.get("staged_components", [])
                            staged_arts = v_data.get("staged_artifacts", [])
                            staged_fups = v_data.get("staged_followups", [])
                            staged_mods = v_data.get("staged_modals", [])
                            dur = max(1, v_data.get("duration_seconds", 1))
                            has_t = v_data.get("has_thoughts", True)
                            mod_map = {m["modal_id"]: m for m in staged_mods}

                            for art in staged_arts:
                                if art.get("artifact_id") == art_id:
                                    art["active_version"] = chosen_v

                            if timeline_blocks:
                                for block in timeline_blocks:
                                    if block.get("type") == "artifact" and block.get("artifact", {}).get("artifact_id") == art_id:
                                        block["artifact"]["active_version"] = chosen_v

                            show_reply = should_show_reply_button(
                                bot=self,
                                guild=interaction.guild,
                                channel=interaction.channel,
                                interaction=interaction
                            )

                            updated_view = build_v2_message_layout(
                                raw_text=v_content if not timeline_blocks else None,
                                timeline_blocks=timeline_blocks,
                                guild=interaction.guild,
                                staged_components=staged_comps,
                                staged_artifacts=staged_arts,
                                staged_followups=staged_fups,
                                modals_map=mod_map,
                                thought_duration=dur,
                                has_thoughts=has_t,
                                show_reply_button=show_reply,
                                active_version=active_v,
                                total_versions=len(versions),
                                message_id=root_id,
                                is_live_stream=False
                            )
                            await interaction.response.edit_message(view=updated_view)
                            return

            await interaction.response.send_message(content="❌ History selector expired.", ephemeral=True)
            return

        if custom_id.startswith("branch_view_"):
            branch_id = custom_id.replace("branch_view_", "")
            transcript_view = BranchTranscriptView(branch_id=branch_id, page=0)
            await interaction.response.send_message(view=transcript_view, ephemeral=True)
            return

        if custom_id.startswith("branch_settings_"):
            branch_id = custom_id.replace("branch_settings_", "")
            branch = branch_manager.get_branch_by_id(branch_id)
            if not branch:
                await interaction.response.send_message(content="❌ Branch record not found.", ephemeral=True)
                return

            is_creator = str(interaction.user.id) == branch.get("creator_id")
            is_collab = str(interaction.user.id) in branch.get("collaborators", [])
            is_mod = interaction.guild and interaction.user.guild_permissions.manage_threads

            if not (is_creator or is_collab or is_mod or not interaction.guild):
                await interaction.response.send_message(content="❌ You lack permission to manage branch settings.", ephemeral=True)
                return

            async def on_settings_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                new_title = data.get("branch_title", "").strip() or branch.get("title", "Branch Discussion")
                new_auto_reply_str = data.get("auto_reply", "1")
                if isinstance(new_auto_reply_str, list) and new_auto_reply_str:
                    new_auto_reply_str = new_auto_reply_str[0]
                new_auto_reply = int(new_auto_reply_str) if str(new_auto_reply_str).isdigit() else 1

                raw_collabs = data.get("collaborators", [])
                if isinstance(raw_collabs, str):
                    raw_collabs = [raw_collabs] if raw_collabs else []
                elif not isinstance(raw_collabs, list):
                    raw_collabs = []

                new_collabs = [str(c.get("id", c) if isinstance(c, dict) else c) for c in raw_collabs if str(c).strip()]
                if str(branch.get("creator_id")) not in new_collabs:
                    new_collabs.append(str(branch.get("creator_id")))

                branch_manager.update_branch_settings(
                    branch_id=branch_id,
                    title=new_title,
                    collaborators=new_collabs,
                    auto_reply=new_auto_reply
                )

                thread_obj = None
                if interaction.guild and branch.get("thread_id"):
                    try:
                        thread_obj = interaction.guild.get_thread(int(branch["thread_id"])) or await interaction.guild.fetch_channel(int(branch["thread_id"]))
                    except Exception:
                        thread_obj = None

                if thread_obj:
                    try:
                        await thread_obj.edit(name=new_title[:60])
                    except Exception as ex:
                        logger.debug(f"Failed to edit thread title: {ex}")

                    for c_id in new_collabs:
                        try:
                            m_obj = interaction.guild.get_member(int(c_id)) or await interaction.guild.fetch_member(int(c_id))
                            if m_obj:
                                await thread_obj.add_user(m_obj)
                        except Exception:
                            pass

                await sub_inter.response.send_message(content="✅ **Branch settings updated successfully.**", ephemeral=True)

            modal = build_branch_settings_modal(branch, on_settings_submit, guild=interaction.guild)
            await interaction.response.send_modal(modal)
            return

        if custom_id.startswith("branch_edit_msg_"):
            parts = custom_id.replace("branch_edit_msg_", "").split("_")
            if len(parts) >= 2:
                branch_id = parts[0]
                msg_idx = int(parts[1])

                branch = branch_manager.get_branch_by_id(branch_id)
                if not branch:
                    await interaction.response.send_message(content="❌ Branch record not found.", ephemeral=True)
                    return

                is_creator = str(interaction.user.id) == branch.get("creator_id")
                is_collab = str(interaction.user.id) in branch.get("collaborators", [])
                is_mod = interaction.guild and interaction.user.guild_permissions.manage_threads

                if not (is_creator or is_collab or is_mod or not interaction.guild):
                    await interaction.response.send_message(content="❌ You lack permission to edit messages in this branch.", ephemeral=True)
                    return

                messages = branch.get("messages", [])
                if not (0 <= msg_idx < len(messages)):
                    await interaction.response.send_message(content="❌ Message record not found.", ephemeral=True)
                    return

                target_msg_data = messages[msg_idx]

                async def on_edit_msg_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                    new_content = data.get("content", "").strip()
                    raw_author = data.get("author_id", target_msg_data.get("author_id", "0"))
                    if isinstance(raw_author, list) and raw_author:
                        raw_author = raw_author[0]

                    new_author_id = str(raw_author.get("id", raw_author) if isinstance(raw_author, dict) else raw_author) if raw_author else "0"
                    new_author_name = target_msg_data.get("author", "User")
                    if sub_inter.guild and new_author_id and new_author_id != "0":
                        try:
                            m_user = sub_inter.guild.get_member(int(new_author_id)) or await sub_inter.guild.fetch_member(int(new_author_id))
                            if m_user:
                                new_author_name = m_user.display_name
                        except Exception:
                            pass

                    raw_data = getattr(sub_inter, "data", {})
                    resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
                    attachment_records = []
                    if resolved_attachments:
                        for att_id, att_obj in resolved_attachments.items():
                            att_url = att_obj.get("url")
                            att_fname = att_obj.get("filename", f"file_{att_id}")
                            if att_url:
                                attachment_records.append({"filename": att_fname, "url": att_url})

                    branch_manager.edit_branch_message(
                        branch_id=branch_id,
                        message_index=msg_idx,
                        new_author_id=new_author_id,
                        new_author_name=new_author_name,
                        new_content=new_content,
                        new_attachments=attachment_records if resolved_attachments else None
                    )

                    updated_transcript = BranchTranscriptView(branch_id=branch_id, page=0)
                    await sub_inter.response.edit_message(view=updated_transcript)

                modal = build_branch_message_edit_modal(
                    branch_id=branch_id,
                    msg_idx=msg_idx,
                    msg_data=target_msg_data,
                    on_submit=on_edit_msg_submit,
                    guild=interaction.guild
                )
                await interaction.response.send_modal(modal)
                return

        if custom_id.startswith("branch_bulk_prune_"):
            branch_id = custom_id.replace("branch_bulk_prune_", "")
            selected_indices = [int(v) for v in interaction.data.get("values", []) if str(v).isdigit()]

            if selected_indices:
                branch = branch_manager.get_branch_by_id(branch_id)
                if branch:
                    is_creator = str(interaction.user.id) == branch.get("creator_id")
                    is_collab = str(interaction.user.id) in branch.get("collaborators", [])
                    is_mod = interaction.guild and interaction.user.guild_permissions.manage_threads

                    if is_creator or is_collab or is_mod or not interaction.guild:
                        branch_manager.bulk_prune_branch_messages(branch_id, selected_indices)
                        updated_transcript = BranchTranscriptView(branch_id=branch_id, page=0)
                        await interaction.response.edit_message(view=updated_transcript)
                        return

            await interaction.response.send_message(content="❌ Failed to prune selected messages.", ephemeral=True)
            return

        if custom_id.startswith("branch_export_"):
            branch_id = custom_id.replace("branch_export_", "")
            branch = branch_manager.get_branch_by_id(branch_id)
            if not branch:
                await interaction.response.send_message(content="❌ Branch record not found.", ephemeral=True)
                return

            messages = branch.get("messages", [])
            lines = [f"# Branch Export: {branch.get('title', 'Discussion')}\n"]
            lines.append(f"Exported at: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
            lines.append(f"Total Messages: {len(messages)}\n\n---\n")

            for m in messages:
                author = m.get("author", "User")
                content = m.get("content", "")
                ts = m.get("timestamp", "")
                lines.append(f"### {author} ({ts})\n{content}\n")

            export_bytes = "\n".join(lines).encode("utf-8")
            file_obj = discord.File(io.BytesIO(export_bytes), filename=f"branch_{branch_id}_export.md")
            await interaction.response.send_message(
                content=f"{OCTICONS_MAP['oct_rocket']} **Branch Export Ready:** Attached is the complete Markdown record.",
                file=file_obj,
                ephemeral=True
            )
            return

        if custom_id.startswith("branch_del_"):
            branch_id = custom_id.replace("branch_del_", "")
            branch = branch_manager.get_branch_by_id(branch_id)
            if not branch:
                await interaction.response.send_message(content="❌ Branch record not found.", ephemeral=True)
                return

            is_creator = str(interaction.user.id) == branch.get("creator_id")
            is_mod = interaction.guild and interaction.user.guild_permissions.manage_threads
            if not (is_creator or is_mod or not interaction.guild):
                await interaction.response.send_message(content="❌ You lack permission to delete this branch.", ephemeral=True)
                return

            branch_manager.delete_branch(branch_id)
            await interaction.response.send_message(content="🗑️ **Branch deleted.** Deleting thread...", ephemeral=True)
            try:
                if isinstance(interaction.channel, discord.Thread):
                    agent_ses = session_manager.get_session_by_thread_id(interaction.channel.id)
                    if agent_ses:
                        asyncio.create_task(session_manager.cleanup_session(agent_ses["session_id"]))
                    await interaction.channel.delete()
                elif branch.get("thread_id"):
                    thread_obj = interaction.guild.get_thread(int(branch["thread_id"])) or await interaction.guild.fetch_channel(int(branch["thread_id"]))
                    if thread_obj:
                        agent_ses = session_manager.get_session_by_thread_id(thread_obj.id)
                        if agent_ses:
                            asyncio.create_task(session_manager.cleanup_session(agent_ses["session_id"]))
                        await thread_obj.delete()
            except Exception as ex:
                logger.warning(f"Failed to delete thread channel: {ex}")
            return

        if custom_id.startswith("branch_prune_"):
            parts = custom_id.replace("branch_prune_", "").split("_")
            if len(parts) >= 2:
                branch_id = parts[0]
                msg_idx = int(parts[1])
                success = branch_manager.prune_branch_message(branch_id, msg_idx)
                if success:
                    updated_view = BranchTranscriptView(branch_id=branch_id, page=0)
                    await interaction.response.edit_message(view=updated_view)
                else:
                    await interaction.response.send_message(content="❌ Failed to prune message.", ephemeral=True)
            return

        if custom_id.startswith("sub_prev:") or custom_id.startswith("sub_next:"):
            is_prev_sub = custom_id.startswith("sub_prev:")
            clean_str = custom_id.replace("sub_prev:", "") if is_prev_sub else custom_id.replace("sub_next:", "")
            parts = clean_str.split(":")

            if len(parts) >= 3:
                msg_id = parts[0]
                v_num = int(parts[1]) if parts[1].isdigit() else 1
                cur_sp = int(parts[2]) if parts[2].isdigit() else 0
                target_sp = (cur_sp - 1) if is_prev_sub else (cur_sp + 1)

                gen_record = branch_manager.get_generation(msg_id)
                if gen_record and interaction.message:
                    root_id = str(gen_record["message_id"])
                    versions = gen_record.get("versions", [])
                    if 1 <= v_num <= len(versions):
                        v_data = versions[v_num - 1]
                        v_content = v_data.get("content", "")
                        raw_timeline = v_data.get("timeline_blocks") or ([{"type": "text", "content": v_content}] if v_content else [])
                        slices = chunk_timeline(raw_timeline, max_chars=3500)
                        num_slices = max(1, len(slices))

                        if 0 <= target_sp < num_slices:
                            slice_blocks = slices[target_sp]
                            dur = max(1, v_data.get("duration_seconds", 1))
                            has_t = v_data.get("has_thoughts", True)
                            staged_comps = v_data.get("staged_components", [])
                            staged_arts = v_data.get("staged_artifacts", [])
                            staged_fups = v_data.get("staged_followups", [])
                            staged_mods = v_data.get("staged_modals", [])
                            mod_map = {m["modal_id"]: m for m in staged_mods}

                            show_reply = should_show_reply_button(
                                bot=self,
                                guild=interaction.guild,
                                channel=interaction.channel,
                                interaction=interaction
                            )

                            is_last_sp = (target_sp == num_slices - 1)
                            updated_view = build_v2_message_layout(
                                timeline_blocks=slice_blocks,
                                guild=interaction.guild,
                                staged_components=staged_comps if is_last_sp else None,
                                staged_artifacts=staged_arts,
                                staged_followups=staged_fups if is_last_sp else None,
                                modals_map=mod_map if is_last_sp else None,
                                thought_duration=dur,
                                has_thoughts=has_t,
                                show_reply_button=show_reply,
                                active_version=v_num,
                                total_versions=len(versions),
                                sub_page=target_sp,
                                total_sub_pages=num_slices,
                                message_id=root_id,
                                is_live_stream=False
                            )
                            await interaction.response.edit_message(view=updated_view)
                            return

            await interaction.response.send_message(content="❌ Page selector expired.", ephemeral=True)
            return

        if custom_id.startswith("gen_prev_") or custom_id.startswith("gen_next_"):
            is_prev = custom_id.startswith("gen_prev_")
            msg_id = custom_id.replace("gen_prev_", "") if is_prev else custom_id.replace("gen_next_", "")

            gen_record = branch_manager.get_generation(msg_id)
            if not gen_record:
                await interaction.response.send_message(content="❌ Generation record expired.", ephemeral=True)
                return

            root_id = str(gen_record["message_id"])
            current_v = gen_record.get("active_version", 1)
            versions = gen_record.get("versions", [])
            total_v = len(versions)
            target_v = (current_v - 1) if is_prev else (current_v + 1)

            if not (1 <= target_v <= total_v):
                await interaction.response.send_message(content="❌ Target version out of range.", ephemeral=True)
                return

            target_version_data = branch_manager.set_active_version(root_id, target_v)
            if target_version_data and interaction.channel:
                curr_v_data = versions[current_v - 1] if 1 <= current_v <= len(versions) else {}
                old_message_ids = [str(x) for x in curr_v_data.get("message_ids", [root_id])]
                if root_id not in old_message_ids:
                    old_message_ids.insert(0, root_id)

                if target_version_data.get("status") == "generating":
                    generating_view = build_retry_placeholder_layout(
                        status_text=f"Generating version {target_v}",
                        target_version=target_v,
                        total_versions=total_v,
                        message_id=root_id
                    )
                    await interaction.response.edit_message(view=generating_view)
                    return

                dur = max(1, target_version_data.get("duration_seconds", 1))
                has_t = target_version_data.get("has_thoughts", True)
                v_content = target_version_data.get("content", "")
                raw_timeline = target_version_data.get("timeline_blocks") or ([{"type": "text", "content": v_content}] if v_content else [])
                staged_comps = target_version_data.get("staged_components", [])
                staged_arts = target_version_data.get("staged_artifacts", [])
                staged_fups = target_version_data.get("staged_followups", [])
                staged_mods = target_version_data.get("staged_modals", [])
                mod_map = {m["modal_id"]: m for m in staged_mods}

                show_reply = should_show_reply_button(
                    bot=self,
                    guild=interaction.guild,
                    channel=interaction.channel,
                    interaction=interaction
                )

                files = []
                img_name = None
                for att in target_version_data.get("attachments", []):
                    b64 = att.get("data_b64", "")
                    if b64:
                        raw = base64.b64decode(b64)
                        fname = att.get("filename", "file.bin")
                        if fname.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                            img_name = fname
                        files.append(discord.File(io.BytesIO(raw), filename=fname))

                target_slices = chunk_timeline(raw_timeline)
                num_slices = max(1, len(target_slices))
                first_slice = target_slices[0]

                v2_view_primary = build_v2_message_layout(
                    timeline_blocks=first_slice,
                    guild=interaction.guild,
                    staged_components=staged_comps if (num_slices == 1) else None,
                    staged_artifacts=staged_arts,
                    staged_followups=staged_fups if (num_slices == 1) else None,
                    modals_map=mod_map if (num_slices == 1) else None,
                    image_filename=img_name,
                    has_image=bool(img_name),
                    thought_duration=dur,
                    has_thoughts=has_t,
                    show_reply_button=show_reply,
                    active_version=target_v,
                    total_versions=total_v,
                    sub_page=0,
                    total_sub_pages=num_slices,
                    message_id=root_id,
                    is_live_stream=False
                )

                try:
                    if files:
                        await interaction.response.edit_message(view=v2_view_primary, attachments=files)
                    else:
                        await interaction.response.edit_message(view=v2_view_primary)
                except Exception as ex:
                    logger.debug(f"Version swap edit error: {ex}")

            return

        if custom_id.startswith("gen_thought_") and not custom_id.startswith("gen_thought_agent_"):
            is_forced = custom_id.startswith("gen_thought_force_")
            clean_id = custom_id.replace("gen_thought_force_", "") if is_forced else custom_id.replace("gen_thought_", "")
            parts = clean_id.split("_")

            if len(parts) >= 2:
                msg_id = parts[0]
                v_idx = int(parts[1]) if parts[1].isdigit() else 1
                gen = branch_manager.get_generation(msg_id)
                if gen:
                    root_id = gen["message_id"]
                    versions = gen.get("versions", [])
                    if 1 <= v_idx <= len(versions):
                        v_data = versions[v_idx - 1]

                        has_quiz_block = False
                        for block in v_data.get("timeline_blocks", []):
                            if block.get("type") == "quiz":
                                has_quiz_block = True
                                break

                        if (has_quiz_block or v_data.get("is_quiz")) and not is_forced:
                            warning_card = build_quiz_spoiler_warning_view(root_id, v_idx)
                            if interaction.response.is_done():
                                await interaction.followup.send(view=warning_card, ephemeral=True)
                            else:
                                await interaction.response.send_message(view=warning_card, ephemeral=True)
                            return

                        raw_thoughts = v_data.get("thoughts", "")
                        formatted_thoughts = v_data.get("formatted_thoughts")
                        model_name = v_data.get("model")
                        dur_sec = max(1, v_data.get("duration_seconds", 1))
                        is_generating = (v_data.get("status") == "generating")

                        files = []
                        for att in v_data.get("attachments", []):
                            b64 = att.get("data_b64", "")
                            if b64:
                                raw = base64.b64decode(b64)
                                fname = att.get("filename", "file.bin")
                                files.append(discord.File(io.BytesIO(raw), filename=fname))

                        container = ThoughtContainerView(
                            raw_thoughts=raw_thoughts,
                            formatted_thoughts=formatted_thoughts,
                            tool_calls=v_data.get("tool_calls", []),
                            duration_seconds=dur_sec,
                            is_thinking=is_generating,
                            show_toggle=not is_generating,
                            message_id=root_id,
                            version_idx=v_idx,
                            model_name=model_name
                        )
                        try:
                            if files:
                                if interaction.response.is_done():
                                    await interaction.followup.send(view=container, files=files, ephemeral=True)
                                else:
                                    await interaction.response.send_message(view=container, files=files, ephemeral=True)
                            else:
                                if interaction.response.is_done():
                                    await interaction.followup.send(view=container, ephemeral=True)
                                else:
                                    await interaction.response.send_message(view=container, ephemeral=True)
                            return
                        except Exception as ex:
                            logger.warning(f"Failed to open thought container: {ex}")
                            return

            if interaction.response.is_done():
                await interaction.followup.send(content="❌ Thoughts unavailable for this version.", ephemeral=True)
            else:
                await interaction.response.send_message(content="❌ Thoughts unavailable for this version.", ephemeral=True)
            return

        if custom_id.startswith("agent_step_view_"):
            parts = custom_id.replace("agent_step_view_", "").split("_")
            if len(parts) >= 2:
                session_id = parts[0]
                step_idx = parts[1]
                
                step_data = session_manager.get_step_log(session_id, step_idx)
                if step_data:
                    inspector = AgentStepInspectorView(step_data)
                    await interaction.response.send_message(view=inspector, ephemeral=True)
                    return

            await interaction.response.send_message(content="❌ Step inspection data expired.", ephemeral=True)
            return

        if custom_id.startswith("gen_thought_agent_"):
            session_id = custom_id.replace("gen_thought_agent_", "")
            t_data = session_manager.get_session_thoughts(session_id)
            if t_data:
                container = ThoughtContainerView(
                    raw_thoughts=t_data.get("thoughts", "") or "Researching workspace and analyzing repository architecture...",
                    tool_calls=t_data.get("tool_calls", []),
                    duration_seconds=max(1, t_data.get("duration_seconds", 1)),
                    is_thinking=False,
                    show_toggle=False,
                    model_name="gemma-4-31b-it"
                )
                await interaction.response.send_message(view=container, ephemeral=True)
                return

            await interaction.response.send_message(content="❌ Reasoning details unavailable for this session.", ephemeral=True)
            return

        if custom_id.startswith("agent_stop_"):
            session_id = custom_id.replace("agent_stop_", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can stop the agent.", ephemeral=True)
                return

            session_manager.trigger_abort(session_id)
            session_manager.update_session(session_id, state="stopped")

            last_completed_mid = session.get("last_completed_message_id")
            if last_completed_mid and interaction.channel:
                try:
                    c_msg = await interaction.channel.fetch_message(int(last_completed_mid))
                    if c_msg:
                        deliverable_art = branch_manager.get_artifact_by_channel_and_file(interaction.channel.id, "report.html")
                        re_enabled_view = AgentFinalDeliverableView(
                            summary_text="",
                            artifact=deliverable_art,
                            session=session,
                            citations=session.get("citations", []),
                            thought_duration=1,
                            guild=interaction.guild,
                            is_new_task_disabled=False
                        )
                        await c_msg.edit(view=re_enabled_view)
                except Exception:
                    pass

            await interaction.response.send_message(content=f"⏹️ **Agent Stopped:** Signal received from {interaction.user.mention}. Stopping after current step. You can chat naturally or start a new task.", ephemeral=False)
            return

        if custom_id.startswith("agent_new_task_"):
            session_id = custom_id.replace("agent_new_task_", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can start a new task in this workspace.", ephemeral=True)
                return

            if session.get("state") == "planning":
                await interaction.response.send_message(content="⚠️ A task planning turn is already in progress.", ephemeral=True)
                return

            origin_msg = interaction.message

            async def on_new_task_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                next_prompt = data.get("prompt", "").strip()
                if not next_prompt:
                    await sub_inter.response.send_message(content="❌ Task prompt cannot be empty.", ephemeral=True)
                    return

                await sub_inter.response.defer(ephemeral=True)

                if origin_msg:
                    try:
                        deliverable_art = branch_manager.get_artifact_by_channel_and_file(interaction.channel_id, "report.html")
                        disabled_view = AgentFinalDeliverableView(
                            summary_text="",
                            artifact=deliverable_art,
                            session=session,
                            citations=session.get("citations", []),
                            thought_duration=1,
                            guild=interaction.guild,
                            is_new_task_disabled=True
                        )
                        await origin_msg.edit(view=disabled_view)
                    except Exception as ex:
                        logger.debug(f"Failed to disable origin new task button: {ex}")

                raw_data = getattr(sub_inter, "data", {})
                resolved_attachments = raw_data.get("resolved", {}).get("attachments", {})
                if resolved_attachments:
                    async with aiohttp.ClientSession() as http_session:
                        for att_id, att_obj in resolved_attachments.items():
                            att_url = att_obj.get("url")
                            att_fname = att_obj.get("filename", f"attachment_{att_id}")
                            if att_url:
                                try:
                                    async with http_session.get(att_url) as resp:
                                        if resp.status == 200:
                                            file_bytes = await resp.read()
                                            dest = os.path.join(session["workspace_path"], att_fname)
                                            with open(dest, "wb") as f_out:
                                                f_out.write(file_bytes)
                                            logger.info(f"[Agent] Saved New Task modal attachment '{att_fname}' to workspace.")
                                except Exception as dl_err:
                                    logger.warning(f"[Agent] Failed to download New Task attachment '{att_fname}': {dl_err}")

                thread = interaction.channel
                if isinstance(thread, discord.Thread):
                    session_manager.clear_abort_event(session_id)
                    session_manager.update_session(session_id, state="planning")
                    await thread.send(content=f"{OCTICONS_MAP['oct_checklist']} **New Task Started by {interaction.user.mention}:**\n> {next_prompt}")
                    asyncio.create_task(AgentEngine.start_planning_turn(thread, session, feedback=next_prompt))
                else:
                    await sub_inter.followup.send(content="❌ Cannot find the agent thread.", ephemeral=True)

            modal = build_agent_new_task_modal(session_id, on_submit=on_new_task_submit)
            await interaction.response.send_modal(modal)
            return

        if custom_id.startswith("agent_signoff:"):
            session_id = custom_id.replace("agent_signoff:", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can sign off on this commit.", ephemeral=True)
                return

            pr_data = session.get("github_pr_data", {})
            prefilled_commit = pr_data.get("commit_message", "feat: implement requested changes")
            is_creator = (str(interaction.user.id) == str(session.get("creator_id")))
            collabs = session.get("collaborators", [])
            total_collabs = max(1, len(collabs))

            u_cfg = config_manager.get_user_config(interaction.user.id)
            prefilled_name = u_cfg.get("git_name", "")
            prefilled_email = u_cfg.get("git_email", "")

            async def on_signoff_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
                c_msg = data.get("commit_message", "").strip() or prefilled_commit
                g_name = data.get("git_name", "").strip() or prefilled_name
                g_email = data.get("git_email", "").strip() or prefilled_email
                force_push_val = data.get("force_push", [])
                
                is_force = False
                if isinstance(force_push_val, list):
                    is_force = "force" in force_push_val
                elif isinstance(force_push_val, str):
                    is_force = (force_push_val == "force")

                is_anonymous = not (g_name and g_email and "@" in g_email)

                session_manager.record_signoff(
                    session_id=session_id,
                    user_id=interaction.user.id,
                    user_name=interaction.user.display_name,
                    git_name=g_name,
                    git_email=g_email,
                    commit_message=c_msg,
                    is_anonymous=is_anonymous
                )

                await sub_inter.response.defer(ephemeral=True)

                thread = interaction.channel
                if isinstance(thread, discord.Thread):
                    step_card = AgentSignOffStepView(
                        user_name=interaction.user.display_name,
                        user_id=interaction.user.id,
                        session_id=session_id,
                        is_anonymous=is_anonymous
                    )
                    await thread.send(view=step_card)

                updated_session = session_manager.get_session_by_id(session_id)
                rev_mid = updated_session.get("review_message_id") if updated_session else None
                is_immediate_publish = (total_collabs == 1) or (is_creator and is_force)

                if rev_mid and isinstance(thread, discord.Thread):
                    try:
                        r_msg = await thread.fetch_message(int(rev_mid))
                        if r_msg:
                            updated_pr_data = updated_session.get("github_pr_data", {})
                            updated_view = AgentReadyForReviewView(
                                session=updated_session,
                                pr_data=updated_pr_data,
                                is_installed=True,
                                is_publishing=is_immediate_publish
                            )
                            await r_msg.edit(view=updated_view)
                    except Exception as ex:
                        logger.debug(f"Failed to edit review card on signoff: {ex}")

                if is_immediate_publish:
                    await sub_inter.followup.send(content="🚀 **Publishing Branch & Creating Pull Request...**", ephemeral=True)
                    asyncio.create_task(self._execute_publish_pr_pipeline(thread, updated_session))
                else:
                    signoffs = updated_session.get("signoffs", {}) if updated_session else {}
                    if len(signoffs) >= total_collabs:
                        await sub_inter.followup.send(content="✅ **All Sign-offs Complete!** Click **Publish Branch** to open the PR.", ephemeral=True)
                    else:
                        await sub_inter.followup.send(content="✅ **Sign-off Recorded!** Waiting for remaining collaborators...", ephemeral=True)

            modal = build_agent_signoff_modal(
                session_id=session_id,
                prefilled_commit_message=prefilled_commit,
                is_creator=is_creator,
                total_collaborators=total_collabs,
                on_submit=on_signoff_modal_submit
            )
            if prefilled_name or prefilled_email:
                for comp in modal.children:
                    if hasattr(comp, "custom_id") and comp.custom_id == "git_name" and prefilled_name:
                        comp.default = prefilled_name
                    elif hasattr(comp, "custom_id") and comp.custom_id == "git_email" and prefilled_email:
                        comp.default = prefilled_email

            await interaction.response.send_modal(modal)
            return

        if custom_id.startswith("agent_signoff_view:"):
            parts = custom_id.split(":")
            if len(parts) >= 3:
                session_id = parts[1]
                target_uid = parts[2]
                session = session_manager.get_session_by_id(session_id)
                if session:
                    signoffs = session.get("signoffs", {})
                    s_data = signoffs.get(target_uid)
                    if s_data:
                        inspector = AgentSignOffInspectorView(s_data)
                        await interaction.response.send_message(view=inspector, ephemeral=True)
                        return

            await interaction.response.send_message(content="❌ Sign-off record expired.", ephemeral=True)
            return

        if custom_id.startswith("agent_publish_pr:"):
            session_id = custom_id.replace("agent_publish_pr:", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can publish this branch.", ephemeral=True)
                return

            await interaction.response.defer(ephemeral=False)
            thread = interaction.channel
            if isinstance(thread, discord.Thread):
                rev_mid = session.get("review_message_id")
                if rev_mid:
                    try:
                        r_msg = await thread.fetch_message(int(rev_mid))
                        if r_msg:
                            updated_pr_data = session.get("github_pr_data", {})
                            publishing_view = AgentReadyForReviewView(
                                session=session,
                                pr_data=updated_pr_data,
                                is_installed=True,
                                is_publishing=True
                            )
                            await r_msg.edit(view=publishing_view)
                    except Exception:
                        pass

                await self._execute_publish_pr_pipeline(thread, session, interaction=interaction)
            else:
                await interaction.followup.send(content="❌ Thread channel unavailable.", ephemeral=True)
            return

        if custom_id.startswith("agent_fix_ci:"):
            parts = custom_id.split(":")
            if len(parts) >= 2:
                session_id = parts[1]
                check_run_id = parts[2] if len(parts) > 2 else ""
                session = session_manager.get_session_by_id(session_id)
                if not session:
                    await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                    return

                perms = getattr(interaction.user, "guild_permissions", None)
                if not session_manager.is_collaborator(session, interaction.user.id, perms):
                    await interaction.response.send_message(content="❌ Only session collaborators can trigger CI fixes.", ephemeral=True)
                    return

                await interaction.response.defer(ephemeral=False)
                thread = interaction.channel
                if isinstance(thread, discord.Thread):
                    repo_url = session.get("repo_url", "")
                    _, owner, repo = normalize_repo_url(repo_url)
                    check_details = await github_app_client.get_check_run_details(owner, repo, check_run_id) if check_run_id else {}
                    
                    ci_output = check_details.get("output", {})
                    summary = ci_output.get("summary") or ci_output.get("text") or check_details.get("name", "CI Checks Failed")

                    fix_prompt = (
                        f"GitHub Actions CI failed on the branch `{session.get('github_pr_data', {}).get('branch_name', '')}`.\n"
                        f"CI Failure Details:\n{summary[:1500]}\n\n"
                        f"Please analyze the failure traceback, patch the affected source files, verify with the test runner in the terminal, and update the PR."
                    )
                    await thread.send(content=f"{OCTICONS_MAP['oct_checklist']} **CI Auto-Fix Triggered by {interaction.user.mention}:**\n> Debugging failed checks...")
                    asyncio.create_task(AgentEngine.start_planning_turn(thread, session, feedback=fix_prompt))
                else:
                    await interaction.followup.send(content="❌ Cannot find the agent thread.", ephemeral=True)
            return

        if custom_id.startswith("agent_close_thread:"):
            session_id = custom_id.replace("agent_close_thread:", "")
            session = session_manager.get_session_by_id(session_id)
            if not session:
                await interaction.response.send_message(content="❌ Agent session not found.", ephemeral=True)
                return

            perms = getattr(interaction.user, "guild_permissions", None)
            if not session_manager.is_collaborator(session, interaction.user.id, perms):
                await interaction.response.send_message(content="❌ Only session collaborators can close this thread.", ephemeral=True)
                return

            await interaction.response.send_message(content="🔒 **Thread Closed:** Work successfully merged into production. Archiving channel...", ephemeral=False)
            asyncio.create_task(session_manager.cleanup_session(session_id, delete_workspace=True))
            
            if isinstance(interaction.channel, discord.Thread):
                try:
                    await asyncio.sleep(2.0)
                    await interaction.channel.edit(archived=True, locked=True)
                except Exception as ex:
                    logger.debug(f"Failed to archive thread: {ex}")
            return

    async def _execute_publish_pr_pipeline(
        self,
        thread: discord.Thread,
        session: dict[str, Any],
        interaction: discord.Interaction | None = None
    ):
        session_id = session["session_id"]
        repo_url = session.get("repo_url", "").strip()
        pr_data = session.get("github_pr_data", {})

        if not repo_url or not pr_data:
            msg_text = "❌ Missing repository or PR configuration."
            if interaction:
                await interaction.followup.send(content=msg_text, ephemeral=True)
            else:
                await thread.send(content=msg_text)
            return

        _, owner, repo = normalize_repo_url(repo_url)
        token, _ = await github_app_client.get_installation_token_for_repo(owner, repo)
        if not token:
            msg_text = f"❌ GitHub App is not installed on **{owner}/{repo}**. Please install it to proceed."
            if interaction:
                await interaction.followup.send(content=msg_text, ephemeral=True)
            else:
                await thread.send(content=msg_text)
            return

        workspace_path = session["workspace_path"]
        branch_name = pr_data.get("branch_name", "priestyai/feature-update")
        commit_msg = pr_data.get("commit_message", "feat: implement requested changes")
        pr_title = pr_data.get("pr_title", "feat: implement requested changes")
        pr_body = pr_data.get("pr_body", "Code changes ready for review.")

        signoffs_dict = session.get("signoffs", {})
        signoffs_list = list(signoffs_dict.values())

        await git_manager.stage_and_commit(workspace_path, commit_msg, signoffs_list, branch_name=branch_name)

        pr_res = await git_manager.publish_branch_and_create_pr(
            workspace_path=workspace_path,
            repo_url=repo_url,
            branch_name=branch_name,
            pr_title=pr_title,
            pr_body=pr_body,
            signoffs=signoffs_list,
            token=token,
            changed_files=pr_data.get("changed_files", [])
        )

        if "error" in pr_res:
            msg_text = f"❌ Failed to publish PR: `{pr_res['error']}`"
            if interaction:
                await interaction.followup.send(content=msg_text, ephemeral=True)
            else:
                await thread.send(content=msg_text)
            return

        pr_number = pr_res.get("pr_number", 1)
        pr_url_val = pr_res.get("html_url", f"https://github.com/{owner}/{repo}/pull/{pr_number}")

        session_manager.update_session(
            session_id,
            pr_url=pr_url_val,
            pr_number=pr_number
        )

        co_author_names = [s["git_name"] for s in signoffs_list if not s.get("is_anonymous") and s.get("git_name")]

        rev_mid = session.get("review_message_id")
        if rev_mid:
            try:
                r_msg = await thread.fetch_message(int(rev_mid))
                if r_msg:
                    published_view = AgentPRPublishedView(
                        pr_number=pr_number,
                        pr_title=pr_title,
                        pr_url=pr_url_val,
                        branch_name=branch_name,
                        co_authors=co_author_names
                    )
                    await r_msg.edit(view=published_view)
            except Exception as ex:
                logger.debug(f"Failed to edit review message to published: {ex}")

        pr_announcement = f"{OCTICONS_MAP['oct_pr']} **Pull Request Opened:** [#{pr_number} {pr_title}]({pr_url_val})"
        if interaction:
            await interaction.followup.send(content=pr_announcement, ephemeral=False)
        else:
            await thread.send(content=pr_announcement)

if __name__ == "__main__":
    bot = PriestyBot()
    bot.run(DISCORD_TOKEN)