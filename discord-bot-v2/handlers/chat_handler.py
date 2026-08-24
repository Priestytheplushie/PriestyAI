import io
import re
import time
import json
import base64
import asyncio
import logging
import mimetypes
from typing import Any

import aiohttp
import discord
from google.genai import types

from config.settings import LOADING_EMOJI
from core.engine import ChatEngine
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.moderation import check_moderation, generate_friendly_refusal, log_moderation_violation
from handlers.stream_handler import DiscordStreamDispatcher, apply_message_parsers
from tools.registry import ToolExecutionContext
from ui.thought_container import PlaceholderLayoutView
from ui.onboarding_views import WelcomeOnboardingCardView

logger = logging.getLogger("PriestyAI.ChatHandler")

MAX_INLINE_FILE_SIZE = 20 * 1024 * 1024

async def extract_message_attachments_raw(message: discord.Message) -> tuple[list[types.Part], list[bytes]]:
    parts: list[types.Part] = []
    raw_image_bytes: list[bytes] = []

    target_attachments = list(message.attachments)
    if not target_attachments and message.reference and message.reference.resolved:
        if isinstance(message.reference.resolved, discord.Message):
            target_attachments = list(message.reference.resolved.attachments)

    if not target_attachments:
        return parts, raw_image_bytes

    async with aiohttp.ClientSession() as session:
        for attachment in target_attachments:
            if attachment.size > MAX_INLINE_FILE_SIZE:
                continue

            mime_type = attachment.content_type
            if not mime_type or mime_type == "application/octet-stream":
                mime_type, _ = mimetypes.guess_type(attachment.filename)
                mime_type = mime_type or "application/octet-stream"

            mime_type = mime_type.split(";")[0].strip()

            try:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        raw_data = await resp.read()
                        part = types.Part.from_bytes(data=raw_data, mime_type=mime_type)
                        parts.append(part)
                        if mime_type.startswith("image/"):
                            raw_image_bytes.append(raw_data)
            except Exception as e:
                logger.warning(f"Failed to download attachment {attachment.filename}: {e}")

    return parts, raw_image_bytes

def get_tool_subtext(tool_name: str, args: dict[str, Any]) -> str | None:
    if tool_name in ["create_artifact", "update_artifact"]:
        t = args.get("title") or args.get("filename", "Artifact")
        return f"-# 📦 Packaging artifact: **{t}**..."
    elif tool_name == "execute_code":
        lang = args.get("language", "Python").capitalize()
        pkgs = args.get("packages", "")
        pkg_str = f" ({pkgs})" if pkg_str else ""
        return f"-# 💻 Running {lang} sandbox{pkg_str}..."
    elif tool_name == "search_web":
        q = args.get("query", "")[:35]
        return f'-# 🔍 Searching: "{q}"...'
    elif tool_name == "read_link":
        url = args.get("url", "")
        domain = url.split("//")[-1].split("/")[0] if "//" in url else url[:30]
        return f"-# 📄 Reading link from `{domain}`..."
    elif tool_name == "fetch_image":
        q = args.get("query", "")[:30]
        return f"-# 🖼️ Fetching image for '{q}'..."
    elif tool_name == "fetch_github":
        r = args.get("repo_url", "")[:30]
        return f"-# 🐙 Inspecting GitHub repo `{r}`..."
    elif tool_name == "create_poll":
        return "-# 📊 Creating Discord poll..."
    elif tool_name == "calc":
        return "-# 🔢 Computing math..."
    elif tool_name == "generate_image":
        return "-# 🎨 Rendering artwork..."
    elif tool_name == "ask_expert":
        return "-# 🧠 Consulting deep reasoning expert..."
    elif tool_name == "add_component":
        c_type = str(args.get("component_type", "component")).lower().replace(" ", "_")
        type_names = {
            "button": "Button", "string_select": "Dropdown", "user_select": "User Select",
            "role_select": "Role Select", "channel_select": "Channel Select", "mentionable_select": "Mentionable Select"
        }
        return f"-# 🔘 Staging {type_names.get(c_type, 'Component')}..."
    return None

def format_placeholder_content(witty_text: str, subtext: str | None = None) -> str:
    content = f"{LOADING_EMOJI} *{witty_text}...*"
    if subtext:
        content += f"\n{subtext}"
    return content

