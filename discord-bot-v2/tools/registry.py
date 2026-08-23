import inspect
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
    staged_image_bytes: bytes | None = None
    staged_image_filename: str = "generated_image.png"
    active_thread: Any | None = None
    clear_history_requested: bool = False

def python_type_to_genai_type(py_type: Any) -> types.Type:
    if py_type in (str, Any):
        return types.Type.STRING
    elif py_type == int:
        return types.Type.INTEGER
    elif py_type == float:
        return types.Type.NUMBER
    elif py_type == bool:
        return types.Type.BOOLEAN
    elif py_type in (list, list[str], list[int], list[Any]):
        return types.Type.ARRAY
    elif py_type in (dict, dict[str, Any]):
        return types.Type.OBJECT
    return types.Type.STRING

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
                genai_type = python_type_to_genai_type(param_type)

                properties[param_name] = types.Schema(
                    type=genai_type,
                    description=f"Parameter: {param_name}"
                )

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

    def get_tool_declarations(self) -> list[types.Tool]:
        if not self._declarations:
            return []
        return [types.Tool(function_declarations=self._declarations)]

    async def execute(self, tool_name: str, args: dict[str, Any], context: ToolExecutionContext) -> dict[str, Any]:
        if tool_name not in self._tools:
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