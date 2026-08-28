import io
import re
import time
import json
import random
import base64
import urllib.parse
from typing import Any
import logging
import asyncio
import httpx
import discord
from discord import app_commands
from agent.constants import BETA_EMOJI
from config.settings import (
    DISCORD_TOKEN,
    GROQ_API_KEY,
    OPENROUTER_API_KEY,
    OLLAMA_URL,
    FLAGSHIP_MODELS,
    LITE_MODELS,
    GEMMA_MODELS
)
from core.client_manager import client_manager
from core.moderation import (
    check_moderation,
    log_moderation_violation,
    is_user_banned,
    ban_user,
    generate_friendly_refusal
)
from ui.onboarding_views import BannedUserNoticeView

logger = logging.getLogger("PriestyAI.Commands.Generate")

DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/jpeg,image/png,image/gif,*/*;q=0.8",
}

def clean_model_name(raw_name: str, model_id: str) -> str:
    name = raw_name or model_id.split("/")[-1]
    
    name = re.sub(r'(?i)\(?\s*free\s*\)?|:free|\[.*?\]', '', name)
    
    name = re.sub(r'^(?:Meta|Google|Qwen|Mistral|DeepSeek|Anthropic|OpenAI):\s*', '', name, flags=re.IGNORECASE)
    
    name = name.replace("instruct", "").replace("Instruct", "").replace("-", " ").strip()
    
    if "deepseek r1 distill llama 70b" in name.lower() or "deepseek r1 distill" in name.lower():
        name = "DeepSeek R1 Distill 70B"
    elif "deepseek r1" in name.lower():
        name = "DeepSeek R1"
    elif "llama 3.3 70b" in name.lower():
        name = "Llama 3.3 70B"
    elif "llama 3.1 8b" in name.lower():
        name = "Llama 3.1 8B"
    elif "qwen 2.5 72b" in name.lower() or "qwen2.5 72b" in name.lower():
        name = "Qwen 2.5 72B"
    elif "qwen 2.5 coder 32b" in name.lower() or "qwen2.5 coder 32b" in name.lower():
        name = "Qwen 2.5 Coder 32B"
    elif "mistral 7b" in name.lower():
        name = "Mistral 7B"
    elif "mixtral 8x7b" in name.lower():
        name = "Mixtral 8x7B"
    elif "gemma 2 9b" in name.lower():
        name = "Gemma 2 9B"

    name = re.sub(r'\s+', ' ', name).strip()
    return name or model_id.split("/")[-1].title()


def generate_waveform_b64(audio_bytes: bytes, num_samples: int = 128) -> str:
    if len(audio_bytes) < num_samples:
        return base64.b64encode(bytes([128] * 64)).decode("utf-8")
    
    step = max(1, len(audio_bytes) // num_samples)
    samples = []
    for i in range(num_samples):
        chunk = audio_bytes[i * step : (i + 1) * step]
        if chunk:
            avg = sum(chunk) // len(chunk)
            val = min(255, max(16, int(abs(avg - 128) * 2 + 32)))
            samples.append(val)
        else:
            samples.append(64)
    return base64.b64encode(bytes(samples)).decode("utf-8")

async def mp3_to_ogg_opus(mp3_bytes: bytes) -> bytes | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "48k", "-f", "ogg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        ogg_bytes, _ = await asyncio.wait_for(proc.communicate(input=mp3_bytes), timeout=8.0)
        if proc.returncode == 0 and len(ogg_bytes) > 100:
            return ogg_bytes
    except Exception as e:
        logger.debug(f"[VoiceMessage] ffmpeg conversion skipped/unavailable: {e}")
    return None

async def send_native_discord_voice_message(
    channel_id: int | str,
    ogg_bytes: bytes,
    duration_secs: float
) -> bool:
    file_size = len(ogg_bytes)
    waveform_b64 = generate_waveform_b64(ogg_bytes, num_samples=128)
    auth_headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            req_data = {
                "files": [{
                    "filename": "voice-message.ogg",
                    "file_size": file_size,
                    "id": "0"
                }]
            }
            resp = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/attachments",
                headers=auth_headers,
                json=req_data
            )
            if resp.status_code != 200:
                logger.warning(f"[VoiceMessage] Attachment URL request failed ({resp.status_code}): {resp.text}")
                return False

            up_info = resp.json()["attachments"][0]
            upload_url = up_info["upload_url"]
            upload_filename = up_info["upload_filename"]

            up_resp = await client.put(
                upload_url,
                content=ogg_bytes,
                headers={"Content-Type": "audio/ogg"}
            )
            if up_resp.status_code not in (200, 204):
                logger.warning(f"[VoiceMessage] Raw upload failed ({up_resp.status_code}): {up_resp.text}")
                return False

            msg_payload = {
                "flags": 8192,
                "attachments": [{
                    "id": "0",
                    "filename": "voice-message.ogg",
                    "uploaded_filename": upload_filename,
                    "duration_secs": max(1.0, round(duration_secs, 1)),
                    "waveform": waveform_b64
                }]
            }
            post_resp = await client.post(
                f"https://discord.com/api/v10/channels/{channel_id}/messages",
                headers=auth_headers,
                json=msg_payload
            )
            return post_resp.status_code in (200, 201)

    except Exception as e:
        logger.warning(f"[VoiceMessage] Native send failed: {e}")
        return False


class ModelCatalogEngine:
    def __init__(self):
        self.text_models: dict[str, dict[str, Any]] = {}
        self.image_models: dict[str, dict[str, Any]] = {}
        self.audio_models: dict[str, dict[str, Any]] = {}
        self.last_refresh_time: float = 0.0
        self._refresh_lock = asyncio.Lock()

    async def ensure_initialized(self):
        if not self.text_models or (time.time() - self.last_refresh_time) > 3600:
            asyncio.create_task(self.refresh_catalog())

    async def refresh_catalog(self):
        if self._refresh_lock.locked():
            return

        async with self._refresh_lock:
            logger.info("[ModelCatalog] Dynamically indexing free multi-provider catalogs...")
            new_text: dict[str, dict[str, Any]] = {}
            new_image: dict[str, dict[str, Any]] = {}
            new_audio: dict[str, dict[str, Any]] = {}

            all_google_models = FLAGSHIP_MODELS + LITE_MODELS + GEMMA_MODELS
            for g_id in all_google_models:
                clean_name = g_id.replace("-", " ").title()
                if "gemini" in g_id.lower():
                    clean_name = clean_name.replace("Gemini", "Gemini ")
                    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
                elif "gemma" in g_id.lower():
                    if "31b" in g_id.lower():
                        clean_name = "Gemma 4 31B"
                    elif "26b" in g_id.lower() or "a4b" in g_id.lower():
                        clean_name = "Gemma 4 26B MoE"
                
                new_text[f"google/{g_id}"] = {
                    "id": g_id,
                    "display_name": clean_name,
                    "provider": "google",
                    "type": "text"
                }

            if GROQ_API_KEY:
                try:
                    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
                    async with httpx.AsyncClient(timeout=6.0) as client:
                        resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
                        if resp.status_code == 200:
                            g_data = resp.json().get("data", [])
                            for m in g_data:
                                m_id = m.get("id", "")
                                if any(x in m_id.lower() for x in ["whisper", "guard", "embedding", "audio"]):
                                    continue
                                
                                clean_name = clean_model_name("", m_id)
                                key = f"groq/{m_id}"
                                new_text[key] = {
                                    "id": m_id,
                                    "display_name": clean_name,
                                    "provider": "groq",
                                    "type": "text"
                                }
                except Exception as e:
                    logger.debug(f"[ModelCatalog] Groq discovery skipped: {e}")

            if OPENROUTER_API_KEY:
                try:
                    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}
                    async with httpx.AsyncClient(timeout=7.0) as client:
                        resp = await client.get("https://openrouter.ai/api/v1/models", headers=headers)
                        if resp.status_code == 200:
                            or_data = resp.json().get("data", [])
                            for m in or_data:
                                m_id = m.get("id", "")
                                pricing = m.get("pricing", {})
                                
                                p_prompt = float(pricing.get("prompt", 1.0))
                                p_comp = float(pricing.get("completion", 1.0))
                                is_free = m_id.endswith(":free") or (p_prompt == 0.0 and p_comp == 0.0)

                                if not is_free:
                                    continue

                                clean_name = clean_model_name(m.get("name", ""), m_id)
                                key = f"openrouter/{m_id}"
                                
                                if any(e["display_name"].lower() == clean_name.lower() for e in new_text.values()):
                                    clean_name = f"{clean_name} (OpenRouter)"

                                new_text[key] = {
                                    "id": m_id,
                                    "display_name": clean_name,
                                    "provider": "openrouter",
                                    "type": "text"
                                }
                except Exception as e:
                    logger.debug(f"[ModelCatalog] OpenRouter discovery skipped: {e}")

            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    resp = await client.get(f"{OLLAMA_URL}/api/tags")
                    if resp.status_code == 200:
                        o_models = resp.json().get("models", [])
                        for om in o_models:
                            om_name = om.get("name", "")
                            if om_name:
                                key = f"ollama/{om_name}"
                                new_text[key] = {
                                    "id": om_name,
                                    "display_name": f"{om_name} (Local)",
                                    "provider": "ollama",
                                    "type": "text"
                                }
            except Exception:
                pass

            image_catalog_presets = [
                ("flux", "FLUX.1-schnell"),
                ("flux-realism", "FLUX Realism"),
                ("flux-anime", "FLUX Anime"),
                ("flux-3d", "FLUX 3D"),
                ("dreamshaper", "DreamShaper 8"),
                ("turbo", "SDXL Turbo")
            ]
            for im_id, im_name in image_catalog_presets:
                key = f"pollinations/{im_id}"
                new_image[key] = {
                    "id": im_id,
                    "display_name": im_name,
                    "provider": "pollinations_image",
                    "type": "image"
                }

            try:
                import edge_tts
                voices = await edge_tts.list_voices()
                for v in voices:
                    v_id = v.get("ShortName", "")
                    if not v_id:
                        continue
                    
                    locale = v.get("Locale", "")
                    gender = v.get("Gender", "")
                    
                    display_name = f"{v_id} ({locale} - {gender})"
                    key = f"edgetts/{v_id}"
                    
                    new_audio[key] = {
                        "id": v_id,
                        "display_name": display_name,
                        "provider": "edge_tts",
                        "type": "audio"
                    }
            except Exception as e:
                logger.error(f"[ModelCatalog] Edge-TTS voice discovery failed: {e}")

            self.text_models = new_text
            self.image_models = new_image
            self.audio_models = new_audio
            self.last_refresh_time = time.time()
            logger.info(f"[ModelCatalog] Indexed {len(new_text)} free text models, {len(new_image)} image models, and {len(new_audio)} voice models.")

    def get_choices(self, gen_type: str, query: str) -> list[app_commands.Choice[str]]:
        t_clean = gen_type.lower().strip()
        if t_clean == "image":
            target_dict = self.image_models
        elif t_clean == "audio":
            target_dict = self.audio_models
        else:
            target_dict = self.text_models

        q_lower = query.lower().strip()
        matched = []
        for key, entry in target_dict.items():
            name = entry["display_name"]
            if not q_lower or q_lower in name.lower() or q_lower in key.lower():
                matched.append(app_commands.Choice(name=name[:100], value=key[:100]))
                if len(matched) >= 25:
                    break
        return matched

    def is_allowed(self, model_key: str, gen_type: str) -> bool:
        t_clean = gen_type.lower().strip()
        if t_clean == "image":
            target_dict = self.image_models
        elif t_clean == "audio":
            target_dict = self.audio_models
        else:
            target_dict = self.text_models
        return model_key in target_dict

    def get_model_entry(self, model_key: str, gen_type: str) -> dict[str, Any] | None:
        t_clean = gen_type.lower().strip()
        if t_clean == "image":
            target_dict = self.image_models
        elif t_clean == "audio":
            target_dict = self.audio_models
        else:
            target_dict = self.text_models
        return target_dict.get(model_key)

model_catalog = ModelCatalogEngine()


async def model_autocomplete_handler(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    await model_catalog.ensure_initialized()
    chosen_type = interaction.namespace.type or "Text"
    return model_catalog.get_choices(chosen_type, current)

def setup_generate_commands(tree: app_commands.CommandTree):

    @tree.command(name="generate", description="Directly generate raw text, voice messages, or images using top AI models")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.checks.cooldown(1, 6.0, key=lambda i: (i.guild_id, i.user.id))
    @app_commands.describe(
        type="Choose between Text, Audio (Voice Message), or Image generation",
        model="Select the model/voice backend to run your prompt",
        prompt="The text or description to generate"
    )
    @app_commands.choices(type=[
        app_commands.Choice(name="Text", value="Text"),
        app_commands.Choice(name="Audio", value="Audio"),
        app_commands.Choice(name="Image", value="Image")
    ])
    @app_commands.autocomplete(model=model_autocomplete_handler)
    async def generate_command(
        interaction: discord.Interaction,
        type: str,
        model: str,
        prompt: str
    ):
        if is_user_banned(interaction.user.id):
            ban_view = BannedUserNoticeView(author=interaction.user)
            await interaction.response.send_message(view=ban_view, ephemeral=True)
            return

        await model_catalog.ensure_initialized()
        model_entry = model_catalog.get_model_entry(model, type)

        if not model_entry:
            t_clean = type.lower().strip()
            target_dict = model_catalog.image_models if t_clean == "image" else (model_catalog.audio_models if t_clean == "audio" else model_catalog.text_models)
            for k, entry in target_dict.items():
                if entry["display_name"].lower() == model.lower() or k.lower() == model.lower():
                    model_entry = entry
                    break

        if not model_entry:
            await interaction.response.send_message(content="❌ This model is not allowed.", ephemeral=True)
            return

        is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(prompt)
        if is_flagged:
            log_moderation_violation(interaction.user.id, interaction.guild_id, flagged_cats, score)
            if is_zero_tolerance:
                ban_user(interaction.user.id, reason=f"Zero-tolerance violation: {', '.join(flagged_cats)}")
                ban_view = BannedUserNoticeView(author=interaction.user)
                await interaction.response.send_message(view=ban_view, ephemeral=True)
                return

            refusal_text = await generate_friendly_refusal(flagged_cats)
            await interaction.response.send_message(content=refusal_text, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=False)
        start_time = time.time()

        provider = model_entry.get("provider")
        raw_model_id = model_entry.get("id")
        display_label = model_entry.get("display_name", model)

        if type.lower() == "image":
            seed = random.randint(1, 99999999)
            encoded_prompt = urllib.parse.quote(prompt.strip())
            image_url = (
                f"https://image.pollinations.ai/prompt/{encoded_prompt}?"
                f"width=1024&height=1024&model={raw_model_id}&nologo=true&seed={seed}"
            )

            try:
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    resp = await client.get(image_url, headers=DOWNLOAD_HEADERS)
                    elapsed = max(0.1, time.time() - start_time)

                    if resp.status_code == 200 and len(resp.content) > 5000:
                        file_obj = discord.File(io.BytesIO(resp.content), filename=f"generated_{int(start_time)}.png")
                        footer_text = f"> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"
                        await interaction.followup.send(content=footer_text, file=file_obj)
                        return
                    else:
                        await interaction.followup.send(
                            content=f"⚠️ Image generation failed ({resp.status_code}). Please try a different prompt."
                        )
                        return
            except Exception as e:
                logger.error(f"[/generate image error] {e}")
                await interaction.followup.send(content=f"⚠️ Generation error: `{e}`")
                return

        elif type.lower() == "audio":
            try:
                import edge_tts
                voice_id = raw_model_id or "en-US-ChristopherNeural"
                communicate = edge_tts.Communicate(prompt.strip()[:1500], voice=voice_id)

                audio_buffer = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        audio_buffer.write(chunk["data"])

                mp3_bytes = audio_buffer.getvalue()
                elapsed = max(0.1, time.time() - start_time)

                if len(mp3_bytes) > 100:
                    approx_duration = max(1.0, len(mp3_bytes) / 16000.0)

                    ogg_bytes = await mp3_to_ogg_opus(mp3_bytes)
                    voice_sent = False

                    if ogg_bytes and interaction.channel_id:
                        voice_sent = await send_native_discord_voice_message(
                            channel_id=interaction.channel_id,
                            ogg_bytes=ogg_bytes,
                            duration_secs=approx_duration
                        )

                    if voice_sent:
                        try:
                            await interaction.delete_original_response()
                        except Exception:
                            pass
                        return

                    audio_buffer.seek(0)
                    file_obj = discord.File(audio_buffer, filename="voice-message.mp3")
                    footer_text = f"> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"
                    await interaction.followup.send(content=footer_text, file=file_obj)
                    return
                else:
                    await interaction.followup.send(content="⚠️ Failed to synthesize audio.")
                    return
            except Exception as e:
                logger.error(f"[/generate audio error] {e}")
                await interaction.followup.send(content=f"⚠️ Audio generation error: `{e}`")
                return

        else:
            if provider == "groq":
                if not GROQ_API_KEY:
                    await interaction.followup.send(content="❌ This model is not allowed.")
                    return

                try:
                    payload = {
                        "model": raw_model_id,
                        "messages": [{"role": "user", "content": prompt.strip()}],
                        "temperature": 0.7
                    }
                    headers = {
                        "Authorization": f"Bearer {GROQ_API_KEY}",
                        "Content-Type": "application/json"
                    }
                    async with httpx.AsyncClient(timeout=35.0) as client:
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers=headers,
                            json=payload
                        )
                        elapsed = max(0.1, time.time() - start_time)

                        if resp.status_code == 200:
                            data = resp.json()
                            result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                            footer = f"\n\n> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"

                            if len(result_text) + len(footer) <= 2000:
                                await interaction.followup.send(content=f"{result_text}{footer}")
                            else:
                                cutoff = 2000 - len(footer) - 5
                                await interaction.followup.send(content=f"{result_text[:cutoff]}...{footer}")
                            return
                        else:
                            await interaction.followup.send(content=f"⚠️ Provider returned HTTP {resp.status_code}.")
                            return
                except Exception as e:
                    logger.error(f"[/generate text Groq error] {e}")
                    await interaction.followup.send(content=f"⚠️ Generation error: `{e}`")
                    return

            elif provider == "openrouter":
                if not OPENROUTER_API_KEY:
                    await interaction.followup.send(content="❌ This model is not allowed.")
                    return

                if not raw_model_id.endswith(":free") and f"openrouter/{raw_model_id}" not in model_catalog.text_models:
                    await interaction.followup.send(content="❌ This model is not allowed.")
                    return

                try:
                    payload = {
                        "model": raw_model_id,
                        "messages": [{"role": "user", "content": prompt.strip()}],
                        "temperature": 0.7
                    }
                    headers = {
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "HTTP-Referer": "https://github.com/PriestyAI",
                        "X-Title": "PriestyAI Discord",
                        "Content-Type": "application/json"
                    }
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await client.post(
                            "https://openrouter.ai/api/v1/chat/completions",
                            headers=headers,
                            json=payload
                        )
                        elapsed = max(0.1, time.time() - start_time)

                        if resp.status_code == 200:
                            data = resp.json()
                            result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                            footer = f"\n\n> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"

                            if len(result_text) + len(footer) <= 2000:
                                await interaction.followup.send(content=f"{result_text}{footer}")
                            else:
                                cutoff = 2000 - len(footer) - 5
                                await interaction.followup.send(content=f"{result_text[:cutoff]}...{footer}")
                            return
                        else:
                            await interaction.followup.send(content=f"⚠️ Provider returned HTTP {resp.status_code}.")
                            return
                except Exception as e:
                    logger.error(f"[/generate text OpenRouter error] {e}")
                    await interaction.followup.send(content=f"⚠️ Generation error: `{e}`")
                    return

            elif provider == "ollama":
                try:
                    payload = {
                        "model": raw_model_id,
                        "messages": [{"role": "user", "content": prompt.strip()}],
                        "stream": False
                    }
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        resp = await client.post(f"{OLLAMA_URL}/v1/chat/completions", json=payload)
                        elapsed = max(0.1, time.time() - start_time)

                        if resp.status_code == 200:
                            data = resp.json()
                            result_text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                            footer = f"\n\n> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"

                            if len(result_text) + len(footer) <= 2000:
                                await interaction.followup.send(content=f"{result_text}{footer}")
                            else:
                                cutoff = 2000 - len(footer) - 5
                                await interaction.followup.send(content=f"{result_text[:cutoff]}...{footer}")
                            return
                        else:
                            await interaction.followup.send(content=f"⚠️ Local Ollama returned HTTP {resp.status_code}.")
                            return
                except Exception as e:
                    logger.error(f"[/generate text Ollama error] {e}")
                    await interaction.followup.send(content=f"⚠️ Local Ollama connection error: `{e}`")
                    return

            elif provider == "google":
                client, key_idx, active_model = client_manager.get_client_for_model(raw_model_id)
                if not client:
                    await interaction.followup.send(content="⚠️ Gemini service is currently busy. Please try again.")
                    return

                try:
                    res = await client.aio.models.generate_content(
                        model=active_model,
                        contents=prompt.strip()
                    )
                    elapsed = max(0.1, time.time() - start_time)
                    response_text = res.text.strip() if (res and res.text) else "*No response generated.*"
                    
                    footer = f"\n\n> Took {elapsed:.1f}s • {display_label} • {BETA_EMOJI}"
                    if len(response_text) + len(footer) <= 2000:
                        await interaction.followup.send(content=f"{response_text}{footer}")
                    else:
                        cutoff = 2000 - len(footer) - 5
                        await interaction.followup.send(content=f"{response_text[:cutoff]}...{footer}")
                    return
                except Exception as e:
                    logger.error(f"[/generate text Gemini error] {e}")
                    await interaction.followup.send(content=f"⚠️ Gemini error: `{e}`")
                    return

            else:
                await interaction.followup.send(content="❌ This model is not allowed.")

    @generate_command.error
    async def generate_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    content=f"Hold on! You can use `/generate` again in {error.retry_after:.1f}s.",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    content=f"Hold on! You can use `/generate` again in {error.retry_after:.1f}s.",
                    ephemeral=True
                )
        else:
            logger.error(f"Generate command error: {error}")
            if not interaction.response.is_done():
                await interaction.response.send_message(content="⚠️ An error occurred while executing the command.", ephemeral=True)
            else:
                await interaction.followup.send(content="⚠️ An error occurred while executing the command.", ephemeral=True)