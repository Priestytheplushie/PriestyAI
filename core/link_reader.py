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
            "Accept-Language": "en-US,en;q=0.5"
        }

    def extract_urls(self, text: str) -> list[str]:
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        urls = re.findall(url_pattern, text)
        return [url.strip(',.()[]{}') for url in urls]

    async def fetch_and_clean(self, url: str) -> str:
        logger.info(f"Attempting to read link: {url}")
        
        if url.startswith("www."):
            url = "https://" + url

        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as response:
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
            
            for element in soup(["script", "style", "noscript", "iframe", "header", "footer", "nav", "svg", "form"]):
                element.decompose()
                
            title = soup.title.string.strip() if soup.title and soup.title.string else "Untitled Page"
            
            text_blocks = []
            for element in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li', 'article']):
                text = element.get_text().strip()
                if text:
                    text_blocks.append(text)
                    
            cleaned_text = "\n\n".join(text_blocks)
            
            cleaned_text = re.sub(r'\n{3,}', '\n\n', cleaned_text)
            cleaned_text = re.sub(r' {2,}', ' ', cleaned_text)
            
            summary = f"Title: {title}\nURL: {url}\n\n{cleaned_text}"
            
            return summary[:6000] if len(summary) > 6000 else summary
            
        except Exception as e:
            logger.error(f"Error reading URL {url}: {e}")
            return f"[Failed to read URL: {str(e)}]"