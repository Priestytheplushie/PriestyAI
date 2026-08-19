import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import discord
import numpy as np
import pytest
from core.memory import (
    ChatHistoryTracker,
    should_preserve_message,
    generate_embedding,
    save_config,
    load_config,
    save_context_snippet,
    fetch_all_contexts_for_user,
    save_news_state,
    load_news_state,
    save_fact,
    forget_fact,
    fetch_memory_block,
)


def test_chat_history_tracker_add_and_format():
    tracker = ChatHistoryTracker(limit=3)
    user = SimpleNamespace(name="jordan", display_name="Jordan", nick=None)
    msg1 = SimpleNamespace(
        channel=SimpleNamespace(id=100),
        author=user,
        clean_content="Hello world",
        attachments=[],
    )

    tracker.add_message(msg1)
    history = tracker.get_formatted_history(100)
    assert "Jordan (@jordan): Hello world" in history


def test_chat_history_tracker_limit_overflow():
    tracker = ChatHistoryTracker(limit=2)
    user = SimpleNamespace(name="alex", display_name="Alex", nick=None)

    for i in range(4):
        msg = SimpleNamespace(
            channel=SimpleNamespace(id=200),
            author=user,
            clean_content=f"Message {i}",
            attachments=[],
        )
        tracker.add_message(msg)

    history = tracker.get_formatted_history(200)
    assert "Message 0" not in history
    assert "Message 1" not in history
    assert "Message 2" in history
    assert "Message 3" in history


def test_chat_history_tracker_system_action():
    tracker = ChatHistoryTracker()
    tracker.add_system_action(300, "User clicked confirmation button")
    history = tracker.get_formatted_history(300)
    assert "[ACTION RECORDED: User clicked confirmation button]" in history


def test_should_preserve_message_heuristics():
    msg_attachment = SimpleNamespace(attachments=["file.png"], content="Casual chatter")
    assert should_preserve_message(msg_attachment) is True

    msg_fact_vec = SimpleNamespace(attachments=[], content="[FACT_VEC] {...}")
    assert should_preserve_message(msg_fact_vec) is True

    msg_visual = SimpleNamespace(
        attachments=[], content="[VISUAL MEMORY] ```json ... ```"
    )
    assert should_preserve_message(msg_visual) is True

    msg_upload = SimpleNamespace(
        attachments=[], content="**Image Upload: profile.jpg**"
    )
    assert should_preserve_message(msg_upload) is True

    msg_normal = SimpleNamespace(
        attachments=[], content="Just chatting about lunch today"
    )
    assert should_preserve_message(msg_normal) is False


def test_vector_cosine_similarity_ranking():
    query_vec = np.array([1.0, 0.0, 0.0])

    memories = [
        {
            "fact": "Likes Python programming",
            "category": "TECHNICAL ENVIRONMENT",
            "vector": [1.0, 0.1, 0.0],
        },
        {
            "fact": "Lives in New York",
            "category": "PROFILE & IDENTITY",
            "vector": [0.0, 1.0, 0.0],
        },
        {
            "fact": "Uses FastAPI and Docker",
            "category": "TECHNICAL ENVIRONMENT",
            "vector": [0.9, 0.2, 0.0],
        },
    ]

    scored = []
    q_norm = np.linalg.norm(query_vec)
    for mem in memories:
        f_vec = np.array(mem["vector"])
        f_norm = np.linalg.norm(f_vec)
        sim = float(np.dot(query_vec, f_vec) / (q_norm * f_norm))
        scored.append((sim, mem))

    scored.sort(key=lambda x: x[0], reverse=True)

    assert scored[0][1]["fact"] == "Likes Python programming"
    assert scored[1][1]["fact"] == "Uses FastAPI and Docker"
    assert scored[2][1]["fact"] == "Lives in New York"


