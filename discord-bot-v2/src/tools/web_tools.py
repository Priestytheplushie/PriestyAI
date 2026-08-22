import re
import json
import logging
import aiohttp
from typing import Dict, Any, List
from html.parser import HTMLParser

logger = logging.getLogger("PriestyAI.WebTools")

class SimpleHTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_parts: List[str] = []
        self.ignore_tags = {"script", "style", "head", "noscript", "svg", "nav", "footer"}
        self._current_ignore = 0

    def handle_starttag(self, tag: str, attrs):
        if tag.lower() in self.ignore_tags:
            self._current_ignore += 1

    def handle_endtag(self, tag: str):
        if tag.lower() in self.ignore_tags and self._current_ignore > 0:
            self._current_ignore -= 1

    def handle_data(self, data: str):
        if self._current_ignore == 0:
            cleaned = data.strip()
            if cleaned:
                self.text_parts.append(cleaned)

    def get_text(self) -> str:
        return " ".join(self.text_parts)


class WebTools:
    
    @staticmethod
    async def web_search(query: str, max_results: int = 4) -> List[Dict[str, str]]:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        data = {"q": query}
        results = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        links = re.findall(r'<a class="result__url" href="([^"]+)".*?>(.*?)</a>', html)
                        snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html)
                        titles = re.findall(r'<h2 class="result__title">[\s\S]*?<a[^>]*>(.*?)</a>', html)

                        for i in range(min(len(titles), max_results)):
                            clean_title = re.sub(r"<.*?>", "", titles[i]).strip()
                            clean_snippet = re.sub(r"<.*?>", "", snippets[i]).strip() if i < len(snippets) else ""
                            raw_url = links[i][0].strip() if i < len(links) else ""
                            actual_url = raw_url
                            if "uddg=" in raw_url:
                                match = re.search(r"uddg=([^&]+)", raw_url)
                                if match:
                                    import urllib.parse
                                    actual_url = urllib.parse.unquote(match.group(1))

                            results.append({
                                "title": clean_title,
                                "snippet": clean_snippet,
                                "url": actual_url
                            })
        except Exception as e:
            logger.error(f"Web search failed for '{query}': {e}")

        return results

    @staticmethod
    async def scrape_website(url: str, max_chars: int = 3500) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        parser = SimpleHTMLTextExtractor()
                        parser.feed(html)
                        text = parser.get_text()
                        return text[:max_chars] if text else "Page contained no readable text."
                    return f"Failed to load page. HTTP status code: {resp.status}"
        except Exception as e:
            return f"Error scraping URL: {str(e)}"

    @staticmethod
    async def fetch_youtube_metadata(video_url: str) -> Dict[str, Any]:
        oembed_url = f"https://www.youtube.com/oembed?url={video_url}&format=json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(oembed_url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return {
                            "title": data.get("title"),
                            "author": data.get("author_name"),
                            "url": video_url,
                            "thumbnail_url": data.get("thumbnail_url")
                        }
        except Exception as e:
            logger.error(f"YouTube metadata fetch error: {e}")
        return {"url": video_url, "error": "Could not fetch YouTube metadata."}