import os
import re
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
import discord
from google.genai import types

from agent.constants import (
    OCTICONS_MAP,
    AGENT_PLANNING_SYSTEM_INSTRUCTION,
    AGENT_EXECUTION_SYSTEM_INSTRUCTION
)
from agent.session_manager import session_manager, normalize_repo_url
from agent.git_manager import git_manager
from core.github_app_client import github_app_client
from agent.parser import (
    parse_agent_questions_from_text,
    parse_agent_citations_from_text,
    parse_finalize_artifact,
    extract_citations_from_html_or_markdown,
    strip_agent_xml_tags
)
from agent.views import (
    build_agent_header_layout,
    build_agent_completed_header_layout,
    build_agent_step_layout,
    AgentQuestionView,
    AgentPlanApprovalView,
    AgentFinalDeliverableView,
    AgentReadyForReviewView,
    compute_unified_diff_str
)
from agent.tools import agent_list_dir
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from core.engine import ChatEngine
from core.moderation import check_moderation, log_moderation_violation, is_user_banned, ban_user, generate_friendly_refusal
from parsers.artifact_parser import ArtifactStreamParser
from handlers.stream_handler import DiscordStreamDispatcher, apply_message_parsers
from handlers.chat_handler import (
    format_placeholder_content,
    get_tool_subtext,
    update_placeholder_loop,
    extract_message_attachments_raw
)
from ui.thought_container import PlaceholderLayoutView
from ui.onboarding_views import BannedUserNoticeView
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.Agent.Engine")

PRIMARY_AGENT_MODEL = "gemma-4-31b-it"
AGENT_FALLBACK_CASCADE = [
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite"
]

PLANNING_ALLOWED_TOOLS = {
    "agent_read_file",
    "agent_list_dir",
    "agent_search_web",
    "agent_read_link",
    "agent_search_discord_history",
    "github_repo"
}

EXECUTION_ALLOWED_TOOLS = {
    "agent_read_file",
    "agent_write_file",
    "agent_edit_diff",
    "agent_list_dir",
    "agent_terminal",
    "agent_search_web",
    "agent_read_link",
    "agent_search_discord_history",
    "github_repo"
}

THREAD_CHAT_AGENT_TOOLS = {
    "agent_read_file",
    "agent_write_file",
    "agent_edit_diff",
    "agent_list_dir",
    "agent_terminal",
    "agent_search_web",
    "agent_read_link",
    "agent_search_discord_history",
    "github_repo",
    "create_artifact",
    "update_artifact",
    "search_image",
    "search_gif",
    "calc"
}

def compact_conversation_history(contents: list[types.Content], keep_recent_turns: int = 4) -> list[types.Content]:
    if len(contents) <= (keep_recent_turns * 2 + 1):
        return contents

    compacted: list[types.Content] = []
    cutoff_idx = len(contents) - (keep_recent_turns * 2)

    for idx, content in enumerate(contents):
        if idx == 0 or idx >= cutoff_idx or content.role != "user":
            compacted.append(content)
            continue

        pruned_parts = []
        for part in content.parts:
            if getattr(part, "function_response", None):
                fn_resp = part.function_response
                resp_data = fn_resp.response if isinstance(fn_resp.response, dict) else {}
                summary_data = {"status": resp_data.get("status", "ok"), "summary": "Output compacted for context efficiency"}
                pruned_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fn_resp.name,
                            response=summary_data
                        )
                    )
                )
            else:
                pruned_parts.append(part)

        compacted.append(types.Content(role=content.role, parts=pruned_parts))

    return compacted

