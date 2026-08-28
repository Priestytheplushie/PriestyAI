import re
from typing import Any

DFM_EMOJI_MAP = {
    "gfm_note": "<:gfm_note:1541928204263235594>",
    "gfm_tip": "<:gfm_tip:1541928204892241931>",
    "gfm_important": "<:gfm_important:1541928206347673730>",
    "gfm_warning": "<:gfm_warning:1541928207950028850>",
    "gfm_caution": "<:gfm_caution:1541928208855863307>",
    "gfm_checked": "<:gfm_checked:1541928209338339390>",
    "gfm_unchecked": "<:gfm_unchecked:1541928210697035917>",
}

ALERT_METADATA = {
    "NOTE": {"emoji": DFM_EMOJI_MAP["gfm_note"], "title": "Note", "color": 0x1f6feb},
    "TIP": {"emoji": DFM_EMOJI_MAP["gfm_tip"], "title": "Tip", "color": 0x238636},
    "IMPORTANT": {"emoji": DFM_EMOJI_MAP["gfm_important"], "title": "Important", "color": 0x8957e5},
    "WARNING": {"emoji": DFM_EMOJI_MAP["gfm_warning"], "title": "Warning", "color": 0xd29922},
    "CAUTION": {"emoji": DFM_EMOJI_MAP["gfm_caution"], "title": "Caution", "color": 0xda3633},
}

ALERT_REGEX = r'^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*\n((?:^>[^\n]*\n?)*)'
TABLE_REGEX = r'(?:^[ \t]*\|[^\n]+\|[ \t]*\n^[ \t]*\|[-: |]+\|[ \t]*(?:\n^[ \t]*\|[^\n]+\|[ \t]*)*)'

def transform_table_to_natural_markdown(table_str: str) -> str:
    lines = [line.strip() for line in table_str.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return table_str

    parsed_rows = []
    for line in lines:
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line[1:-1].split("|")]
        else:
            cells = [c.strip() for c in line.split("|")]
        parsed_rows.append(cells)

    if len(parsed_rows) < 2:
        return table_str

    header_row = parsed_rows[0]
    delimiter_row = parsed_rows[1]

    is_delimiter = all(re.match(r'^:?-+:?$', c) for c in delimiter_row if c)
    if not is_delimiter:
        return table_str

    data_rows = parsed_rows[2:] if len(parsed_rows) > 2 else []
    num_cols = len(header_row)

    if num_cols == 0 or not data_rows:
        return table_str

    normalized_data = []
    for r in data_rows:
        if len(r) < num_cols:
            r = r + [""] * (num_cols - len(r))
        elif len(r) > num_cols:
            r = r[:num_cols]
        normalized_data.append(r)

    if num_cols == 2:
        bullet_lines = []
        for r in normalized_data:
            k, v = r[0].strip(), r[1].strip()
            if not k and not v:
                continue

            if not (k.startswith("`") or k.startswith("**")):
                k_formatted = f"`{k}`"
            else:
                k_formatted = k

            if v:
                bullet_lines.append(f"• {k_formatted} — {v}")
            else:
                bullet_lines.append(f"• {k_formatted}")
        return "\n".join(bullet_lines)

    feature_col_name = header_row[0].strip()
    is_feature_matrix = feature_col_name.lower() in ("feature", "metric", "dimension", "aspect", "property", "attribute", "criteria", "")

    if is_feature_matrix or len(header_row) >= 3:
        subjects = header_row[1:]
        subject_blocks = []

        for sub_idx, subject in enumerate(subjects):
            col_i = sub_idx + 1
            sub_title = subject.strip()
            if not (sub_title.startswith("**") or sub_title.startswith("`")):
                sub_title = f"**{sub_title}**"

            item_lines = []
            for r in normalized_data:
                feat_name = r[0].strip()
                val = r[col_i].strip()
                if not feat_name or not val:
                    continue
                clean_feat = feat_name.strip("*_` ")
                item_lines.append(f"• **{clean_feat}:** {val}")

            if item_lines:
                subject_blocks.append(f"{sub_title}\n" + "\n".join(item_lines))

        if subject_blocks:
            return "\n\n".join(subject_blocks)

    output_lines = []
    for r in normalized_data:
        primary = r[0].strip()
        details = [f"**{header_row[i]}:** {r[i]}" for i in range(1, num_cols) if r[i].strip()]
        detail_str = " • ".join(details)
        if detail_str:
            output_lines.append(f"• **{primary}** — {detail_str}")
        else:
            output_lines.append(f"• **{primary}**")
    return "\n".join(output_lines)

