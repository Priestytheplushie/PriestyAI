import re
import time
import httpx
import urllib.parse
import logging
from typing import Any
from bs4 import BeautifulSoup
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.MediaTools")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

def clean_image_query(query: str) -> str:
    cleaned = re.sub(r'(?i)\b(image of|photo of|picture of|screenshot of|high resolution|4k|hd|wallpaper|epic|ultra|vs|versus)\b', '', query)
    return " ".join(cleaned.split()).strip()

async def _search_openverse(query: str) -> list[dict[str, Any]]:
    url = f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(query)}&page_size=6"
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                results = []
                for item in data.get("results", []):
                    img_url = item.get("url")
                    if img_url and img_url.startswith("http"):
                        results.append({
                            "title": item.get("title") or query,
                            "url": img_url,
                            "source": "Openverse / CreativeCommons"
                        })
                return results
    except Exception as e:
        logger.debug(f"Openverse search error: {e}")
    return []

async def _search_wikimedia(query: str) -> list[dict[str, Any]]:
    url = (
        f"https://commons.wikimedia.org/w/api.php?action=query&generator=search"
        f"&gsrsearch={urllib.parse.quote(query)}&gsrnamespace=6&gsrlimit=6"
        f"&prop=imageinfo&iiprop=url|mime&format=json"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get("query", {}).get("pages", {})
                results = []
                for _, p in pages.items():
                    infos = p.get("imageinfo", [])
                    if infos:
                        img_url = infos[0].get("url")
                        mime = infos[0].get("mime", "")
                        if img_url and ("image" in mime or img_url.endswith((".png", ".jpg", ".jpeg", ".webp"))):
                            results.append({
                                "title": p.get("title", query).replace("File:", ""),
                                "url": img_url,
                                "source": "Wikimedia Commons"
                            })
                return results
    except Exception as e:
        logger.debug(f"Wikimedia search error: {e}")
    return []

async def _search_bing_images(query: str) -> list[dict[str, Any]]:
    url = f"https://www.bing.com/images/search?q={urllib.parse.quote(query)}&form=HDRSC2&first=1"
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=BROWSER_HEADERS)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                results = []
                for link in soup.find_all("a", class_="iusc"):
                    m_attr = link.get("m")
                    if m_attr and '"murl":"' in m_attr:
                        match = re.search(r'"murl":"(https?://[^"]+)"', m_attr)
                        if match:
                            murl = match.group(1).replace("\\/", "/")
                            results.append({
                                "title": query,
                                "url": murl,
                                "source": "Bing Web Images"
                            })
                            if len(results) >= 5:
                                break
                return results
    except Exception as e:
        logger.debug(f"Bing images fallback error: {e}")
    return []

@tool_registry.register(
    name="fetch_image",
    description=(
        "Searches the web for real-world photos, screenshots, product designs, or official logos.\n"
        "Automatically attaches the retrieved image directly into your response in an in-stream MediaGallery.\n"
        "Use this whenever explaining visual concepts, comparisons (e.g. games, devices, cars), landmarks, or brands."
    )
)
async def fetch_image(
    query: str,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    clean_q = clean_image_query(query)
    logger.info(f"[fetch_image] Search: '{clean_q}' (Raw: '{query}')")

    results = await _search_openverse(clean_q)

    if not results:
        results = await _search_wikimedia(clean_q)

    if not results:
        logger.info(f"[fetch_image] Openverse/Wiki empty. Running Bing search for '{clean_q}'...")
        results = await _search_bing_images(clean_q)

    if not results and len(clean_q.split()) > 1:
        core_word = clean_q.split()[0]
        logger.info(f"[fetch_image] Retrying with core subject: '{core_word}'...")
        results = await _search_bing_images(core_word) or await _search_openverse(core_word)

    if not results:
        return {"status": "not_found", "query": query, "message": "No images found for this query."}

    downloaded = False
    for candidate in results[:4]:
        img_url = candidate.get("url")
        if not img_url:
            continue

        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
                resp = await client.get(img_url, headers=BROWSER_HEADERS)
                if resp.status_code == 200 and len(resp.content) > 2048:
                    content_type = resp.headers.get("content-type", "").lower()
                    if "image" in content_type or img_url.endswith((".png", ".jpg", ".jpeg", ".webp")):
                        ext = "png"
                        if "jpeg" in content_type or "jpg" in content_type or ".jpg" in img_url:
                            ext = "jpg"
                        elif "webp" in content_type or ".webp" in img_url:
                            ext = "webp"

                        if context:
                            context.staged_image_bytes = resp.content
                            context.staged_image_filename = f"fetch_{int(time.time()*1000)}.{ext}"
                            downloaded = True
                            logger.info(f"[fetch_image] Successfully downloaded {len(resp.content)} bytes from '{candidate['source']}'.")
                            return {
                                "status": "fetched",
                                "title": candidate.get("title", clean_q),
                                "source": candidate.get("source", "Web"),
                                "image_url": img_url,
                                "filename": context.staged_image_filename
                            }
        except Exception as e:
            logger.debug(f"[fetch_image] Candidate download failed for {img_url}: {e}")

    return {"status": "not_found", "query": query, "message": "Failed to download image from available candidates."}

@tool_registry.register(
    name="generate_image",
    description=(
        "Generates an AI artwork or custom illustration from a detailed text prompt. "
        "Automatically displays the generated artwork in-stream with your response."
    )
)
async def generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    model: str = "flux",
    enhance: bool = True,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    logger.info(f"[generate_image] Prompt: '{prompt}' | Model: {model} | Resolution: {width}x{height}")

    encoded_prompt = urllib.parse.quote(prompt.strip())
    image_url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width={width}&height={height}&model={model}&nologo=true&enhance={'true' if enhance else 'false'}"
    )

    try:
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
            resp = await client.get(image_url)
            if resp.status_code == 200 and context:
                context.staged_image_bytes = resp.content
                context.staged_image_filename = f"gen_{int(time.time()*1000)}.png"
                logger.info(f"[generate_image] Downloaded {len(resp.content)} bytes.")
            else:
                logger.warning(f"[generate_image] Download failed with status {resp.status_code}")
    except Exception as e:
        logger.error(f"[generate_image] Failed to download image bytes: {e}")

    return {
        "status": "generated",
        "image_url": image_url,
        "prompt": prompt,
        "dimensions": f"{width}x{height}",
        "filename": getattr(context, "staged_image_filename", "generated_image.png")
    }