def test_visual_memory_json_payload_format():
    payload = {
        "prompt": "Cyberpunk city in neon rain",
        "style": "cyberpunk",
        "ratio": "16:9",
        "seed": 424242,
        "cdn_url": "https://cdn.discordapp.com/attachments/123/generated.png",
        "timestamp": "2026-08-18T22:00:00Z",
    }
    raw_message = f"[VISUAL MEMORY] ```json\n{json.dumps(payload, indent=2)}\n```"

    assert raw_message.startswith("[VISUAL MEMORY]")
    assert "```json" in raw_message

    json_str = raw_message.split("```json\n")[1].split("\n```")[0]
    parsed = json.loads(json_str)
    assert parsed["seed"] == 424242
    assert parsed["style"] == "cyberpunk"


@pytest.mark.asyncio
async def test_generate_embedding_fallback_on_api_error():
    client = MagicMock()
    client.chat_handler.client.aio.models.embed_content = AsyncMock(
        side_effect=Exception("Gemini Embedding Quota Error")
    )

    vector = await generate_embedding(client, "Test text to embed")
    assert len(vector) == 768
    assert all(v == 0.0 for v in vector)


@pytest.mark.asyncio
async def test_save_and_load_config():
    client = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    client.get_guild = MagicMock(return_value=guild)

    target_config = {
        "system_prompt": "Custom prompt",
        "thinking_level": "High",
        "system_tools": ["Google Search", "Code Execution"],
    }

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 9001
    mock_thread.name = "config-100"
    mock_thread.send = AsyncMock()

    async def async_history(limit=5):
        msg = SimpleNamespace(
            id=123,
            content=f"```json\n{json.dumps(target_config, indent=2)}\n```",
        )
        yield msg

    mock_thread.history = MagicMock(side_effect=async_history)

    with patch(
        "core.memory.get_or_create_db_thread", new=AsyncMock(return_value=mock_thread)
    ):
        save_ok = await save_config(
            client,
            brain_server_id=1,
            target_id=100,
            is_dm=False,
            config_dict=target_config,
        )
        assert save_ok is True
        mock_thread.send.assert_called_once()
        assert "Custom prompt" in mock_thread.send.call_args[0][0]

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "configurations"
    mock_forum.type = discord.ChannelType.forum
    mock_forum.threads = [mock_thread]
    guild.channels = [mock_forum]

    loaded = await load_config(client, brain_server_id=1, target_id=100, is_dm=False)
    assert loaded == target_config


@pytest.mark.asyncio
async def test_save_and_fetch_context_snippets():
    client = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    client.get_guild = MagicMock(return_value=guild)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.name = "1001"
    mock_thread.send = AsyncMock()

    snippet_payload = {
        "alias": "dev_profile",
        "type": "User Profile Snapshot",
        "data": {"user_id": 42, "role": "Maintainer"},
        "notes": "Primary developer profile",
    }

    async def async_history(limit=50):
        msg = SimpleNamespace(
            id=456,
            content=f"```json\n{json.dumps(snippet_payload, indent=2)}\n```",
            edit=AsyncMock(),
        )
        yield msg

    mock_thread.history = MagicMock(side_effect=async_history)

    with patch(
        "core.memory.get_or_create_db_thread", new=AsyncMock(return_value=mock_thread)
    ):
        saved = await save_context_snippet(
            client,
            brain_server_id=1,
            user_id=1001,
            alias="dev_profile",
            type_name="User Profile Snapshot",
            data_payload={"user_id": 42, "role": "Maintainer"},
            notes="Primary developer profile",
        )
        assert saved is True

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "context-snippets"
    mock_forum.type = discord.ChannelType.forum
    mock_forum.threads = [mock_thread]
    guild.channels = [mock_forum]

    results = await fetch_all_contexts_for_user(client, brain_server_id=1, user_id=1001)
    assert len(results) == 1
    assert results[0]["alias"] == "dev_profile"
    assert results[0]["data"]["role"] == "Maintainer"


