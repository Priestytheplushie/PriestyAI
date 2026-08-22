import json
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import discord
from google.genai import types

from src.tools.docker_sandbox import sandbox
from src.tools.image_gen import ImageGenerator
from src.tools.latex_render import LatexRenderer
from src.tools.web_tools import WebTools
from src.tools.discord_tools import DiscordTools
from src.ui.modals import DynamicModalLauncherView

logger = logging.getLogger("PriestyAI.ToolManager")

@dataclass
class ToolContext:
    guild: Optional[discord.Guild]
    channel: discord.abc.Messageable
    author: discord.User | discord.Member
    message: discord.Message

class ToolManager:

    @staticmethod
    def get_tool_declarations() -> List[types.Tool]:
        declarations = [
            types.FunctionDeclaration(
                name="run_sandbox_code",
                description="Executes code in an isolated sandbox. Supported languages: python, javascript, rust, cpp, c, bash.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "language": types.Schema(type=types.Type.STRING, description="Programming language (e.g. python, javascript, rust, bash)"),
                        "code": types.Schema(type=types.Type.STRING, description="Source code to execute")
                    },
                    required=["language", "code"]
                )
            ),
            types.FunctionDeclaration(
                name="generate_image",
                description="Generates an image via Pollinations AI when a user asks to draw, illustrate, or create an image.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "prompt": types.Schema(type=types.Type.STRING, description="Detailed descriptive prompt for the image"),
                        "width": types.Schema(type=types.Type.INTEGER, description="Image width (e.g. 1024)"),
                        "height": types.Schema(type=types.Type.INTEGER, description="Image height (e.g. 1024)")
                    },
                    required=["prompt"]
                )
            ),
            types.FunctionDeclaration(
                name="render_latex_math",
                description="Renders complex mathematical equations or formulas as a clean PNG image.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "latex_code": types.Schema(type=types.Type.STRING, description="LaTeX math expression, e.g. \\int_{0}^{\\infty} x^2 dx")
                    },
                    required=["latex_code"]
                )
            ),
            types.FunctionDeclaration(
                name="web_search",
                description="Searches the live web for current facts, news, documentation, or events.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "query": types.Schema(type=types.Type.STRING, description="Search query string")
                    },
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="scrape_website",
                description="Reads and extracts the textual content of any given webpage URL.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "url": types.Schema(type=types.Type.STRING, description="Webpage URL to fetch")
                    },
                    required=["url"]
                )
            ),
            types.FunctionDeclaration(
                name="fetch_youtube_info",
                description="Fetches title, author, and info for a YouTube video URL.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "video_url": types.Schema(type=types.Type.STRING, description="YouTube URL")
                    },
                    required=["video_url"]
                )
            ),
            types.FunctionDeclaration(
                name="get_user_profile",
                description="Retrieves a user's Discord profile, roles, join date, status, and activity.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "user_id": types.Schema(type=types.Type.STRING, description="The user's numeric Discord ID")
                    },
                    required=["user_id"]
                )
            ),
            types.FunctionDeclaration(
                name="read_channel_messages",
                description="Reads recent messages from another text channel in this Discord server.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "channel_id": types.Schema(type=types.Type.STRING, description="Numeric Discord Channel ID"),
                        "limit": types.Schema(type=types.Type.INTEGER, description="Number of messages to retrieve (max 25)")
                    },
                    required=["channel_id"]
                )
            ),
            types.FunctionDeclaration(
                name="react_to_message",
                description="Adds an emoji reaction to a message in the channel.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "message_id": types.Schema(type=types.Type.STRING, description="ID of the message to react to"),
                        "emoji": types.Schema(type=types.Type.STRING, description="Unicode emoji or custom Discord emote string")
                    },
                    required=["message_id", "emoji"]
                )
            ),
            types.FunctionDeclaration(
                name="watch_channel",
                description="Temporarily watches a channel to listen and participate in ongoing conversation.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "channel_id": types.Schema(type=types.Type.STRING, description="Channel ID to watch"),
                        "duration_minutes": types.Schema(type=types.Type.INTEGER, description="Duration to watch in minutes (default 30)")
                    },
                    required=["channel_id"]
                )
            ),
            types.FunctionDeclaration(
                name="reset_channel_memory",
                description="Clears the active conversation buffer for this channel when user says goodbye or wants a fresh start.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                )
            ),
            types.FunctionDeclaration(
                name="attach_modal_button",
                description="Attaches an interactive button that launches a rich Modal V2 with dropdowns, radios, checkboxes, and inputs.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "button_label": types.Schema(type=types.Type.STRING, description="Label for the launcher button"),
                        "modal_title": types.Schema(type=types.Type.STRING, description="Title of the modal popup"),
                        "modal_custom_id": types.Schema(type=types.Type.STRING, description="Unique identifier for modal tracking"),
                        "components_json": types.Schema(type=types.Type.STRING, description="JSON array of Modal V2 components (text_input, string_select, channel_select, radio_group, checkbox_group, file_upload)")
                    },
                    required=["button_label", "modal_title", "modal_custom_id", "components_json"]
                )
            )
        ]

        return [types.Tool(function_declarations=declarations)]

    @classmethod
    async def dispatch_tool_call(
        cls,
        func_call: types.FunctionCall,
        context: ToolContext
    ) -> Dict[str, Any]:
        name = func_call.name
        args = func_call.args or {}
        logger.info(f"Executing tool call: {name} with args: {args}")

        try:
            if name == "attach_modal_button":
                components_raw = args.get("components_json", "[]")
                try:
                    components_list = json.loads(components_raw) if isinstance(components_raw, str) else components_raw
                except Exception:
                    components_list = []

                view = DynamicModalLauncherView(
                    button_label=str(args.get("button_label", "Open Form")),
                    modal_title=str(args.get("modal_title", "Form")),
                    modal_custom_id=str(args.get("modal_custom_id", "dynamic_modal")),
                    modal_components=components_list
                )
                return {"success": True, "_modal_view": view}

            elif name == "run_sandbox_code":
                return await sandbox.run_code(
                    language=str(args.get("language", "python")),
                    code=str(args.get("code", ""))
                )

            elif name == "generate_image":
                url = ImageGenerator.get_image_url(
                    prompt=str(args.get("prompt", "")),
                    width=int(args.get("width", 1024)),
                    height=int(args.get("height", 1024))
                )
                return {"image_url": url, "prompt": args.get("prompt")}

            elif name == "render_latex_math":
                latex = str(args.get("latex_code", ""))
                png_bytes = LatexRenderer.render_to_png(latex)
                return {
                    "rendered": png_bytes is not None,
                    "latex_code": latex,
                    "_latex_bytes": png_bytes
                }

            elif name == "web_search":
                query = str(args.get("query", ""))
                results = await WebTools.web_search(query)
                return {"query": query, "results": results}

            elif name == "scrape_website":
                url = str(args.get("url", ""))
                text = await WebTools.scrape_website(url)
                return {"url": url, "extracted_text": text}

            elif name == "fetch_youtube_info":
                url = str(args.get("video_url", ""))
                return await WebTools.fetch_youtube_metadata(url)

            elif name == "get_user_profile":
                uid = int(str(args.get("user_id", "0")).strip("<@!>"))
                return await DiscordTools.get_user_profile(context.guild, uid)

            elif name == "read_channel_messages":
                cid = int(str(args.get("channel_id", "0")).strip("<#>"))
                limit = int(args.get("limit", 10))
                return {"messages": await DiscordTools.read_channel_messages(context.guild, cid, limit)}

            elif name == "react_to_message":
                mid = int(args.get("message_id", context.message.id))
                emoji = str(args.get("emoji", "👍"))
                return await DiscordTools.react_to_message(context.channel, mid, emoji)

            elif name == "watch_channel":
                cid = str(args.get("channel_id", context.channel.id)).strip("<#>")
                gid = str(context.guild.id) if context.guild else "0"
                mins = int(args.get("duration_minutes", 30))
                return await DiscordTools.watch_channel(gid, cid, mins)

            elif name == "reset_channel_memory":
                return await DiscordTools.reset_channel_memory(str(context.channel.id))

            else:
                return {"error": f"Unknown tool function: {name}"}

        except Exception as e:
            logger.error(f"Error executing tool {name}: {e}", exc_info=True)
            return {"error": f"Tool execution failed: {str(e)}"}