import logging
from typing import Any
from google.genai import types
from tools.registry import tool_registry, ToolExecutionContext
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.ExpertTools")

EXPERT_SYSTEM_INSTRUCTION = """
You are the elite reasoning specialist for PriestyAI.
You have been invoked to solve a complex sub-problem, difficult math derivation, deep algorithmic debugging task, or architectural design.

STRICT FORMATTING DIRECTIVES:
1. NEVER output LaTeX notation ($ or $$ or \\frac or \\sqrt). Discord does not render LaTeX.
2. ALWAYS use pure Unicode math symbols:
   - √2, ∛x, a/b, x², y³, a² = 2b²
   - ∈, ∉, ⊂, ℤ, ℝ, ℚ, ℕ, ±, ∓, ≠, ≤, ≥, ≈, ≡, →, ⇒, ⟺, π, θ
3. Multi-line derivations should be formatted with standard text or formatted inside ```text code blocks.
4. Be direct, authoritative, rigorous, and step-by-step.
"""

@tool_registry.register(
    name="ask_expert",
    description=(
        "Escalates a difficult sub-problem, complex math proof, deep code debugging question, "
        "or high-level architecture design to a flagship high-reasoning expert model. "
        "Use this when you hit a reasoning wall or need advanced logical verification."
    )
)
async def ask_expert(
    question: str,
    context_details: str = "",
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    logger.info(f"[ask_expert] Invoking flagship reasoning expert for: '{question[:60]}...'")

    prompt_payload = f"Context Details:\n{context_details}\n\nTarget Problem:\n{question}"
    expert_model_candidates = ["gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemma-4-31b-it"]

    for model_name in expert_model_candidates:
        attempted_keys: set[int] = set()
        while True:
            client, key_idx, active_model = client_manager.get_client_for_model(
                model_name,
                exclude_keys=attempted_keys,
                fallback=False
            )
            if not client or key_idx in attempted_keys:
                break

            attempted_keys.add(key_idx)
            try:
                config = types.GenerateContentConfig(
                    system_instruction=EXPERT_SYSTEM_INSTRUCTION,
                    thinking_config=types.ThinkingConfig(
                        thinking_level="HIGH",
                        include_thoughts=True
                    ),
                    temperature=0.3
                )

                response = await client.aio.models.generate_content(
                    model=active_model,
                    contents=prompt_payload,
                    config=config
                )

                if response.text:
                    logger.info(f"[ask_expert] Expert solution generated on '{active_model}' (Key #{key_idx})")
                    return {
                        "status": "success",
                        "expert_model": active_model,
                        "solution": response.text.strip()
                    }

            except Exception as e:
                err_desc = str(e)
                client_manager.report_error(key_idx, active_model, e)
                logger.warning(f"Expert attempt error on {active_model} (Key #{key_idx}): {err_desc}")
                err_lower = err_desc.lower()
                if any(x in err_lower for x in ["503", "429", "500", "overloaded", "unavailable", "timeout"]):
                    continue
                break

    return {
        "status": "fallback",
        "error": "Reasoning expert models are currently busy. Proceed using best internal knowledge."
    }