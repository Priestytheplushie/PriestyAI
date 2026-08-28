import os
import shutil
import asyncio
import logging
from typing import Any
import httpx
from config.settings import SEARXNG_URL

logger = logging.getLogger("PriestyAI.SearXNG")

IGNORED_EXTENSIONS = (".svg", ".ico", ".eps", ".ai")
IGNORED_DOMAINS = (
    "jsdelivr.net",
    "shields.io",
    "badge",
    "favicon",
    "gravatar.com",
    "wikimedia.org/wikipedia/commons/thumb",
    "unsplash.com",
    "images.unsplash.com",
    "plus.unsplash.com",
    "pexels.com",
    "pixabay.com",
    "shutterstock.com",
    "istockphoto.com",
    "stock.adobe.com",
    "gettyimages.com",
    "freepik.com"
)

class SearXNGClient:
    def __init__(self, base_url: str = SEARXNG_URL):
        self.base_url = base_url.rstrip("/")

    async def ensure_running(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get(f"{self.base_url}/search", params={"q": "test", "format": "json"})
                if resp.status_code == 200:
                    logger.info("[SearXNG] Local service is online and healthy.")
                    return True
        except Exception:
            pass

        if not shutil.which("docker"):
            logger.warning("[SearXNG] Docker executable not found in system PATH. Cannot auto-start container.")
            return False

        logger.info("[SearXNG] Service not responding on localhost. Attempting to start container via Docker...")

        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "compose", "up", "-d",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            
            if proc.returncode != 0:
                proc_fallback = await asyncio.create_subprocess_exec(
                    "docker", "start", "priesty_searxng",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc_fallback.communicate()
        except Exception as e:
            logger.warning(f"[SearXNG] Auto-start command failed: {e}")
            return False

        for attempt in range(8):
            await asyncio.sleep(1.0)
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get(f"{self.base_url}/search", params={"q": "test", "format": "json"})
                    if resp.status_code == 200:
                        logger.info(f"[SearXNG] Container booted and ready after {attempt + 1}s!")
                        return True
            except Exception:
                continue

        logger.warning("[SearXNG] Container start initiated, but service did not become healthy within 8s.")
        return False

    async def search_web(self, query: str, limit: int = 4) -> list[dict[str, str]]:
        clean_query = query.strip()
        params = {
            "q": clean_query,
            "format": "json",
            "categories": "general",
            "language": "en-US"
        }

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
                if resp.status_code != 200:
                    logger.warning(f"[SearXNG] Web search returned status {resp.status_code} for '{clean_query}'")
                    return []

                data = resp.json()
                raw_results = data.get("results", [])

                formatted = []
                seen_links = set()
                for item in raw_results:
                    if len(formatted) >= limit:
                        break

                    url = item.get("url", "")
                    title = item.get("title", "").strip()
                    snippet = item.get("content", "").strip()

                    if not url or url in seen_links or not title:
                        continue

                    seen_links.add(url)
                    formatted.append({
                        "title": title,
                        "link": url,
                        "snippet": snippet[:350]
                    })

                logger.info(f"[SearXNG] Web search returned {len(formatted)} result(s) for '{clean_query}'")
                return formatted

        except httpx.ConnectError:
            logger.error(f"[SearXNG] Cannot connect to local SearXNG at {self.base_url}. Ensure Docker is running.")
            return []
        except Exception as e:
            logger.error(f"[SearXNG] Error querying web search for '{clean_query}': {e}")
            return []

    async def search_images(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        clean_query = query.strip()
        params = {
            "q": clean_query,
            "format": "json",
            "categories": "images",
            "engines": "bing images,google images,duckduckgo images,brave.images,qwant images",
            "language": "en-US"
        }

        try:
            async with httpx.AsyncClient(timeout=7.0) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
                if resp.status_code != 200:
                    logger.warning(f"[SearXNG] Image search returned status {resp.status_code} for '{clean_query}'")
                    return []

                data = resp.json()
                raw_results = data.get("results", [])

                formatted = []
                seen_sources = set()
                for item in raw_results:
                    if len(formatted) >= limit:
                        break

                    img_src = item.get("img_src") or item.get("image_url") or item.get("thumbnail_src")
                    if not img_src or not img_src.startswith("http"):
                        continue

                    img_lower = img_src.lower()

                    if any(img_lower.endswith(ext) or ext in img_lower for ext in IGNORED_EXTENSIONS):
                        continue

                    if any(domain in img_lower for domain in IGNORED_DOMAINS):
                        continue

                    if img_src in seen_sources:
                        continue

                    seen_sources.add(img_src)
                    title = item.get("title", clean_query).strip()
                    source_url = item.get("url", "Web")
                    engine = item.get("engine", "SearXNG")

                    source_domain = source_url.split("//")[-1].split("/")[0] if "//" in source_url else "Web"

                    formatted.append({
                        "title": title[:100],
                        "image_url": img_src,
                        "source": source_domain,
                        "engine": engine,
                        "resolution": item.get("resolution", "")
                    })

                logger.info(f"[SearXNG] Image search returned {len(formatted)} candidate(s) for '{clean_query}'")
                return formatted

        except httpx.ConnectError:
            logger.error(f"[SearXNG] Cannot connect to local SearXNG at {self.base_url}. Ensure Docker is running.")
            return []
        except Exception as e:
            logger.error(f"[SearXNG] Error querying image search for '{clean_query}': {e}")
            return []

searxng_client = SearXNGClient()