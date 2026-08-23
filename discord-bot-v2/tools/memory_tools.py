import logging
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext
from core.memory_manager import memory_manager

logger = logging.getLogger("PriestyAI.MemoryTools")

@tool_registry.register(
    name="remember",
    description=(
        "Stores a persistent memory into long-term memory banks.\n"
        "- category: 'user' (facts about user, preferences, tech habits) or 'server' (guild lore, rules, projects)\n"
        "- memory_text: Concise, clear factual statement (e.g. 'User prefers TypeScript and dark mode', 'Chaos Conquest was founded in 2024')\n"
        "- importance: Float between 0.1 and 1.0 (default 0.7)"
    )
)
async def remember(
    category: str,
    memory_text: str,
    importance: float = 0.7,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not context:
        return {"error": "Execution context unavailable."}

    cat_clean = category.strip().lower()
    if cat_clean == "user":
        if not context.author:
            return {"error": "Author context missing for user memory."}
        entity_id = str(context.author.id)
    elif cat_clean == "server":
        if not context.guild:
            return {"error": "Server lore can only be saved inside Discord servers, not DMs."}
        entity_id = str(context.guild.id)
    else:
        return {"error": "Category must be 'user' or 'server'."}

    return await memory_manager.remember(
        category=cat_clean,
        entity_id=entity_id,
        memory_text=memory_text,
        importance=min(max(float(importance), 0.1), 1.0)
    )

@tool_registry.register(
    name="forget",
    description=(
        "Removes or updates outdated memories when a user's preferences change or server lore is invalidated.\n"
        "- memory_id: The numeric ID of the memory from recalled_memories or search_memories\n"
        "- reason: Brief explanation for forgetting"
    )
)
async def forget(memory_id: int, reason: str = "", context: ToolExecutionContext = None) -> dict[str, Any]:
    try:
        m_id = int(memory_id)
        return await memory_manager.forget(memory_id=m_id, reason=reason)
    except ValueError:
        return {"error": f"Invalid memory ID: '{memory_id}'. Must be an integer."}

@tool_registry.register(
    name="search_memories",
    description=(
        "Explicitly searches through user memories or server lore for specific historical facts.\n"
        "- query: Semantic search query string"
    )
)
async def search_memories(query: str, context: ToolExecutionContext = None) -> dict[str, Any]:
    if not context or not context.author:
        return {"error": "Context unavailable."}

    user_id = context.author.id
    guild_id = context.guild.id if context.guild else None

    results = await memory_manager.recall_relevant_memories(query=query, user_id=user_id, guild_id=guild_id, top_k=5)
    return {
        "query": query,
        "user_memories": results["user_memories"],
        "server_lore": results["server_lore"]
    }