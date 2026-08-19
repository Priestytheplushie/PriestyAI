import pytest
from agents.discord_react.agent import AgentSession
from agents.discord_react.views import AgentStepDiagnosticsView


@pytest.fixture
def agent_session():

    return AgentSession(
        thread_id=12345,
        user_id=67890,
        prompt="Audit server roles and permissions",
        loaded_contexts="",
        channel=None,
    )


def test_compile_react_transcript_empty(agent_session):
    transcript = agent_session.compile_react_transcript()
    assert transcript == "No steps completed yet."


def test_compile_react_transcript_with_steps(agent_session):
    agent_session.react_history.append(
        {
            "thought": "I should list the available channels first.",
            "tool": "list_server_channels",
            "args": {},
            "observation": "Found #general, #dev, #logs.",
        }
    )
    transcript = agent_session.compile_react_transcript()

    assert "--- STEP 1 ---" in transcript
    assert "Thought: I should list the available channels first." in transcript
    assert "Action Call: `list_server_channels` with args {}" in transcript
    assert "Observation Result: Found #general, #dev, #logs." in transcript


def test_parse_react_output_markdown_fences(agent_session):
    raw_output = """
```thought
We need to search for user messages in the dev channel.
```
```action
{
  "tool": "search_server_messages",
  "arguments": {
    "query": "bug",
    "limit": 10
  }
}
```
"""
    thought, tool, args, error = agent_session.parse_react_output(raw_output)

    assert error is None
    assert thought == "We need to search for user messages in the dev channel."
    assert tool == "search_server_messages"
    assert args == {"query": "bug", "limit": 10}


def test_parse_react_output_xml_tags(agent_session):
    raw_output = """
<thought>Checking channel metadata.</thought>
<action>{"tool": "get_channel_metadata", "arguments": {"channel_id": 999}}</action>
"""
    thought, tool, args, error = agent_session.parse_react_output(raw_output)

    assert error is None
    assert thought == "Checking channel metadata."
    assert tool == "get_channel_metadata"
    assert args == {"channel_id": 999}


def test_parse_react_output_final_report_fallback(agent_session):
    raw_output = """
```thought
I have gathered all the needed data. I am ready to conclude.
```
Here is the final report for the user.
"""
    thought, tool, args, error = agent_session.parse_react_output(raw_output)

    assert error is None
    assert thought == "I have gathered all the needed data. I am ready to conclude."
    assert tool == "final_report"
    assert args == {}


def test_parse_react_output_invalid_json_action(agent_session):
    raw_output = """
```thought
Executing action.
```
```action
{ this is not valid json }
```
"""
    thought, tool, args, error = agent_session.parse_react_output(raw_output)

    assert tool is None
    assert error is not None
    assert "Invalid Action block JSON syntax" in error


def test_step_diagnostics_view_tabs():
    view = AgentStepDiagnosticsView(
        step_index=0,
        thought="Thinking about step 1",
        tool="fetch_user_profile",
        args={"user_id": 12345},
        observation="User profile data loaded successfully.",
    )

    assert view.active_tab == "thoughts"
    content_thoughts = view.get_content()
    assert "Step 1 - Internal Thoughts" in content_thoughts
    assert "Thinking about step 1" in content_thoughts

    view.active_tab = "tool"
    content_tool = view.get_content()
    assert "Step 1 - Task Details" in content_tool
    assert "fetch_user_profile" in content_tool
    assert "12345" in content_tool

    view.active_tab = "observation"
    content_obs = view.get_content()
    assert "Step 1 - Gathered Findings" in content_obs
    assert "User profile data loaded successfully." in content_obs
