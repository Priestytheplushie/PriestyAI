import inspect
import typing
import logging
from typing import Callable, Any, get_type_hints
from dataclasses import dataclass, field
import discord
from google.genai import types

logger = logging.getLogger("PriestyAI.ToolRegistry")

@dataclass
class ToolExecutionContext:
    channel: discord.abc.Messageable | None = None
    guild: discord.Guild | None = None
    author: discord.User | discord.Member | None = None
    bot: discord.Client | None = None
    staged_components: list[Any] = field(default_factory=list)
    staged_modals: list[Any] = field(default_factory=list)
    staged_artifacts: list[Any] = field(default_factory=list)
    input_image_bytes: bytes | None = None
    staged_image_bytes: bytes | None = None
    staged_image_filename: str = "generated_image.png"
    active_thread: Any | None = None
    clear_history_requested: bool = False
    agent_session_id: str | None = None
    message: discord.Message | None = None

def python_type_to_schema(py_type: Any, description: str = "") -> types.Schema:
    origin = typing.get_origin(py_type)
    args = typing.get_args(py_type)

    if origin in (typing.Union, getattr(types, "UnionType", None)):
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            py_type = non_none[0]
            origin = typing.get_origin(py_type)
            args = typing.get_args(py_type)

    if py_type in (int,):
        return types.Schema(type=types.Type.INTEGER, description=description)
    elif py_type in (float,):
        return types.Schema(type=types.Type.NUMBER, description=description)
    elif py_type in (bool,):
        return types.Schema(type=types.Type.BOOLEAN, description=description)
    elif py_type in (str, Any):
        return types.Schema(type=types.Type.STRING, description=description)
    elif py_type is list or origin is list:
        item_type = args[0] if args else str
        item_schema = python_type_to_schema(item_type)
        return types.Schema(type=types.Type.ARRAY, items=item_schema, description=description)
    elif py_type is dict or origin is dict:
        return types.Schema(type=types.Type.OBJECT, description=description)

    return types.Schema(type=types.Type.STRING, description=description)

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._declarations: list[types.FunctionDeclaration] = []

    def register(self, name: str | None = None, description: str = ""):
        def decorator(func: Callable):
            tool_name = name or func.__name__
            sig = inspect.signature(func)
            type_hints = get_type_hints(func)

            properties: dict[str, types.Schema] = {}
            required_params: list[str] = []

            for param_name, param in sig.parameters.items():
                if param_name == "context":
                    continue

                param_type = type_hints.get(param_name, str)
                param_desc = f"Parameter: {param_name}"
                properties[param_name] = python_type_to_schema(param_type, description=param_desc)

                if param.default == inspect.Parameter.empty:
                    required_params.append(param_name)

            parameters_schema = types.Schema(
                type=types.Type.OBJECT,
                properties=properties,
                required=required_params
            )

            doc = description or func.__doc__ or f"Executes {tool_name}"
            declaration = types.FunctionDeclaration(
                name=tool_name,
                description=doc.strip(),
                parameters=parameters_schema
            )

            self._tools[tool_name] = func
            self._declarations.append(declaration)
            logger.info(f"Registered tool: '{tool_name}'")
            return func

        return decorator

    def get_tool_declarations(self, disabled_tools: list[str] | None = None) -> list[types.Tool]:
        disabled_set = set(disabled_tools or [])
        active_declarations = [
            decl for decl in self._declarations
            if decl.name not in disabled_set
        ]
        if not active_declarations:
            return []
        return [types.Tool(function_declarations=active_declarations)]

    async def execute(self, tool_name: str, args: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        if tool_name not in self._tools:
            from core.custom_tool_manager import custom_tool_manager
            guild_id = context.guild.id if context.guild else None
            author_id = context.author.id if context.author else None
            active_custom_tools = custom_tool_manager.get_active_custom_tools(guild_id, author_id)
            for ct in active_custom_tools:
                if ct["name"] == tool_name:
                    return await custom_tool_manager.execute_custom_tool(ct, args)

            logger.error(f"Tool '{tool_name}' not found in registry.")
            return {"error": f"Tool '{tool_name}' is not recognized."}

        func = self._tools[tool_name]
        sig = inspect.signature(func)

        call_args = dict(args)
        if "context" in sig.parameters:
            call_args["context"] = context

        logger.info(f"Executing tool '{tool_name}' with args: {args}")
        try:
            if inspect.iscoroutinefunction(func):
                result = await func(**call_args)
            else:
                result = func(**call_args)
            
            if isinstance(result, dict):
                return result
            return {"output": str(result)}

        except Exception as e:
            logger.exception(f"Error executing tool '{tool_name}': {e}")
            return {"error": f"Tool execution failed: {str(e)}"}

tool_registry = ToolRegistry()