import io
import gc
import time
import random
import urllib.parse
import logging
import asyncio
import httpx
from typing import Any
from PIL import Image
from tools.registry import tool_registry, ToolExecutionContext
from core.searxng_client import searxng_client

logger = logging.getLogger("PriestyAI.MediaTools")

DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,image/gif,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "cross-site",
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
    target_text = f"{title} {image_url}".lower()
    query_lower = query.lower()
    stopwords = {"official", "image", "photo", "picture", "png", "jpg", "jpeg", "gif", "the", "a", "of", "in", "for", "with", "render"}
    query_words = [w for w in query_lower.split() if w not in stopwords and len(w) >= 2]
    if not query_words:
        return True
    return any(word in target_text for word in query_words)


_pipe_cache = None

def _prep_pil_image_and_mask(image_bytes: bytes, max_side: int = 512) -> tuple[Image.Image, Image.Image | None]:
    raw_img = Image.open(io.BytesIO(image_bytes))
    has_alpha = raw_img.mode in ("RGBA", "LA") or (raw_img.mode == "P" and "transparency" in raw_img.info)
    
    alpha_mask = None
    if has_alpha:
        raw_img = raw_img.convert("RGBA")
        alpha_mask = raw_img.split()[-1]
        white_canvas = Image.new("RGBA", raw_img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(white_canvas, raw_img).convert("RGB")
    else:
        img = raw_img.convert("RGB")

    w, h = img.size
    scale = min(max_side / w, max_side / h)
    new_w = int((w * scale) // 8 * 8)
    new_h = int((h * scale) // 8 * 8)
    
    resized_img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    resized_mask = alpha_mask.resize((new_w, new_h), Image.Resampling.LANCZOS) if alpha_mask else None
    
    return resized_img, resized_mask

def _get_local_pipeline(mode: str = "img2img"):
    global _pipe_cache
    import torch
    from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image, LCMScheduler

    if _pipe_cache is not None and _pipe_cache.get("mode") == mode:
        return _pipe_cache["pipe"]

    if _pipe_cache is not None:
        del _pipe_cache["pipe"]
        _pipe_cache = None
        gc.collect()

    model_id = "Lykon/dreamshaper-8-lcm"
    logger.info(f"[LocalDiffusion] Loading '{model_id}' ({mode})...")

    if mode == "img2img":
        pipe = AutoPipelineForImage2Image.from_pretrained(
            model_id,
            torch_dtype=torch.float32,
            safety_checker=None
        )
    else:
        pipe = AutoPipelineForText2Image.from_pretrained(
            model_id,
            torch_dtype=torch.float32,
            safety_checker=None
        )

    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
    pipe.enable_attention_slicing()
    _pipe_cache = {"pipe": pipe, "mode": mode}
    return pipe

def _run_dreamshaper_img2img_sync(
    image_bytes: bytes,
    prompt: str,
    negative_prompt: str,
    steps: int,
    strength: float,
    guidance: float
) -> bytes:
    prep_img, alpha_mask = _prep_pil_image_and_mask(image_bytes, max_side=512)
    pipe = _get_local_pipeline(mode="img2img")

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        image=prep_img,
        num_inference_steps=max(4, min(steps, 10)),
        strength=max(0.2, min(strength, 0.9)),
        guidance_scale=max(1.0, min(guidance, 4.0))
    ).images[0]

    if alpha_mask:
        result = result.convert("RGBA")
        result.putalpha(alpha_mask)
        logger.info("[LocalDiffusion] Restored transparent alpha mask on output.")

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()

def _run_dreamshaper_text2img_sync(
    prompt: str,
    negative_prompt: str,
    steps: int,
    guidance: float,
    width: int,
    height: int
) -> bytes:
    pipe = _get_local_pipeline(mode="text2img")
    w = int(width // 8 * 8)
    h = int(height // 8 * 8)

    result = pipe(
        prompt=prompt,
        negative_prompt=negative_prompt,
        num_inference_steps=max(4, min(steps, 8)),
        guidance_scale=max(1.0, min(guidance, 4.0)),
        width=w,
        height=h
    ).images[0]

    buf = io.BytesIO()
    result.save(buf, format="PNG")
    return buf.getvalue()


@tool_registry.register(
    name="search_image",
    description=(
        "MANDATORY tool to find, look up, and attach real-world pictures, character renders, "
        "game art, screenshots, mob renders, items, hardware, or photos to your response in a single step.\n"
        "Parameters:\n"
        "- query: The exact entity name and context (e.g. 'Minecraft Warden render png', 'RTX 5090 founder edition').\n"
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
        return {
            "status": "no_results",
            "query": query,
            "message": f"No high-resolution images found for '{query}'. Explain naturally to the user."
        }

    async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
        for cand in candidates:
            img_url = cand.get("image_url", "")
            title = cand.get("title") or caption or query
            source = cand.get("source", "Web")

            if not img_url.startswith("http"):
                continue

            if not is_relevant_candidate(query, title, img_url):
                continue

            try:
                resp = await client.get(img_url, headers=DOWNLOAD_HEADERS)
                content_type = resp.headers.get("content-type", "").lower()
                size_bytes = len(resp.content)

                if "svg" in content_type or "text/" in content_type or "html" in content_type:
                    continue

                if size_bytes < 15000:
                    continue

                detected_ext = detect_image_format(resp.content)
                if not detected_ext:
                    continue

                filename = f"search_{int(time.time() * 1000)}.{detected_ext}"

                if context:
                    context.staged_image_bytes = resp.content
                    context.staged_image_filename = filename

                logger.info(f"[search_image] Attached '{title}' ({size_bytes:,} bytes, {detected_ext.upper()}) from {source}")
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
                logger.debug(f"[search_image] Download error from {source}: {e}")

    return {
        "status": "download_failed",
        "query": query,
        "message": f"Candidate images for '{query}' were protected or corrupted."
    }

@tool_registry.register(
    name="search_gif",
    description=(
        "Finds, downloads, and embeds an animated reaction GIF, meme, or video clip directly into chat.\n"
        "Use this for humorous reactions, emotion, pop-culture memes, celebratory moments, or expressive responses!\n"
        "- query: The search phrase or emotion (e.g. 'cat typing fast', 'mind blown anime', 'steve harvey confused', 'applause celebrate')."
    )
)
async def search_gif(
    query: str,
    caption: str = "",
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not query or not query.strip():
        return {"error": "GIF search query cannot be empty."}

    if context and context.staged_image_bytes:
        return {
            "status": "already_attached",
            "message": "A media asset is already attached for this turn.",
            "filename": context.staged_image_filename
        }

    clean_query = query.strip()
    search_q = f"{clean_query} gif" if not clean_query.lower().endswith("gif") else clean_query
    logger.info(f"[search_gif] Searching animated GIF for: '{search_q}'")

    candidates = await searxng_client.search_images(query=search_q, limit=12)
    if not candidates:
        return {
            "status": "no_results",
            "query": query,
            "message": f"No GIFs found for '{query}'."
        }

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        for cand in candidates:
            img_url = cand.get("image_url", "")
            title = cand.get("title") or caption or query
            source = cand.get("source", "Web")

            if not img_url.startswith("http"):
                continue

            try:
                resp = await client.get(img_url, headers=DOWNLOAD_HEADERS)
                content_type = resp.headers.get("content-type", "").lower()
                size_bytes = len(resp.content)

                if "svg" in content_type or "text/" in content_type or "html" in content_type:
                    continue

                detected_ext = detect_image_format(resp.content)
                if detected_ext != "gif" and "gif" not in content_type:
                    continue

                if size_bytes > 12 * 1024 * 1024:
                    continue

                filename = f"gif_{int(time.time() * 1000)}.gif"

                if context:
                    context.staged_image_bytes = resp.content
                    context.staged_image_filename = filename

                logger.info(f"[search_gif] Attached GIF '{title}' ({size_bytes:,} bytes) from {source}")
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
                logger.debug(f"[search_gif] Download error from {source}: {e}")

    return {
        "status": "download_failed",
        "query": query,
        "message": f"Candidate GIFs for '{query}' could not be downloaded."
    }

@tool_registry.register(
    name="edit_image",
    description=(
        "Transforms, modifies, stylizes, restyles, sketches, or redraws an image uploaded or replied to by the user.\n"
        "- prompt: Complete descriptive prompt specifying subject, art medium, textures, and lighting (e.g. 'clean fine art graphite pencil sketch of a character, black and white lineart, crosshatching shading, white background').\n"
        "- negative_prompt: Styles/artifacts to avoid (default: 'ugly, deformed, abstract, blurry, dark background, realistic skin, photographic, messy, distorted face').\n"
        "- strength: Transformation intensity (0.35 to 0.48 to retain original lines/shading; 0.52 to 0.65 for complete artistic redraw/anime/3D).\n"
        "- steps: Number of diffusion steps (default 6, recommended 5-8)."
    )
)
async def edit_image(
    prompt: str,
    negative_prompt: str = "ugly, deformed, abstract, blurry, low quality, dark background, realistic skin, photographic, messy, distorted face",
    strength: float = 0.52,
    steps: int = 6,
    guidance: float = 2.0,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not context or not context.input_image_bytes:
        return {
            "error": "No input image found. Ensure you are replying to an image or attaching an image file."
        }

    logger.info(f"[edit_image] Running local DreamShaper 8 LCM (strength={strength}, steps={steps}) for: '{prompt[:50]}'")

    try:
        out_bytes = await asyncio.to_thread(
            _run_dreamshaper_img2img_sync,
            image_bytes=context.input_image_bytes,
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            strength=strength,
            guidance=guidance
        )

        filename = f"edit_{int(time.time() * 1000)}.png"
        context.staged_image_bytes = out_bytes
        context.staged_image_filename = filename

        logger.info(f"[edit_image] Successfully rendered {len(out_bytes):,} bytes to {filename}")
        return {
            "status": "success",
            "prompt": prompt,
            "filename": filename,
            "strength": strength,
            "steps": steps,
            "model": "DreamShaper-8-LCM"
        }
    except Exception as e:
        logger.exception(f"[edit_image] Local diffusion failed: {e}")
        return {"error": f"Image editing failed: {str(e)}"}

@tool_registry.register(
    name="generate_image",
    description=(
        "Generates an original AI artwork or illustration from a text prompt from scratch.\n"
        "- prompt: Detailed description of the scene, character, lighting, and style.\n"
        "- width: Image width (default 1024, or 512).\n"
        "- height: Image height (default 1024, or 512)."
    )
)
async def generate_image(
    prompt: str,
    negative_prompt: str = "ugly, deformed, abstract, blurry, low quality, bad anatomy",
    width: int = 1024,
    height: int = 1024,
    steps: int = 6,
    guidance: float = 2.0,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    logger.info(f"[generate_image] Prompt: '{prompt[:50]}' | Resolution: {width}x{height}")

    seed = random.randint(1, 99999999)
    encoded_prompt = urllib.parse.quote(prompt.strip())
    image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={width}&height={height}&model=flux&nologo=true&seed={seed}"

    try:
        async with httpx.AsyncClient(timeout=14.0, follow_redirects=True) as client:
            resp = await client.get(image_url)
            if resp.status_code == 200 and len(resp.content) > 10000:
                filename = f"gen_{int(time.time() * 1000)}.png"
                if context:
                    context.staged_image_bytes = resp.content
                    context.staged_image_filename = filename

                logger.info(f"[generate_image] Pollinations Flux generated ({len(resp.content):,} bytes)")
                return {
                    "status": "generated",
                    "prompt": prompt,
                    "dimensions": f"{width}x{height}",
                    "filename": filename,
                    "model": "Pollinations-Flux",
                    "image_url": image_url
                }
    except Exception as cloud_err:
        logger.warning(f"[generate_image] Cloud Pollinations failed ({cloud_err}). Falling back to local DreamShaper...")

    try:
        out_bytes = await asyncio.to_thread(
            _run_dreamshaper_text2img_sync,
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=steps,
            guidance=guidance,
            width=min(width, 512),
            height=min(height, 512)
        )

        filename = f"gen_{int(time.time() * 1000)}.png"
        if context:
            context.staged_image_bytes = out_bytes
            context.staged_image_filename = filename

        logger.info(f"[generate_image] Fallback local image generated ({len(out_bytes):,} bytes)")
        return {
            "status": "generated",
            "prompt": prompt,
            "dimensions": f"{min(width, 512)}x{min(height, 512)}",
            "filename": filename,
            "model": "DreamShaper-8-LCM"
        }
    except Exception as local_err:
        logger.error(f"[generate_image] All image generation methods failed: {local_err}")
        return {"error": f"Image generation failed: {str(local_err)}"}