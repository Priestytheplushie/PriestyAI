import os
import logging
import aiohttp
import urllib.parse
import asyncio
import base64
import random

logger = logging.getLogger("ImageGen")


class SafetyBlockError(Exception):
    pass


class RateLimitError(Exception):
    pass


STYLE_MODIFIERS = {
    "photorealistic": "professional photorealistic 8k portrait, cinematic lighting, sharp focus, dramatic shadows, volumetric atmosphere, octane render, highly detailed, realistic textures",
    "anime": "stunning anime illustration, vibrant colors, clean hand-drawn lineart, gentle daylight watercolor aesthetic, studio ghibli style, whimsical, highly detailed",
    "cyberpunk": "futuristic cyberpunk aesthetic, glowing neon lights, rain-slicked dark streets, high contrast, blue and magenta accent hues, volumetric smog, octane render, industrial design",
    "clay": "charming 3D toy claymation style, adorable plasticine sculpture, soft and smooth clay textures, warm cozy studio lighting, minimalist composition, blender render, cute",
    "watercolor": "beautiful classic watercolor wash painting, elegant textured canvas paper, soft flowing bleeding pigments, expressive brushstrokes, delicate, pastel color palette",
    "pixel": "detailed retro 16-bit pixel art, vibrant color palette, precise block textures, classic video game layout, clean grids, isometric perspective",
    "sketch": "hand-drawn graphite pencil sketch, fine cross-hatching detail, soft charcoal shading, textured canvas sketch paper, elegant monochrome line-art",
    "origami": "delicate folded papercraft illustration, crisp geometric paper creases, layered dimensional cutouts, soft ambient drop shadows, craft paper texture",
    "neon": "glowing vibrant neon sign design, glass tubes with electric gas light, high-contrast black backdrop, colorful ambient neon reflections, cinematic moody atmosphere",
    "oilpainting": "masterpiece oil painting on thick canvas, rich impasto brush strokes, oil paint texture and cracks, chiaroscuro lighting, classic fine art gallery style",
    "fantasy": "mythical fantasy digital painting, epic high-fantasy landscape, towering ancient structures, majestic lighting, ethereal atmospheric golden glow",
}


