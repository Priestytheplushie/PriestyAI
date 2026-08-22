import json
import logging
from typing import Dict, Any, List, Optional
from google.genai import types

from tools.discord_tools import (
    DiscordToolsContext,
    execute_react,
    execute_send_message,
    execute_read_channel_history,
    execute_get_server_channels,
    execute_get_user_profile,
    execute_search_server,
    execute_create_thread
)
from tools.web_tools import execute_search_web
from tools.image_tools import execute_generate_image
from tools.code_tools import execute_code
from tools.expert_tools import execute_ask_expert

logger = logging.getLogger("PriestyAI.ToolRegistry")

TOOL_DECLARATIONS = [
    types.FunctionDeclaration(
        name="react",
        description="Adds an emoji reaction to any message in the channel or server.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "emoji": types.Schema(type="STRING", description="The emoji character or custom emoji string, e.g. '🔥', '👍'."),
                "message_id": types.Schema(type="STRING", description="Optional message ID to react to. Defaults to current message.")
            },
            required=["emoji"]
        )
    ),
    types.FunctionDeclaration(
        name="send_message",
        description="Sends a standalone message to the current channel or another channel.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "content": types.Schema(type="STRING", description="The message content to send."),
                "channel_id": types.Schema(type="STRING", description="Optional channel ID.")
            },
            required=["content"]
        )
    ),
    types.FunctionDeclaration(
        name="search_web",
        description="Searches the live internet for up-to-date information, news, documentation, or patch notes.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(type="STRING", description="The search query."),
                "num_results": types.Schema(type="INTEGER", description="Number of results (1 to 5).")
            },
            required=["query"]
        )
    ),
    types.FunctionDeclaration(
        name="generate_image",
        description="Generates an image from a prompt using Pollinations AI.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "prompt": types.Schema(type="STRING", description="Descriptive prompt of the image."),
                "model": types.Schema(type="STRING", description="Image model: 'flux', 'turbo', or 'flux-real'."),
                "width": types.Schema(type="INTEGER", description="Image width (default 1024)."),
                "height": types.Schema(type="INTEGER", description="Image height (default 1024).")
            },
            required=["prompt"]
        )
    ),
    types.FunctionDeclaration(
        name="execute_code",
        description="Executes code in an isolated Docker sandbox (Python, JS, Bash, C++, Rust) with network access.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "language": types.Schema(type="STRING", description="Language: 'python', 'javascript', 'bash', 'cpp', 'rust'."),
                "code": types.Schema(type="STRING", description="The code snippet to run.")
            },
            required=["language", "code"]
        )
    ),
    types.FunctionDeclaration(
        name="read_channel_history",
        description="Reads recent messages from any channel in the server.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "channel_id": types.Schema(type="STRING", description="Optional channel ID."),
                "limit": types.Schema(type="INTEGER", description="Number of messages (up to 30).")
            }
        )
    ),
    types.FunctionDeclaration(
        name="get_server_channels",
        description="Lists all text and voice channels available in the Discord server.",
        parameters=types.Schema(type="OBJECT", properties={})
    ),
    types.FunctionDeclaration(
        name="get_user_profile",
        description="Inspects detailed user info (roles, account age, avatar, permissions).",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "user_id": types.Schema(type="STRING", description="User ID or mention tag.")
            },
            required=["user_id"]
        )
    ),
    types.FunctionDeclaration(
        name="search_server",
        description="Searches messages in the channel for specific keywords or discussions.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "query": types.Schema(type="STRING", description="Keyword or phrase to search for."),
                "limit": types.Schema(type="INTEGER", description="Max matches (default 15).")
            },
            required=["query"]
        )
    ),
    types.FunctionDeclaration(
        name="create_thread",
        description="Creates a public thread from a message or topic.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "thread_name": types.Schema(type="STRING", description="Name of the thread."),
                "message_id": types.Schema(type="STRING", description="Optional message ID to attach thread to.")
            },
            required=["thread_name"]
        )
    ),
    types.FunctionDeclaration(
        name="ask_expert",
        description="Escalates a complex mathematical or logical subproblem to Gemini 3.7 Flash.",
        parameters=types.Schema(
            type="OBJECT",
            properties={
                "prompt": types.Schema(type="STRING", description="The rigorous problem prompt.")
            },
            required=["prompt"]
        )
    )
]

GEMINI_TOOLS = [types.Tool(function_declarations=TOOL_DECLARATIONS)]


class ToolDispatcher:
    def __init__(self, key_pool: Any):
        self.key_pool = key_pool

    async def dispatch(
        self,
        name: str,
        args: Dict[str, Any],
        context: DiscordToolsContext
    ) -> Dict[str, Any]:
        logger.info(f"Executing tool '{name}' with args: {args}")
        try:
            if name == "react":
                return await execute_react(context, emoji=args.get("emoji", "👍"), message_id=args.get("message_id"))
            elif name == "send_message":
                return await execute_send_message(context, content=args.get("content", ""), channel_id=args.get("channel_id"))
            elif name == "search_web":
                return await execute_search_web(query=args.get("query", ""), num_results=int(args.get("num_results", 5)))
            elif name == "generate_image":
                return await execute_generate_image(
                    prompt=args.get("prompt", ""),
                    model=args.get("model", "flux"),
                    width=int(args.get("width", 1024)),
                    height=int(args.get("height", 1024))
                )
            elif name == "execute_code":
                return await execute_code(language=args.get("language", "python"), code=args.get("code", ""))
            elif name == "read_channel_history":
                return await execute_read_channel_history(context, channel_id=args.get("channel_id"), limit=int(args.get("limit", 15)))
            elif name == "get_server_channels":
                return await execute_get_server_channels(context)
            elif name == "get_user_profile":
                return await execute_get_user_profile(context, user_id=args.get("user_id", ""))
            elif name == "search_server":
                return await execute_search_server(context, query=args.get("query", ""), limit=int(args.get("limit", 15)))
            elif name == "create_thread":
                return await execute_create_thread(context, thread_name=args.get("thread_name", "Discussion"), message_id=args.get("message_id"))
            elif name == "ask_expert":
                return await execute_ask_expert(self.key_pool, prompt=args.get("prompt", ""))
            else:
                return {"status": "error", "error": f"Tool '{name}' is not recognized."}
        except Exception as e:
            logger.error(f"Error executing tool '{name}': {e}", exc_info=True)
            return {"status": "error", "error": str(e)}