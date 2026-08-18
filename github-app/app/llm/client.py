import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional
import google.auth
from google.auth.transport.requests import Request
import httpx
from app.config import settings
from app.core.key_manager import key_manager

logger = logging.getLogger("priesty.llm")

REASONING_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]

ROUTING_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]


class LLMClient:
    """Supports both Google AI Studio (Key Rotation) and Google Cloud Vertex AI (Enterprise ADC)."""

    def __init__(self):
        self.provider = settings.LLM_PROVIDER
        if self.provider == "vertex":
            logger.info(
                "Initializing Enterprise Vertex AI Provider (ADC / Workload Identity)..."
            )
            self.credentials, self.project_id = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if settings.GCP_PROJECT_ID:
                self.project_id = settings.GCP_PROJECT_ID

    def _get_vertex_token(self) -> str:
        if not self.credentials.valid:
            self.credentials.refresh(Request())
        return self.credentials.token

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_tier: str = "reasoning",
        response_json: bool = False,
        temperature: float = 0.2,
    ) -> str:
        if self.provider == "vertex":
            return await self._generate_vertex(
                prompt, system_prompt, response_json, temperature
            )
        return await self._generate_gemini_studio(
            prompt, system_prompt, model_tier, response_json, temperature
        )

    async def _generate_vertex(
        self,
        prompt: str,
        system_prompt: Optional[str],
        response_json: bool,
        temperature: float,
    ) -> str:
        token = self._get_vertex_token()
        url = (
            f"https://{settings.GCP_REGION}-aiplatform.googleapis.com/v1/projects/"
            f"{self.project_id}/locations/{settings.GCP_REGION}/publishers/google/models/"
            f"{settings.VERTEX_MODEL}:generateContent"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        gen_config: Dict[str, Any] = {"temperature": temperature}
        if response_json:
            gen_config["responseMimeType"] = "application/json"

        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError("No candidates returned from Vertex AI.")
            content_parts = candidates[0].get("content", {}).get("parts", [])
            return "".join([p.get("text", "") for p in content_parts])

    async def _generate_gemini_studio(
        self,
        prompt: str,
        system_prompt: Optional[str],
        model_tier: str,
        response_json: bool,
        temperature: float,
    ) -> str:
        model_chain = REASONING_MODELS if model_tier == "reasoning" else ROUTING_MODELS

        for model in model_chain:
            for _ in range(max(1, len(key_manager.keys))):
                api_key = key_manager.get_available_key(model)
                if not api_key:
                    break

                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
                    gen_config: Dict[str, Any] = {"temperature": temperature}
                    if response_json:
                        gen_config["responseMimeType"] = "application/json"

                    payload: Dict[str, Any] = {
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": gen_config,
                    }
                    if system_prompt:
                        payload["systemInstruction"] = {
                            "parts": [{"text": system_prompt}]
                        }

                    async with httpx.AsyncClient(timeout=120.0) as client:
                        resp = await client.post(url, json=payload)
                        if resp.status_code == 429:
                            key_manager.mark_tpm_limit(api_key, cooldown_seconds=180)
                            continue
                        resp.raise_for_status()
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if candidates:
                            content_parts = (
                                candidates[0].get("content", {}).get("parts", [])
                            )
                            return "".join([p.get("text", "") for p in content_parts])

                except Exception as e:
                    logger.warning(f"Error on model '{model}': {e}. Falling back...")
                    continue

        raise RuntimeError("All Gemini API keys and fallback models exhausted.")

    async def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_tier: str = "reasoning",
    ) -> Dict[str, Any]:
        raw = await self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            model_tier=model_tier,
            response_json=True,
        )
        try:
            return json.loads(raw, strict=False)
        except Exception:
            sanitized = re.sub(
                r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", r"\1", raw, flags=re.DOTALL
            )
            return json.loads(sanitized, strict=False)


llm_client = LLMClient()
