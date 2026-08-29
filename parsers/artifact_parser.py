import re
import time
import io
import zipfile
import asyncio
import logging
from typing import Any
from core.branch_manager import branch_manager
from core.screenshot_service import screenshot_service

logger = logging.getLogger("PriestyAI.ArtifactParser")

def parse_artifact_attributes(tag_str: str) -> tuple[str, str, str]:
    fn_match = re.search(r'(?:identifier|filename|name|id)=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    if not fn_match:
        fn_match = re.search(r'(?:identifier|filename|name|id)=([^\s>]+)', tag_str, re.IGNORECASE)
    filename = fn_match.group(1).strip() if fn_match else ""

    title_match = re.search(r'title=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    if not title_match:
        title_match = re.search(r'title=([^\s>]+)', tag_str, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else ""

    type_match = re.search(r'type=["\']?([^"\'\s>]+)["\']?', tag_str, re.IGNORECASE)
    art_type = type_match.group(1).strip() if type_match else ""

    if not filename and title:
        filename = title.lower().replace(" ", "_") + ".txt"

    if not filename and not title:
        return "", "", ""

    if not title:
        title = filename

    return filename, title, art_type

def parse_quiz_attributes(tag_str: str) -> tuple[str, str, str]:
    title_match = re.search(r'title=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else "Quiz"

    topic_match = re.search(r'topic=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    topic = topic_match.group(1).strip() if topic_match else "Knowledge Check"

    diff_match = re.search(r'difficulty=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    difficulty = diff_match.group(1).strip() if diff_match else "Medium"

    return title, topic, difficulty

def parse_xml_quiz_body(body_content: str, default_title: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    q_matches = re.finditer(
        r'<question\s+([^>]*?)>(.*?)</question>',
        body_content,
        re.DOTALL | re.IGNORECASE
    )

    for q_match in q_matches:
        q_attrs = q_match.group(1)
        q_body = q_match.group(2)

        text_m = re.search(r'text=["\']([^"\'\n>]+)["\']', q_attrs, re.IGNORECASE)
        cat_m = re.search(r'category=["\']([^"\'\n>]+)["\']', q_attrs, re.IGNORECASE)

        q_text = text_m.group(1).strip() if text_m else ""
        if not q_text:
            inner_text_m = re.search(r'<text>(.*?)</text>', q_body, re.DOTALL | re.IGNORECASE)
            q_text = inner_text_m.group(1).strip() if inner_text_m else "Question"

        q_cat = cat_m.group(1).strip() if cat_m else "General"

        options: list[dict[str, Any]] = []
        opt_matches = re.finditer(
            r'<option\s+([^>]*?)(?:\/>|>(.*?)<\/option>)',
            q_body,
            re.DOTALL | re.IGNORECASE
        )

        for o_match in opt_matches:
            o_attrs = o_match.group(1)
            o_inner = o_match.group(2) or ""

            ot_m = re.search(r'text=["\']([^"\'\n>]+)["\']', o_attrs, re.IGNORECASE)
            oc_m = re.search(r'correct=["\']?(true|1|yes)["\']?', o_attrs, re.IGNORECASE)
            oe_m = re.search(r'explanation=["\']([^"\'\n>]+)["\']', o_attrs, re.IGNORECASE)

            opt_text = ot_m.group(1).strip() if ot_m else o_inner.strip()
            is_correct = bool(oc_m)
            explanation = oe_m.group(1).strip() if oe_m else ""

            if opt_text:
                options.append({
                    "text": opt_text,
                    "correct": is_correct,
                    "explanation": explanation
                })

        if q_text and options:
            questions.append({
                "text": q_text,
                "category": q_cat,
                "options": options
            })

    return questions

def parse_followup_attributes(tag_str: str) -> str:
    label_match = re.search(r'(?:label|title|text)=["\']([^"\'\n>]+)["\']', tag_str, re.IGNORECASE)
    if not label_match:
        label_match = re.search(r'(?:label|title|text)=([^"\'\s>]+)', tag_str, re.IGNORECASE)
    return label_match.group(1).strip() if label_match else "Follow-up"

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
    file_blocks = re.findall(
        r'<file\s+(?:filename|name|identifier)=["\']?([^"\'\s>]+)["\']?\s*>(.*?)</file>',
        body_content,
        re.DOTALL | re.IGNORECASE
    )

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

def find_outer_closing_tag(buffer: str, tag_name: str) -> tuple[int, int]:
    pattern = re.compile(rf'</(?:{tag_name}|ant{tag_name})>', re.IGNORECASE)
    in_fence = False
    pos = 0

    while pos < len(buffer):
        fence_idx = buffer.find("```", pos)
        tag_match = pattern.search(buffer, pos)

        if not tag_match:
            break

        tag_start = tag_match.start()
        tag_end = tag_match.end()

        if fence_idx != -1 and fence_idx < tag_start:
            in_fence = not in_fence
            pos = fence_idx + 3
            continue

        if not in_fence:
            return tag_start, tag_end

        pos = tag_end

    return -1, -1

class ArtifactStreamParser:
    def __init__(self, stream_dispatcher: Any, tool_context: Any, channel_id: str | int = "global"):
        self.dispatcher = stream_dispatcher
        self.tool_context = tool_context
        self.channel_id = channel_id
        
        self.state = "TEXT"
        self.in_code_block = False
        self.in_inline_code = False
        self.text_buffer = ""
        self.artifact_buffer = ""
        self.followup_buffer = ""
        self.quiz_buffer = ""
        
        self.current_filename = ""
        self.current_title = ""
        self.current_art_id = ""
        self.current_art_type = ""
        self.current_start_time = 0.0
        self.current_followup_label = ""
        
        self.current_quiz_id = ""
        self.current_quiz_title = ""
        self.current_quiz_topic = ""
        self.current_quiz_difficulty = ""

    async def feed(self, chunk: str):
        if not chunk:
            return

        if self.state == "TEXT":
            self.text_buffer += chunk
            await self._process_text_buffer()
        elif self.state == "IN_ARTIFACT":
            self.artifact_buffer += chunk
            await self._process_artifact_buffer()
        elif self.state == "IN_FOLLOWUP":
            self.followup_buffer += chunk
            await self._process_followup_buffer()
        elif self.state == "IN_QUIZ":
            self.quiz_buffer += chunk
            await self._process_quiz_buffer()

    async def _process_text_buffer(self):
        while self.text_buffer and self.state == "TEXT":
            if self.in_code_block:
                closing_fence_idx = self.text_buffer.find("```")
                if closing_fence_idx != -1:
                    text_chunk = self.text_buffer[:closing_fence_idx + 3]
                    await self.dispatcher.append_text(text_chunk)
                    self.text_buffer = self.text_buffer[closing_fence_idx + 3:]
                    self.in_code_block = False
                    continue
                else:
                    if len(self.text_buffer) > 2:
                        safe_chunk = self.text_buffer[:-2]
                        await self.dispatcher.append_text(safe_chunk)
                        self.text_buffer = self.text_buffer[-2:]
                    break

            if self.in_inline_code:
                closing_tick_idx = self.text_buffer.find("`")
                if closing_tick_idx != -1:
                    text_chunk = self.text_buffer[:closing_tick_idx + 1]
                    await self.dispatcher.append_text(text_chunk)
                    self.text_buffer = self.text_buffer[closing_tick_idx + 1:]
                    self.in_inline_code = False
                    continue
                else:
                    await self.dispatcher.append_text(self.text_buffer)
                    self.text_buffer = ""
                    break

            fence_idx = self.text_buffer.find("```")
            tick_idx = self.text_buffer.find("`")
            tag_start = self.text_buffer.find("<")

            if fence_idx != -1 and (tag_start == -1 or fence_idx < tag_start):
                text_chunk = self.text_buffer[:fence_idx + 3]
                await self.dispatcher.append_text(text_chunk)
                self.text_buffer = self.text_buffer[fence_idx + 3:]
                self.in_code_block = True
                continue

            if tick_idx != -1 and (tag_start == -1 or tick_idx < tag_start):
                text_chunk = self.text_buffer[:tick_idx + 1]
                await self.dispatcher.append_text(text_chunk)
                self.text_buffer = self.text_buffer[tick_idx + 1:]
                self.in_inline_code = True
                continue

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
                    if not filename:
                        await self.dispatcher.append_text(self.text_buffer[0])
                        self.text_buffer = self.text_buffer[1:]
                        continue

                    self.current_filename = filename
                    self.current_title = title or filename
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

            possible_quiz = lower_buf.startswith("<quiz") or "<quiz".startswith(lower_buf)
            if possible_quiz:
                gt_idx = self.text_buffer.find(">")
                if gt_idx != -1:
                    full_tag_str = self.text_buffer[:gt_idx + 1]
                    remainder = self.text_buffer[gt_idx + 1:]

                    q_title, q_topic, q_diff = parse_quiz_attributes(full_tag_str)
                    self.current_quiz_id = f"quiz_{int(time.time() * 1000)}"
                    self.current_quiz_title = q_title
                    self.current_quiz_topic = q_topic
                    self.current_quiz_difficulty = q_diff
                    self.current_start_time = time.time()

                    quiz_placeholder = {
                        "quiz_id": self.current_quiz_id,
                        "title": self.current_quiz_title,
                        "topic": self.current_quiz_topic,
                        "difficulty": self.current_quiz_difficulty,
                        "status": "generating",
                        "is_generating": True,
                        "start_time": self.current_start_time,
                        "question_count": 5
                    }
                    self.dispatcher.add_quiz_placeholder_record(quiz_placeholder)
                    await self.dispatcher.flush(is_final=False, force=True)

                    self.state = "IN_QUIZ"
                    self.text_buffer = ""

                    if remainder:
                        self.quiz_buffer += remainder
                        await self._process_quiz_buffer()
                    break
                else:
                    break

            possible_followup = False
            for prefix in ["<followup", "<follow_up", "<suggest_followup"]:
                if lower_buf.startswith(prefix) or prefix.startswith(lower_buf):
                    possible_followup = True
                    break

            if possible_followup:
                gt_idx = self.text_buffer.find(">")
                if gt_idx != -1:
                    full_tag_str = self.text_buffer[:gt_idx + 1]
                    remainder = self.text_buffer[gt_idx + 1:]

                    self.current_followup_label = parse_followup_attributes(full_tag_str)
                    self.state = "IN_FOLLOWUP"
                    self.text_buffer = ""

                    if remainder:
                        self.followup_buffer += remainder
                        await self._process_followup_buffer()
                    break
                else:
                    break

            await self.dispatcher.append_text(self.text_buffer[0])
            self.text_buffer = self.text_buffer[1:]

    async def _process_artifact_buffer(self):
        end_start, end_end = find_outer_closing_tag(self.artifact_buffer, "artifact")
        if end_start != -1:
            artifact_body = self.artifact_buffer[:end_start]
            post_artifact_text = self.artifact_buffer[end_end:]
            
            await self._complete_artifact(artifact_body)
            
            self.state = "TEXT"
            self.artifact_buffer = ""
            self.in_code_block = False
            self.in_inline_code = False
            
            if post_artifact_text:
                self.text_buffer += post_artifact_text
                await self._process_text_buffer()
        else:
            if self.dispatcher:
                now_t = asyncio.get_event_loop().time()
                last_t = getattr(self.dispatcher, "last_edit_time", 0.0)
                if (now_t - last_t) >= 1.0:
                    await self.dispatcher.flush(is_final=False)

    async def _process_quiz_buffer(self):
        end_start, end_end = find_outer_closing_tag(self.quiz_buffer, "quiz")
        if end_start != -1:
            quiz_body = self.quiz_buffer[:end_start]
            post_quiz_text = self.quiz_buffer[end_end:]

            await self._complete_quiz(quiz_body)

            self.state = "TEXT"
            self.quiz_buffer = ""
            self.in_code_block = False
            self.in_inline_code = False

            if post_quiz_text:
                self.text_buffer += post_quiz_text
                await self._process_text_buffer()
        else:
            if self.dispatcher:
                now_t = asyncio.get_event_loop().time()
                last_t = getattr(self.dispatcher, "last_edit_time", 0.0)
                if (now_t - last_t) >= 1.0:
                    await self.dispatcher.flush(is_final=False)

    async def _process_followup_buffer(self):
        end_match = re.search(r'</(?:followup|follow_up|suggest_followup)>', self.followup_buffer, re.IGNORECASE)
        if end_match:
            end_start = end_match.start()
            end_end = end_match.end()
            
            followup_prompt = self.followup_buffer[:end_start].strip()
            post_followup_text = self.followup_buffer[end_end:]
            
            if followup_prompt and self.current_followup_label:
                self.dispatcher.add_followup_button(
                    label=self.current_followup_label,
                    prompt=followup_prompt
                )
                logger.info(f"[ArtifactParser] Staged follow-up button: '{self.current_followup_label}'")
            
            self.state = "TEXT"
            self.followup_buffer = ""
            self.current_followup_label = ""
            
            if post_followup_text:
                self.text_buffer += post_followup_text
                await self._process_text_buffer()

    async def _complete_quiz(self, body_content: str):
        questions = parse_xml_quiz_body(body_content, self.current_quiz_title)
        channel_id = getattr(self.tool_context.channel, "id", self.channel_id) if self.tool_context else self.channel_id

        quiz_record = branch_manager.save_quiz(
            channel_id=channel_id,
            title=self.current_quiz_title,
            topic=self.current_quiz_topic,
            difficulty=self.current_quiz_difficulty,
            questions=questions,
            quiz_id=self.current_quiz_id
        )

        quiz_payload = {
            "quiz_id": quiz_record["quiz_id"],
            "title": quiz_record["title"],
            "topic": quiz_record["topic"],
            "difficulty": quiz_record["difficulty"],
            "questions": questions,
            "question_count": len(questions),
            "status": "ready",
            "is_generating": False
        }

        self.dispatcher.update_quiz_ready(quiz_payload)
        await self.dispatcher.flush(is_final=False, force=True)
        logger.info(f"[ArtifactParser] Completed quiz '{self.current_quiz_title}' ({len(questions)} questions)")

    async def _complete_artifact(self, body_content: str):
        if not self.current_filename:
            return

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

        existing_record = branch_manager.get_artifact_by_channel_and_file(channel_id, filename)
        summary = f"Updated {filename}" if existing_record else f"Created {filename}"

        record = branch_manager.save_or_update_artifact(
            channel_id=channel_id,
            filename=filename,
            title=self.current_title or filename,
            content=content_str,
            files=parsed_files,
            change_summary=summary
        )

        artifact_payload = {
            "artifact_id": record["artifact_id"],
            "type": "project_zip" if is_multi else "single_file",
            "title": record["title"],
            "filename": filename,
            "description": f"v{record['active_version']} • {filename}",
            "file_count": len(parsed_files),
            "total_lines": record["latest_version_data"]["lines"],
            "size_bytes": len(data_bytes),
            "active_version": record["active_version"],
            "total_versions": record["total_versions"],
            "versions": record["versions"],
            "additions": record.get("additions", 0),
            "deletions": record.get("deletions", 0),
            "diff": record.get("diff", ""),
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

        is_previewable_web = filename.lower().endswith((".html", ".htm", ".svg", ".jsx", ".tsx")) or (
            is_multi and any(f.get("filename", "").lower().endswith((".html", ".htm", ".svg", ".jsx", ".tsx")) for f in parsed_files)
        )

        if is_previewable_web:
            try:
                bundled_html = screenshot_service.bundle_artifact_html(parsed_files, filename)
                if bundled_html:
                    shot_bytes = await screenshot_service.capture_html_preview(bundled_html)
                    if shot_bytes:
                        preview_fn = f"preview_{record['artifact_id']}.png"
                        self.dispatcher.add_media_block(preview_fn, shot_bytes)
                        logger.info(f"[ArtifactParser] Attached visual preview snapshot '{preview_fn}' ({len(shot_bytes):,} bytes)")
            except Exception as snap_err:
                logger.debug(f"[ArtifactParser] Visual snapshot skipped: {snap_err}")

        await self.dispatcher.flush(is_final=False, force=True)
        logger.info(f"[ArtifactParser] Completed '{filename}' (v{record['active_version']}, {len(parsed_files)} file(s))")

    async def finish(self):
        if self.state == "IN_QUIZ":
            if self.current_quiz_title and self.quiz_buffer.strip():
                logger.warning(f"[ArtifactParser] Auto-closing unclosed quiz '{self.current_quiz_title}' at stream end.")
                await self._complete_quiz(self.quiz_buffer)
            self.state = "TEXT"
            self.quiz_buffer = ""

        if self.state == "IN_ARTIFACT":
            if self.current_filename and self.artifact_buffer.strip():
                logger.warning(f"[ArtifactParser] Auto-closing unclosed artifact '{self.current_filename}' at stream end.")
                await self._complete_artifact(self.artifact_buffer)
            else:
                if self.artifact_buffer:
                    await self.dispatcher.append_text(self.artifact_buffer)
            self.state = "TEXT"
            self.artifact_buffer = ""
            
        if self.state == "IN_FOLLOWUP":
            if self.followup_buffer.strip() and self.current_followup_label:
                self.dispatcher.add_followup_button(
                    label=self.current_followup_label,
                    prompt=self.followup_buffer.strip()
                )
            self.state = "TEXT"
            self.followup_buffer = ""

        if self.text_buffer:
            await self.dispatcher.append_text(self.text_buffer)
            self.text_buffer = ""