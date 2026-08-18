import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional
import httpx
from app.core.key_manager import key_manager

logger = logging.getLogger("priesty.llm")

REASONING_MODELS = [
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemma-4-31b-it",
]

ROUTING_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-3.5-flash",
]


class LLMClient:
    """Async client for calling Gemini API with key rotation, model cascading, and relaxed JSON parsing."""

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_tier: str = "reasoning",
        response_json: bool = False,
        temperature: float = 0.2,
    ) -> str:
        model_chain = REASONING_MODELS if model_tier == "reasoning" else ROUTING_MODELS

        for model in model_chain:
            consecutive_server_errors = 0

            for _ in range(max(1, len(key_manager.keys))):
                api_key = key_manager.get_available_key(model)
                if not api_key:
                    break

                try:
                    result = await self._call_gemini_with_backoff(
                        api_key=api_key,
                        model=model,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        response_json=response_json,
                        temperature=temperature,
                    )
                    return result

                except httpx.HTTPStatusError as e:
                    status = e.response.status_code
                    body = e.response.text.lower()

                    if status == 429:
                        if "quota" in body or "resource_exhausted" in body:
                            key_manager.mark_rpd_limit(api_key, model)
                        else:
                            key_manager.mark_tpm_limit(api_key, cooldown_seconds=180)
                        continue

                    elif status in (500, 503):
                        consecutive_server_errors += 1
                        logger.warning(
                            f"{model} on key (...{api_key[-6:]}) returned {status} "
                            f"({consecutive_server_errors}/2)."
                        )

                        if consecutive_server_errors >= 2:
                            logger.warning(
                                f"Model '{model}' is overloaded or down at Google. Cascading to next model..."
                            )
                            break
                        continue

                    elif status == 404:
                        logger.warning(
                            f"Model '{model}' returned 404 Not Found. Skipping model..."
                        )
                        break

                    else:
                        logger.error(f"Gemini API returned {status}: {e.response.text}")
                        break

                except Exception as e:
                    logger.warning(f"Error on model '{model}': {e}. Trying fallback...")
                    continue

        raise RuntimeError("All Gemini API keys and model fallbacks exhausted.")

    async def _call_gemini_with_backoff(
        self,
        api_key: str,
        model: str,
        prompt: str,
        system_prompt: Optional[str],
        response_json: bool,
        temperature: float,
    ) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        gen_config: Dict[str, Any] = {"temperature": temperature}
        if response_json:
            gen_config["responseMimeType"] = "application/json"

        payload: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_config,
        }

        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        backoffs = [1, 2]
        for attempt, delay in enumerate(backoffs):
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code in (500, 503):
                    if attempt < len(backoffs) - 1:
                        await asyncio.sleep(delay)
                        continue
                    else:
                        resp.raise_for_status()

                resp.raise_for_status()
                data = resp.json()

                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("No candidates returned from Gemini API")

                content_parts = candidates[0].get("content", {}).get("parts", [])
                return "".join([p.get("text", "") for p in content_parts])

        raise RuntimeError(f"Model {model} failed after retries.")

    async def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_tier: str = "reasoning",
    ) -> Dict[str, Any]:
        """Generates and safely parses JSON, supporting multi-line strings and unescaped newlines."""
        raw = await self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            model_tier=model_tier,
            response_json=True,
        )

        try:
            return json.loads(raw, strict=False)
        except Exception:
            pass

        match = re.search(r"```(?:json)?\s*(\{.*\}|\[.*\])\s*```", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1), strict=False)
            except Exception:
                pass

        sanitized = re.sub(r"[\x00-\x09\x0b-\x1f\x7f-\x9f]", "", raw)
        try:
            return json.loads(sanitized, strict=False)
        except Exception:
            raise ValueError(f"Failed to parse LLM JSON: {raw}")


llm_client = LLMClient()
