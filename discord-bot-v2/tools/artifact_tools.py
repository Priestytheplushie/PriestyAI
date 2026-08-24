import io
import time
import zipfile
import difflib
import logging
from typing import Any
import discord
from tools.registry import tool_registry, ToolExecutionContext
from core.branch_manager import branch_manager

logger = logging.getLogger("PriestyAI.ArtifactTools")

def generate_unified_diff(old_code: str, new_code: str, filename: str, v_old: int, v_new: int) -> tuple[str, int, int]:
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)
    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"{filename} (v{v_old})",
        tofile=f"{filename} (v{v_new})",
        n=3
    ))
    
    diff_text = "".join(diff)
    additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

    res_text = diff_text if diff_text.strip() else f"# No textual changes detected between v{v_old} and v{v_new}"
    return res_text, additions, deletions

@tool_registry.register(
    name="create_artifact",
    description=(
        "MANDATORY tool to create standalone code deliverables, complete scripts, applications, or multi-file projects for the user.\n"
        "Whenever asked to 'build / make / create a script, program, or app' (e.g. calculator, solver, keygen, web app, game), "
        "you MUST invoke this tool rather than writing the entire script in chat markdown!\n"
        "- Single script: provide 'filename', 'title', and 'content' (e.g. filename='calculator.py', content='...').\n"
        "- Multi-file project: provide 'files' as a list of dicts: [{'filename': 'index.html', 'content': '...'}, ...].\n"
        "Multi-file projects are automatically bundled into a downloadable .zip archive!"
    )
)
async def create_artifact(
    title: str,
    filename: str = "",
    content: str = "",
    description: str = "",
    files: list[dict[str, Any]] | None = None,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not context:
        return {"error": "Execution context unavailable."}

    channel_id = getattr(context.channel, "id", "global")
    parsed_files: list[dict[str, Any]] = []

    if files and isinstance(files, list):
        for f in files:
            if isinstance(f, dict) and "filename" in f and "content" in f:
                f_name = f["filename"].strip()
                f_content = f["content"]
                lines = len(f_content.splitlines())
                parsed_files.append({
                    "filename": f_name,
                    "content": f_content,
                    "lines": max(1, lines),
                    "size_bytes": len(f_content.encode("utf-8"))
                })

        if not filename.endswith(".zip"):
            clean_name = filename.rsplit(".", 1)[0] if "." in filename else (title.lower().replace(" ", "_") or "project")
            zip_filename = f"{clean_name}.zip"
        else:
            zip_filename = filename

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in parsed_files:
                zf.writestr(f["filename"], f["content"])
        
        zip_buffer.seek(0)
        zip_bytes = zip_buffer.getvalue()

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=zip_filename,
            title=title or "Project Workspace",
            content="",
            files=parsed_files,
            change_summary=description or "Initial project bundle",
            is_update=False
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "project_zip",
            "title": record["title"],
            "filename": zip_filename,
            "description": description or f"Multi-file project with {len(parsed_files)} files",
            "file_count": len(parsed_files),
            "total_lines": record["latest_version_data"]["lines"],
            "size_bytes": len(zip_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "files": parsed_files,
            "data_bytes": zip_bytes
        }

    else:
        file_name = filename or "script.txt"
        file_content = content or ""
        lines = len(file_content.splitlines())
        raw_bytes = file_content.encode("utf-8")

        parsed_files.append({
            "filename": file_name,
            "content": file_content,
            "lines": max(1, lines),
            "size_bytes": len(raw_bytes)
        })

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=file_name,
            title=title or file_name,
            content=file_content,
            files=parsed_files,
            change_summary=description or "Initial script",
            is_update=False
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "single_file",
            "title": record["title"],
            "filename": file_name,
            "description": description or f"Standalone file ({lines} lines)",
            "file_count": 1,
            "total_lines": max(1, lines),
            "size_bytes": len(raw_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "files": parsed_files,
            "data_bytes": raw_bytes
        }

    if not hasattr(context, "staged_artifacts"):
        context.staged_artifacts = []
    context.staged_artifacts.append(artifact_payload)

    logger.info(f"[create_artifact] Created '{artifact_payload['title']}' ({artifact_payload['filename']}, v{artifact_payload['active_version']})")
    return {
        "status": "created",
        "artifact_id": artifact_payload["artifact_id"],
        "title": artifact_payload["title"],
        "filename": artifact_payload["filename"],
        "version": artifact_payload["active_version"]
    }

@tool_registry.register(
    name="update_artifact",
    description=(
        "Updates or refactors an existing code artifact in this conversation (e.g. adding features, fixing bugs, or changing styling).\n"
        "- filename: The name of the existing artifact to update (e.g. 'calculator.py', 'server.py').\n"
        "- content: The complete, updated source code for the file.\n"
        "- change_summary: Brief 1-sentence description of what changed (e.g. 'Added trig mode toggle and dark theme').\n"
        "Automatically increments the artifact version, calculates a visual code diff, and adds it to version history!"
    )
)
async def update_artifact(
    filename: str,
    content: str = "",
    change_summary: str = "",
    files: list[dict[str, Any]] | None = None,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    if not context:
        return {"error": "Execution context unavailable."}

    channel_id = getattr(context.channel, "id", "global")
    existing = branch_manager.get_artifact_by_channel_and_file(channel_id, filename)

    parsed_files: list[dict[str, Any]] = []
    diff_text = ""
    additions = 0
    deletions = 0
    v_old = len(existing.get("versions", [])) if existing else 1
    v_new = v_old + 1

    if filename.endswith(".zip") and files:
        for f in files:
            if isinstance(f, dict) and "filename" in f and "content" in f:
                f_name = f["filename"].strip()
                f_content = f["content"]
                lines = len(f_content.splitlines())
                parsed_files.append({
                    "filename": f_name,
                    "content": f_content,
                    "lines": max(1, lines),
                    "size_bytes": len(f_content.encode("utf-8"))
                })

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in parsed_files:
                zf.writestr(f["filename"], f["content"])
        
        zip_buffer.seek(0)
        zip_bytes = zip_buffer.getvalue()

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=filename,
            title=existing.get("title", filename) if existing else filename,
            content="",
            files=parsed_files,
            change_summary=change_summary or "Updated project files",
            is_update=True
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "project_zip",
            "title": record["title"],
            "filename": filename,
            "description": change_summary or f"Updated project bundle (v{record['active_version']})",
            "file_count": len(parsed_files),
            "total_lines": record["latest_version_data"]["lines"],
            "size_bytes": len(zip_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "files": parsed_files,
            "data_bytes": zip_bytes
        }

    else:
        file_content = content or ""
        lines = len(file_content.splitlines())
        raw_bytes = file_content.encode("utf-8")

        prev_versions = existing.get("versions", []) if existing else []
        old_code = prev_versions[-1].get("content", "") if prev_versions else ""
        diff_text, additions, deletions = generate_unified_diff(old_code, file_content, filename, v_old, v_new)

        parsed_files.append({
            "filename": filename,
            "content": file_content,
            "lines": max(1, lines),
            "size_bytes": len(raw_bytes)
        })

        summary_with_stats = change_summary or "Updated code implementation"

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=filename,
            title=existing.get("title", filename) if existing else filename,
            content=file_content,
            files=parsed_files,
            change_summary=summary_with_stats,
            is_update=True
        )

        if record.get("versions"):
            record["versions"][-1]["diff"] = diff_text
            record["versions"][-1]["additions"] = additions
            record["versions"][-1]["deletions"] = deletions

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "single_file",
            "title": record["title"],
            "filename": filename,
            "description": summary_with_stats,
            "file_count": 1,
            "total_lines": max(1, lines),
            "size_bytes": len(raw_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "additions": additions,
            "deletions": deletions,
            "diff": diff_text,
            "files": parsed_files,
            "data_bytes": raw_bytes
        }

    if not hasattr(context, "staged_artifacts"):
        context.staged_artifacts = []
    context.staged_artifacts.append(artifact_payload)

    logger.info(f"[update_artifact] Updated '{artifact_payload['filename']}' -> v{artifact_payload['active_version']} (+{additions} -{deletions})")
    return {
        "status": "updated",
        "artifact_id": artifact_payload["artifact_id"],
        "filename": artifact_payload["filename"],
        "old_version": v_old,
        "new_version": v_new,
        "total_versions": artifact_payload["total_versions"],
        "additions": additions,
        "deletions": deletions,
        "summary": change_summary,
        "diff": diff_text
    }