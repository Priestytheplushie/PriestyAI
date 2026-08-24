import re
import time
import urllib.parse
import logging
import httpx
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext
from core.searxng_client import searxng_client

logger = logging.getLogger("PriestyAI.MediaTools")

DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,*/*;q=0.8"
}

def detect_image_format(data: bytes) -> str | None:
    if len(data) < 16:
        return None

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "gif"

    return None

def is_relevant_candidate(query: str, title: str, image_url: str) -> bool:
    stopwords = {"official", "key", "art", "render", "image", "photo", "picture", "png", "jpg", "the", "a", "of", "in", "for", "with", "character"}
    query_words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', query) if len(w) >= 3 and w.lower() not in stopwords]
    
    if not query_words:
        return True

    target_text = f"{title} {image_url}".lower()

    matches = [qw for qw in query_words if qw in target_text]

    if len(query_words) >= 3:
        return len(matches) >= 2
    
    return len(matches) >= 1

@tool_registry.register(
    name="search_image",
    description=(
        "MANDATORY tool to find, look up, and attach real-world pictures, character renders, "
        "game art, screenshots, mob renders, items, hardware, or photos to your response in a single step.\n"
        "Parameters:\n"
        "- query: The exact entity name and context (e.g. 'Luna mo.co character official art', 'Minecraft Warden render png', 'PlayStation 5 Pro console photo').\n"
        "- caption: Optional short title or caption describing the asset."
    )
)
async def search_image(
    query: str,
    caption: str = "",
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not query or not query.strip():
        return {"error": "Search query cannot be empty."}

    if context and context.staged_image_bytes:
        return {
            "status": "already_attached",
            "message": "An image is already attached for this turn.",
            "filename": context.staged_image_filename
        }

    logger.info(f"[search_image] Starting visual image search via SearXNG for: '{query}'")

    candidates = await searxng_client.search_images(query=query, limit=10)
    if not candidates:
        logger.warning(f"[search_image] No candidate images returned for '{query}'.")
        return {
            "status": "no_results",
            "query": query,
            "message": f"No high-resolution images found for '{query}'. Do not loop queries. Explain naturally to the user."
        }

    async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
        for cand in candidates:
            img_url = cand.get("image_url", "")
            title = cand.get("title") or caption or query
            source = cand.get("source", "Web")

            if not img_url.startswith("http"):
                continue

            if not is_relevant_candidate(query, title, img_url):
                logger.debug(f"[search_image] Skipping non-matching candidate '{title}' for query '{query}'")
                continue

            try:
                resp = await client.get(img_url, headers=DOWNLOAD_HEADERS)
                content_type = resp.headers.get("content-type", "").lower()
                size_bytes = len(resp.content)

                if "svg" in content_type or "text/" in content_type or "html" in content_type:
                    logger.debug(f"[search_image] Skipping non-raster format ({content_type}) from {source}")
                    continue

                if size_bytes < 15000:
                    logger.debug(f"[search_image] Skipping undersized asset ({size_bytes:,} bytes) from {source}")
                    continue

                detected_ext = detect_image_format(resp.content)
                if not detected_ext:
                    logger.debug(f"[search_image] Header magic bytes verification failed for {img_url[:60]}")
                    continue

                filename = f"search_{int(time.time() * 1000)}.{detected_ext}"

                if context:
                    context.staged_image_bytes = resp.content
                    context.staged_image_filename = filename

                logger.info(f"[search_image] Validated and attached '{title}' ({size_bytes:,} bytes, {detected_ext.upper()}) from {source}")
                return {
                    "status": "attached",
                    "title": title,
                    "caption": caption,
                    "query": query,
                    "source": source,
                    "image_url": img_url,
                    "size_bytes": size_bytes,
                    "filename": filename
                }

            except Exception as e:
                logger.debug(f"[search_image] Candidate download error from {source}: {e}")

    return {
        "status": "download_failed",
        "query": query,
        "message": f"Candidate images for '{query}' were protected or corrupted. Do not retry in a loop."
    }

@tool_registry.register(
    name="generate_image",
    description=(
        "Generates an AI artwork or custom illustration from a detailed text prompt. "
        "Use ONLY when the user explicitly asks to generate, draw, or paint artificial artwork or fantasy concepts.\n"
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
                context.staged_image_filename = f"gen_{int(time.time() * 1000)}.png"
                logger.info(f"[generate_image] Downloaded {len(resp.content):,} bytes.")
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