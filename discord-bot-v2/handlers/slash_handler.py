import io
import time
import uuid
import logging
from typing import Any
import discord
from discord import app_commands, ui
from config.settings import LOADING_EMOJI
from core.engine import ChatEngine
from core.memory_manager import memory_manager
from handlers.stream_handler import DiscordStreamDispatcher, merge_views
from tools.registry import ToolExecutionContext
from ui.thought_container import ThinkingButtonView

logger = logging.getLogger("PriestyAI.SlashHandler")

def get_tool_subtext(tool_name: str, args: dict[str, Any]) -> str | None:
    if tool_name == "execute_code":
        lang = args.get("language", "Python").capitalize()
        pkgs = args.get("packages", "")
        pkg_str = f" ({pkgs})" if pkgs else ""
        return f"-# 💻 Running {lang} sandbox{pkg_str}..."
    elif tool_name == "search_web":
        q = args.get("query", "")[:35]
        return f'-# 🔍 Searching: "{q}"...'
    elif tool_name == "read_link":
        url = args.get("url", "")
        domain = url.split("//")[-1].split("/")[0] if "//" in url else url[:30]
        return f"-# 📄 Reading article from `{domain}`..."
    elif tool_name == "generate_image":
        return "-# 🎨 Rendering artwork via Image Studio..."
    elif tool_name == "ask_expert":
        return "-# 🧠 Consulting deep reasoning expert..."
    return None

def format_placeholder_content(witty_text: str, subtext: str | None = None) -> str:
    content = f"{LOADING_EMOJI} *{witty_text}...*"
    if subtext:
        content += f"\n{subtext}"
    return content


class ChatReplyModal(ui.Modal):
    def __init__(
        self,
        title: str,
        session_id: str,
        on_submit_callback: Any
    ):
        super().__init__(title=title[:45], custom_id=f"chat_modal_{session_id}")
        self.session_id = session_id
        self.on_submit_callback = on_submit_callback

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "custom_id": self.custom_id,
            "components": [
                {
                    "type": 18,
                    "label": "Your Message",
                    "description": "Type your question or message",
                    "component": {
                        "type": 4,
                        "custom_id": "message_text",
                        "style": 2,
                        "placeholder": "What would you like to discuss or build?",
                        "required": True,
                        "max_length": 4000
                    }
                },
                {
                    "type": 18,
                    "label": "Upload Files",
                    "description": "Attach up to 10 files (code, logs, documents, images)",
                    "component": {
                        "type": 19,
                        "custom_id": "uploaded_files",
                        "min_values": 0,
                        "max_values": 10,
                        "required": False
                    }
                }
            ]
        }

    async def on_submit(self, interaction: discord.Interaction):
        text_val = ""
        uploaded_files = []

        raw_components = getattr(interaction, "data", {}).get("components", [])
        for comp in raw_components:
            if comp.get("type") == 18 and "component" in comp:
                inner = comp["component"]
                cid = inner.get("custom_id")
                if cid == "message_text":
                    text_val = inner.get("value", "")
                elif cid == "uploaded_files":
                    uploaded_files = inner.get("values", [])

        await self.on_submit_callback(interaction, self.session_id, text_val, uploaded_files)


class ChatReplyView(ui.View):
    def __init__(self, session_id: str, bot_user_id: int):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.bot_user_id = bot_user_id

        self.reply_button = ui.Button(
            label="Reply",
            emoji="💬",
            style=discord.ButtonStyle.primary,
            custom_id=f"chat_reply_btn_{session_id}"
        )
        self.reply_button.callback = self._on_reply_clicked
        self.add_item(self.reply_button)

    async def _on_reply_clicked(self, interaction: discord.Interaction):
        modal = ChatReplyModal(
            title="Reply to PriestyAI",
            session_id=self.session_id,
            on_submit_callback=handle_chat_modal_submit
        )
        await interaction.response.send_modal(modal)


