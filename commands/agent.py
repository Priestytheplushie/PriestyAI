import os
import uuid
import logging
import asyncio
import aiohttp
from typing import Any
import discord
from discord import app_commands
from agent.views import build_agent_create_modal
from agent.session_manager import session_manager
from agent.engine import AgentEngine
from agent.constants import OCTICONS_MAP
from core.moderation import check_moderation, is_user_banned
from core.config_manager import config_manager
from ui.onboarding_views import build_welcome_terms_modal, BannedUserNoticeView

logger = logging.getLogger("PriestyAI.Commands.Agent")

def setup_agent_commands(tree: app_commands.CommandTree):

    @tree.command(name="agent", description="Start an autonomous agent session in a private workspace thread")
    @app_commands.allowed_installs(guilds=True, users=False)
    @app_commands.allowed_contexts(guilds=True, dms=False, private_channels=False)
    async def agent_command(interaction: discord.Interaction):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                content="❌ Agent sessions can only be created inside standard server text channels.",
                ephemeral=True
            )
            return

        bot_member = interaction.guild.me
        channel_perms = interaction.channel.permissions_for(bot_member)
        
        if not (channel_perms.create_private_threads and channel_perms.send_messages_in_threads and channel_perms.manage_threads):
            await interaction.response.send_message(
                content=(
                    "❌ I lack the required permissions to manage private threads in this channel. "
                    "Please grant me `Create Private Threads`, `Send Messages in Threads`, and `Manage Threads` permissions."
                ),
                ephemeral=True
            )
            return

        async def on_modal_submit(sub_inter: discord.Interaction, data: dict[str, Any]):
            prompt = data.get("prompt", "").strip()
            collaborator_ids = data.get("collaborators", [])
            repo_url = data.get("repo_url", "").strip()

            is_flagged, is_zt, flagged_cats, _ = await check_moderation(prompt)
            if is_flagged:
                await sub_inter.response.send_message(
                    content="❌ This request conflicts with automated safety guardrails.",
                    ephemeral=True
                )
                return

            await sub_inter.response.defer(ephemeral=True)

            thread_title, witty_statuses, task_type = await AgentEngine.bootstrap_thread_meta(prompt)
            is_coding = bool(repo_url or task_type in ["coding", "hybrid"])

            try:
                thread = await interaction.channel.create_thread(
                    name=thread_title,
                    type=discord.ChannelType.private_thread,
                    auto_archive_duration=1440
                )
            except Exception as e:
                await sub_inter.followup.send(content=f"❌ Failed to create private thread: {e}", ephemeral=True)
                return

            if str(interaction.user.id) not in collaborator_ids:
                collaborator_ids.append(str(interaction.user.id))

            for uid in collaborator_ids:
                try:
                    u_obj = interaction.guild.get_member(int(uid)) or await interaction.guild.fetch_member(int(uid))
                    if u_obj:
                        await thread.add_user(u_obj)
                except Exception as ex:
                    logger.debug(f"Failed to add collaborator {uid} to thread: {ex}")

            session_id = str(uuid.uuid4())[:8]
            session = session_manager.create_session(
                session_id=session_id,
                thread_id=thread.id,
                channel_id=interaction.channel_id,
                guild_id=interaction.guild_id,
                creator_id=interaction.user.id,
                collaborators=collaborator_ids,
                repo_url=repo_url,
                initial_prompt=prompt,
                witty_statuses=witty_statuses,
                is_coding_task=is_coding,
                task_type=task_type,
                thread_title=thread_title
            )

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
                                        logger.info(f"[Agent] Successfully saved modal attachment '{att_fname}' to workspace.")
                            except Exception as dl_err:
                                logger.warning(f"[Agent] Failed to download modal attachment '{att_fname}': {dl_err}")

            await sub_inter.followup.send(
                content=f"{OCTICONS_MAP['oct_branch']} **Agent Session Launched:** Joined private thread <#{thread.id}>.",
                ephemeral=True
            )
            asyncio.create_task(AgentEngine.start_planning_turn(thread, session))

        if not config_manager.has_user_agreed(interaction.user.id):
            async def on_agreed(sub_inter: discord.Interaction):
                agent_modal = build_agent_create_modal(default_user_id=sub_inter.user.id, on_submit=on_modal_submit)
                await sub_inter.response.send_modal(agent_modal)

            welcome_modal = build_welcome_terms_modal(on_agree_callback=on_agreed)
            await interaction.response.send_modal(welcome_modal)
            return

        modal = build_agent_create_modal(default_user_id=interaction.user.id, on_submit=on_modal_submit)
        await interaction.response.send_modal(modal)