import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.link_reader import LinkReader


@pytest.fixture
def link_reader():
    return LinkReader()


def make_mock_aiohttp_session(status=200, headers=None, text=""):
    """Helper to mock nested async context managers for aiohttp.ClientSession and session.get."""
    if headers is None:
        headers = {"Content-Type": "text/html; charset=utf-8"}

    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.headers = headers
    mock_response.text = AsyncMock(return_value=text)

    mock_get_ctx = MagicMock()
    mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_get_ctx)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    return mock_session


def test_extract_urls(link_reader):
    text = (
        "Check this out https://github.com/PriestyAI, or visit www.python.org/doc. "
        "Also see (https://example.com/item) and https://test.io/page."
    )
    urls = link_reader.extract_urls(text)
    assert urls == [
        "https://github.com/PriestyAI",
        "www.python.org/doc",
        "https://example.com/item",
        "https://test.io/page",
    ]


def test_extract_urls_empty(link_reader):
    text = "There are no links in this plain conversation message."
    assert link_reader.extract_urls(text) == []


@pytest.mark.asyncio
async def test_fetch_and_clean_html_content(link_reader):
    sample_html = """
    <!DOCTYPE html>
    <html>
    <head><title>Test Article Title</title><script>alert('bad');</script></head>
    <body>
        <nav><a href="#">Home</a></nav>
        <header><h1>Header to Ignore</h1></header>
        <article>
            <h2>Main Announcement</h2>
            <p>PriestyAI version 2.0 has been officially released with deep research capabilities.</p>
            <p>It includes multi-modal tools, semantic vector memory, and automated news compiling.</p>
            <p>Additional paragraphs ensure the scraped text length exceeds the minimal 150-character threshold.</p>
        </article>
        <footer><p>Copyright 2026</p></footer>
    </body>
    </html>
    """

    mock_session = make_mock_aiohttp_session(
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        text=sample_html,
    )

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await link_reader.fetch_and_clean("https://example.com/news")

        assert "Title: Test Article Title" in result
        assert "Main Announcement" in result
        assert "PriestyAI version 2.0" in result

        assert "alert('bad')" not in result
        assert "Copyright 2026" not in result
        assert "Header to Ignore" not in result


@pytest.mark.asyncio
async def test_fetch_and_clean_triggers_jina_fallback_on_403(link_reader):
    mock_session = make_mock_aiohttp_session(status=403)

    with patch("aiohttp.ClientSession", return_value=mock_session), patch.object(
        link_reader,
        "_fetch_via_jina",
        new=AsyncMock(return_value="Markdown content from Jina"),
    ) as mock_jina:
        result = await link_reader.fetch_and_clean("https://protected-site.com")

        assert result == "Markdown content from Jina"
        mock_jina.assert_called_once_with("https://protected-site.com")


@pytest.mark.asyncio
async def test_fetch_and_clean_plain_text_or_json(link_reader):
    raw_json_text = (
        '{"status": "ok", "version": "2.0.0", "message": "API Health Check Successful"}'
    )

    mock_session = make_mock_aiohttp_session(
        status=200,
        headers={"Content-Type": "application/json"},
        text=raw_json_text,
    )

    with patch("aiohttp.ClientSession", return_value=mock_session):
        result = await link_reader.fetch_and_clean("https://api.example.com/health")
        assert result == raw_json_text