@pytest.mark.asyncio
async def test_save_and_load_news_state():
    client = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    client.get_guild = MagicMock(return_value=guild)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 8888
    mock_thread.name = "news-state-500"
    mock_thread.send = AsyncMock()

    news_state = {
        "last_episode_number": 42,
        "show_name": "PriestyAI Daily Chronicle",
    }

    async def async_history(limit=5):
        msg = SimpleNamespace(
            id=789,
            content=f"```json\n{json.dumps(news_state, indent=2)}\n```",
        )
        yield msg

    mock_thread.history = MagicMock(side_effect=async_history)

    with patch(
        "core.memory.get_or_create_db_thread", new=AsyncMock(return_value=mock_thread)
    ):
        saved = await save_news_state(
            client, brain_server_id=1, guild_id=500, state_dict=news_state
        )
        assert saved is True

    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.name = "configurations"
    mock_forum.type = discord.ChannelType.forum
    mock_forum.threads = [mock_thread]
    guild.channels = [mock_forum]

    loaded = await load_news_state(client, brain_server_id=1, guild_id=500)
    assert loaded == news_state
    assert loaded["last_episode_number"] == 42


@pytest.mark.asyncio
async def test_save_fact_and_forget_fact():
    client = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    client.get_guild = MagicMock(return_value=guild)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 1111
    mock_thread.name = "12345"
    mock_thread.send = AsyncMock()

    mock_user = SimpleNamespace(id=12345, display_name="TestUser")

    fact_payload = {"fact": "Prefers dark mode", "category": "PROFILE & IDENTITY"}
    mock_att = MagicMock()
    mock_att.filename = "fact_vec.json"
    mock_att.read = AsyncMock(return_value=json.dumps(fact_payload).encode("utf-8"))

    msg_to_delete = SimpleNamespace(
        id=2222,
        content="[FACT_VEC]",
        attachments=[mock_att],
        delete=AsyncMock(),
    )

    async def async_history(limit=100):
        yield msg_to_delete

    mock_thread.history = MagicMock(side_effect=async_history)

    with patch(
        "core.memory.get_or_create_db_thread", new=AsyncMock(return_value=mock_thread)
    ), patch(
        "core.memory.generate_embedding", new=AsyncMock(return_value=[0.1] * 768)
    ), patch(
        "core.memory.consolidate_memories_if_needed", new=AsyncMock()
    ):

        saved = await save_fact(
            client, brain_server_id=1, user=mock_user, fact="Prefers dark mode"
        )
        assert saved is True
        mock_thread.send.assert_called_once()

        forgotten = await forget_fact(
            client,
            brain_server_id=1,
            category_name="🧠 User Memories",
            channel_name="12345-memory",
            fact="Prefers dark mode",
        )
        assert forgotten is True
        msg_to_delete.delete.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_memory_block_formatting():
    client = MagicMock()
    guild = MagicMock(spec=discord.Guild)
    client.get_guild = MagicMock(return_value=guild)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 3333
    mock_thread.name = "12345"

    fact_1 = {
        "fact": "Uses Python and FastAPI",
        "category": "TECHNICAL ENVIRONMENT",
        "vector": [1.0, 0.0, 0.0],
    }
    fact_2 = {
        "fact": "Prefers dark mode theme",
        "category": "PROFILE & IDENTITY",
        "vector": [0.0, 1.0, 0.0],
    }

    att_1 = MagicMock()
    att_1.filename = "fact_vec.json"
    att_1.read = AsyncMock(return_value=json.dumps(fact_1).encode("utf-8"))

    att_2 = MagicMock()
    att_2.filename = "fact_vec.json"
    att_2.read = AsyncMock(return_value=json.dumps(fact_2).encode("utf-8"))

    msg1 = SimpleNamespace(id=1, content="[FACT_VEC]", attachments=[att_1])
    msg2 = SimpleNamespace(id=2, content="[FACT_VEC]", attachments=[att_2])

    async def async_history(limit=100):
        yield msg1
        yield msg2

    mock_thread.history = MagicMock(side_effect=async_history)

    with patch(
        "core.memory.get_or_create_db_thread", new=AsyncMock(return_value=mock_thread)
    ), patch(
        "core.memory.generate_embedding", new=AsyncMock(return_value=[1.0, 0.0, 0.0])
    ):
        result = await fetch_memory_block(
            client,
            brain_server_id=1,
            category_name="🧠 User Memories",
            channel_name="12345-memory",
            query_text="Python",
        )

        assert "### TECHNICAL ENVIRONMENT" in result
        assert "• Uses Python and FastAPI" in result
        assert "### PROFILE & IDENTITY" in result
        assert "• Prefers dark mode theme" in result