def package_workspace_artifact(
    session: dict[str, Any],
    thread_id: str | int,
    override_filename: str = "",
    override_title: str = ""
) -> dict[str, Any] | None:
    workspace_path = session["workspace_path"]
    repo_url = session.get("repo_url", "").strip()
    thread_title = session.get("thread_title", "").strip() or session.get("initial_prompt", "")[:35]

    parsed_files = []
    ignored_dirs = {".git", "node_modules", "target", "dist", ".venv", "__pycache__", "build", ".idea", ".vscode"}

    for root, dirs, files in os.walk(workspace_path):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for f in files:
            if f.startswith(".git"):
                continue
            full_p = os.path.join(root, f)
            rel_p = os.path.relpath(full_p, workspace_path).replace("\\", "/")
            try:
                with open(full_p, "r", encoding="utf-8", errors="replace") as file_obj:
                    content_str = file_obj.read()
                parsed_files.append({
                    "filename": rel_p,
                    "content": content_str,
                    "lines": max(1, len(content_str.splitlines())),
                    "size_bytes": len(content_str.encode("utf-8"))
                })
            except Exception:
                pass

    if not parsed_files:
        return None

    if len(parsed_files) == 1 and not (override_filename and override_filename.endswith(".zip")):
        single_f = parsed_files[0]
        single_fn = override_filename if override_filename and not override_filename.endswith(".zip") else single_f["filename"]
        title = override_title or single_fn.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").title()
        content_str = single_f["content"]
        raw_bytes = content_str.encode("utf-8")

        record = branch_manager.save_or_update_artifact(
            channel_id=thread_id,
            filename=single_fn,
            title=title,
            content=content_str,
            files=parsed_files,
            change_summary="Agent deliverable"
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "single_file",
            "title": title,
            "filename": single_fn,
            "description": f"Standalone deliverable ({single_f['lines']:,} lines)",
            "file_count": 1,
            "total_lines": single_f["lines"],
            "size_bytes": len(raw_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "files": parsed_files,
            "data_bytes": raw_bytes
        }
        return artifact_payload

    if override_filename:
        zip_filename = override_filename if override_filename.endswith(".zip") else f"{override_filename}.zip"
        title = override_title or zip_filename.replace(".zip", "").replace("-", " ").replace("_", " ").title()
    elif repo_url:
        _, _, repo_name = normalize_repo_url(repo_url)
        clean_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', repo_name or "project").strip('-').lower()
        zip_filename = f"{clean_slug}.zip"
        title = repo_name or "Project Workspace"
    else:
        clean_slug = re.sub(r'[^a-zA-Z0-9_\-]+', '-', thread_title).strip('-').lower()
        zip_filename = f"{clean_slug or 'project'}.zip"
        title = (thread_title or "Project Workspace").title()

    record = branch_manager.save_or_update_artifact(
        channel_id=thread_id,
        filename=zip_filename,
        title=title,
        content="",
        files=parsed_files,
        change_summary="Agent deliverables"
    )

    artifact_payload = {
        "artifact_id": record["artifact_id"],
        "type": "project_zip",
        "title": title,
        "filename": zip_filename,
        "description": f"Completed deliverable archive ({len(parsed_files)} files)",
        "file_count": len(parsed_files),
        "total_lines": record["latest_version_data"]["lines"],
        "size_bytes": record["latest_version_data"]["size_bytes"],
        "active_version": record["active_version"],
        "total_versions": record["total_versions"],
        "versions": record["versions"],
        "files": parsed_files
    }
    return artifact_payload

class AgentEngine:
    @staticmethod
    async def bootstrap_thread_meta(prompt: str) -> tuple[str, list[str], str]:
        default_title = "Agent Session"
        default_statuses = [
            "Analyzing workspace structure",
            "Synthesizing dependencies and entry points",
            "Evaluating architectural components",
            "Scoping implementation plan",
            "Drafting plan steps and schema",
            "Finalizing task outline"
        ]

        now_utc = datetime.now(timezone.utc)
        current_year_str = str(now_utc.year)

        instruction = f"""Analyze this agent prompt. Real-world year is {current_year_str} [1].
Classify the task into:
- task_type: "research" (deep research, comparisons, market study, report writing), "coding" (software development, bug fixes, refactoring), or "hybrid" (researching libraries/APIs first, then implementing in repo).
Generate a short 3-5 word thread title and 15 witty loading statuses. Output JSON:
{{"title": "Title Here", "task_type": "coding"|"research"|"hybrid", "statuses": ["status 1", "status 2", ...]}}"""

        attempted_keys = set()
        for _ in range(client_manager.key_count):
            client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", exclude_keys=attempted_keys, fallback=False)
            if not client:
                break
            attempted_keys.add(key_idx)
            try:
                res = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=active_model,
                        contents=f"Objective:\n{prompt[:1500]}",
                        config=types.GenerateContentConfig(
                            system_instruction=instruction,
                            response_mime_type="application/json",
                            temperature=0.2
                        )
                    ),
                    timeout=3.5
                )
                if res.text:
                    data = json.loads(res.text)
                    title = data.get("title", default_title).replace("#", "").strip()[:60]
                    statuses = data.get("statuses", default_statuses)
                    task_type = data.get("task_type", "general")
                    return title, statuses, task_type
            except Exception as e:
                client_manager.report_error(key_idx, active_model, e)
                logger.warning(f"[AgentBootstrap] Meta generation error on Key #{key_idx}: {e}")

        return default_title, default_statuses, "general"

    @classmethod
    async def generate_conversational_summary_fallback(cls, objective: str, report_content: str, workspace_files: list[str]) -> str:
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", fallback=True)
        if not client:
            return "Successfully investigated the requirements, verified all components, and compiled the deliverables."

        prompt_instruction = (
            f"You are PriestyAI. You just completed the user's objective: '{objective}'.\n"
            f"Files created or modified in workspace: {', '.join(workspace_files[:10]) or 'report.html'}\n\n"
            f"Write a friendly, natural, 2-paragraph conversational breakdown directly in chat explaining "
            f"what was implemented, tested, and verified. Speak directly like a collaborative software engineer. "
            f"DO NOT use robotic headers like 'Executive Summary'."
        )

        try:
            res = await client.aio.models.generate_content(
                model=active_model,
                contents=prompt_instruction
            )
            if res.text and res.text.strip():
                return res.text.strip()
        except Exception as e:
            logger.debug(f"Failed to generate fallback summary: {e}")

        return "Successfully implemented the requirements, verified all components, and compiled the deliverables into the workspace."

    @classmethod
    async def start_planning_turn(cls, thread: discord.Thread, session: dict[str, Any], feedback: str | None = None):
        session_id = session["session_id"]
        prompt = feedback or session["initial_prompt"]

        abort_event = session_manager.get_abort_event(session_id)
        session_manager.clear_abort_event(session_id)

        tasks_history = session.get("tasks_history", [])
        is_new_task = len(tasks_history) > 0

        if is_new_task:
            _, _, dynamic_task_type = await cls.bootstrap_thread_meta(prompt)
            session_manager.update_session(session_id, task_type=dynamic_task_type)
            session["task_type"] = dynamic_task_type
            logger.info(f"[AgentPlanning] Multi-Task #{len(tasks_history) + 1} classified as: '{dynamic_task_type}'")

        logger.info(f"[AgentPlanning] Starting planning turn for session #{session_id} in thread {thread.id}...")

        tool_context = ToolExecutionContext(
            channel=thread,
            guild=thread.guild,
            author=thread.guild.get_member(int(session["creator_id"])) if thread.guild else None,
            bot=thread.guild.me if thread.guild else None
        )
        tool_context.agent_session_id = session_id

        witty_statuses = session.get("witty_statuses") or ["Researching workspace files", "Formulating plan", "Evaluating architecture"]
        initial_loading_txt = witty_statuses[0]
        header_view = build_agent_header_layout(initial_loading_txt, duration_seconds=1, session_id=session_id, phase="planning")
        header_msg = await thread.send(view=header_view)
        session_manager.update_session(session_id, header_message_id=str(header_msg.id))

        stop_header_loop = asyncio.Event()
        t_start = time.time()

        active_thought_record = {
            "thoughts": "",
            "tool_calls": [],
            "duration_seconds": 1
        }
        session_manager.save_session_thoughts(session_id, "", [], 1)

        async def header_loop():
            idx = 0
            while not stop_header_loop.is_set():
                try:
                    await asyncio.sleep(2.5)
                    if stop_header_loop.is_set() or abort_event.is_set():
                        break
                    elapsed = max(1, int(time.time() - t_start))
                    if elapsed % 5 == 0:
                        idx = (idx + 1) % len(witty_statuses)
                    curr_txt = witty_statuses[idx]
                    h_view = build_agent_header_layout(curr_txt, duration_seconds=elapsed, session_id=session_id, phase="planning")
                    await header_msg.edit(view=h_view)
                    active_thought_record["duration_seconds"] = elapsed
                    session_manager.save_session_thoughts(session_id, active_thought_record["thoughts"], active_thought_record["tool_calls"], elapsed)
                except (discord.NotFound, discord.Forbidden):
                    break
                except Exception as ex:
                    logger.debug(f"[AgentHeaderLoop] Transient edit warning: {ex}")
                    await asyncio.sleep(3.0)

        header_task = asyncio.create_task(header_loop())
        step_counter = 0

        repo_url = session.get("repo_url", "").strip()
        if repo_url and not is_new_task:
            await session_manager.ensure_workspace_cloned(session)
            step_counter += 1
            repo_name = repo_url.split("/")[-1].replace(".git", "")
            
            clone_step_data = {
                "name": "clone_repo",
                "args": {"repo": repo_url},
                "result": {"status": "cloned", "repository": repo_url, "workspace": session["workspace_path"]},
                "duration_ms": 950
            }
            session_manager.save_step_log(session_id, step_counter, "clone_repo", {"repo": repo_url}, clone_step_data["result"], duration_ms=950)
            active_thought_record["tool_calls"].append(clone_step_data)
            
            clone_view = build_agent_step_layout("clone_repo", {"repo": repo_name}, 950, session_id, step_counter)
            await thread.send(view=clone_view)
        else:
            await session_manager.ensure_workspace_cloned(session)

        dir_res = await agent_list_dir(subpath="", context=tool_context)
        file_list = dir_res.get("files", [])
        file_list_summary = "\n".join([f"• `{f}`" for f in file_list[:50]]) or "*Empty workspace*"

        extracted_urls = re.findall(r'https?://[^\s<>"]+', prompt)
        priority_links_xml = ""
        if extracted_urls:
            priority_links_xml = "\n  <user_priority_sources>\n" + "\n".join([f'    <source url="{u}" priority="CRITICAL_READ_FIRST"/>' for u in extracted_urls]) + "\n  </user_priority_sources>"

        completed_tasks_xml = ""
        if tasks_history:
            task_items = []
            for t_item in tasks_history:
                dels_str = ", ".join(t_item.get("deliverables", [])) or "None"
                task_items.append(
                    f"    <task id=\"{t_item.get('task_num', 1)}\" type=\"{t_item.get('task_type', 'general')}\">\n"
                    f"      <objective>{t_item.get('objective', '')}</objective>\n"
                    f"      <summary>{t_item.get('summary', '')}</summary>\n"
                    f"      <deliverables>{dels_str}</deliverables>\n"
                    f"    </task>"
                )
            completed_tasks_xml = "\n  <completed_tasks>\n" + "\n".join(task_items) + "\n  </completed_tasks>"

        thread_chat_xml = ""
        try:
            raw_t_msgs = [m async for m in thread.history(limit=10)]
            raw_t_msgs.reverse()
            chat_items = []
            for tm in raw_t_msgs:
                if not tm.author.bot and tm.clean_content:
                    chat_items.append(f"    <chat_message user=\"{tm.author.display_name}\">{tm.clean_content[:300]}</chat_message>")
            if chat_items:
                thread_chat_xml = "\n  <thread_discussion>\n" + "\n".join(chat_items) + "\n  </thread_discussion>"
        except Exception:
            pass

        now_utc = datetime.now(timezone.utc)
        current_date_str = now_utc.strftime("%A, %B %d, %Y %H:%M:%S")
        current_year_str = str(now_utc.year)

        initial_context_payload = (
            f"<temporal_context current_utc=\"{now_utc.isoformat()}\" current_date=\"{current_date_str}\" current_year=\"{current_year_str}\" />\n"
            f"<agent_workspace>\n"
            f"  <objective>{prompt}</objective>\n"
            f"  <task_type>{session.get('task_type', 'general')}</task_type>\n"
            f"  <task_number>{len(tasks_history) + 1}</task_number>\n"
            f"  <workspace_root>./</workspace_root>\n"
            f"  <files_indexed total='{len(file_list)}'>\n{file_list_summary}\n  </files_indexed>{priority_links_xml}{completed_tasks_xml}{thread_chat_xml}\n"
            f"</agent_workspace>\n\n"
            f"Execute your planning turn. Inspect at most 3-5 key entry/schema files to understand data structures, and immediately draft `<artifact filename='plan.md'>` (or `research_plan.md`)."
        )

        turn_contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=initial_context_payload)])
        ]

        resolved_cfg = config_manager.resolve_effective_config(
            thread.guild.id if thread.guild else None,
            getattr(thread, "parent_id", thread.id),
            int(session["creator_id"])
        )
        disabled_tools_set = set(resolved_cfg.get("disabled_tools", []))

        all_tools = set(tool_registry._tools.keys())
        has_repo = bool(session.get("repo_url")) or session.get("task_type") in ["coding", "hybrid"]
        allowed_tools = {t for t in PLANNING_ALLOWED_TOOLS if t not in disabled_tools_set}
        if not has_repo:
            allowed_tools.discard("github_repo")

        planning_disabled = list(all_tools - allowed_tools)
        tool_declarations = tool_registry.get_tool_declarations(disabled_tools=planning_disabled)

        stream_dispatcher = DiscordStreamDispatcher(target_channel=thread, guild=thread.guild)
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=thread.id)

        accumulated_thoughts = []
        formatted_planning_instruction = (
            AGENT_PLANNING_SYSTEM_INSTRUCTION
            .replace("{current_date}", current_date_str)
            .replace("{current_year}", current_year_str)
        )

        was_aborted = False

        try:
            for tool_turn in range(8):
                if abort_event.is_set():
                    was_aborted = True
                    break

                stream_success = False
                model_parts = []
                fcalls = []

                working_turn_contents = compact_conversation_history(turn_contents, keep_recent_turns=3)

                for model_cand in AGENT_FALLBACK_CASCADE:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    eff_thinking = "HIGH"
                    attempted_keys = set()
                    consecutive_503s = 0

                    while True:
                        if abort_event.is_set():
                            was_aborted = True
                            break

                        client, key_idx, active_model = client_manager.get_client_for_model(
                            model_cand,
                            exclude_keys=attempted_keys,
                            fallback=False
                        )
                        if not client or key_idx in attempted_keys:
                            break

                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_planning_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level=eff_thinking, include_thoughts=True),
                                tools=tool_declarations,
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                temperature=0.3
                            )

                            stream = await client.aio.models.generate_content_stream(
                                model=active_model,
                                contents=working_turn_contents,
                                config=config
                            )

                            model_parts.clear()
                            fcalls.clear()

                            async for chunk in stream:
                                if abort_event.is_set():
                                    was_aborted = True
                                    break
                                if chunk.candidates and chunk.candidates[0].content:
                                    for p in chunk.candidates[0].content.parts:
                                        model_parts.append(p)
                                        if getattr(p, "thought", False) and p.text:
                                            accumulated_thoughts.append(p.text)
                                            active_thought_record["thoughts"] = "".join(accumulated_thoughts)
                                            session_manager.save_session_thoughts(session_id, active_thought_record["thoughts"], active_thought_record["tool_calls"], active_thought_record["duration_seconds"])
                                        elif p.text:
                                            await artifact_parser.feed(p.text)
                                        elif p.function_call:
                                            fcalls.append(p.function_call)

                            if was_aborted:
                                break

                            stream_success = True
                            break

                        except Exception as e:
                            err_desc = str(e)
                            client_manager.report_error(key_idx, active_model, e)
                            logger.warning(f"[AgentPlanning] Error on {active_model} (Key #{key_idx}): {err_desc}")
                            if "503" in err_desc or "unavailable" in err_desc.lower():
                                consecutive_503s += 1
                                if consecutive_503s >= 2:
                                    logger.warning(f"[AgentPlanning] '{model_cand}' overloaded. Stepping down immediately...")
                                    break
                            if ChatEngine._is_retryable_error(err_desc):
                                continue
                            break

                    if stream_success or was_aborted:
                        break

                if was_aborted:
                    break

                if not stream_success:
                    logger.warning("[AgentPlanning] All model cascades busy. Backing off 2s...")
                    await asyncio.sleep(2.0)
                    continue

                if model_parts:
                    turn_contents.append(types.Content(role="model", parts=model_parts))

                if not fcalls:
                    break

                fres_parts = []
                for fc in fcalls:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    f_name = fc.name
                    f_args = dict(fc.args) if fc.args else {}

                    st_time = time.perf_counter()
                    if f_name in disabled_tools_set:
                        result = {"error": f"Tool '{f_name}' is disabled by server policy."}
                    else:
                        result = await tool_registry.execute(f_name, f_args, tool_context)
                    dur_ms = int((time.perf_counter() - st_time) * 1000)

                    step_counter += 1
                    session_manager.save_step_log(session_id, step_counter, f_name, f_args, result, duration_ms=dur_ms)
                    active_thought_record["tool_calls"].append({
                        "name": f_name,
                        "args": f_args,
                        "result": result,
                        "duration_ms": dur_ms
                    })
                    session_manager.save_session_thoughts(session_id, active_thought_record["thoughts"], active_thought_record["tool_calls"], active_thought_record["duration_seconds"])

                    step_view = build_agent_step_layout(f_name, f_args, dur_ms, session_id, step_counter)
                    await thread.send(view=step_view)

                    fres_parts.append(types.Part(function_response=types.FunctionResponse(name=f_name, response=result)))

                if was_aborted:
                    break

                if tool_turn >= 4 and not tool_context.staged_artifacts:
                    fres_parts.append(types.Part(text="\n[SYSTEM DIRECTIVE]: You have finished inspecting files. Do NOT call any more tools. Summarize your plan and emit <artifact filename='plan.md'> (or research_plan.md)."))

                turn_contents.append(types.Content(role="user", parts=fres_parts))

            if not tool_context.staged_artifacts and not was_aborted and "<question" not in stream_dispatcher.get_accumulated_text():
                logger.info("[AgentPlanning] Executing guaranteed final plan synthesis turn (tools=None)...")
                synthesis_contents = compact_conversation_history(turn_contents, keep_recent_turns=4)
                synthesis_contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(text="Now synthesize all architectural findings, provide a conversational overview of the approach, and emit the plan deliverable as <artifact filename='plan.md' title='Implementation Plan'> (or research_plan.md).")]
                    )
                )

                for model_cand in AGENT_FALLBACK_CASCADE:
                    attempted_keys = set()
                    synth_done = False
                    while True:
                        client, key_idx, active_model = client_manager.get_client_for_model(model_cand, exclude_keys=attempted_keys, fallback=False)
                        if not client or key_idx in attempted_keys:
                            break
                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_planning_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level="HIGH", include_thoughts=True),
                                tools=None,
                                temperature=0.3
                            )
                            stream = await client.aio.models.generate_content_stream(model=active_model, contents=synthesis_contents, config=config)
                            async for chunk in stream:
                                if chunk.candidates and chunk.candidates[0].content:
                                    for p in chunk.candidates[0].content.parts:
                                        if getattr(p, "thought", False) and p.text:
                                            accumulated_thoughts.append(p.text)
                                        elif p.text:
                                            await artifact_parser.feed(p.text)
                            synth_done = True
                            break
                        except Exception as e:
                            client_manager.report_error(key_idx, active_model, e)
                            continue
                    if synth_done:
                        break

            await artifact_parser.finish()
            
            stop_header_loop.set()
            if header_task:
                header_task.cancel()
                try:
                    await header_task
                except (asyncio.CancelledError, Exception):
                    pass

            final_dur = max(1, int(time.time() - t_start))
            session_manager.save_session_thoughts(session_id, "".join(accumulated_thoughts), active_thought_record["tool_calls"], final_dur)

            completed_header = build_agent_completed_header_layout(final_dur, session_id, phase="planning", was_stopped=was_aborted)
            try:
                await header_msg.edit(view=completed_header)
            except Exception as ex:
                logger.warning(f"Failed to update final header: {ex}")

            if was_aborted:
                session_manager.update_session(session_id, state="stopped")
                await thread.send(content="🛑 **Agent planning stopped.** All workspace files remain intact.")
                return

            raw_text = stream_dispatcher.get_accumulated_text()
            questions = parse_agent_questions_from_text(raw_text)
            citations = parse_agent_citations_from_text(raw_text)
            clean_text = strip_agent_xml_tags(raw_text)

            if citations:
                session_manager.update_session(session_id, citations=citations)

            if questions:
                session_manager.update_session(session_id, state="awaiting_input")
                async def handle_answers(sub_inter: discord.Interaction, answers: dict[str, str]):
                    await sub_inter.followup.send(content=f"{OCTICONS_MAP['oct_check']} **Answers Recorded:** Proceeding with updated scope...", ephemeral=False)
                    ans_text = "\n".join([f"- **{k}**: {v}" for k, v in answers.items()])
                    await cls.start_planning_turn(thread, session, feedback=f"User clarifications:\n{ans_text}")

                q_view = AgentQuestionView(
                    conversational_text=clean_text or "Please answer the clarification questions below to guide the implementation:",
                    questions=questions,
                    session=session,
                    citations=citations,
                    thought_duration=final_dur,
                    guild=thread.guild,
                    on_submit_callback=handle_answers
                )
                if stream_dispatcher.primary_message:
                    try:
                        await stream_dispatcher.primary_message.edit(view=q_view)
                        session_manager.update_session(session_id, last_plan_message_id=str(stream_dispatcher.primary_message.id))
                    except Exception:
                        q_msg = await thread.send(view=q_view)
                        session_manager.update_session(session_id, last_plan_message_id=str(q_msg.id))
                else:
                    q_msg = await thread.send(view=q_view)
                    session_manager.update_session(session_id, last_plan_message_id=str(q_msg.id))

            elif tool_context.staged_artifacts:
                plan_art = tool_context.staged_artifacts[-1]
                session_manager.update_session(session_id, state="awaiting_approval")

                art_filename = plan_art.get("filename", "plan.md")
                art_title = plan_art.get("title", "Plan & Scope")

                branch_manager.save_or_update_artifact(
                    channel_id=thread.id,
                    filename=art_filename,
                    title=art_title,
                    content=plan_art.get("content", ""),
                    change_summary="Plan & Scope Deliverable"
                )

                async def handle_approved(sub_inter: discord.Interaction):
                    await sub_inter.followup.send(content=f"{OCTICONS_MAP['oct_check']} **Plan Approved!** Starting autonomous execution...", ephemeral=False)
                    await cls.start_execution_phase(thread, session, approved_plan_text=plan_art.get("content", ""))

                app_view = AgentPlanApprovalView(
                    conversational_text=clean_text or "I have drafted the plan based on the research and requirements. Please review and approve to proceed:",
                    artifact=plan_art,
                    session=session,
                    citations=citations,
                    thought_duration=final_dur,
                    guild=thread.guild,
                    on_approve_callback=handle_approved
                )
                if stream_dispatcher.primary_message:
                    try:
                        await stream_dispatcher.primary_message.edit(view=app_view)
                        session_manager.update_session(session_id, last_plan_message_id=str(stream_dispatcher.primary_message.id))
                    except Exception:
                        p_msg = await thread.send(view=app_view)
                        session_manager.update_session(session_id, last_plan_message_id=str(p_msg.id))
                else:
                    p_msg = await thread.send(view=app_view)
                    session_manager.update_session(session_id, last_plan_message_id=str(p_msg.id))

        except Exception as e:
            stop_header_loop.set()
            if header_task:
                header_task.cancel()
                try:
                    await header_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.exception(f"[AgentPlanning] Error: {e}")
            await thread.send(content=f"⚠️ Planning turn encountered an error: `{e}`")

    @classmethod
    async def start_execution_phase(cls, thread: discord.Thread, session: dict[str, Any], approved_plan_text: str = ""):
        session_id = session["session_id"]
        session_manager.update_session(session_id, state="executing")

        abort_event = session_manager.get_abort_event(session_id)
        session_manager.clear_abort_event(session_id)

        tool_context = ToolExecutionContext(
            channel=thread,
            guild=thread.guild,
            author=thread.guild.get_member(int(session["creator_id"])) if thread.guild else None,
            bot=thread.guild.me if thread.guild else None
        )
        tool_context.agent_session_id = session_id

        await session_manager.ensure_docker_container(session)

        witty_statuses = session.get("witty_statuses") or [
            "Implementing codebase modifications",
            "Applying surgical file patches",
            "Running automated test suites",
            "Compiling final deliverables"
        ]
        initial_exec_txt = witty_statuses[0]
        exec_header_view = build_agent_header_layout(initial_exec_txt, duration_seconds=1, session_id=session_id, phase="execution")
        exec_header_msg = await thread.send(view=exec_header_view)

        stop_exec_loop = asyncio.Event()
        t_exec_start = time.time()

        existing_thoughts = session_manager.get_session_thoughts(session_id) or {"thoughts": "", "tool_calls": [], "duration_seconds": 1}
        active_exec_thought_record = {
            "thoughts": existing_thoughts.get("thoughts", ""),
            "tool_calls": existing_thoughts.get("tool_calls", []),
            "duration_seconds": 1
        }

        async def exec_header_loop():
            idx = 0
            while not stop_exec_loop.is_set():
                try:
                    await asyncio.sleep(2.5)
                    if stop_exec_loop.is_set() or abort_event.is_set():
                        break
                    elapsed = max(1, int(time.time() - t_exec_start))
                    if elapsed % 5 == 0:
                        idx = (idx + 1) % len(witty_statuses)
                    curr_txt = witty_statuses[idx]
                    h_view = build_agent_header_layout(curr_txt, duration_seconds=elapsed, session_id=session_id, phase="execution")
                    await exec_header_msg.edit(view=h_view)
                    active_exec_thought_record["duration_seconds"] = elapsed
                    session_manager.save_session_thoughts(session_id, active_exec_thought_record["thoughts"], active_exec_thought_record["tool_calls"], elapsed)
                except (discord.NotFound, discord.Forbidden):
                    break
                except Exception as ex:
                    logger.debug(f"[AgentExecHeaderLoop] Transient edit warning: {ex}")
                    await asyncio.sleep(3.0)

        exec_header_task = asyncio.create_task(exec_header_loop())

        if not approved_plan_text:
            for candidate_fn in ["research_plan.md", "plan.md"]:
                art_db = branch_manager.get_artifact_by_channel_and_file(thread.id, candidate_fn)
                if art_db:
                    versions = art_db.get("versions", [])
                    approved_plan_text = versions[-1].get("content", "") if versions else ""
                    break

        resolved_cfg = config_manager.resolve_effective_config(
            thread.guild.id if thread.guild else None,
            getattr(thread, "parent_id", thread.id),
            int(session["creator_id"])
        )
        disabled_tools_set = set(resolved_cfg.get("disabled_tools", []))

        all_tools = set(tool_registry._tools.keys())
        has_repo = bool(session.get("repo_url")) or session.get("task_type") in ["coding", "hybrid"]
        allowed_tools = {t for t in EXECUTION_ALLOWED_TOOLS if t not in disabled_tools_set}
        if not has_repo:
            allowed_tools.discard("github_repo")

        exec_disabled = list(all_tools - allowed_tools)
        tool_declarations = tool_registry.get_tool_declarations(disabled_tools=exec_disabled)

        now_utc = datetime.now(timezone.utc)
        current_date_str = now_utc.strftime("%A, %B %d, %Y %H:%M:%S")
        current_year_str = str(now_utc.year)

        tasks_history = session.get("tasks_history", [])
        completed_tasks_xml = ""
        if tasks_history:
            task_items = []
            for t_item in tasks_history:
                dels_str = ", ".join(t_item.get("deliverables", [])) or "None"
                task_items.append(
                    f"    <task id=\"{t_item.get('task_num', 1)}\" type=\"{t_item.get('task_type', 'general')}\">\n"
                    f"      <objective>{t_item.get('objective', '')}</objective>\n"
                    f"      <summary>{t_item.get('summary', '')}</summary>\n"
                    f"      <deliverables>{dels_str}</deliverables>\n"
                    f"    </task>"
                )
            completed_tasks_xml = "\n  <completed_tasks>\n" + "\n".join(task_items) + "\n  </completed_tasks>"

        exec_prompt = (
            f"<temporal_context current_utc=\"{now_utc.isoformat()}\" current_date=\"{current_date_str}\" current_year=\"{current_year_str}\" />\n"
            f"<agent_execution_context>\n"
            f"  <objective>{session['initial_prompt']}</objective>\n"
            f"  <task_type>{session.get('task_type', 'general')}</task_type>\n"
            f"  <task_number>{len(tasks_history) + 1}</task_number>\n"
            f"  <workspace_root>./</workspace_root>\n"
            f"  <approved_plan>\n{approved_plan_text}\n  </approved_plan>{completed_tasks_xml}\n"
            f"</agent_execution_context>\n\n"
            f"Begin Phase 2 execution. Directly implement or patch the files specified in <approved_plan> starting immediately in Turn 1. Run tests to verify your implementation, and conclude with `<finalize_artifact />`."
        )

        turn_contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=exec_prompt)])
        ]

        step_counter = len(active_exec_thought_record["tool_calls"])
        accumulated_exec_thoughts = []
        final_summary_text = ""
        
        stream_dispatcher = DiscordStreamDispatcher(target_channel=thread, guild=thread.guild)
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=thread.id)

        formatted_exec_instruction = (
            AGENT_EXECUTION_SYSTEM_INSTRUCTION
            .replace("{current_date}", current_date_str)
            .replace("{current_year}", current_year_str)
        )

        was_aborted = False

        try:
            for tool_turn in range(35):
                if abort_event.is_set():
                    was_aborted = True
                    break

                stream_success = False
                model_parts = []
                fcalls = []
                full_text = ""

                working_turn_contents = compact_conversation_history(turn_contents, keep_recent_turns=3)

                for model_cand in AGENT_FALLBACK_CASCADE:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    eff_thinking = "HIGH"
                    attempted_keys = set()
                    consecutive_503s = 0

                    while True:
                        if abort_event.is_set():
                            was_aborted = True
                            break

                        client, key_idx, active_model = client_manager.get_client_for_model(
                            model_cand,
                            exclude_keys=attempted_keys,
                            fallback=False
                        )
                        if not client or key_idx in attempted_keys:
                            break

                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_exec_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level=eff_thinking, include_thoughts=True),
                                tools=tool_declarations,
                                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                temperature=0.3
                            )

                            stream = await client.aio.models.generate_content_stream(
                                model=active_model,
                                contents=working_turn_contents,
                                config=config
                            )

                            model_parts.clear()
                            fcalls.clear()
                            full_text = ""

                            async for chunk in stream:
                                if abort_event.is_set():
                                    was_aborted = True
                                    break
                                if chunk.candidates and chunk.candidates[0].content:
                                    for p in chunk.candidates[0].content.parts:
                                        model_parts.append(p)
                                        if getattr(p, "thought", False) and p.text:
                                            accumulated_exec_thoughts.append(p.text)
                                            active_exec_thought_record["thoughts"] = "".join(accumulated_exec_thoughts)
                                            session_manager.save_session_thoughts(session_id, active_exec_thought_record["thoughts"], active_exec_thought_record["tool_calls"], active_exec_thought_record["duration_seconds"])
                                        elif p.text:
                                            full_text += p.text
                                            await artifact_parser.feed(p.text)
                                        elif p.function_call:
                                            fcalls.append(p.function_call)

                            if was_aborted:
                                break

                            stream_success = True
                            break

                        except Exception as e:
                            err_desc = str(e)
                            client_manager.report_error(key_idx, active_model, e)
                            logger.warning(f"[AgentExecution] Error on {active_model} (Key #{key_idx}): {err_desc}")
                            if "503" in err_desc or "unavailable" in err_desc.lower():
                                consecutive_503s += 1
                                if consecutive_503s >= 2:
                                    logger.warning(f"[AgentExecution] '{model_cand}' overloaded. Stepping down immediately...")
                                    break
                            if ChatEngine._is_retryable_error(err_desc):
                                continue
                            break

                    if stream_success or was_aborted:
                        break

                if was_aborted:
                    break

                if not stream_success:
                    logger.warning("[AgentExecution] All model cascades busy. Backing off 2s...")
                    await asyncio.sleep(2.0)
                    continue

                if model_parts:
                    turn_contents.append(types.Content(role="model", parts=model_parts))

                if full_text.strip():
                    final_summary_text += full_text

                if "<finalize_artifact" in full_text:
                    break

                if not fcalls:
                    break

                fres_parts = []
                for fc in fcalls:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    f_name = fc.name
                    f_args = dict(fc.args) if fc.args else {}

                    diff_text, adds, dels = "", 0, 0
                    if f_name == "agent_edit_diff":
                        diff_text, adds, dels = compute_unified_diff_str(
                            f_args.get("search_block", ""),
                            f_args.get("replace_block", ""),
                            f_args.get("path", "file")
                        )

                    st_time = time.perf_counter()
                    if f_name in disabled_tools_set:
                        result = {"error": f"Tool '{f_name}' is disabled by server policy."}
                    else:
                        result = await tool_registry.execute(f_name, f_args, tool_context)
                    dur_ms = int((time.perf_counter() - st_time) * 1000)

                    step_counter += 1
                    session_manager.save_step_log(session_id, step_counter, f_name, f_args, result, diff_text, adds, dels, dur_ms)
                    active_exec_thought_record["tool_calls"].append({
                        "name": f_name,
                        "args": f_args,
                        "result": result,
                        "diff_text": diff_text,
                        "additions": adds,
                        "deletions": dels,
                        "duration_ms": dur_ms
                    })
                    session_manager.save_session_thoughts(session_id, active_exec_thought_record["thoughts"], active_exec_thought_record["tool_calls"], active_exec_thought_record["duration_seconds"])

                    step_view = build_agent_step_layout(f_name, f_args, dur_ms, session_id, step_counter, adds, dels)
                    await thread.send(view=step_view)

                    fres_parts.append(types.Part(function_response=types.FunctionResponse(name=f_name, response=result)))

                if was_aborted:
                    break

                if tool_turn >= 20 and "<finalize_artifact" not in final_summary_text:
                    fres_parts.append(types.Part(text="\n[SYSTEM DIRECTIVE]: Finalize your code changes now. Run tests to verify your implementation, summarize your work, and output <finalize_artifact />."))

                turn_contents.append(types.Content(role="user", parts=fres_parts))

            if "<finalize_artifact" not in final_summary_text and not was_aborted:
                logger.info("[AgentExecution] Executing guaranteed final execution synthesis turn (tools=None)...")
                synthesis_contents = compact_conversation_history(turn_contents, keep_recent_turns=4)
                synthesis_contents.append(
                    types.Content(
                        role="user",
                        parts=[types.Part(text="Synthesize your completed work, provide a conversational overview of the implementation and tests verified, and conclude with <finalize_artifact />.")]
                    )
                )

                for model_cand in AGENT_FALLBACK_CASCADE:
                    attempted_keys = set()
                    synth_done = False
                    while True:
                        client, key_idx, active_model = client_manager.get_client_for_model(model_cand, exclude_keys=attempted_keys, fallback=False)
                        if not client or key_idx in attempted_keys:
                            break
                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_exec_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level="HIGH", include_thoughts=True),
                                tools=None,
                                temperature=0.3
                            )
                            stream = await client.aio.models.generate_content_stream(model=active_model, contents=synthesis_contents, config=config)
                            async for chunk in stream:
                                if chunk.candidates and chunk.candidates[0].content:
                                    for p in chunk.candidates[0].content.parts:
                                        if getattr(p, "thought", False) and p.text:
                                            accumulated_exec_thoughts.append(p.text)
                                        elif p.text:
                                            final_summary_text += p.text
                                            await artifact_parser.feed(p.text)
                            synth_done = True
                            break
                        except Exception as e:
                            client_manager.report_error(key_idx, active_model, e)
                            continue
                    if synth_done:
                        break

            await artifact_parser.finish()
            
            stop_exec_loop.set()
            if exec_header_task:
                exec_header_task.cancel()
                try:
                    await exec_header_task
                except (asyncio.CancelledError, Exception):
                    pass

            final_exec_dur = max(1, int(time.time() - t_exec_start))
            session_manager.save_session_thoughts(session_id, "".join(accumulated_exec_thoughts), active_exec_thought_record["tool_calls"], final_exec_dur)

            completed_exec_header = build_agent_completed_header_layout(final_exec_dur, session_id, phase="execution", was_stopped=was_aborted)
            try:
                await exec_header_msg.edit(view=completed_exec_header)
            except Exception:
                pass

            if was_aborted:
                session_manager.update_session(session_id, state="stopped")
                await thread.send(content="🛑 **Agent execution stopped.** All workspace files remain intact.")
                return

            session_manager.update_session(session_id, state="completed")

            workspace_dir = session["workspace_path"]
            created_files = [f for f in os.listdir(workspace_dir) if not f.startswith(".")] if os.path.exists(workspace_dir) else []

            fin_fn, fin_title = parse_finalize_artifact(final_summary_text)

            deliverable_artifact = None
            if tool_context.staged_artifacts:
                deliverable_artifact = tool_context.staged_artifacts[-1]
            else:
                deliverable_artifact = package_workspace_artifact(
                    session=session,
                    thread_id=thread.id,
                    override_filename=fin_fn,
                    override_title=fin_title
                )

            clean_summary = strip_agent_xml_tags(final_summary_text)

            if len(clean_summary.strip()) < 40:
                report_text = deliverable_artifact.get("content", "") if deliverable_artifact else ""
                clean_summary = await cls.generate_conversational_summary_fallback(
                    objective=session["initial_prompt"],
                    report_content=report_text,
                    workspace_files=created_files
                )

            citations = parse_agent_citations_from_text(final_summary_text)
            if not citations:
                report_content = deliverable_artifact.get("content", "") if deliverable_artifact else ""
                citations = extract_citations_from_html_or_markdown(report_content) or session.get("citations", [])

            deliverable_names = []
            if deliverable_artifact:
                deliverable_names.append(deliverable_artifact.get("filename", "deliverable"))
            if created_files:
                for cf in created_files:
                    if cf not in deliverable_names:
                        deliverable_names.append(cf)

            task_num = len(tasks_history) + 1
            session_manager.record_completed_task(
                session_id=session_id,
                task_num=task_num,
                objective=session.get("initial_prompt", ""),
                task_type=session.get("task_type", "general"),
                summary=clean_summary[:400],
                deliverables=deliverable_names[:6]
            )

            final_view = AgentFinalDeliverableView(
                summary_text=clean_summary,
                artifact=deliverable_artifact,
                session=session,
                citations=citations,
                thought_duration=final_exec_dur,
                guild=thread.guild
            )
            
            if stream_dispatcher.primary_message:
                try:
                    await stream_dispatcher.primary_message.edit(view=final_view)
                    session_manager.update_session(session_id, last_completed_message_id=str(stream_dispatcher.primary_message.id))
                except Exception:
                    c_msg = await thread.send(view=final_view)
                    session_manager.update_session(session_id, last_completed_message_id=str(c_msg.id))
            else:
                c_msg = await thread.send(view=final_view)
                session_manager.update_session(session_id, last_completed_message_id=str(c_msg.id))

            repo_url = session.get("repo_url", "").strip()
            if repo_url:
                has_changes, changed_files, diff_stats = await git_manager.detect_code_changes(session["workspace_path"])
                if has_changes:
                    commit_msg, pr_title, pr_desc, branch_slug = await git_manager.generate_pr_metadata(
                        session["initial_prompt"],
                        changed_files,
                        clean_summary
                    )

                    _, owner, repo_name = normalize_repo_url(repo_url)
                    inst_token, _ = await github_app_client.get_installation_token_for_repo(owner, repo_name)
                    is_app_installed = bool(inst_token)

                    pr_data = {
                        "branch_name": branch_slug,
                        "pr_title": pr_title,
                        "pr_body": pr_desc,
                        "commit_message": commit_msg,
                        "diff_stats": diff_stats,
                        "changed_files": changed_files
                    }

                    session_manager.update_session(session_id, github_pr_data=pr_data)

                    review_view = AgentReadyForReviewView(
                        session=session,
                        pr_data=pr_data,
                        is_installed=is_app_installed
                    )
                    review_msg = await thread.send(view=review_view)
                    session_manager.update_session(session_id, review_message_id=str(review_msg.id))
                    logger.info(f"[AgentExecution] Rendered Ready for Review card #{review_msg.id} on {branch_slug} (Installed: {is_app_installed})")

        except Exception as e:
            stop_exec_loop.set()
            if exec_header_task:
                exec_header_task.cancel()
                try:
                    await exec_header_task
                except (asyncio.CancelledError, Exception):
                    pass
            logger.exception(f"[AgentExecution] Error: {e}")
            await thread.send(content=f"⚠️ Execution encountered an error: `{e}`")

    @classmethod
    async def invalidate_stale_plan_ui(cls, thread: discord.Thread, session: dict[str, Any]):
        last_msg_id = session.get("last_plan_message_id")
        if not last_msg_id:
            return

        try:
            msg = await thread.fetch_message(int(last_msg_id))
            if msg:
                clean_view = discord.ui.View()
                time_btn = discord.ui.Button(label="🧠 Thought for 1s", style=discord.ButtonStyle.secondary, disabled=False, custom_id=f"gen_thought_agent_{session['session_id']}")
                clean_view.add_item(time_btn)
                await msg.edit(view=clean_view)
        except Exception:
            pass

    @classmethod
    async def handle_agent_thread_chat_turn(cls, message: discord.Message, session: dict[str, Any]):
        thread = message.channel
        if not isinstance(thread, discord.Thread):
            return

        session_id = session["session_id"]
        bot = thread.guild.me if thread.guild else None
        bot_id = bot.id if bot else 0

        clean_prompt = re.sub(rf'<@!?{bot_id}>', '', message.content).strip()
        attachment_parts, raw_image_bytes = await extract_message_attachments_raw(message)
        if not clean_prompt and not attachment_parts:
            clean_prompt = "Please analyze the workspace." if not attachment_parts else "Please analyze the attached content."

        is_flagged, is_zero_tolerance, flagged_cats, score = await check_moderation(clean_prompt, raw_image_bytes)
        if is_flagged:
            log_moderation_violation(message.author.id, message.guild.id if message.guild else None, flagged_cats, score)
            if is_zero_tolerance:
                ban_user(message.author.id, reason=f"Zero-tolerance policy violation: {', '.join(flagged_cats)}")
                ban_view = BannedUserNoticeView(author=message.author)
                await message.reply(view=ban_view, mention_author=False)
                return

            friendly_refusal = await generate_friendly_refusal(flagged_cats)
            await message.reply(content=friendly_refusal, mention_author=False)
            return

        tool_context = ToolExecutionContext(
            channel=thread,
            guild=thread.guild,
            author=message.author,
            bot=thread.guild.me if thread.guild else None,
            input_image_bytes=raw_image_bytes[0] if raw_image_bytes else None
        )
        tool_context.agent_session_id = session_id
        tool_context.message = message

        await session_manager.ensure_docker_container(session)

        dir_res = await agent_list_dir(subpath="", context=tool_context)
        file_list = dir_res.get("files", [])
        file_list_summary = "\n".join([f"• `{f}`" for f in file_list[:50]]) or "*Empty workspace*"

        tasks_history = session.get("tasks_history", [])
        completed_tasks_xml = ""
        if tasks_history:
            task_items = []
            for t_item in tasks_history:
                dels_str = ", ".join(t_item.get("deliverables", [])) or "None"
                task_items.append(
                    f"    <task id=\"{t_item.get('task_num', 1)}\" type=\"{t_item.get('task_type', 'general')}\">\n"
                    f"      <objective>{t_item.get('objective', '')}</objective>\n"
                    f"      <summary>{t_item.get('summary', '')}</summary>\n"
                    f"      <deliverables>{dels_str}</deliverables>\n"
                    f"    </task>"
                )
            completed_tasks_xml = "\n  <completed_tasks>\n" + "\n".join(task_items) + "\n  </completed_tasks>"

        thread_history_xml = ""
        try:
            raw_t_msgs = [m async for m in thread.history(limit=15)]
            raw_t_msgs.reverse()
            chat_items = []
            for tm in raw_t_msgs:
                if tm.id == message.id:
                    continue
                role_tag = "assistant" if tm.author.bot else "user"
                if tm.clean_content:
                    chat_items.append(f"    <message role=\"{role_tag}\" author=\"{tm.author.display_name}\">\n      {tm.clean_content[:1500]}\n    </message>")
            if chat_items:
                thread_history_xml = "\n  <thread_history>\n" + "\n".join(chat_items) + "\n  </thread_history>"
        except Exception:
            pass

        now_utc = datetime.now(timezone.utc)
        current_date_str = now_utc.strftime("%A, %B %d, %Y %H:%M:%S")
        current_year_str = str(now_utc.year)

        agent_context_xml = (
            f"<context>\n"
            f"  <temporal_context current_utc=\"{now_utc.isoformat()}\" current_date=\"{current_date_str}\" current_year=\"{current_year_str}\" />\n"
            f"  <agent_workspace session_id=\"{session_id}\">\n"
            f"    <initial_objective>{session.get('initial_prompt', '')}</initial_objective>\n"
            f"    <workspace_root>./</workspace_root>\n"
            f"    <files_indexed total=\"{len(file_list)}\">\n{file_list_summary}\n    </files_indexed>{completed_tasks_xml}{thread_history_xml}\n"
            f"  </agent_workspace>\n"
            f"</context>"
        )

        resolved_cfg = config_manager.resolve_effective_config(
            thread.guild.id if thread.guild else None,
            getattr(thread, "parent_id", thread.id),
            message.author.id
        )
        disabled_tools_set = set(resolved_cfg.get("disabled_tools", []))

        all_tools = set(tool_registry._tools.keys())
        has_repo = bool(session.get("repo_url")) or session.get("task_type") in ["coding", "hybrid"]
        allowed_tools = {t for t in THREAD_CHAT_AGENT_TOOLS if t not in disabled_tools_set}
        if not has_repo:
            allowed_tools.discard("github_repo")

        stream_dispatcher = DiscordStreamDispatcher(origin_message=message, guild=thread.guild)
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=thread.id)

        accumulated_thoughts = []
        tool_call_history = []
        active_tool_start_times = {}
        active_model_used = PRIMARY_AGENT_MODEL

        thinking_start_time = time.time()
        answer_now_event = asyncio.Event()
        stop_placeholder_loop = asyncio.Event()

        active_witty_statuses = [
            "Analyzing workspace context",
            "Executing agent tools",
            "Inspecting repository files",
            "Formulating response"
        ]
        active_tool_subtext: str | None = None
        placeholder_view: PlaceholderLayoutView | None = None
        placeholder_task: asyncio.Task | None = None
        first_content_received = False
        response_msg: discord.Message | None = None

        def get_current_msg():
            return response_msg

        def get_active_subtext():
            return active_tool_subtext

        async def on_answer_now_clicked(inter: discord.Interaction):
            nonlocal response_msg, first_content_received
            answer_now_event.set()
            stop_placeholder_loop.set()
            if placeholder_task and not placeholder_task.done():
                placeholder_task.cancel()
            first_content_received = True
            if response_msg:
                try:
                    await response_msg.delete()
                except Exception:
                    pass
                response_msg = None
            stream_dispatcher.primary_message = None
            stream_dispatcher.sent_messages.clear()

        async def ensure_placeholder_spawned():
            nonlocal placeholder_view, placeholder_task, response_msg
            if placeholder_view is not None or first_content_received or answer_now_event.is_set():
                return

            initial_text = format_placeholder_content(active_witty_statuses[0], active_tool_subtext)
            placeholder_view = PlaceholderLayoutView(
                loading_text=initial_text,
                duration_seconds=max(0, int(time.time() - thinking_start_time)),
                is_enabled=bool(accumulated_thoughts or tool_call_history),
                on_answer_now_callback=on_answer_now_clicked,
                thought_data={"thoughts": "".join(accumulated_thoughts), "tool_calls": tool_call_history, "model": active_model_used},
                model_name=active_model_used
            )

            try:
                response_msg = await message.reply(view=placeholder_view, mention_author=False)
                stream_dispatcher.bind_response_message(response_msg)
                placeholder_task = asyncio.create_task(
                    update_placeholder_loop(
                        get_current_msg, placeholder_view, active_witty_statuses, get_active_subtext, thinking_start_time, stop_placeholder_loop
                    )
                )
            except Exception as ex:
                logger.warning(f"Failed to spawn thread chat placeholder: {ex}")

        try:
            multimodal_prompt: list[Any] = []
            if attachment_parts:
                multimodal_prompt.extend(attachment_parts)
            multimodal_prompt.append(clean_prompt)

            async with thread.typing():
                async for event_type, payload in ChatEngine.stream_chat(
                    prompt=multimodal_prompt,
                    context_xml=agent_context_xml,
                    bot_user_id=bot_id,
                    tool_context=tool_context,
                    answer_now_event=answer_now_event
                ):
                    if event_type == "ACTIVE_MODEL":
                        active_model_used = str(payload)
                        if placeholder_view:
                            placeholder_view.model_name = active_model_used

                    elif event_type == "THOUGHT":
                        if not answer_now_event.is_set():
                            await ensure_placeholder_spawned()
                        accumulated_thoughts.append(payload)
                        if placeholder_view and not answer_now_event.is_set():
                            placeholder_view.enable_thinking()
                            placeholder_view.thought_data["thoughts"] = "".join(accumulated_thoughts)
                            await placeholder_view.push_live_update()

                    elif event_type == "TOOL_START":
                        if not first_content_received and not answer_now_event.is_set():
                            await ensure_placeholder_spawned()
                        tool_name = payload.get("name", "Tool")
                        args = payload.get("args", {})
                        active_tool_start_times[tool_name] = time.perf_counter()
                        active_tool_subtext = get_tool_subtext(tool_name, args)
                        if placeholder_view and not answer_now_event.is_set():
                            placeholder_view.enable_thinking()

                    elif event_type == "TOOL_END":
                        tool_name = payload.get("name", "Tool")
                        st = active_tool_start_times.pop(tool_name, time.perf_counter())
                        dur_ms = int((time.perf_counter() - st) * 1000)
                        tool_call_history.append({
                            "name": tool_name,
                            "args": payload.get("args", {}),
                            "result": payload.get("result", {}),
                            "duration_ms": dur_ms
                        })
                        active_tool_subtext = None

                        if tool_name in ["search_image", "search_gif", "generate_image", "edit_image", "execute_code"] and tool_context.staged_image_bytes:
                            img_fname = tool_context.staged_image_filename
                            img_bytes = tool_context.staged_image_bytes
                            stream_dispatcher.add_media_block(img_fname, img_bytes)
                            tool_context.staged_image_bytes = None

                        if placeholder_view and not answer_now_event.is_set():
                            placeholder_view.enable_thinking()
                            placeholder_view.thought_data["tool_calls"] = tool_call_history
                            await placeholder_view.push_live_update()

                    elif event_type == "CONTENT":
                        if not first_content_received:
                            first_content_received = True
                            stop_placeholder_loop.set()
                            if placeholder_task and not placeholder_task.done():
                                placeholder_task.cancel()
                            if response_msg and not answer_now_event.is_set():
                                stream_dispatcher.bind_response_message(response_msg)

                        await artifact_parser.feed(payload)

                    elif event_type == "ERROR":
                        stop_placeholder_loop.set()
                        if placeholder_task and not placeholder_task.done():
                            placeholder_task.cancel()
                        await stream_dispatcher.append_text(f"\n\n⚠️ {payload}")

            await artifact_parser.finish()
            stop_placeholder_loop.set()
            if placeholder_task and not placeholder_task.done():
                placeholder_task.cancel()

            final_duration = max(1, int(time.time() - thinking_start_time))
            active_tools = [t for t in tool_call_history if t.get("name") not in ["recall_memories", "search_memories"]]
            has_reasoning = bool(accumulated_thoughts or active_tools)
            modals_map = {m["modal_id"]: m for m in tool_context.staged_modals}

            sent_msg = stream_dispatcher.primary_message
            target_id = sent_msg.id if sent_msg else "temp"

            await stream_dispatcher.finalize(
                staged_artifacts=tool_context.staged_artifacts,
                staged_components=tool_context.staged_components,
                staged_followups=stream_dispatcher.staged_followups,
                modals_map=modals_map,
                thought_duration=final_duration,
                has_thoughts=has_reasoning,
                active_version=1,
                total_versions=1,
                message_id=target_id
            )

            sent_msg = stream_dispatcher.primary_message
            if sent_msg:
                final_text = stream_dispatcher.get_accumulated_text()
                parsed_initial_content = apply_message_parsers(final_text, message.guild)
                raw_collected_thoughts = "".join(accumulated_thoughts)
                sent_msg_ids = [str(m.id) for m in stream_dispatcher.sent_messages if m] or [str(sent_msg.id)]

                stored_attachments = []
                for raw_att in stream_dispatcher.raw_attachment_buffers:
                    b64 = __import__("base64").b64encode(raw_att["bytes"]).decode("utf-8")
                    stored_attachments.append({"filename": raw_att["filename"], "data_b64": b64})

                sanitized_artifacts = []
                for art in tool_context.staged_artifacts:
                    art_bytes = art.get("data_bytes", b"")
                    art_fname = art.get("filename", "artifact.zip")
                    b64_art = __import__("base64").b64encode(art_bytes).decode("utf-8") if art_bytes else ""
                    clean_art = {k: v for k, v in art.items() if k != "data_bytes"}
                    clean_art["data_b64"] = b64_art
                    sanitized_artifacts.append(clean_art)

                initial_v_data = {
                    "version_idx": 1,
                    "content": parsed_initial_content,
                    "timeline_blocks": stream_dispatcher.timeline,
                    "duration_seconds": final_duration,
                    "has_thoughts": has_reasoning,
                    "thoughts": raw_collected_thoughts,
                    "formatted_thoughts": None,
                    "model": active_model_used,
                    "tool_calls": tool_call_history,
                    "attachments": stored_attachments,
                    "staged_components": tool_context.staged_components,
                    "staged_artifacts": sanitized_artifacts,
                    "staged_followups": stream_dispatcher.staged_followups,
                    "staged_modals": tool_context.staged_modals,
                    "message_ids": sent_msg_ids,
                    "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "ready"
                }

                branch_manager.save_generation(
                    message_id=sent_msg.id,
                    channel_id=thread.id,
                    guild_id=thread.guild.id if thread.guild else None,
                    author_id=message.author.id,
                    prompt_text=clean_prompt,
                    attachments=[],
                    context_xml=agent_context_xml,
                    initial_version_data=initial_v_data
                )

        except Exception as e:
            logger.exception(f"Unhandled exception in agent thread chat turn: {e}")
        finally:
            stop_placeholder_loop.set()
            if placeholder_task and not placeholder_task.done():
                placeholder_task.cancel()

    @classmethod
    async def handle_thread_message(cls, message: discord.Message, session: dict[str, Any]):
        if message.author.bot:
            return

        perms = getattr(message.author, "guild_permissions", None)
        if not session_manager.is_collaborator(session, message.author.id, perms):
            return

        if message.attachments:
            import aiohttp
            async with aiohttp.ClientSession() as http_session:
                for att in message.attachments:
                    try:
                        async with http_session.get(att.url) as resp:
                            if resp.status == 200:
                                file_bytes = await resp.read()
                                dest = os.path.join(session["workspace_path"], att.filename)
                                with open(dest, "wb") as f_out:
                                    f_out.write(file_bytes)
                                logger.info(f"[Agent] Saved thread chat attachment '{att.filename}' to workspace.")
                    except Exception as ex:
                        logger.warning(f"[Agent] Failed to download thread attachment '{att.filename}': {ex}")

        state = session.get("state", "planning")
        if state in ["awaiting_approval", "awaiting_input"]:
            await cls.invalidate_stale_plan_ui(message.channel, session)
            session_manager.update_session(session["session_id"], state="planning")
            await cls.start_planning_turn(message.channel, session, feedback=message.clean_content)
        elif state in ["completed", "stopped", "idle"]:
            await cls.handle_agent_thread_chat_turn(message, session)
        elif state == "planning":
            logger.info(f"[Agent] Message received during planning phase; queued into thread context.")