async def update_placeholder_loop(
    get_message_func,
    placeholder_view: PlaceholderLayoutView,
    statuses: list[str],
    get_active_subtext_func,
    start_time: float,
    stop_event: asyncio.Event,
    interval: float = 1.0
):
    idx = 0
    last_status_change = time.time()
    current_status = statuses[0] if statuses else "Thinking"

    while not stop_event.is_set():
        try:
            await asyncio.sleep(interval)
            if stop_event.is_set():
                break

            now = time.time()
            if (now - last_status_change) >= 4.0 and statuses:
                idx = (idx + 1) % len(statuses)
                current_status = statuses[idx]
                last_status_change = now

            elapsed = int(now - start_time)
            subtext = get_active_subtext_func()
            full_text = format_placeholder_content(current_status, subtext)

            placeholder_view.update_state(full_text, elapsed)
            msg = get_message_func()
            if msg:
                await msg.edit(view=placeholder_view)
            await placeholder_view.push_live_update()
        except discord.HTTPException:
            break
        except Exception:
            break

class ChatHandler:
    @staticmethod
    def extract_server_emojis(guild: discord.Guild | None) -> str:
        if not guild or not guild.emojis:
            return ""

        available_emojis = [e for e in guild.emojis if getattr(e, "available", True)][:40]
        if not available_emojis:
            return ""

        lines = ["<server_emojis>"]
        for e in available_emojis:
            formatted = f"<a:{e.name}:{e.id}>" if e.animated else f"<:{e.name}:{e.id}>"
            lines.append(f'  <emoji name="{e.name}" id="{e.id}" tag="{formatted}"/>')
        lines.append("</server_emojis>")
        return "\n".join(lines)

    @staticmethod
    async def build_context_xml(channel: discord.abc.Messageable, current_user_id: int, guild: discord.Guild | None = None, author: discord.Member | discord.User | None = None, limit: int = 8) -> str:
        envelope = ['<context>']

        if guild:
            ch_name = getattr(channel, "name", "unknown")
            ch_topic = getattr(channel, "topic", None) or "No topic set"
            envelope.append(
                f'  <server_info name="{guild.name}" id="{guild.id}" members="{guild.member_count}">\n'
                f'    <current_channel name="#{ch_name}" id="{getattr(channel, "id", 0)}">\n'
                f'      <topic>{ch_topic}</topic>\n'
                f'    </current_channel>\n'
                f'  </server_info>'
            )

        emojis_xml = ChatHandler.extract_server_emojis(guild)
        if emojis_xml:
            envelope.append(f"  {emojis_xml}")

        try:
            raw_history = [msg async for msg in channel.history(limit=limit)]
            raw_history.reverse()

            envelope.append("  <chat_history>")
            for msg in raw_history:
                if msg.author.bot and (LOADING_EMOJI in msg.content):
                    continue

                is_invoking = "true" if msg.author.id == current_user_id else "false"
                display_name = getattr(msg.author, "display_name", msg.author.name)
                safe_content = msg.clean_content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                envelope.append(
                    f'    <message id="{msg.id}" user_id="{msg.author.id}" '
                    f'username="{msg.author.name}" display_name="{display_name}" '
                    f'is_invoking_user="{is_invoking}" timestamp="{msg.created_at.isoformat()}">\n'
                    f'      {safe_content}\n'
                    f'    </message>'
                )
            envelope.append("  </chat_history>")
        except Exception as e:
            logger.warning(f"Failed to fetch channel history: {e}")

        envelope.append('</context>')
        return "\n".join(envelope)

    @classmethod
    async def handle_message(cls, bot: discord.Client, message: discord.Message, force_respond: bool = False):
        if message.author.bot:
            return

        bot_id = bot.user.id
        is_dm = isinstance(message.channel, discord.DMChannel)

        is_mentioned_in_list = any(m.id == bot_id for m in message.mentions)
        is_mentioned_in_text = (f"<@{bot_id}>" in message.content) or (f"<@!{bot_id}>" in message.content)

        is_role_mentioned = False
        bot_member = message.guild.me if message.guild else None
        if bot_member and message.role_mentions:
            bot_role_ids = {r.id for r in bot_member.roles}
            mentioned_role_ids = {r.id for r in message.role_mentions}
            if bot_role_ids.intersection(mentioned_role_ids):
                is_role_mentioned = True

        is_mentioned = is_mentioned_in_list or is_mentioned_in_text or is_role_mentioned

        is_reply_to_bot = False
        if message.reference:
            if message.reference.resolved and isinstance(message.reference.resolved, discord.Message):
                is_reply_to_bot = (message.reference.resolved.author.id == bot_id)
            elif message.reference.message_id:
                try:
                    ref_msg = await message.channel.fetch_message(message.reference.message_id)
                    is_reply_to_bot = (ref_msg.author.id == bot_id)
                except Exception:
                    pass

        if not (force_respond or is_dm or is_mentioned or is_reply_to_bot):
            return

        if not config_manager.has_user_agreed(message.author.id):
            async def on_user_agreed_in_card(interaction: discord.Interaction, card_msg: discord.Message):
                await interaction.response.defer()
                try:
                    await card_msg.delete()
                except Exception as ex:
                    logger.debug(f"Failed to delete welcome card: {ex}")
                await cls.handle_message(bot, message, force_respond=True)

            welcome_view = WelcomeOnboardingCardView(
                author=message.author,
                on_accepted_callback=on_user_agreed_in_card
            )
            welcome_msg = await message.reply(view=welcome_view, mention_author=False)
            welcome_view.message = welcome_msg
            welcome_view.start_cleanup_timer()
            return

        clean_prompt = re.sub(rf'<@!?{bot_id}>', '', message.content)
        if bot_member:
            for r in bot_member.roles:
                clean_prompt = clean_prompt.replace(f"<@&{r.id}>", "")
        clean_prompt = clean_prompt.replace(f"@{bot.user.name}", "").strip()

        attachment_parts, raw_image_bytes = await extract_message_attachments_raw(message)
        if not clean_prompt:
            clean_prompt = "Please analyze the attached content." if attachment_parts else "Hello!"

        is_flagged, flagged_cats, score = await check_moderation(clean_prompt, raw_image_bytes)
        if is_flagged:
            log_moderation_violation(message.author.id, message.guild.id if message.guild else None, flagged_cats, score)
            friendly_refusal = await generate_friendly_refusal(flagged_cats)
            await message.reply(content=friendly_refusal, mention_author=False)
            return

        logger.info(f"Incoming message from {message.author} ({message.author.id}): {clean_prompt[:60]}")

        multimodal_prompt: list[Any] = []
        if attachment_parts:
            multimodal_prompt.extend(attachment_parts)
        multimodal_prompt.append(clean_prompt)

        tool_context = ToolExecutionContext(channel=message.channel, guild=message.guild, author=message.author, bot=bot)
        context_xml = await cls.build_context_xml(channel=message.channel, current_user_id=message.author.id, guild=message.guild, author=message.author)

        response_msg: discord.Message | None = None
        stream_dispatcher = DiscordStreamDispatcher(origin_message=message, guild=message.guild)

        accumulated_thought_buffer: list[str] = []
        tool_call_history: list[dict[str, Any]] = []
        active_tool_start_times: dict[str, float] = {}

        thinking_start_time: float = time.time()
        answer_now_event = asyncio.Event()
        stop_loop = asyncio.Event()

        active_witty_statuses = ["Thinking", "Consulting neural cores", "Formulating response"]
        active_tool_subtext: str | None = None
        placeholder_view: PlaceholderLayoutView | None = None
        placeholder_task: asyncio.Task | None = None
        first_content_received = False

        def get_current_msg():
            return response_msg

        def get_active_subtext():
            return active_tool_subtext

        async def on_answer_now_clicked(inter: discord.Interaction):
            logger.info(f"[Answer Now Triggered] User {inter.user} requested instant response.")
            answer_now_event.set()
            stop_loop.set()
            if placeholder_task:
                placeholder_task.cancel()

        async def ensure_placeholder_spawned():
            nonlocal placeholder_view, placeholder_task, response_msg
            if placeholder_view is not None or first_content_received:
                return

            initial_text = format_placeholder_content(active_witty_statuses[0], active_tool_subtext)
            placeholder_view = PlaceholderLayoutView(
                loading_text=initial_text,
                duration_seconds=max(0, int(time.time() - thinking_start_time)),
                is_enabled=bool(accumulated_thought_buffer or [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]),
                on_answer_now_callback=on_answer_now_clicked,
                thought_data={"thoughts": "".join(accumulated_thought_buffer), "tool_calls": tool_call_history}
            )

            try:
                response_msg = await message.reply(view=placeholder_view, mention_author=False)
                stream_dispatcher.bind_response_message(response_msg)
                placeholder_task = asyncio.create_task(
                    update_placeholder_loop(
                        get_current_msg, placeholder_view, active_witty_statuses, get_active_subtext, thinking_start_time, stop_loop
                    )
                )
            except Exception as ex:
                logger.warning(f"Failed to spawn placeholder LayoutView: {ex}")

        try:
            async with message.channel.typing():
                async for event_type, payload in ChatEngine.stream_chat(
                    prompt=multimodal_prompt,
                    context_xml=context_xml,
                    bot_user_id=bot.user.id,
                    tool_context=tool_context,
                    answer_now_event=answer_now_event
                ):
                    if event_type == "ROUTED":
                        if payload.witty_statuses:
                            active_witty_statuses = payload.witty_statuses
                        if payload.thinking_level in ["HIGH", "MEDIUM", "LOW"]:
                            await ensure_placeholder_spawned()

                    elif event_type == "RECALLED_MEMORIES":
                        count = payload.get("count", 0)
                        tool_call_history.insert(0, {
                            "name": "recall_memories",
                            "args": {"count": count},
                            "result": payload,
                            "duration_ms": 0,
                            "order": -1.0
                        })
                        if placeholder_view:
                            placeholder_view.thought_data["tool_calls"] = tool_call_history
                            await placeholder_view.push_live_update()

                    elif event_type == "THOUGHT":
                        await ensure_placeholder_spawned()
                        accumulated_thought_buffer.append(payload)
                        if placeholder_view:
                            placeholder_view.enable_thinking()
                            placeholder_view.thought_data["thoughts"] = "".join(accumulated_thought_buffer)
                            await placeholder_view.push_live_update()

                    elif event_type == "TOOL_START":
                        await ensure_placeholder_spawned()
                        tool_name = payload.get("name", "Tool")
                        args = payload.get("args", {})
                        active_tool_start_times[tool_name] = time.perf_counter()
                        active_tool_subtext = get_tool_subtext(tool_name, args)
                        if placeholder_view:
                            placeholder_view.enable_thinking()

                        if tool_name in ["create_artifact", "update_artifact"]:
                            stream_dispatcher.add_artifact_placeholder(tool_name, args)

                    elif event_type == "TOOL_END":
                        await ensure_placeholder_spawned()
                        tool_name = payload.get("name", "Tool")
                        start_t = active_tool_start_times.pop(tool_name, time.perf_counter())
                        duration_ms = int((time.perf_counter() - start_t) * 1000)
                        tool_call_history.append({
                            "name": tool_name,
                            "args": payload.get("args", {}),
                            "result": payload.get("result", {}),
                            "duration_ms": duration_ms
                        })
                        active_tool_subtext = None

                        if tool_name in ["create_artifact", "update_artifact"] and tool_context.staged_artifacts:
                            last_art = tool_context.staged_artifacts[-1]
                            stream_dispatcher.update_artifact_ready(last_art)
                            art_bytes = last_art.get("data_bytes", b"")
                            art_fname = last_art.get("filename", "artifact.zip")
                            if art_bytes:
                                stream_dispatcher.add_raw_attachment(art_fname, art_bytes)

                        elif tool_name in ["generate_image", "fetch_image", "execute_code"] and tool_context.staged_image_bytes:
                            img_fname = tool_context.staged_image_filename
                            img_bytes = tool_context.staged_image_bytes
                            stream_dispatcher.add_media_block(img_fname, img_bytes)
                            tool_context.staged_image_bytes = None

                        if placeholder_view:
                            placeholder_view.enable_thinking()
                            placeholder_view.thought_data["tool_calls"] = tool_call_history
                            await placeholder_view.push_live_update()

                    elif event_type == "CASCADE_RESET":
                        active_tool_start_times.clear()
                        active_tool_subtext = None
                        if placeholder_view:
                            placeholder_view.thought_data["thoughts"] = "".join(accumulated_thought_buffer)
                            placeholder_view.thought_data["tool_calls"] = tool_call_history
                            await placeholder_view.push_live_update()

                    elif event_type == "CONTENT":
                        if not first_content_received:
                            first_content_received = True
                            stop_loop.set()
                            if placeholder_task:
                                placeholder_task.cancel()

                            if response_msg:
                                stream_dispatcher.bind_response_message(response_msg)

                        await stream_dispatcher.append_text(payload)

                    elif event_type == "ERROR":
                        stop_loop.set()
                        if placeholder_task:
                            placeholder_task.cancel()
                        await stream_dispatcher.append_text(f"\n\n⚠️ {payload}")

            stop_loop.set()
            if placeholder_task:
                placeholder_task.cancel()

            final_duration = max(1, int(time.time() - thinking_start_time))
            active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
            has_reasoning = bool(accumulated_thought_buffer or active_tools)

            modals_map = {m["modal_id"]: m for m in tool_context.staged_modals}

            async def handle_interaction_event(inter: discord.Interaction, ev_type: str, data: Any):
                if not inter.response.is_done():
                    await inter.response.defer(ephemeral=False)

                data_str = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
                interaction_prompt = f'<interaction_event type="{ev_type}" user="{inter.user.name}">\n  {data_str}\n</interaction_event>'
                sub_dispatcher = DiscordStreamDispatcher(origin_message=inter.message, guild=inter.guild)
                sub_tool_ctx = ToolExecutionContext(channel=inter.channel, guild=inter.guild, author=inter.user, bot=bot)

                async for sub_type, sub_payload in ChatEngine.stream_chat(
                    prompt=interaction_prompt,
                    context_xml=await cls.build_context_xml(inter.channel, inter.user.id, inter.guild, inter.user),
                    bot_user_id=bot.user.id,
                    tool_context=sub_tool_ctx
                ):
                    if sub_type == "CONTENT":
                        await sub_dispatcher.append_text(sub_payload)

                await sub_dispatcher.finalize()

            stored_attachments: list[dict[str, Any]] = []

            for raw_att in stream_dispatcher.raw_attachment_buffers:
                b64 = base64.b64encode(raw_att["bytes"]).decode("utf-8")
                stored_attachments.append({"filename": raw_att["filename"], "data_b64": b64})

            sanitized_artifacts: list[dict[str, Any]] = []
            for art in tool_context.staged_artifacts:
                art_bytes = art.get("data_bytes", b"")
                art_fname = art.get("filename", "artifact.zip")
                b64_art = base64.b64encode(art_bytes).decode("utf-8") if art_bytes else ""
                clean_art = {k: v for k, v in art.items() if k != "data_bytes"}
                clean_art["data_b64"] = b64_art
                sanitized_artifacts.append(clean_art)

            sent_msg = stream_dispatcher.primary_message
            target_id = sent_msg.id if sent_msg else "temp"

            await stream_dispatcher.finalize(
                staged_artifacts=tool_context.staged_artifacts,
                staged_components=tool_context.staged_components,
                modals_map=modals_map,
                interaction_dispatcher=handle_interaction_event,
                thought_duration=final_duration,
                has_thoughts=has_reasoning,
                active_version=1,
                total_versions=1,
                message_id=target_id
            )

            sent_msg = stream_dispatcher.primary_message
            if sent_msg:
                final_text = stream_dispatcher.get_accumulated_text()
                parsed_initial_content = apply_message_parsers(final_text, message.guild)

                sanitized_timeline: list[dict[str, Any]] = []
                for b in stream_dispatcher.timeline:
                    b_copy = dict(b)
                    if b_copy.get("type") == "artifact" and "artifact" in b_copy:
                        art_copy = dict(b_copy["artifact"])
                        art_copy.pop("data_bytes", None)
                        b_copy["artifact"] = art_copy
                    sanitized_timeline.append(b_copy)

                initial_v_data = {
                    "version_idx": 1,
                    "content": parsed_initial_content,
                    "timeline_blocks": sanitized_timeline,
                    "duration_seconds": final_duration,
                    "has_thoughts": has_reasoning,
                    "thoughts": "".join(accumulated_thought_buffer),
                    "tool_calls": tool_call_history,
                    "attachments": stored_attachments,
                    "staged_components": tool_context.staged_components,
                    "staged_artifacts": sanitized_artifacts,
                    "staged_modals": tool_context.staged_modals,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
                branch_manager.save_generation(
                    message_id=sent_msg.id,
                    channel_id=message.channel.id,
                    guild_id=message.guild.id if message.guild else None,
                    author_id=message.author.id,
                    prompt_text=clean_prompt,
                    attachments=[],
                    context_xml=context_xml,
                    initial_version_data=initial_v_data
                )

        except Exception as e:
            logger.exception(f"Unhandled exception in chat loop: {e}")
        finally:
            stop_loop.set()
            if placeholder_task:
                placeholder_task.cancel()