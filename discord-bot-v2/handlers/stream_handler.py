import io
import re
import time
import json
import asyncio
import logging
from typing import Any, Callable
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    TextDisplay,
    Separator,
    Section,
    MediaGallery,
    ActionRow,
    Button,
    Select,
    UserSelect,
    RoleSelect,
    ChannelSelect,
    MentionableSelect
)
from config.settings import STREAM_DEBOUNCE_INTERVAL, LOADING_EMOJI
from parsers.mention_parser import parse_mentions
from parsers.timestamp_parser import parse_timestamps
from parsers.emoji_parser import parse_emojis
from parsers.math_parser import sanitize_latex
from ui.modals import DynamicModalV2
from ui.artifact_views import build_artifact_components_for_message

logger = logging.getLogger("PriestyAI.StreamHandler")

MAX_V2_MESSAGE_TEXT_BUDGET = 3600

def clean_discord_markdown(text: str) -> str:
    text = re.sub(r'(?m)^#{4,}\s+', '### ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def apply_message_parsers(text: str, guild: discord.Guild | None) -> str:
    text = clean_discord_markdown(text)
    text = sanitize_latex(text)
    text = parse_mentions(text, guild)
    text = parse_timestamps(text)
    text = parse_emojis(text, guild)
    return text

def split_text_into_v2_message_chunks(text: str, max_chars: int = MAX_V2_MESSAGE_TEXT_BUDGET) -> list[str]:
    if len(text) <= max_chars:
        return [text] if text.strip() else []

    chunks = []
    current_text = text
    active_code_lang = None

    while current_text:
        if active_code_lang:
            prefix = f"```{active_code_lang}\n"
            if not current_text.startswith("```"):
                current_text = prefix + current_text

        if len(current_text) <= max_chars:
            if current_text.strip():
                chunks.append(current_text)
            break

        split_idx = current_text.rfind("\n\n", 0, max_chars)
        if split_idx == -1:
            split_idx = current_text.rfind("\n", 0, max_chars)
        if split_idx == -1:
            split_idx = current_text.rfind(". ", 0, max_chars)
        if split_idx == -1 or split_idx < 500:
            split_idx = max_chars

        chunk = current_text[:split_idx].rstrip()
        remainder = current_text[split_idx:].lstrip("\r\n")

        fences = re.findall(r"```([a-zA-Z0-9_+-]*)", chunk)
        if len(fences) % 2 != 0:
            last_lang = fences[-1] or (active_code_lang or "")
            chunk += "\n```"
            active_code_lang = last_lang
        else:
            active_code_lang = None

        if chunk.strip():
            chunks.append(chunk)
        current_text = remainder

    return chunks if chunks else [text]

def chunk_timeline(timeline: list[dict[str, Any]], max_chars: int = MAX_V2_MESSAGE_TEXT_BUDGET) -> list[list[dict[str, Any]]]:
    if not timeline:
        return []

    message_slices: list[list[dict[str, Any]]] = []
    current_slice: list[dict[str, Any]] = []
    current_slice_chars = 0

    for block in timeline:
        b_type = block.get("type", "text")
        
        if b_type == "text":
            text_content = block.get("content", "")
            if not text_content:
                continue

            text_subchunks = split_text_into_v2_message_chunks(text_content, max_chars=max_chars)
            for sub_text in text_subchunks:
                sub_len = len(sub_text)
                if (current_slice_chars + sub_len > max_chars or len(current_slice) >= 30) and current_slice:
                    message_slices.append(current_slice)
                    current_slice = [{"type": "text", "content": sub_text}]
                    current_slice_chars = sub_len
                else:
                    if current_slice and current_slice[-1]["type"] == "text":
                        current_slice[-1]["content"] += sub_text
                    else:
                        current_slice.append({"type": "text", "content": sub_text})
                    current_slice_chars += sub_len

        elif b_type in ["artifact", "media", "component"]:
            overhead = 150
            if (current_slice_chars + overhead > max_chars or len(current_slice) >= 30) and current_slice:
                message_slices.append(current_slice)
                current_slice = [block]
                current_slice_chars = overhead
            else:
                current_slice.append(block)
                current_slice_chars += overhead

    if current_slice:
        message_slices.append(current_slice)

    return message_slices if message_slices else [[{"type": "text", "content": "*Thinking...*"}]]

class ChatMessageLayoutView(LayoutView):
    def __init__(self, timeout: float | None = None):
        super().__init__(timeout=timeout)

def build_v2_message_layout(
    raw_text: str | None = None,
    guild: discord.Guild | None = None,
    timeline_blocks: list[dict[str, Any]] | None = None,
    staged_components: list[dict[str, Any]] | None = None,
    staged_artifacts: list[dict[str, Any]] | None = None,
    modals_map: dict[str, dict[str, Any]] | None = None,
    interaction_dispatcher: Callable[[discord.Interaction, str, Any], Any] | None = None,
    image_filename: str | None = None,
    has_image: bool = False,
    thought_duration: int = 0,
    has_thoughts: bool = False,
    active_version: int = 1,
    total_versions: int = 1,
    message_id: str | int | None = None,
    is_live_stream: bool = False
) -> ChatMessageLayoutView:
    view = ChatMessageLayoutView(timeout=900)
    target_mid = message_id or "temp"
    _modals = modals_map or {}

    elements: list[Any] = []

    if timeline_blocks:
        ordered_blocks = list(timeline_blocks)

        if len(ordered_blocks) >= 2 and ordered_blocks[0].get("type") in ["media", "artifact"]:
            first_text_idx = None
            for idx, b in enumerate(ordered_blocks):
                if b.get("type") == "text" and b.get("content", "").strip():
                    first_text_idx = idx
                    break

            if first_text_idx is not None:
                first_text_raw = ordered_blocks[first_text_idx]["content"]
                
                split_match = re.search(r'(?m)^(?=##\s+)', first_text_raw)
                split_pos = split_match.start() if split_match and split_match.start() > 10 else -1

                if split_pos == -1:
                    para_match = re.search(r'\n\n', first_text_raw)
                    if para_match and para_match.start() > 10:
                        split_pos = para_match.end()

                if split_pos != -1:
                    intro_part = first_text_raw[:split_pos].strip()
                    remainder_part = first_text_raw[split_pos:].strip()
                    ordered_blocks[first_text_idx]["content"] = remainder_part
                    ordered_blocks.insert(0, {"type": "text", "content": intro_part})

        idx = 0
        while idx < len(ordered_blocks) and len(elements) < 35:
            block = ordered_blocks[idx]
            b_type = block.get("type", "text")

            if b_type == "text":
                text_content = block.get("content", "").strip()
                if text_content:
                    parsed = apply_message_parsers(text_content, guild)
                    sections = re.split(r'(?m)^\s*(?:---|\*\*\*|___)\s*$', parsed)
                    for s_idx, sec in enumerate(sections):
                        sec_clean = sec.strip()
                        if sec_clean:
                            elements.append(TextDisplay(sec_clean))
                        if s_idx < len(sections) - 1 and len(elements) < 35:
                            elements.append(Separator(visible=True))
                idx += 1

            elif b_type == "artifact":
                art_data = block.get("artifact", {})
                art_items = build_artifact_components_for_message(art_data, message_id=target_mid, is_live_stream=is_live_stream)
                elements.extend(art_items)
                idx += 1

            elif b_type == "media":
                consecutive_media = []
                while idx < len(ordered_blocks) and ordered_blocks[idx].get("type") == "media":
                    fn = ordered_blocks[idx].get("filename")
                    if fn:
                        consecutive_media.append(fn)
                    idx += 1

                if consecutive_media and not is_live_stream:
                    gallery_items = [discord.MediaGalleryItem(f"attachment://{fn}") for fn in consecutive_media[:10]]
                    elements.append(MediaGallery(*gallery_items))

            else:
                idx += 1

    elif raw_text is not None:
        parsed_full_text = apply_message_parsers(raw_text, guild)
        sections = re.split(r'(?m)^\s*(?:---|\*\*\*|___)\s*$', parsed_full_text)
        for s_idx, sec in enumerate(sections):
            if len(elements) >= 35:
                break
            sec_clean = sec.strip()
            if sec_clean:
                elements.append(TextDisplay(sec_clean))
            if s_idx < len(sections) - 1 and len(elements) < 35:
                elements.append(Separator(visible=True))

        for art in (staged_artifacts or []):
            if len(elements) < 35:
                art_items = build_artifact_components_for_message(art, message_id=target_mid, is_live_stream=is_live_stream)
                elements.extend(art_items)

    if not elements:
        elements.append(TextDisplay("*Thinking...*"))

    if has_image and image_filename and len(elements) < 38 and not is_live_stream:
        if not any(isinstance(e, MediaGallery) for e in elements):
            gallery = MediaGallery(discord.MediaGalleryItem(f"attachment://{image_filename}"))
            elements.append(gallery)

    if staged_components and not is_live_stream:
        staged_buttons = []

        for comp in staged_components:
            if len(elements) >= 38:
                break

            c_type = str(comp.get("type", "button")).lower().strip().replace(" ", "_")
            c_placement = comp.get("placement", "action_row")
            c_label = comp.get("label", "Action")[:80]
            c_id = comp.get("custom_id", f"btn_{len(elements)}")
            sec_text = comp.get("section_text", "").strip()
            target_modal_id = comp.get("modal_id") or c_id
            disabled = comp.get("disabled", False)

            if c_type in ["button", "btn"] and c_placement == "section":
                style_map = {
                    "primary": discord.ButtonStyle.primary,
                    "secondary": discord.ButtonStyle.secondary,
                    "success": discord.ButtonStyle.success,
                    "danger": discord.ButtonStyle.danger
                }
                btn_style = style_map.get(comp.get("style", "secondary").lower(), discord.ButtonStyle.secondary)
                acc_btn = Button(label=c_label, style=btn_style, custom_id=c_id, disabled=disabled)

                if interaction_dispatcher:
                    def attach_section_btn_cb(button_obj, m_id=target_modal_id, c_id=c_id, b_lbl=c_label):
                        async def cb(inter: discord.Interaction):
                            if m_id and m_id in _modals:
                                m_spec = _modals[m_id]
                                async def on_m_sub(s_inter: discord.Interaction, d: dict[str, Any]):
                                    await interaction_dispatcher(s_inter, "modal_submit", {"modal_id": m_id, "values": d})
                                m_obj = DynamicModalV2(title=m_spec.get("title", "Form"), custom_id=m_id, fields_schema=m_spec.get("fields", []), on_submit_callback=on_m_sub)
                                await inter.response.send_modal(m_obj)
                            else:
                                await interaction_dispatcher(inter, "button_click", {"custom_id": c_id, "label": b_lbl})
                        button_obj.callback = cb
                    attach_section_btn_cb(acc_btn)

                target_text = sec_text
                if not target_text and elements:
                    last_item = elements[-1]
                    if isinstance(last_item, TextDisplay):
                        target_text = last_item.content
                        elements.pop()
                elif sec_text and elements:
                    last_item = elements[-1]
                    if isinstance(last_item, TextDisplay):
                        elements.pop()

                elements.append(Section(TextDisplay((target_text or "Action")[:2000]), accessory=acc_btn))

            elif c_type in ["button", "btn"]:
                style_map = {
                    "primary": discord.ButtonStyle.primary,
                    "secondary": discord.ButtonStyle.secondary,
                    "success": discord.ButtonStyle.success,
                    "danger": discord.ButtonStyle.danger
                }
                btn_style = style_map.get(comp.get("style", "secondary").lower(), discord.ButtonStyle.secondary)
                row_btn = Button(label=c_label, style=btn_style, custom_id=c_id, disabled=disabled)

                if interaction_dispatcher:
                    def attach_btn_cb(button_obj, m_id=target_modal_id, c_id=c_id, b_lbl=c_label):
                        async def cb(inter: discord.Interaction):
                            if m_id and m_id in _modals:
                                m_spec = _modals[m_id]
                                async def on_m_sub(s_inter: discord.Interaction, d: dict[str, Any]):
                                    await interaction_dispatcher(s_inter, "modal_submit", {"modal_id": m_id, "values": d})
                                m_obj = DynamicModalV2(title=m_spec.get("title", "Form"), custom_id=m_id, fields_schema=m_spec.get("fields", []), on_submit_callback=on_m_sub)
                                await inter.response.send_modal(m_obj)
                            else:
                                await interaction_dispatcher(inter, "button_click", {"custom_id": c_id, "label": b_lbl})
                        button_obj.callback = cb
                    attach_btn_cb(row_btn)

                staged_buttons.append(row_btn)
                if len(staged_buttons) >= 5:
                    elements.append(ActionRow(*staged_buttons))
                    staged_buttons = []

            elif c_type in ["select", "stringselect", "string_select"]:
                raw_opts = comp.get("options", [])
                opt_modals = comp.get("option_modals", {})
                options = [
                    discord.SelectOption(
                        label=o.get("label", "Option")[:100],
                        value=o.get("value", o.get("label", ""))[:100],
                        description=o.get("description", "")[:100] if o.get("description") else None,
                        emoji=o.get("emoji")
                    )
                    for o in raw_opts if isinstance(o, dict)
                ] or [discord.SelectOption(label="Option", value="default")]

                min_v = max(0, int(comp.get("min_values", 1)))
                max_v = max(1, min(25, int(comp.get("max_values", 1))))
                sel = Select(
                    custom_id=c_id,
                    placeholder=(comp.get("placeholder") or "Select...")[:100],
                    options=options[:25],
                    min_values=min(min_v, len(options)),
                    max_values=min(max_v, len(options)),
                    disabled=disabled
                )

                if interaction_dispatcher:
                    def attach_sel_cb(select_obj, c_id=c_id, opt_map=opt_modals):
                        async def sel_cb(inter: discord.Interaction):
                            chosen_val = select_obj.values[0] if select_obj.values else None
                            m_id = opt_map.get(chosen_val)
                            if m_id and m_id in _modals:
                                m_spec = _modals[m_id]
                                async def on_s_sub(s_inter: discord.Interaction, d: dict[str, Any]):
                                    await interaction_dispatcher(s_inter, "modal_submit", {"modal_id": m_id, "selected_option": chosen_val, "values": d})
                                m_obj = DynamicModalV2(title=m_spec.get("title", "Form"), custom_id=m_id, fields_schema=m_spec.get("fields", []), on_submit_callback=on_s_sub)
                                await inter.response.send_modal(m_obj)
                            else:
                                await interaction_dispatcher(inter, "select_option", {"custom_id": c_id, "component_type": "string_select", "selected": select_obj.values})
                        select_obj.callback = sel_cb
                    attach_sel_cb(sel)

                elements.append(ActionRow(sel))

            elif c_type in ["user_select", "userselect"]:
                u_sel = UserSelect(custom_id=c_id, placeholder=(comp.get("placeholder") or "Select user...")[:100], min_values=max(0, int(comp.get("min_values", 1))), max_values=max(1, min(25, int(comp.get("max_values", 1)))), disabled=disabled)
                if interaction_dispatcher:
                    def attach_u_cb(u_obj, c_id=c_id):
                        async def u_cb(inter: discord.Interaction):
                            await interaction_dispatcher(inter, "select_option", {"custom_id": c_id, "component_type": "user_select", "selected": [str(u.id) for u in u_obj.values]})
                        u_obj.callback = u_cb
                    attach_u_cb(u_sel)
                elements.append(ActionRow(u_sel))

            elif c_type in ["role_select", "roleselect"]:
                r_sel = RoleSelect(custom_id=c_id, placeholder=(comp.get("placeholder") or "Select role...")[:100], min_values=max(0, int(comp.get("min_values", 1))), max_values=max(1, min(25, int(comp.get("max_values", 1)))), disabled=disabled)
                if interaction_dispatcher:
                    def attach_r_cb(r_obj, c_id=c_id):
                        async def r_cb(inter: discord.Interaction):
                            await interaction_dispatcher(inter, "select_option", {"custom_id": c_id, "component_type": "role_select", "selected": [str(r.id) for r in r_obj.values]})
                        r_obj.callback = r_cb
                    attach_r_cb(r_sel)
                elements.append(ActionRow(r_sel))

            elif c_type in ["channel_select", "channelselect"]:
                c_sel = ChannelSelect(custom_id=c_id, placeholder=(comp.get("placeholder") or "Select channel...")[:100], min_values=max(0, int(comp.get("min_values", 1))), max_values=max(1, min(25, int(comp.get("max_values", 1)))), disabled=disabled)
                if interaction_dispatcher:
                    def attach_c_cb(c_obj, c_id=c_id):
                        async def c_cb(inter: discord.Interaction):
                            await interaction_dispatcher(inter, "select_option", {"custom_id": c_id, "component_type": "channel_select", "selected": [str(ch.id) for ch in c_obj.values]})
                        c_obj.callback = c_cb
                    attach_c_cb(c_sel)
                elements.append(ActionRow(c_sel))

            elif c_type in ["mentionable_select", "mentionableselect"]:
                m_sel = MentionableSelect(custom_id=c_id, placeholder=(comp.get("placeholder") or "Select mentionable...")[:100], min_values=max(0, int(comp.get("min_values", 1))), max_values=max(1, min(25, int(comp.get("max_values", 1)))), disabled=disabled)
                if interaction_dispatcher:
                    def attach_m_cb(m_obj, c_id=c_id):
                        async def m_cb(inter: discord.Interaction):
                            await interaction_dispatcher(inter, "select_option", {"custom_id": c_id, "component_type": "mentionable_select", "selected": [str(m.id) for m in m_obj.values]})
                        m_obj.callback = m_cb
                    attach_m_cb(m_sel)
                elements.append(ActionRow(m_sel))

        if staged_buttons:
            elements.append(ActionRow(*staged_buttons))

    if not is_live_stream and message_id:
        if has_thoughts and len(elements) < 39:
            time_str = f"{thought_duration}s" if thought_duration > 0 else "<1s"
            t_btn = Button(label=f"🧠 Thought for {time_str}", style=discord.ButtonStyle.secondary, custom_id=f"gen_thought_{message_id}_{active_version}")
            elements.append(ActionRow(t_btn))

        if total_versions >= 2 and len(elements) < 39:
            prev_btn = Button(label="◀", style=discord.ButtonStyle.secondary, disabled=(active_version <= 1), custom_id=f"gen_prev_{message_id}")
            ind_btn = Button(label=f"{active_version} / {total_versions}", style=discord.ButtonStyle.secondary, disabled=True, custom_id=f"gen_ind_{message_id}")
            next_btn = Button(label="▶", style=discord.ButtonStyle.secondary, disabled=(active_version >= total_versions), custom_id=f"gen_next_{message_id}")
            elements.append(ActionRow(prev_btn, ind_btn, next_btn))

    for el in elements:
        view.add_item(el)

    return view

class DiscordStreamDispatcher:
    def __init__(
        self,
        origin_message: discord.Message | None = None,
        guild: discord.Guild | None = None,
        existing_response_msg: discord.Message | None = None,
        interaction: discord.Interaction | None = None,
        is_ephemeral: bool = False
    ):
        self.origin_message = origin_message
        self.guild = guild
        self.primary_message = existing_response_msg
        self.interaction = interaction
        self.is_ephemeral = is_ephemeral

        self.sent_messages: list[discord.Message] = [existing_response_msg] if existing_response_msg else []
        self.interaction_overflow_count = 1 if interaction else 0

        self.timeline: list[dict[str, Any]] = []
        self.raw_attachment_buffers: list[dict[str, Any]] = []
        self.last_edit_time = 0.0
        self.flush_lock = asyncio.Lock()

    def bind_response_message(self, msg: discord.Message):
        self.primary_message = msg
        if msg not in self.sent_messages:
            self.sent_messages.append(msg)

    async def append_text(self, delta: str):
        if not delta:
            return

        if self.timeline and self.timeline[-1]["type"] == "text":
            self.timeline[-1]["content"] += delta
        else:
            self.timeline.append({"type": "text", "content": delta})

        now = asyncio.get_event_loop().time()
        if (now - self.last_edit_time) >= STREAM_DEBOUNCE_INTERVAL:
            await self.flush(is_final=False)

    def add_artifact_placeholder(self, tool_name: str, args: dict[str, Any]):
        filename = args.get("filename") or args.get("title") or "artifact.txt"
        title = args.get("title") or filename
        placeholder_art = {
            "artifact_id": f"art_gen_{int(time.time()*1000)}",
            "filename": filename,
            "title": title,
            "status": "generating",
            "is_generating": True,
            "start_time": time.time()
        }
        self.timeline.append({"type": "artifact", "artifact": placeholder_art, "status": "generating"})
        logger.info(f"[Dispatcher] Anchored live artifact placeholder: '{filename}'")

    def add_artifact_placeholder_record(self, placeholder_art: dict[str, Any]):
        if "start_time" not in placeholder_art:
            placeholder_art["start_time"] = time.time()
        placeholder_art["status"] = "generating"
        placeholder_art["is_generating"] = True
        self.timeline.append({"type": "artifact", "artifact": placeholder_art, "status": "generating"})
        logger.info(f"[Dispatcher] Anchored live XML artifact placeholder: '{placeholder_art.get('filename')}'")

    def update_artifact_ready(self, artifact_data: dict[str, Any]):
        target_fn = artifact_data.get("filename")
        target_id = artifact_data.get("artifact_id")
        found = False

        for block in reversed(self.timeline):
            if block.get("type") == "artifact":
                art = block.get("artifact", {})
                if (
                    (target_id and art.get("artifact_id") == target_id)
                    or (target_fn and art.get("filename") == target_fn)
                    or block.get("status") == "generating"
                    or art.get("status") == "generating"
                    or art.get("is_generating")
                ):
                    block["artifact"] = dict(artifact_data)
                    block["artifact"]["status"] = "ready"
                    block["artifact"]["is_generating"] = False
                    block["status"] = "ready"
                    found = True
                    logger.info(f"[Dispatcher] Replaced generating placeholder with ready artifact: '{target_fn}'")
                    break

        if not found:
            ready_art = dict(artifact_data)
            ready_art["status"] = "ready"
            ready_art["is_generating"] = False
            self.timeline.append({"type": "artifact", "artifact": ready_art, "status": "ready"})
            logger.info(f"[Dispatcher] Appended ready artifact block: '{target_fn}'")

    def add_media_block(self, filename: str, data_bytes: bytes):
        if data_bytes and filename:
            if not any(a["filename"] == filename for a in self.raw_attachment_buffers):
                self.raw_attachment_buffers.append({"filename": filename, "bytes": data_bytes})
            self.timeline.append({"type": "media", "filename": filename})
            logger.info(f"[Dispatcher] Anchored in-stream media block: '{filename}'")

    def add_raw_attachment(self, filename: str, data_bytes: bytes):
        if data_bytes and filename:
            if not any(a["filename"] == filename for a in self.raw_attachment_buffers):
                self.raw_attachment_buffers.append({"filename": filename, "bytes": data_bytes})

    def get_slice_attachments(self, slice_blocks: list[dict[str, Any]]) -> list[discord.File]:
        referenced_filenames = set()
        for b in slice_blocks:
            if b.get("type") == "media" and b.get("filename"):
                referenced_filenames.add(b["filename"])
            elif b.get("type") == "artifact":
                art = b.get("artifact", {})
                if art.get("filename"):
                    referenced_filenames.add(art["filename"])

        files = []
        for a in self.raw_attachment_buffers:
            if a["filename"] in referenced_filenames:
                files.append(discord.File(io.BytesIO(a["bytes"]), filename=a["filename"]))
        return files

    def get_accumulated_text(self) -> str:
        text_parts = []
        for block in self.timeline:
            if block["type"] == "text":
                text_parts.append(block["content"])
        return "".join(text_parts)

    async def flush(
        self,
        staged_artifacts: list[dict[str, Any]] | None = None,
        staged_components: list[dict[str, Any]] | None = None,
        modals_map: dict[str, dict[str, Any]] | None = None,
        interaction_dispatcher: Callable | None = None,
        thought_duration: int = 0,
        has_thoughts: bool = False,
        active_version: int = 1,
        total_versions: int = 1,
        message_id: str | int | None = None,
        is_final: bool = False,
        force: bool = False
    ):
        if not self.timeline and not is_final:
            return

        if not is_final and not force and self.flush_lock.locked():
            return

        async with self.flush_lock:
            try:
                message_slices = chunk_timeline(self.timeline, max_chars=MAX_V2_MESSAGE_TEXT_BUDGET)
                target_msg_id = message_id or (self.primary_message.id if self.primary_message else None)

                for i, slice_blocks in enumerate(message_slices):
                    is_last_slice = (i == len(message_slices) - 1)
                    chunk_comps = staged_components if (is_last_slice and is_final) else None
                    chunk_mod_map = modals_map if (is_last_slice and is_final) else None
                    chunk_dispatcher = interaction_dispatcher if (is_last_slice and is_final) else None

                    layout_view = build_v2_message_layout(
                        guild=self.guild,
                        timeline_blocks=slice_blocks,
                        staged_components=chunk_comps,
                        staged_artifacts=staged_artifacts,
                        modals_map=chunk_mod_map,
                        interaction_dispatcher=chunk_dispatcher,
                        thought_duration=thought_duration if is_last_slice else 0,
                        has_thoughts=has_thoughts if is_last_slice else False,
                        active_version=active_version,
                        total_versions=total_versions,
                        message_id=target_msg_id if is_last_slice else None,
                        is_live_stream=not is_final
                    )

                    slice_files = self.get_slice_attachments(slice_blocks) if is_final else []
                    attachments_list = slice_files if slice_files else discord.utils.MISSING

                    if self.interaction:
                        if i == 0:
                            if attachments_list is not discord.utils.MISSING:
                                await self.interaction.edit_original_response(view=layout_view, attachments=attachments_list)
                            else:
                                await self.interaction.edit_original_response(view=layout_view)
                        else:
                            if i >= self.interaction_overflow_count:
                                if attachments_list is not discord.utils.MISSING:
                                    await self.interaction.followup.send(view=layout_view, files=slice_files, ephemeral=self.is_ephemeral)
                                else:
                                    await self.interaction.followup.send(view=layout_view, ephemeral=self.is_ephemeral)
                                self.interaction_overflow_count += 1
                    else:
                        if i < len(self.sent_messages):
                            msg = self.sent_messages[i]
                            if attachments_list is not discord.utils.MISSING:
                                await msg.edit(view=layout_view, attachments=attachments_list)
                            else:
                                await msg.edit(view=layout_view)
                        else:
                            target_chan = self.origin_message.channel if self.origin_message else (self.primary_message.channel if self.primary_message else None)
                            if target_chan:
                                if attachments_list is not discord.utils.MISSING:
                                    new_msg = await target_chan.send(view=layout_view, files=slice_files)
                                else:
                                    new_msg = await target_chan.send(view=layout_view)
                                self.sent_messages.append(new_msg)

                self.last_edit_time = asyncio.get_event_loop().time()
            except discord.DiscordServerError:
                pass
            except Exception as e:
                logger.warning(f"Components V2 stream flush warning: {e}")

    async def finalize(
        self,
        staged_artifacts: list[dict[str, Any]] | None = None,
        staged_components: list[dict[str, Any]] | None = None,
        modals_map: dict[str, dict[str, Any]] | None = None,
        interaction_dispatcher: Callable | None = None,
        thought_duration: int = 0,
        has_thoughts: bool = False,
        active_version: int = 1,
        total_versions: int = 1,
        message_id: str | int | None = None
    ):
        await self.flush(
            staged_artifacts=staged_artifacts,
            staged_components=staged_components,
            modals_map=modals_map,
            interaction_dispatcher=interaction_dispatcher,
            thought_duration=thought_duration,
            has_thoughts=has_thoughts,
            active_version=active_version,
            total_versions=total_versions,
            message_id=message_id,
            is_final=True
        )