import re
import time
import io
import zipfile
import asyncio
import logging
from typing import Any
from core.branch_manager import branch_manager

logger = logging.getLogger("PriestyAI.ArtifactParser")

def parse_artifact_attributes(tag_str: str) -> tuple[str, str, str]:
    fn_match = re.search(r'(?:identifier|filename|name|id)=["\']?([^"\'\s>]+)["\']?', tag_str, re.IGNORECASE)
    filename = fn_match.group(1).strip() if fn_match else ""
    
    title_match = re.search(r'title=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    if not title_match:
        title_match = re.search(r'title=([^"\'\s>]+)', tag_str, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    type_match = re.search(r'type=["\']?([^"\'\s>]+)["\']?', tag_str, re.IGNORECASE)
    art_type = type_match.group(1).strip() if type_match else ""

    if not filename and title:
        filename = title.lower().replace(" ", "_") + ".txt"
    elif not filename:
        filename = "artifact.txt"
        
    if not title:
        title = filename

    return filename, title, art_type

def clean_code_content(raw_code: str) -> str:
    cleaned = raw_code.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        if first_nl != -1:
            cleaned = cleaned[first_nl + 1:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].rstrip()
    return cleaned

def parse_xml_files_from_body(body_content: str, default_filename: str) -> tuple[list[dict[str, Any]], bool]:
    file_blocks = re.findall(r'<file\s+(?:filename|name|identifier)=["\']?([^"\'\s>]+)["\']?\s*>(.*?)</file>', body_content, re.DOTALL | re.IGNORECASE)
    
    if file_blocks:
        parsed_files = []
        for fn, fcontent in file_blocks:
            f_clean = clean_code_content(fcontent)
            lines = max(1, len(f_clean.splitlines()))
            parsed_files.append({
                "filename": fn.strip(),
                "content": f_clean,
                "lines": lines,
                "size_bytes": len(f_clean.encode("utf-8"))
            })
        return parsed_files, True
    else:
        clean_content = clean_code_content(body_content)
        lines = max(1, len(clean_content.splitlines()))
        parsed_files = [{
            "filename": default_filename,
            "content": clean_content,
            "lines": lines,
            "size_bytes": len(clean_content.encode("utf-8"))
        }]
        return parsed_files, False

class ArtifactStreamParser:
    def __init__(self, stream_dispatcher: Any, tool_context: Any, channel_id: str | int = "global"):
        self.dispatcher = stream_dispatcher
        self.tool_context = tool_context
        self.channel_id = channel_id
        
        self.state = "TEXT"
        self.text_buffer = ""
        self.artifact_buffer = ""
        
        self.current_filename = ""
        self.current_title = ""
        self.current_art_id = ""
        self.current_art_type = ""
        self.current_start_time = 0.0

    async def feed(self, chunk: str):
        if not chunk:
            return

        if self.state == "TEXT":
            self.text_buffer += chunk
            await self._process_text_buffer()
        elif self.state == "IN_ARTIFACT":
            self.artifact_buffer += chunk
            await self._process_artifact_buffer()

    async def _process_text_buffer(self):
        while self.text_buffer and self.state == "TEXT":
            tag_start = self.text_buffer.find("<")
            
            if tag_start == -1:
                await self.dispatcher.append_text(self.text_buffer)
                self.text_buffer = ""
                break

            if tag_start > 0:
                preceding_text = self.text_buffer[:tag_start]
                await self.dispatcher.append_text(preceding_text)
                self.text_buffer = self.text_buffer[tag_start:]

            lower_buf = self.text_buffer.lower()

            possible_artifact = False
            for prefix in ["<artifact", "<antartifact"]:
                if lower_buf.startswith(prefix) or prefix.startswith(lower_buf):
                    possible_artifact = True
                    break

            if possible_artifact:
                gt_idx = self.text_buffer.find(">")
                if gt_idx != -1:
                    full_tag_str = self.text_buffer[:gt_idx + 1]
                    remainder = self.text_buffer[gt_idx + 1:]

                    filename, title, art_type = parse_artifact_attributes(full_tag_str)
                    self.current_filename = filename
                    self.current_title = title
                    self.current_art_type = art_type
                    self.current_art_id = f"art_gen_{int(time.time()*1000)}"
                    self.current_start_time = time.time()

                    placeholder = {
                        "artifact_id": self.current_art_id,
                        "filename": self.current_filename,
                        "title": self.current_title,
                        "status": "generating",
                        "is_generating": True,
                        "start_time": self.current_start_time
                    }
                    self.dispatcher.add_artifact_placeholder_record(placeholder)
                    await self.dispatcher.flush(is_final=False, force=True)

                    self.state = "IN_ARTIFACT"
                    self.text_buffer = ""

                    if remainder:
                        self.artifact_buffer += remainder
                        await self._process_artifact_buffer()
                    break
                else:
                    break
            else:
                await self.dispatcher.append_text(self.text_buffer[0])
                self.text_buffer = self.text_buffer[1:]

    async def _process_artifact_buffer(self):
        end_match = re.search(r'</(?:artifact|antartifact)>', self.artifact_buffer, re.IGNORECASE)
        if end_match:
            end_start = end_match.start()
            end_end = end_match.end()
            
            artifact_body = self.artifact_buffer[:end_start]
            post_artifact_text = self.artifact_buffer[end_end:]
            
            await self._complete_artifact(artifact_body)
            
            self.state = "TEXT"
            self.artifact_buffer = ""
            
            if post_artifact_text:
                self.text_buffer += post_artifact_text
                await self._process_text_buffer()
        else:
            if self.dispatcher:
                now_t = asyncio.get_event_loop().time()
                last_t = getattr(self.dispatcher, "last_edit_time", 0.0)
                if (now_t - last_t) >= 1.0:
                    await self.dispatcher.flush(is_final=False)

    async def _complete_artifact(self, body_content: str):
        parsed_files, is_multi = parse_xml_files_from_body(body_content, self.current_filename)
        
        filename = self.current_filename
        if is_multi and not filename.endswith(".zip"):
            clean_name = filename.rsplit(".", 1)[0] if "." in filename else (self.current_title.lower().replace(" ", "_") or "project")
            filename = f"{clean_name}.zip"
        
        channel_id = getattr(self.tool_context.channel, "id", self.channel_id) if self.tool_context else self.channel_id
        
        if is_multi:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in parsed_files:
                    zf.writestr(f["filename"], f["content"])
            zip_buffer.seek(0)
            data_bytes = zip_buffer.getvalue()
            content_str = ""
        else:
            content_str = parsed_files[0]["content"] if parsed_files else ""
            data_bytes = content_str.encode("utf-8")

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=filename,
            title=self.current_title or filename,
            content=content_str,
            files=parsed_files,
            change_summary=f"Created artifact {filename}",
            is_update=False
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "project_zip" if is_multi else "single_file",
            "title": record["title"],
            "filename": filename,
            "description": f"Created {filename}",
            "file_count": len(parsed_files),
            "total_lines": record["latest_version_data"]["lines"],
            "size_bytes": len(data_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "files": parsed_files,
            "data_bytes": data_bytes,
            "status": "ready",
            "is_generating": False
        }

        if self.tool_context:
            if not hasattr(self.tool_context, "staged_artifacts"):
                self.tool_context.staged_artifacts = []
            self.tool_context.staged_artifacts.append(artifact_payload)

        self.dispatcher.update_artifact_ready(artifact_payload)
        if data_bytes:
            self.dispatcher.add_raw_attachment(filename, data_bytes)

        await self.dispatcher.flush(is_final=False, force=True)
        logger.info(f"[ArtifactParser] Completed XML artifact '{filename}' ({len(parsed_files)} file(s))")

    async def finish(self):
        if self.state == "IN_ARTIFACT":
            logger.warning("[ArtifactParser] Stream ended before </artifact> tag; auto-closing artifact...")
            await self._complete_artifact(self.artifact_buffer)
            self.state = "TEXT"
            self.artifact_buffer = ""
            
        if self.text_buffer:
            await self.dispatcher.append_text(self.text_buffer)
            self.text_buffer = ""
