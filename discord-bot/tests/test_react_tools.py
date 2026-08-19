import json
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import discord
from agents.discord_react.tools import (
    run_local_agent_tool,
    tool_custom_web_scrape,
    tool_fetch_user_profile,
    tool_list_server_channels,
    tool_get_channel_metadata,
    tool_list_active_threads,
    tool_ask_user_question,
    tool_update_context_snapshot,
)


@pytest.fixture
def mock_agent_context():

    bot = MagicMock()
    bot.link_reader = MagicMock()
    bot.link_reader.fetch_and_clean = AsyncMock(return_value="Scraped web content")
    bot._compile_user_activity = MagicMock(return_value="Playing Game: Test")
    bot.user_context_cache = {}

    user_perms = SimpleNamespace(
        read_messages=True,
        read_message_history=True,
    )
    user_member = MagicMock(spec=discord.Member)
    user_member.id = 1001
    user_member.name = "caller"
    user_member.display_name = "CallerUser"

    target_member = MagicMock(spec=discord.Member)
    target_member.id = 2002
    target_member.name = "target"
    target_member.display_name = "TargetUser"
    target_member.nick = "TargetNick"
    target_member.bot = False
    target_member.joined_at = SimpleNamespace(
        strftime=lambda fmt: "2025-01-01 12:00:00"
    )
    target_member.created_at = SimpleNamespace(
        strftime=lambda fmt: "2024-01-01 12:00:00"
    )
    target_member.roles = [
        SimpleNamespace(name="@everyone", is_default=lambda: True),
        SimpleNamespace(name="Admin", is_default=lambda: False),
    ]

    text_channel = MagicMock(spec=discord.TextChannel)
    text_channel.id = 3003
    text_channel.name = "general"
    text_channel.type = discord.ChannelType.text
    text_channel.category = SimpleNamespace(name="Community", id=4004)
    text_channel.topic = "General chatter"
    text_channel.nsfw = False
    text_channel.slowmode_delay = 5
    text_channel.created_at = SimpleNamespace(
        strftime=lambda fmt: "2024-01-01 00:00:00"
    )
    text_channel.permissions_for = MagicMock(return_value=user_perms)

    mock_thread = MagicMock(spec=discord.Thread)
    mock_thread.id = 5005
    mock_thread.name = "feature-discussion"
    mock_thread.type = discord.ChannelType.public_thread
    mock_thread.parent = text_channel

    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 4004
    category.name = "Community"
    category.channels = [text_channel]

    guild = MagicMock(spec=discord.Guild)
    guild.id = 9999
    guild.name = "Test Guild"
    guild.me = MagicMock()
    guild.get_member = MagicMock(
        side_effect=lambda uid: (
            user_member if uid == 1001 else (target_member if uid == 2002 else None)
        )
    )
    guild.fetch_member = AsyncMock(
        side_effect=lambda uid: (
            user_member if uid == 1001 else (target_member if uid == 2002 else None)
        )
    )
    guild.get_channel = MagicMock(return_value=text_channel)
    guild.fetch_channel = AsyncMock(return_value=text_channel)
    guild.categories = [category]
    guild.channels = [text_channel]
    guild.threads = [mock_thread]
    guild.text_channels = [text_channel]

    bot.get_guild = MagicMock(return_value=guild)

    session_channel = MagicMock(spec=discord.Thread)
    session_channel.guild = guild
    session_channel.send = AsyncMock()

    session = SimpleNamespace(
        thread_id=5005,
        user_id=1001,
        target_guild_id=9999,
        channel=session_channel,
        status="running",
        react_history=[],
        pending_question_text="",
        pending_options=[],
    )

    return bot, session, guild


@pytest.mark.asyncio
async def test_run_local_agent_tool_unknown_tool(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await run_local_agent_tool(bot, session, "non_existent_tool", {})
    assert "[Error: Selected tool 'non_existent_tool' does not match" in result


@pytest.mark.asyncio
async def test_tool_custom_web_scrape_empty_url(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_custom_web_scrape(bot, "")
    assert "[Error: URL argument is empty]" in result


@pytest.mark.asyncio
async def test_tool_custom_web_scrape_success(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_custom_web_scrape(bot, "https://example.com/page")
    assert result == "Scraped web content"
    bot.link_reader.fetch_and_clean.assert_called_once_with("https://example.com/page")


@pytest.mark.asyncio
async def test_tool_fetch_user_profile_success(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_fetch_user_profile(bot, session, user_id=2002)

    data = json.loads(result)
    assert data["user_id"] == 2002
    assert data["username"] == "target"
    assert data["display_name"] == "TargetUser"
    assert "Admin" in data["server_roles"]
    assert "Playing Game: Test" in data["presence_activities"]


@pytest.mark.asyncio
async def test_tool_fetch_user_profile_not_found(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_fetch_user_profile(bot, session, user_id=999999)
    assert "is not a member of guild" in result


@pytest.mark.asyncio
async def test_tool_list_server_channels(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_list_server_channels(bot, session)

    assert "Available Channels inside 'Test Guild':" in result
    assert "Category: Community" in result
    assert "#general (Text)" in result


@pytest.mark.asyncio
async def test_tool_get_channel_metadata(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_get_channel_metadata(bot, session, channel_id=3003)

    data = json.loads(result)
    assert data["channel_id"] == 3003
    assert data["name"] == "general"
    assert data["category"] == "Community"
    assert data["topic"] == "General chatter"
    assert data["slowmode_delay_seconds"] == 5


@pytest.mark.asyncio
async def test_tool_list_active_threads(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_list_active_threads(bot, session)

    assert "Active Threads inside 'Test Guild':" in result
    assert "#feature-discussion (Public Thread)" in result
    assert "under channel #general" in result


@pytest.mark.asyncio
async def test_tool_ask_user_question_button_prompt(mock_agent_context):
    bot, session, _ = mock_agent_context
    result = await tool_ask_user_question(
        bot,
        session,
        question="Which branch should we audit?",
        component_type="Button",
        options=["main", "dev", "staging"],
    )

    assert "PAUSED: Awaiting user text reply" in result
    assert session.status == "paused_user_question"
    assert session.pending_question_text == "Which branch should we audit?"
    assert session.pending_options == ["main", "dev", "staging"]
    session.channel.send.assert_called_once()


@pytest.mark.asyncio
async def test_tool_update_context_snapshot_success(mock_agent_context):
    bot, session, _ = mock_agent_context
    bot.brain_server_id = 7777
    bot.user_context_cache[session.user_id] = "cached_data"

    mock_contexts = [{"alias": "my_audit", "type": "Custom Snapshot"}]

    with patch(
        "core.memory.fetch_all_contexts_for_user",
        new=AsyncMock(return_value=mock_contexts),
    ), patch("core.memory.save_context_snippet", new=AsyncMock(return_value=True)):
        result = await tool_update_context_snapshot(
            bot,
            session,
            alias="my_audit",
            updated_data={"key": "val"},
            notes="Updated notes",
        )

        assert "Successfully updated saved context snapshot 'my_audit'" in result

        assert session.user_id not in bot.user_context_cache


@pytest.mark.asyncio
async def test_tool_update_context_snapshot_not_found(mock_agent_context):
    bot, session, _ = mock_agent_context
    bot.brain_server_id = 7777

    with patch(
        "core.memory.fetch_all_contexts_for_user", new=AsyncMock(return_value=[])
    ):
        result = await tool_update_context_snapshot(
            bot, session, alias="missing_alias", updated_data={}, notes=""
        )

        assert (
            "Saved context snapshot with alias 'missing_alias' was not found" in result
        )
