import pytest
from core.chat_handler import ChatHandler


@pytest.fixture
def chat_handler(tmp_path):
    prompt_file = tmp_path / "system_prompt.md"
    prompt_file.write_text("System instructions {TOOL_DEFINITION}", encoding="utf-8")
    return ChatHandler(api_key="test-key", system_prompt_path=str(prompt_file))


@pytest.mark.parametrize(
    "query, expected",
    [
        ("what is the weather today?", True),
        ("show me the latest news on python", True),
        ("what is the price of bitcoin", True),
        ("hello, how are you doing?", False),
        ("tell me a funny story about cats", False),
    ],
)
def test_should_use_search(chat_handler, query, expected):
    assert chat_handler._should_use_search(query) == expected


@pytest.mark.parametrize(
    "message, expected_level",
    [
        ("hi", "NONE"),
        ("hey", "NONE"),
        ("thanks", "NONE"),
        ("lmao", "NONE"),
        ("ok cool", "NONE"),
        ("[System: User clicked button on interface]", "NONE"),
        ("mathematical proof of the pythagorean theorem", "HIGH"),
        ("debug this complex algorithm", "HIGH"),
        ("solve this system design architecture optimization", "HIGH"),
        ("[System: Scheduled morning check-in topic]", "MINIMAL"),
    ],
)
def test_select_thinking_level(chat_handler, message, expected_level):
    assert chat_handler._select_thinking_level(message, "") == expected_level


def test_sanitize_mime_type_and_data(chat_handler):

    mime, data = chat_handler._sanitize_mime_type_and_data(
        "image/jpeg", "test.jpg", b"fake-bytes"
    )
    assert mime == "image/jpeg"
    assert data == b"fake-bytes"

    mime, data = chat_handler._sanitize_mime_type_and_data(
        "image/gif", "test.gif", b"fake-bytes"
    )
    assert mime is None
    assert data is None

    mime, data = chat_handler._sanitize_mime_type_and_data(
        "image/bmp", "test.bmp", b"fake-bytes"
    )
    assert mime == "image/png"
    assert data == b"fake-bytes"

    mime, data = chat_handler._sanitize_mime_type_and_data(
        "application/octet-stream", "script.py", b"print('hello')"
    )
    assert mime == "text/plain"
    assert data == b"print('hello')"

    mime, data = chat_handler._sanitize_mime_type_and_data(
        "application/pdf", "doc.pdf", b"%PDF-fake"
    )
    assert mime == "application/pdf"


def test_build_tool_definition(chat_handler):
    config = {
        "system_tools": ["Generate Images", "Message Builder"],
        "discord_tools": ["Buttons", "Native Polls"],
    }
    tool_text = chat_handler.build_tool_definition(config)

    assert "[IMAGE_PENDING:" in tool_text
    assert "[BUILD_MESSAGE:" in tool_text
    assert "[BUTTON:" in tool_text
    assert "[POLL:" in tool_text
    assert "[THREAD:" not in tool_text