def parse_markdown_tables(text: str) -> str:
    codeblock_parts = re.split(r'(```[\s\S]*?```)', text)

    for i in range(0, len(codeblock_parts), 2):
        chunk = codeblock_parts[i]
        chunk = re.sub(TABLE_REGEX, lambda m: transform_table_to_natural_markdown(m.group(0)), chunk, flags=re.MULTILINE)
        codeblock_parts[i] = chunk

    return "".join(codeblock_parts)

def parse_task_lists(text: str) -> str:
    codeblock_parts = re.split(r'(```[\s\S]*?```)', text)

    unchecked_tag = DFM_EMOJI_MAP["gfm_unchecked"]
    checked_tag = DFM_EMOJI_MAP["gfm_checked"]

    for i in range(0, len(codeblock_parts), 2):
        chunk = codeblock_parts[i]
        chunk = re.sub(r'^(\s*)[-*+]\s+\[ \]\s+', rf'\1{unchecked_tag} ', chunk, flags=re.MULTILINE)
        chunk = re.sub(r'^(\s*)[-*+]\s+\[[xX]\]\s+', rf'\1{checked_tag} ', chunk, flags=re.MULTILINE)
        codeblock_parts[i] = chunk

    return "".join(codeblock_parts)

def parse_alerts_inline(text: str) -> str:
    codeblock_parts = re.split(r'(```[\s\S]*?```)', text)

    def replace_alert(match):
        alert_type = match.group(1).upper()
        raw_body = match.group(2)
        meta = ALERT_METADATA.get(alert_type, {"emoji": DFM_EMOJI_MAP["gfm_note"], "title": alert_type.title(), "color": 0x1f6feb})
        emoji = meta["emoji"]
        title = meta["title"]

        body_lines = [re.sub(r'^>\s?', '', line) for line in raw_body.splitlines()]
        body_content = "\n> ".join(body_lines).strip()
        if body_content:
            return f"> {emoji} **{title}**\n> {body_content}"
        return f"> {emoji} **{title}**"

    for i in range(0, len(codeblock_parts), 2):
        chunk = codeblock_parts[i]
        chunk = re.sub(ALERT_REGEX, replace_alert, chunk, flags=re.MULTILINE)
        codeblock_parts[i] = chunk

    return "".join(codeblock_parts)

def parse_dfm_structures_to_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    last_idx = 0

    for match in re.finditer(ALERT_REGEX, text, flags=re.MULTILINE):
        start, end = match.span()
        if start > last_idx:
            pre_text = text[last_idx:start].strip()
            if pre_text:
                blocks.append({"type": "text", "content": pre_text})

        alert_type = match.group(1).upper()
        raw_body = match.group(2)
        body_lines = [re.sub(r'^>\s?', '', line) for line in raw_body.splitlines()]
        body_content = "\n".join(body_lines).strip()

        meta = ALERT_METADATA.get(alert_type, {"emoji": "💡", "title": alert_type.title(), "color": 0x1f6feb})
        blocks.append({
            "type": "alert",
            "alert_type": alert_type,
            "emoji": meta["emoji"],
            "title": meta["title"],
            "color": meta.get("color", 0x1f6feb),
            "content": body_content
        })

        last_idx = end

    if last_idx < len(text):
        tail_text = text[last_idx:].strip()
        if tail_text:
            blocks.append({"type": "text", "content": tail_text})

    return blocks if blocks else [{"type": "text", "content": text}]

def parse_misc_markdown(text: str) -> str:
    text = re.sub(r'<kbd>([^<]+)</kbd>', r'`\1`', text, flags=re.IGNORECASE)
    
    superscript_map = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
    def replace_footnote(m):
        digits = m.group(1)
        return "".join(superscript_map.get(d, d) for d in digits)
    text = re.sub(r'\[\^(\d+)\]', replace_footnote, text)
    
    return text

def apply_dfm(text: str) -> str:
    if not text:
        return ""
    text = parse_task_lists(text)
    text = parse_alerts_inline(text)
    text = parse_markdown_tables(text)
    text = parse_misc_markdown(text)
    return text