async def handle_chat_modal_submit(
    interaction: discord.Interaction,
    session_id: str,
    user_text: str,
    uploaded_files: list[Any]
):
    await interaction.response.defer(ephemeral=False)

    file_context_parts = []
    for f in uploaded_files:
        if isinstance(f, dict):
            fname = f.get("filename", "file.txt")
            fcontent = f.get("content", "")
            if fcontent:
                file_context_parts.append(f"\n--- Attached File: {fname} ---\n{fcontent}\n")

    full_prompt = user_text + "".join(file_context_parts)

    history = memory_manager.get_chat_session(session_id)
    history.append({
        "role": "user",
        "user_name": interaction.user.name,
        "user_id": str(interaction.user.id),
        "content": full_prompt,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    })

    envelope_lines = ["<context>"]
    for turn in history[:-1]:
        u_name = turn.get("user_name", "User")
        u_id = turn.get("user_id", "0")
        c = turn.get("content", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        envelope_lines.append(f'  <message user_id="{u_id}" username="{u_name}">\n    {c}\n  </message>')
    envelope_lines.append("</context>")
    context_xml = "\n".join(envelope_lines)

    active_witty = "Thinking"
    await interaction.edit_original_response(content=format_placeholder_content(active_witty))

    stream_dispatcher = DiscordStreamDispatcher(
        interaction=interaction,
        is_ephemeral=False,
        guild=interaction.guild
    )

    tool_context = ToolExecutionContext(
        channel=interaction.channel,
        guild=interaction.guild,
        author=interaction.user,
        bot=interaction.client
    )

    accumulated_thoughts = []
    tool_call_history = []
    active_tool_start_times = {}
    thinking_start_time = None
    thinking_view = None
    first_content_received = False
    ai_content_buffer = ""

    try:
        async for event_type, payload in ChatEngine.stream_chat(
            prompt=full_prompt,
            context_xml=context_xml,
            bot_user_id=interaction.client.user.id,
            tool_context=tool_context
        ):
            if event_type == "ROUTED":
                if payload.witty_statuses:
                    active_witty = payload.witty_statuses[0]
                    await interaction.edit_original_response(content=format_placeholder_content(active_witty))

            elif event_type == "RECALLED_MEMORIES":
                count = payload.get("count", 0)
                tool_call_history.insert(0, {
                    "name": "recall_memories",
                    "args": {"count": count},
                    "result": payload,
                    "duration_ms": 0
                })
                if not thinking_view:
                    thinking_start_time = time.time()
                    thinking_view = ThinkingButtonView(
                        duration_seconds=0,
                        is_thinking=True,
                        thought_data={"thoughts": "", "tool_calls": tool_call_history}
                    )

            elif event_type == "THOUGHT":
                if not thinking_view:
                    thinking_start_time = time.time()
                    thinking_view = ThinkingButtonView(
                        duration_seconds=0,
                        is_thinking=True,
                        thought_data={"thoughts": "", "tool_calls": tool_call_history}
                    )
                accumulated_thoughts.append(payload)

            elif event_type == "TOOL_START":
                tool_name = payload.get("name", "Tool")
                args = payload.get("args", {})
                active_tool_start_times[tool_name] = time.perf_counter()
                subtext = get_tool_subtext(tool_name, args)

                if not thinking_view:
                    thinking_start_time = time.time()
                    thinking_view = ThinkingButtonView(
                        duration_seconds=0,
                        is_thinking=True,
                        thought_data={"thoughts": "", "tool_calls": tool_call_history}
                    )

                if subtext:
                    try:
                        await interaction.edit_original_response(content=format_placeholder_content(active_witty, subtext))
                    except discord.HTTPException:
                        pass

            elif event_type == "TOOL_END":
                tool_name = payload.get("name", "Tool")
                start_t = active_tool_start_times.pop(tool_name, time.perf_counter())
                dur_ms = int((time.perf_counter() - start_t) * 1000)
                tool_call_history.append({
                    "name": tool_name,
                    "args": payload.get("args", {}),
                    "result": payload.get("result", {}),
                    "duration_ms": dur_ms
                })

            elif event_type == "CONTENT":
                if not first_content_received:
                    first_content_received = True
                    stream_dispatcher.buffer = ""
                    if thinking_view and thinking_start_time:
                        dur_sec = max(1, int(time.time() - thinking_start_time))
                        thinking_view.update_label(dur_sec, is_thinking=False)
                        thinking_view.thought_data["thoughts"] = "".join(accumulated_thoughts)
                        thinking_view.thought_data["tool_calls"] = tool_call_history

                ai_content_buffer += payload
                await stream_dispatcher.append_text(payload, view=thinking_view)

        history.append({
            "role": "assistant",
            "user_name": "PriestyAI",
            "user_id": str(interaction.client.user.id),
            "content": ai_content_buffer,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })
        memory_manager.save_chat_session(
            session_id=session_id,
            channel_id=str(interaction.channel_id),
            guild_id=str(interaction.guild_id) if interaction.guild_id else None,
            user_id=str(interaction.user.id),
            history=history
        )

        reply_view = ChatReplyView(session_id=session_id, bot_user_id=interaction.client.user.id)
        final_view = merge_views(thinking_view, reply_view)

        final_file = None
        if tool_context.staged_image_bytes:
            final_file = discord.File(
                io.BytesIO(tool_context.staged_image_bytes),
                filename=tool_context.staged_image_filename
            )

        await stream_dispatcher.finalize(view=final_view, file=final_file)

    except Exception as e:
        logger.exception(f"Error in /chat session: {e}")
        try:
            await interaction.edit_original_response(content=f"⚠️ An error occurred during chat generation: `{e}`")
        except discord.HTTPException:
            pass


def setup_slash_commands(tree: app_commands.CommandTree):

    @tree.command(name="ask", description="Ask PriestyAI a quick question anywhere on Discord")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(
        query="The prompt or question to ask",
        visibility="Choose whether the response is Public or Private (Ephemeral)"
    )
    @app_commands.choices(visibility=[
        app_commands.Choice(name="Public (Visible to everyone)", value="public"),
        app_commands.Choice(name="Private (Ephemeral, only you)", value="private")
    ])
    async def ask_command(interaction: discord.Interaction, query: str, visibility: str = "public"):
        is_ephemeral = (visibility == "private")
        await interaction.response.defer(ephemeral=is_ephemeral)

        active_witty = "Thinking"
        await interaction.edit_original_response(content=format_placeholder_content(active_witty))

        stream_dispatcher = DiscordStreamDispatcher(
            interaction=interaction,
            is_ephemeral=is_ephemeral,
            guild=interaction.guild
        )

        tool_context = ToolExecutionContext(
            channel=interaction.channel,
            guild=interaction.guild,
            author=interaction.user,
            bot=interaction.client
        )

        accumulated_thoughts = []
        tool_call_history = []
        active_tool_start_times = {}
        thinking_start_time = None
        thinking_view = None
        first_content = False

        try:
            async for event_type, payload in ChatEngine.stream_chat(
                prompt=query,
                context_xml="<context></context>",
                bot_user_id=interaction.client.user.id,
                tool_context=tool_context
            ):
                if event_type == "ROUTED":
                    if payload.witty_statuses:
                        active_witty = payload.witty_statuses[0]
                        await interaction.edit_original_response(content=format_placeholder_content(active_witty))

                elif event_type == "RECALLED_MEMORIES":
                    count = payload.get("count", 0)
                    tool_call_history.insert(0, {
                        "name": "recall_memories",
                        "args": {"count": count},
                        "result": payload,
                        "duration_ms": 0
                    })
                    if not thinking_view:
                        thinking_start_time = time.time()
                        thinking_view = ThinkingButtonView(
                            duration_seconds=0,
                            is_thinking=True,
                            thought_data={"thoughts": "", "tool_calls": tool_call_history}
                        )

                elif event_type == "THOUGHT":
                    if not thinking_view:
                        thinking_start_time = time.time()
                        thinking_view = ThinkingButtonView(
                            duration_seconds=0,
                            is_thinking=True,
                            thought_data={"thoughts": "", "tool_calls": tool_call_history}
                        )
                    accumulated_thoughts.append(payload)

                elif event_type == "TOOL_START":
                    tool_name = payload.get("name", "Tool")
                    args = payload.get("args", {})
                    active_tool_start_times[tool_name] = time.perf_counter()
                    subtext = get_tool_subtext(tool_name, args)

                    if not thinking_view:
                        thinking_start_time = time.time()
                        thinking_view = ThinkingButtonView(
                            duration_seconds=0,
                            is_thinking=True,
                            thought_data={"thoughts": "", "tool_calls": tool_call_history}
                        )

                    if subtext:
                        try:
                            await interaction.edit_original_response(content=format_placeholder_content(active_witty, subtext))
                        except discord.HTTPException:
                            pass

                elif event_type == "TOOL_END":
                    tool_name = payload.get("name", "Tool")
                    start_t = active_tool_start_times.pop(tool_name, time.perf_counter())
                    dur_ms = int((time.perf_counter() - start_t) * 1000)
                    tool_call_history.append({
                        "name": tool_name,
                        "args": payload.get("args", {}),
                        "result": payload.get("result", {}),
                        "duration_ms": dur_ms
                    })

                elif event_type == "CONTENT":
                    if not first_content:
                        first_content = True
                        stream_dispatcher.buffer = ""
                        if thinking_view and thinking_start_time:
                            dur_sec = max(1, int(time.time() - thinking_start_time))
                            thinking_view.update_label(dur_sec, is_thinking=False)
                            thinking_view.thought_data["thoughts"] = "".join(accumulated_thoughts)
                            thinking_view.thought_data["tool_calls"] = tool_call_history

                    await stream_dispatcher.append_text(payload, view=thinking_view)

            final_file = None
            if tool_context.staged_image_bytes:
                final_file = discord.File(
                    io.BytesIO(tool_context.staged_image_bytes),
                    filename=tool_context.staged_image_filename
                )

            await stream_dispatcher.finalize(view=thinking_view, file=final_file)

        except Exception as e:
            logger.exception(f"Error in /ask command: {e}")
            try:
                await interaction.edit_original_response(content=f"⚠️ Error: `{e}`")
            except discord.HTTPException:
                pass

    @tree.command(name="chat", description="Start an interactive conversation session with PriestyAI")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def chat_command(interaction: discord.Interaction):
        session_id = str(uuid.uuid4())[:8]
        modal = ChatReplyModal(
            title="Start a new chat...",
            session_id=session_id,
            on_submit_callback=handle_chat_modal_submit
        )
        await interaction.response.send_modal(modal)

    logger.info("Registered Slash Commands: /ask, /chat (User App & Guild enabled).")