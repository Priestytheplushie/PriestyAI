import re
import logging
import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger("LinkReader")


class LinkReader:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    def extract_urls(self, text: str) -> list[str]:
        """Finds all HTTP/HTTPS URLs inside the given text content."""
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        urls = re.findall(url_pattern, text)
        return [url.strip(",.()[]{}") for url in urls]

    async def fetch_and_clean(self, url: str) -> str:
        """
        Downloads a URL webpage and converts raw HTML layouts into clean paragraphs.
        Includes an automatic fallback to Jina Reader API if standard scraping fails
        or is blocked by standard web firewalls (401, 403, 429, etc.).
        """
        logger.info(f"Attempting to read link: {url}")

        if url.startswith("www."):
            url = "https://" + url

        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=8)
                ) as response:

                    if response.status in (401, 403, 429):
                        logger.warning(
                            f"Standard request blocked with status {response.status}. Triggering Jina fallback..."
                        )
                        return await self._fetch_via_jina(url)

                    if response.status != 200:
                        return f"[Error fetching URL: HTTP Status {response.status}]"

                    content_type = response.headers.get("Content-Type", "").lower()
                    if "text/html" not in content_type:

                        if "text/" in content_type or "json" in content_type:
                            text_content = await response.text()
                            return text_content[:6000]
                        return f"[Ignored non-text content type: {content_type}]"

                    html_content = await response.text()

            soup = BeautifulSoup(html_content, "html.parser")

            for element in soup(
                [
                    "script",
                    "style",
                    "noscript",
                    "iframe",
                    "header",
                    "footer",
                    "nav",
                    "svg",
                    "form",
                ]
            ):
                element.decompose()

            title = (
                soup.title.string.strip()
                if soup.title and soup.title.string
                else "Untitled Page"
            )

            text_blocks = []

            for element in soup.find_all(
                ["h1", "h2", "h3", "h4", "p", "li", "article"]
            ):
                text = element.get_text().strip()
                if text:
                    text_blocks.append(text)

            cleaned_text = "\n\n".join(text_blocks)

            cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)
            cleaned_text = re.sub(r" {2,}", " ", cleaned_text)

            summary = f"Title: {title}\nURL: {url}\n\n{cleaned_text}"

            if len(cleaned_text.strip()) < 150:
                logger.warning(
                    "Scraped raw text content too sparse. Triggering Jina fallback..."
                )
                return await self._fetch_via_jina(url)

            return summary[:6000] if len(summary) > 6000 else summary

        except Exception as e:
            logger.warning(
                f"Standard scraping failed due to error: {e}. Attempting Jina fallback..."
            )
            return await self._fetch_via_jina(url)

    async def _fetch_via_jina(self, url: str) -> str:
        """
        Bypasses standard scraper restrictions by routing the target URL
        through Jina Reader API to get clean, pre-rendered Markdown.
        """
        jina_url = f"https://r.jina.ai/{url}"
        headers = {"X-With-Links-Summary": "true", "X-With-Images-Summary": "false"}
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    jina_url, timeout=aiohttp.ClientTimeout(total=12)
                ) as response:
                    if response.status != 200:
                        return f"[Error: Jina fallback failed with HTTP status {response.status}]"
                    text = await response.text()
                    return text[:6000]
        except Exception as err:
            logger.error(f"Jina Reader fallback failed for {url}: {err}")
            return f"[Failed to read URL: {str(err)}]"
