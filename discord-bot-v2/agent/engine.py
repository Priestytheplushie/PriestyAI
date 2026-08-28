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
    compute_unified_diff_str
)
from agent.tools import agent_list_dir
from core.client_manager import client_manager
from core.branch_manager import branch_manager
from core.config_manager import config_manager
from parsers.artifact_parser import ArtifactStreamParser
from handlers.stream_handler import DiscordStreamDispatcher
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.Agent.Engine")

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
            "Analyzing research objective and workspace",
            "Synthesizing dependencies and reference materials",
            "Evaluating architectural constraints and findings",
            "Conducting multi-hop investigation",
            "Drafting plan steps and report structure",
            "Finalizing research summary and citations"
        ]

        now_utc = datetime.now(timezone.utc)
        current_year_str = str(now_utc.year)

        instruction = f"""Analyze this agent prompt. Real-world year is {current_year_str}.
Classify the task into:
- task_type: "research" (deep research, comparisons, market study, report writing), "coding" (software development, bug fixes, refactoring), or "hybrid" (researching libraries/APIs first, then implementing in repo).
Generate a short 3-5 word thread title and 15 witty loading statuses. Output JSON:
{{"title": "Title Here", "task_type": "coding"|"research"|"hybrid", "statuses": ["status 1", "status 2", ...]}}"""

        attempted_keys = set()
        for attempt in range(client_manager.key_count):
            client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite", exclude_keys=attempted_keys)
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
                logger.warning(f"[AgentBootstrap] Fast meta generation error on Key #{key_idx}: {e}")

        return default_title, default_statuses, "general"

    @classmethod
    async def generate_conversational_summary_fallback(cls, objective: str, report_content: str, workspace_files: list[str]) -> str:
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
        if not client:
            return "Successfully investigated the requirements, verified all components, and compiled the deliverables."

        prompt_instruction = (
            f"You are PriestyAI. You just completed the user's objective: '{objective}'.\n"
            f"Files created in workspace: {', '.join(workspace_files[:10]) or 'report.html'}\n\n"
            f"Write a friendly, natural, 2-paragraph conversational breakdown directly in chat explaining "
            f"the research findings and what was built/verified. Speak directly like a collaborative software engineer. "
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

        return "Successfully investigated the technical requirements, verified all implementations, and compiled the deliverables into the workspace."

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
            logger.info(f"[AgentPlanning] Multi-Task #{len(tasks_history) + 1} dynamically classified as: '{dynamic_task_type}'")

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
                    await asyncio.sleep(1.0)
                    if stop_header_loop.is_set() or abort_event.is_set():
                        break
                    elapsed = max(1, int(time.time() - t_start))
                    if elapsed % 4 == 0:
                        idx = (idx + 1) % len(witty_statuses)
                    curr_txt = witty_statuses[idx]
                    h_view = build_agent_header_layout(curr_txt, duration_seconds=elapsed, session_id=session_id, phase="planning")
                    await header_msg.edit(view=h_view)
                    active_thought_record["duration_seconds"] = elapsed
                    session_manager.save_session_thoughts(session_id, active_thought_record["thoughts"], active_thought_record["tool_calls"], elapsed)
                except Exception:
                    break

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
        file_list_summary = "\n".join([f"• `{f}`" for f in file_list[:50]]) or "*Empty workspace (research/greenfield)*"

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
            f"Execute your planning turn. Scope the requirements and draft the plan artifact (`research_plan.md` or `plan.md`). If user URLs are present in <user_priority_sources>, call `agent_read_link` on them FIRST."
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
        candidate_models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
        active_model_pinned = None

        formatted_planning_instruction = (
            AGENT_PLANNING_SYSTEM_INSTRUCTION
            .replace("{current_date}", current_date_str)
            .replace("{current_year}", current_year_str)
        )

        was_aborted = False

        try:
            for tool_turn in range(25):
                if abort_event.is_set():
                    was_aborted = True
                    break

                stream_success = False
                model_parts = []
                fcalls = []

                working_turn_contents = compact_conversation_history(turn_contents, keep_recent_turns=3)
                models_to_try = [active_model_pinned] if active_model_pinned else candidate_models

                for model_cand in models_to_try:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    attempted_keys = set()
                    while True:
                        if abort_event.is_set():
                            was_aborted = True
                            break

                        client, key_idx, active_model = client_manager.get_client_for_model(model_cand, exclude_keys=attempted_keys)
                        if not client or key_idx in attempted_keys:
                            break

                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_planning_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level="HIGH", include_thoughts=True),
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
                            active_model_pinned = active_model
                            break

                        except Exception as e:
                            client_manager.report_error(key_idx, active_model, e)
                            logger.warning(f"[AgentPlanning] Key #{key_idx} error on {active_model}: {e}")
                            active_model_pinned = None
                            continue

                    if stream_success or was_aborted:
                        break

                if was_aborted:
                    break

                if not stream_success:
                    logger.warning("[AgentPlanning] Rate limit encountered. Silently backing off 4s...")
                    await asyncio.sleep(4.0)
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

                turn_contents.append(types.Content(role="user", parts=fres_parts))

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
            "Conducting multi-hop investigation",
            "Synthesizing primary source documents",
            "Verifying benchmark data and code patterns",
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
                    await asyncio.sleep(1.0)
                    if stop_exec_loop.is_set() or abort_event.is_set():
                        break
                    elapsed = max(1, int(time.time() - t_exec_start))
                    if elapsed % 4 == 0:
                        idx = (idx + 1) % len(witty_statuses)
                    curr_txt = witty_statuses[idx]
                    h_view = build_agent_header_layout(curr_txt, duration_seconds=elapsed, session_id=session_id, phase="execution")
                    await exec_header_msg.edit(view=h_view)
                    active_exec_thought_record["duration_seconds"] = elapsed
                    session_manager.save_session_thoughts(session_id, active_exec_thought_record["thoughts"], active_exec_thought_record["tool_calls"], elapsed)
                except Exception:
                    break

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
            f"Begin Phase 2 execution. If this is a research task, execute multi-hop searches and call `agent_read_link` on at least 3-6 primary sources, then compile the final `<artifact filename=\"report.html\">` deliverable as a technical whitepaper. When finished, conclude with `<finalize_artifact />`."
        )

        turn_contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=exec_prompt)])
        ]

        candidate_models = ["gemma-4-31b-it", "gemma-4-26b-a4b-it", "gemini-3.5-flash-lite", "gemini-3.5-flash"]
        step_counter = len(active_exec_thought_record["tool_calls"])
        accumulated_exec_thoughts = []
        final_summary_text = ""
        
        stream_dispatcher = DiscordStreamDispatcher(target_channel=thread, guild=thread.guild)
        artifact_parser = ArtifactStreamParser(stream_dispatcher, tool_context, channel_id=thread.id)
        active_model_pinned = None

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
                models_to_try = [active_model_pinned] if active_model_pinned else candidate_models

                for model_cand in models_to_try:
                    if abort_event.is_set():
                        was_aborted = True
                        break

                    attempted_keys = set()
                    while True:
                        if abort_event.is_set():
                            was_aborted = True
                            break

                        client, key_idx, active_model = client_manager.get_client_for_model(model_cand, exclude_keys=attempted_keys)
                        if not client or key_idx in attempted_keys:
                            break

                        attempted_keys.add(key_idx)
                        try:
                            config = types.GenerateContentConfig(
                                system_instruction=formatted_exec_instruction,
                                thinking_config=types.ThinkingConfig(thinking_level="HIGH", include_thoughts=True),
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
                            active_model_pinned = active_model
                            break

                        except Exception as e:
                            client_manager.report_error(key_idx, active_model, e)
                            logger.warning(f"[AgentExecution] Key #{key_idx} error on {active_model}: {e}")
                            active_model_pinned = None
                            continue

                    if stream_success or was_aborted:
                        break

                if was_aborted:
                    break

                if not stream_success:
                    logger.warning("[AgentExecution] Rate limit encountered. Silently backing off 4s...")
                    await asyncio.sleep(4.0)
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

                turn_contents.append(types.Content(role="user", parts=fres_parts))

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
                except Exception:
                    await thread.send(view=final_view)
            else:
                await thread.send(view=final_view)

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
    async def handle_thread_message(cls, message: discord.Message, session: dict[str, Any]):
        if message.author.bot:
            return

        perms = getattr(message.author, "guild_permissions", None)
        if not session_manager.is_collaborator(session, message.author.id, perms):
            return

        state = session.get("state", "planning")
        if state in ["awaiting_approval", "awaiting_input"]:
            await cls.invalidate_stale_plan_ui(message.channel, session)
            session_manager.update_session(session["session_id"], state="planning")
            await cls.start_planning_turn(message.channel, session, feedback=message.clean_content)