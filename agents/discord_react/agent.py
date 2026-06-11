
import discord
import logging
import asyncio
import json
import re
import os
from typing import Optional, List, Dict, Any
from google.genai import types
from google.genai.errors import ServerError

from agents.base_agent import BaseAgentSession
from agents.discord_react.views import AgentContinuationView, AgentErrorView, AgentStepButton
from agents.discord_react.tools import run_local_agent_tool

from tools.message_builder.mb_tool import build_message_layout

logger = logging.getLogger("DiscordReactAgent")

class AgentSession(BaseAgentSession):
    def __init__(self, thread_id: int, user_id: int, prompt: str, loaded_contexts: str, channel: discord.Thread):
        super().__init__(thread_id, user_id, prompt, loaded_contexts, channel)
        self.react_history: List[Dict[str, Any]] = []
        
        self.pending_question_text: str = ""
        self.pending_options: List[str] = []

    def compile_react_transcript(self) -> str:
        if not self.react_history:
            return "No steps completed yet."
            
        lines = []
        for i, step in enumerate(self.react_history):
            lines.append(f"--- STEP {i+1} ---")
            lines.append(f"Thought: {step.get('thought', '')}")
            lines.append(f"Action Call: `{step.get('tool', '')}` with args {json.dumps(step.get('args', {}))}")
            lines.append(f"Observation Result: {step.get('observation', '')}\n")
        return "\n".join(lines)

    async def execute_tick(self, bot):
        if self.status != "running":
            logger.info(f"Session {self.thread_id} tick bypassed: Status is '{self.status}'")
            return
            
        if self.current_step >= self.max_steps:
            self.status = "paused_continuation"
            logger.info(f"Agent safety limit hit inside thread {self.thread_id}")
            
            view = AgentContinuationView(self.thread_id)
            warn_msg = (
                f"⚠️ **Safety Boundary Hit!**\n"
                f"The agent has reached its execution limit of **{self.max_steps}** steps. "
                f"Authorize next operations to continue or generate the final summary report immediately."
            )
            await self.channel.send(content=warn_msg, view=view)
            return

        self.current_step += 1
        progress_content = f"🔍 **Step {self.current_step}: Analyzing details...**"
        
        progress_view = discord.ui.View(timeout=None)
        step_btn = AgentStepButton(step_index=self.current_step - 1, session_id=self.thread_id)
        step_btn.disabled = True
        progress_view.add_item(step_btn)
        
        progress_msg = await self.channel.send(content=progress_content, view=progress_view)

        try:
            CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
            PROMPT_PATH = os.path.join(CURRENT_DIR, "prompts", "agent_prompt.md")
            
            system_instruction = "You are an analytical assistant."
            try:
                with open(PROMPT_PATH, 'r', encoding='utf-8') as f:
                    system_instruction = f.read()
            except FileNotFoundError:
                logger.error(f"Could not locate agent prompt markdown instructions file at {PROMPT_PATH}")

            contexts_header = self.loaded_contexts if self.loaded_contexts else "No contexts attached."
            react_transcript = self.compile_react_transcript()
            instructions_header = f"\n=== USER ADDED DIRECTIONS ===\n{self.additional_instructions}\n" if self.additional_instructions else ""
            
            composed_turn_prompt = (
                f"=== TARGET USER CONTEXTS ===\n"
                f"{contexts_header}\n\n"
                f"=== PRIMARY GOAL TASK ===\n"
                f"{self.primary_task}\n"
                f"{instructions_header}\n"
                f"=== REACT PROGRESS HISTORICAL TRANSCRIPT ===\n"
                f"{react_transcript}\n\n"
                f"=== ACTIVE OPERATION TURN ===\n"
                f"You are on step {self.current_step}. Perform your analytical deduction thought block, "
                f"choose your next Action call, and output them exactly according to the ReAct guidelines."
            )

            logger.info(f"Invoking LLM step {self.current_step} inside thread {self.thread_id}")
            
            max_retries = 5
            response = None
            for attempt in range(max_retries):
                try:
                    response = await bot.chat_handler.client.aio.models.generate_content(
                        model=bot.chat_handler.premium_model,
                        contents=composed_turn_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=1.0,
                            thinking_config=types.ThinkingConfig(
                                thinking_level="HIGH",
                                include_thoughts=True
                            )
                        )
                    )
                    
                    candidate = response.candidates[0] if (response and response.candidates) else None
                    finish_reason_str = str(getattr(candidate, 'finish_reason', '')).upper()
                    if "MALFORMED_RESPONSE" in finish_reason_str:
                        logger.warning("Agent Loop detected MALFORMED_RESPONSE. Falling back to standard generation...")
                        response = await bot.chat_handler.client.aio.models.generate_content(
                            model=bot.chat_handler.premium_model,
                            contents=composed_turn_prompt,
                            config=types.GenerateContentConfig(
                                system_instruction=system_instruction,
                                temperature=0.7
                            )
                        )
                    break
                except Exception as e:
                    error_str = str(e).lower()
                    logger.warning(f"Agent step loop API attempt {attempt + 1} failed: {error_str}")
                    
                    if attempt == max_retries - 1:
                        raise e
                        
                    backoff_delay = 3 * (2 ** attempt)
                    await asyncio.sleep(backoff_delay)
                    
            output_text = ""
            native_thoughts_list = []
            if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if getattr(part, 'thought', False) and getattr(part, 'text', None):
                        native_thoughts_list.append(part.text)
                    elif getattr(part, 'text', None):
                        output_text += part.text

            native_thoughts = "".join(native_thoughts_list).strip()
            output_text = output_text.strip()
                
            thought, tool, args, parse_error = self.parse_react_output(output_text, native_thoughts)
            
            if (not tool or tool.lower().strip() in ("none", "final_report")) and self.current_step == 1:
                logger.warning("Agent returned blank/none action on Step 1. Retrying with a standard recovery pass...")
                try:
                    response = await bot.chat_handler.client.aio.models.generate_content(
                        model=bot.chat_handler.premium_model,
                        contents=composed_turn_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            temperature=0.7
                        )
                    )
                    
                    output_text = ""
                    native_thoughts_list = []
                    if response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                        for part in response.candidates[0].content.parts:
                            if getattr(part, 'thought', False) and getattr(part, 'text', None):
                                native_thoughts_list.append(part.text)
                            elif getattr(part, 'text', None):
                                output_text += part.text

                    native_thoughts = "".join(native_thoughts_list).strip()
                    output_text = output_text.strip()
                    thought, tool, args, parse_error = self.parse_react_output(output_text, native_thoughts)
                except Exception as recovery_exc:
                    logger.error(f"Fallback recovery request failed: {recovery_exc}")
            
            if parse_error:
                logger.warning(f"ReAct output parsing failed: {parse_error}")
                self.react_history.append({
                    "thought": "Parser error occurred.",
                    "tool": "None",
                    "args": {},
                    "observation": f"[Error: Your output format was invalid. Ensure you output clean blocks. {parse_error}]"
                })
                await progress_msg.edit(content=f"⚠️ **Step {self.current_step}: Formatting validation failed**")
                bot.loop.create_task(self.execute_tick(bot))
                return
                
            self.react_history.append({
                "thought": thought,
                "tool": tool,
                "args": args,
                "observation": "Processing tool execution..."
            })
            
            if not tool or tool.lower().strip() == "none" or tool.lower().strip() == "final_report":
                logger.info(f"Agent session {self.thread_id} successfully finalized its goals.")
                self.status = "completed"
                step_btn.disabled = False
                await progress_msg.edit(content=f"✅ **Step {self.current_step}: Completed analysis!**", view=progress_view)
                await self.finalize_report(bot, output_text)
                return

            observation_result = await run_local_agent_tool(bot, self, tool, args)
            self.react_history[-1]["observation"] = observation_result
            
            if self.status == "running":
                step_btn.disabled = False
                await progress_msg.edit(content=f"✅ **Step {self.current_step}: Completed task `{tool}`**", view=progress_view)
                bot.loop.create_task(self.execute_tick(bot))
                
        except ServerError as s_err:
            logger.error(f"Unstable backend server error during agent turn {self.current_step}: {s_err}")
            self.status = "paused_error"
            await progress_msg.edit(content=f"❌ **Step {self.current_step}: Terminated due to API server error**")
            
            view = AgentErrorView(self.thread_id, str(s_err))
            error_note = (
                f"⚠️ **Connection Timeout (Google Server Issue)**\n"
                f"----------------------------------------\n"
                f"Google's Gemini API returned a server error: `{s_err}`.\n"
                f"This usually indicates high demand or temporary server instability on their backend.\n\n"
                f"Would you like to retry the current step or conclude with the findings gathered so far?"
            )
            await self.channel.send(content=error_note, view=view)
            
        except Exception as exc:
            logger.error(f"Agent workspace crash on loop tick: {exc}")
            self.status = "paused_error"
            await progress_msg.edit(content=f"❌ **Step {self.current_step}: Investigation crashed**")
            
            view = AgentErrorView(self.thread_id, str(exc))
            error_note = (
                f"🛑 **Investigation Interrupted**\n"
                f"----------------------------------------\n"
                f"An uncaught crash occurred: `{exc}`\n\n"
                f"Would you like to retry the current step or stop execution and report findings?"
            )
            await self.channel.send(content=error_note, view=view)

    def parse_react_output(self, output: str, native_thoughts: str = "") -> tuple[str, Optional[str], dict, Optional[str]]:
        thought_match = re.search(r'(?i)```thought\s*(.*?)\s*```', output, flags=re.DOTALL)
        if not thought_match:
            thought_match = re.search(r'(?i)<\s*thought\s*>\s*(.*?)\s*<\s*/\s*thought\s*>', output, flags=re.DOTALL)
            
        action_match = re.search(r'(?i)```action\s*(.*?)\s*```', output, flags=re.DOTALL)
        if not action_match:
            action_match = re.search(r'(?i)<\s*action\s*>\s*(.*?)\s*<\s*/\s*action\s*>', output, flags=re.DOTALL)

        thought_content = ""
        if thought_match:
            thought_content = thought_match.group(1).strip()
        elif native_thoughts:
            thought_content = native_thoughts.strip()

        if not thought_content:
            if not action_match:
                return "Finalized report", "final_report", {}, None
            return "", None, {}, "Missing required internal thought reasoning."
            
        if not action_match:
            return thought_content, "final_report", {}, None
            
        action_raw = action_match.group(1).strip()
        
        try:
            action_json = json.loads(action_raw)
        except json.JSONDecodeError as json_err:
            return thought_content, None, {}, f"Invalid Action block JSON syntax: {json_err}"
            
        tool_name = action_json.get("tool")
        arguments = action_json.get("arguments", {})
        
        if not tool_name:
            return thought_content, None, {}, "Missing 'tool' property inside Action block JSON."
            
        return thought_content, tool_name, arguments, None

    async def finalize_report(self, bot, raw_model_output: str = "") -> None:
        final_summary = raw_model_output
        
        final_summary = re.sub(r'(?i)```thought\s*.*?\s*```', '', final_summary, flags=re.DOTALL)
        final_summary = re.sub(r'(?i)<\s*thought\s*>.*?</\s*thought\s*>', '', final_summary, flags=re.DOTALL)
        final_summary = re.sub(r'(?i)```action\s*.*?\s*```', '', final_summary, flags=re.DOTALL)
        final_summary = re.sub(r'(?i)<\s*action\s*>.*?</\s*action\s*>', '', final_summary, flags=re.DOTALL)
        final_summary = final_summary.strip()
        
        if not final_summary:
            final_summary = "### 📋 Final Analysis Report\nGoal task successfully completed. Here is a historical overview of the observations."
            
        if self.thread_id in bot.active_agent_sessions:
            del bot.active_agent_sessions[self.thread_id]
            
        await bot._send_split_content(self.channel, final_summary)
        await self.channel.send(content="💡 *The private workspace has successfully concluded its operations.*")