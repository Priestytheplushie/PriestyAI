
import json
import re
import asyncio
import logging
from typing import Optional
from google.genai import types

logger = logging.getLogger("AgentRouter")

def extract_json_block(text: str) -> str:
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        return text[start:end+1]
    return text


def check_static_heuristics(prompt: str) -> Optional[dict]:
    prompt_clean = prompt.lower().strip()
    
    research_triggers = [
        "research", "deep research", "lookup", "look up", "find out", "search", 
        "investigate the web", "scrape", "compare prices", "analyze the market"
    ]
    if any(prompt_clean.startswith(t) or f" {t} " in f" {prompt_clean} " for t in research_triggers):
        return {
            "agent": "deep_research",
            "plan": "Initiating deep research protocols across public indexes to gather, synthesize, and compile report details."
        }
        
    diagnostics_triggers = [
        "audit", "channel", "user", "profile", "history", "member", 
        "server stats", "logs", "roles", "voice status"
    ]
    if any(prompt_clean.startswith(t) or f" {t} " in f" {prompt_clean} " for t in diagnostics_triggers):
        return {
            "agent": "react",
            "plan": "Inspecting available server channels, auditing role tables, and compiling member snapshot metrics."
        }
        
    return None


async def classify_agent_intent(bot, user_prompt: str) -> dict:
    heuristic_match = check_static_heuristics(user_prompt)
    if heuristic_match:
        logger.info(f"Heuristics hit! Bypassing API classification. Routed directly to: '{heuristic_match['agent']}'")
        return heuristic_match

    logger.info(f"Ambiguous query. Falling back to LLM intent classification: '{user_prompt[:30]}...'")
    
    system_instruction = (
        "You are an elite routing and classification orchestrator for a multi-agent system. "
        "Your task is to analyze the user's input and select the most appropriate Agent type.\n\n"
        "Agent Types:\n"
        "1. 'deep_research': Choose this if the user wants current information, news, comparative web lookups, "
        "comprehensive web research, parsing public web documentation, or compile analytical summaries gathered from websites.\n"
        "2. 'react': Choose this if the user is asking about local server configurations, diagnostics, user activity "
        "comparisons, message histories, channel lists, role metadata, or context snapshot audits.\n\n"
        "Guidelines:\n"
        "- Respond ONLY with a clean, standard JSON block. Do not write any explanations, markdown code blocks, or preamble.\n"
        "- Provide a 'plan' consisting of 2-3 highly professional, formal, yet easy-to-understand sentences explaining "
        "exactly what steps you will execute to complete their goal."
    )
    
    schema_prompt = (
        f"Classify this query and output a JSON matching this schema:\n"
        f"{{\n"
        f"  \"agent\": \"deep_research\" or \"react\",\n"
        f"  \"plan\": \"A customized explanation of the planned execution sequence.\"\n"
        f"}}\n\n"
        f"User Prompt: \"{user_prompt}\""
    )

    max_attempts = 4
    for attempt in range(max_attempts):
        output_text = ""
        try:
            response = await bot.chat_handler.client.aio.models.generate_content(
                model=bot.chat_handler.premium_model,
                contents=schema_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0,
                    response_mime_type="application/json"
                )
            )
            
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                output_text = "".join(part.text for part in response.candidates[0].content.parts if getattr(part, 'text', None)).strip()
                
            json_clean = extract_json_block(output_text)
            data = json.loads(json_clean)
            
            agent_type = data.get("agent", "deep_research").lower().strip()
            if agent_type not in ("deep_research", "react"):
                agent_type = "deep_research"
                
            return {
                "agent": agent_type,
                "plan": data.get("plan", "Executing standard information gather and compile protocols.")
            }
        except Exception as e:
            error_str = str(e).lower()
            logger.warning(
                f"Classification API attempt {attempt + 1} failed: {error_str}. "
                f"Raw output text was: '{output_text}'"
            )
            
            if attempt < max_attempts - 1:
                backoff_delay = 1.5 * (2 ** attempt)
                await asyncio.sleep(backoff_delay)

    logger.warning("All classification retries failed. Running safe fallback heuristics...")
    content_lower = user_prompt.lower()
    if any(kw in content_lower for kw in ["user", "channel", "role", "server", "history", "member", "audit", "profile", "log"]):
        return {
            "agent": "react",
            "plan": "Inspecting available server channels, auditing role tables, and compiling member snapshot metrics."
        }
    return {
        "agent": "deep_research",
        "plan": "Initiating deep research protocols across public indexes to gather, synthesize, and compile report details."
    }