class ImageGenerator:
    def __init__(self):
        self.pollinations_url = "https://image.pollinations.ai/prompt/"

        self.pixazo_api_key = os.getenv("PIXAZO_API_KEY") or os.getenv("IMG_API_KEY")
        self.pixazo_flux_url = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData"
        self.pixazo_sdxl_url = "https://gateway.pixazo.ai/getImage/v1/getSDXLImage"

        self.aihorde_key = os.getenv("AI_HORDE_KEY", "0000000000")
        self.aihorde_post_url = "https://aihorde.net/api/v2/generate/async"
        self.aihorde_check_url = "https://aihorde.net/api/v2/generate/check/"
        self.aihorde_status_url = "https://aihorde.net/api/v2/generate/status/"

        self.pollinations_cooldown_until = 0.0

    async def generate(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
        seed: int = None,
        style_key: str = None,
        base_image_bytes: bytes = None,
        strength: str = "0.6",
    ) -> bytes:
        styled_prompt = prompt
        if style_key and style_key.lower() in STYLE_MODIFIERS:
            modifier = STYLE_MODIFIERS[style_key.lower()]
            styled_prompt = f"{prompt}, {modifier}"

        errors = []

        try:
            logger.info("Attempting Image Generation [Attempt 1/4]: Pollinations AI...")
            img_bytes = await self._generate_pollinations(
                styled_prompt, width, height, seed
            )
            if img_bytes:
                logger.info(
                    "Success: Generation completed via Pollinations (Attempt 1)."
                )
                return img_bytes
        except Exception as e:
            logger.warning(f"Pollinations (Attempt 1) failed: {e}")
            errors.append(f"Pollinations Attempt 1: {e}")

        if self.pixazo_api_key:
            try:
                logger.info(
                    "Attempting Image Generation [Attempt 2/4]: Pixazo API Gateway..."
                )
                img_bytes = await self._generate_pixazo(
                    styled_prompt, width, height, seed, style_key
                )
                if img_bytes:
                    logger.info("Success: Generation completed via Pixazo (Attempt 2).")
                    return img_bytes
            except Exception as e:
                logger.warning(f"Pixazo (Attempt 2) failed: {e}")
                errors.append(f"Pixazo Attempt 2: {e}")
        else:
            logger.warning(
                "Pixazo Attempt skipped: PIXAZO_API_KEY / IMG_API_KEY environment variable is not defined."
            )
            errors.append("Pixazo Attempt: API Key missing")

        try:
            logger.info(
                "Attempting Image Generation [Attempt 3/4]: Pollinations AI (Retry)..."
            )
            img_bytes = await self._generate_pollinations(
                styled_prompt, width, height, seed
            )
            if img_bytes:
                logger.info(
                    "Success: Generation completed via Pollinations (Attempt 3)."
                )
                return img_bytes
        except Exception as e:
            logger.warning(f"Pollinations (Attempt 3) failed: {e}")
            errors.append(f"Pollinations Attempt 3: {e}")

        try:
            logger.info(
                "Attempting Image Generation [Attempt 4/4]: AI Horde (Asynchronous Polling)..."
            )
            img_bytes = await self._generate_aihorde(styled_prompt, width, height, seed)
            if img_bytes:
                logger.info("Success: Generation completed via AI Horde (Attempt 4).")
                return img_bytes
        except Exception as e:
            logger.warning(f"AI Horde (Attempt 4) failed: {e}")
            errors.append(f"AI Horde Attempt 4: {e}")

        raise ValueError(
            "Image generation failed across all fallback backends: "
            + " | ".join(errors)
        )

    async def _generate_pollinations(
        self, styled_prompt: str, width: int, height: int, seed: int
    ) -> bytes:

        now = asyncio.get_event_loop().time()
        if now < self.pollinations_cooldown_until:
            remaining = int(self.pollinations_cooldown_until - now)
            logger.info(
                f"Skipping Pollinations: Active cooldown is in place ({remaining}s remaining)."
            )
            raise RateLimitError("Pollinations is under active rate-limit cooldown.")

        encoded_prompt = urllib.parse.quote(styled_prompt)
        params = {
            "width": str(width),
            "height": str(height),
            "nologo": "true",
            "model": "flux",
        }
        if seed is not None:
            params["seed"] = str(seed)

        query_string = urllib.parse.urlencode(params)
        request_url = f"{self.pollinations_url}{encoded_prompt}?{query_string}"

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(request_url, timeout=30, ssl=False) as response:
                if response.status == 200:
                    content = await response.read()
                    if content:
                        return content

                if response.status in (402, 429):
                    self.pollinations_cooldown_until = (
                        asyncio.get_event_loop().time() + 300.0
                    )
                    logger.warning(
                        f"Pollinations returned HTTP {response.status}. Triggering 5-minute lockout timer."
                    )

                raise ValueError(f"HTTP Status {response.status}")

    async def _generate_pixazo(
        self, styled_prompt: str, width: int, height: int, seed: int, style_key: str
    ) -> bytes:
        headers = {
            "Content-Type": "application/json",
            "Ocp-Apim-Subscription-Key": self.pixazo_api_key,
        }

        flux_styles = ["photorealistic", "neon", "oilpainting", "cyberpunk", "fantasy"]
        is_flux = (style_key is None) or (style_key.lower() in flux_styles)

        if is_flux:

            url = self.pixazo_flux_url
            payload = {
                "prompt": styled_prompt,
                "num_steps": 4,
                "seed": seed if seed is not None else random.randint(1, 10000000),
                "height": height,
                "width": width,
            }
        else:

            url = self.pixazo_sdxl_url
            payload = {
                "prompt": styled_prompt,
                "negativePrompt": "low-quality, blurry, visual artifacts, deformed features",
                "width": width,
                "height": height,
                "num_steps": 20,
                "guidance_scale": 5,
                "seed": seed if seed is not None else random.randint(1, 10000000),
            }

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                url, json=payload, headers=headers, timeout=40, ssl=False
            ) as response:
                if response.status != 200:
                    text_err = await response.text()
                    raise ValueError(f"HTTP Status {response.status}: {text_err}")

                resp_json = await response.json()
                img_url = resp_json.get("output") or resp_json.get("url")
                if not img_url and isinstance(resp_json.get("output"), list):
                    img_url = resp_json["output"][0]

                if not img_url:
                    raise ValueError(
                        f"Invalid API response payload structure: {resp_json}"
                    )

                async with session.get(img_url, timeout=30, ssl=False) as img_resp:
                    if img_resp.status == 200:
                        return await img_resp.read()
                    raise ValueError(
                        f"Failed to download image asset from {img_url}. Status: {img_resp.status}"
                    )

    async def _generate_aihorde(
        self, styled_prompt: str, width: int, height: int, seed: int
    ) -> bytes:
        headers = {"Content-Type": "application/json", "apikey": self.aihorde_key}

        payload = {
            "prompt": styled_prompt,
            "params": {
                "n": 1,
                "width": width,
                "height": height,
                "steps": 20,
                "seed": seed if seed is not None else random.randint(1, 10000000),
            },
        }

        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:

            async with session.post(
                self.aihorde_post_url,
                json=payload,
                headers=headers,
                timeout=25,
                ssl=False,
            ) as response:
                if response.status != 202:
                    text_err = await response.text()
                    raise ValueError(
                        f"Job dispatch failed with HTTP Status {response.status}: {text_err}"
                    )
                resp_json = await response.json()
                job_id = resp_json.get("id")
                if not job_id:
                    raise ValueError(
                        f"Missing job tracking identifier from AI Horde: {resp_json}"
                    )

            completed_url = None
            for check_idx in range(16):
                await asyncio.sleep(2.5)
                check_endpoint = f"{self.aihorde_check_url}{job_id}"
                async with session.get(
                    check_endpoint, timeout=15, ssl=False
                ) as check_resp:
                    if check_resp.status == 200:
                        status_json = await check_resp.json()
                        if status_json.get("done") is True:
                            completed_url = f"{self.aihorde_status_url}{job_id}"
                            break
                    else:
                        logger.warning(
                            f"AI Horde polling state warning on check {check_idx+1}: HTTP {check_resp.status}"
                        )

            if not completed_url:
                raise TimeoutError(
                    "AI Horde queues timed out waiting for a volunteer GPU to claim execution."
                )

            async with session.get(completed_url, timeout=15, ssl=False) as status_resp:
                if status_resp.status != 200:
                    raise ValueError(
                        f"Failed to fetch completed status from AI Horde: HTTP {status_resp.status}"
                    )
                final_json = await status_resp.json()
                generations = final_json.get("generations")
                if (
                    not generations
                    or not isinstance(generations, list)
                    or len(generations) == 0
                ):
                    raise ValueError(
                        f"AI Horde returned invalid generation data structures: {final_json}"
                    )

                download_url = generations[0].get("img")
                if not download_url:
                    raise ValueError(
                        f"AI Horde status returned empty image link: {generations[0]}"
                    )

            async with session.get(
                download_url, timeout=20, ssl=False
            ) as download_resp:
                if download_resp.status == 200:
                    return await download_resp.read()
                raise ValueError(
                    f"Failed to download image payload from AI Horde CDN {download_url}. Status: {download_resp.status}"
                )
