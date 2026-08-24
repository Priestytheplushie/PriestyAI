import io
import logging
from typing import Any
import discord
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    Section,
    TextDisplay,
    Separator,
    ActionRow,
    Button,
    Select,
    File as ComponentFile
)
from ui.modals import DynamicModalV2

logger = logging.getLogger("PriestyAI.ArtifactUI")

FILE_ICON_MAP = {
    "c": "<:ext_c:1541304301727645747>",
    "h": "<:ext_c:1541304301727645747>",
    "cpp": "<:ext_cpp:1541304817538834434>",
    "hpp": "<:ext_cpp:1541304817538834434>",
    "cc": "<:ext_cpp:1541304817538834434>",
    "cxx": "<:ext_cpp:1541304817538834434>",
    "cs": "<:ext_cs:1541304308551786496>",
    "css": "<:ext_css:1541304799624962130>",
    "scss": "<:ext_css:1541304799624962130>",
    "sass": "<:ext_css:1541304799624962130>",
    "csv": "<:ext_csv:1541304843216490546>",
    "tsv": "<:ext_csv:1541304843216490546>",
    "docker": "<:ext_docker:1541304341346910389>",
    "dockerfile": "<:ext_docker:1541304341346910389>",
    "git": "<:ext_git:1541304349970665594>",
    "gitignore": "<:ext_git:1541304349970665594>",
    "go": "<:ext_go:1541304295209574410>",
    "html": "<:ext_html:1541304792897556532>",
    "htm": "<:ext_html:1541304792897556532>",
    "java": "<:ext_java:1541304314956480512>",
    "js": "<:ext_js:1541304269037371422>",
    "javascript": "<:ext_js:1541304269037371422>",
    "mjs": "<:ext_js:1541304269037371422>",
    "cjs": "<:ext_js:1541304269037371422>",
    "json": "<:ext_json:1541304356786151495>",
    "lua": "<:ext_lua:1541304321746931752>",
    "md": "<:ext_md:1541304836971036743>",
    "markdown": "<:ext_md:1541304836971036743>",
    "pdf": "<:ext_pdf:1541304849336115260>",
    "php": "<:ext_php:1541304328336318525>",
    "py": "<:ext_py:1541304282211418123>",
    "python": "<:ext_py:1541304282211418123>",
    "rb": "<:ext_rb:1541304334870904894>",
    "ruby": "<:ext_rb:1541304334870904894>",
    "react": "<:ext_react:1541304805459239024>",
    "jsx": "<:ext_react:1541304805459239024>",
    "tsx": "<:ext_react:1541304805459239024>",
    "rs": "<:ext_rs:1541304288981160006>",
    "rust": "<:ext_rs:1541304288981160006>",
    "sh": "<:ext_sh:1541304823708647454>",
    "bash": "<:ext_sh:1541304823708647454>",
    "zsh": "<:ext_sh:1541304823708647454>",
    "sql": "<:ext_sql:1541304365132812318>",
    "svg": "<:ext_svg:1541304372175175680>",
    "toml": "<:ext_toml:1541304830323331202>",
    "ts": "<:ext_ts:1541304275093819393>",
    "typescript": "<:ext_ts:1541304275093819393>",
    "txt": "<:ext_txt:1541304855593881670>",
    "vue": "<:ext_vue:1541304810870022166>",
    "zip": "<:ext_zip:1541304786937192500>",
    "tar": "<:ext_zip:1541304786937192500>",
    "gz": "<:ext_zip:1541304786937192500>",
    "yaml": "<:ext_yaml:1541305732257812570>",
    "yml": "<:ext_yaml:1541305732257812570>",
    "env": "<:ext_env:1541305731591184394>"
}

DEFAULT_FILE_ICON = "<:ext_txt:1541304855593881670>"

def get_file_icon(filename: str) -> str:
    if not filename:
        return DEFAULT_FILE_ICON
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else filename.lower()
    return FILE_ICON_MAP.get(ext, DEFAULT_FILE_ICON)

def format_size(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    return f"{bytes_count / (1024 * 1024):.1f} MB"

def build_code_preview_modal(filename: str, raw_code: str) -> DynamicModalV2:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    formatted_code = f"```{ext}\n{raw_code[:3800]}\n```"

    fields = [
        {
            "type": "text_display",
            "content": formatted_code
        }
    ]

    async def noop_submit(interaction: discord.Interaction, data: dict[str, Any]):
        await interaction.response.defer()

    return DynamicModalV2(
        title=f"{filename}"[:45],
        custom_id=f"modal_preview_{filename[:30]}",
        fields_schema=fields,
        on_submit_callback=noop_submit
    )

def build_artifact_components_for_message(
    artifact: dict[str, Any],
    message_id: str | int = "temp",
    selected_version: int | None = None,
    is_live_stream: bool = False
) -> list[Any]:
    filename = artifact.get("filename", "project.zip")
    artifact_id = artifact.get("artifact_id", "art_0")
    files = artifact.get("files", [])
    icon = get_file_icon(filename)
    versions = artifact.get("versions", [])
    total_versions = max(1, len(versions) if versions else artifact.get("total_versions", 1))
    status = artifact.get("status", "ready")

    if status == "generating" or artifact.get("is_generating"):
        container = Container()
        title = artifact.get("title") or filename
        display_text = f"{icon} **{title}**\n-# 📦 *Packaging artifact...*"
        btn = Button(label="Building...", style=discord.ButtonStyle.secondary, disabled=True)
        container.add_item(Section(TextDisplay(display_text), accessory=btn))
        return [container]

    is_multi_file = filename.endswith(".zip") or len(files) > 1

    if is_multi_file:
        if is_live_stream:
            container = Container()
            display_text = f"{icon} **{filename}**\n-# Packaging {len(files)} files into archive..."
            container.add_item(Section(TextDisplay(display_text), accessory=Button(label="Packaging...", disabled=True)))
            return [container]
        return [ComponentFile(f"attachment://{filename}")]

    active_v = selected_version or artifact.get("active_version", total_versions)
    target_v_data = None
    if versions and 1 <= active_v <= len(versions):
        target_v_data = versions[active_v - 1]

    content = target_v_data.get("content", "") if target_v_data else (files[0].get("content", "") if files else "")
    lines = target_v_data.get("lines", len(content.splitlines())) if target_v_data else len(content.splitlines())
    ts = target_v_data.get("timestamp") if target_v_data else None
    ts_str = f" • <t:{int(ts)}:R>" if ts else ""

    adds = target_v_data.get("additions", artifact.get("additions", 0)) if target_v_data else 0
    dels = target_v_data.get("deletions", artifact.get("deletions", 0)) if target_v_data else 0
    diff_tag = f" (+{adds} -{dels})" if (adds > 0 or dels > 0) else ""

    if len(content) > 3800:
        if is_live_stream:
            container = Container()
            display_text = f"{icon} **{filename}**\n-# Large file ({lines:,} lines) • Preparing download..."
            container.add_item(Section(TextDisplay(display_text), accessory=Button(label="Preparing...", disabled=True)))
            return [container]
        return [ComponentFile(f"attachment://{filename}")]

    container = Container()
    preview_btn = Button(
        label="Preview",
        style=discord.ButtonStyle.secondary,
        custom_id=f"artprev:{message_id}:{artifact_id}:{active_v}",
        disabled=is_live_stream
    )

    if total_versions >= 2:
        display_text = f"{icon} **{filename}**\n-# {lines:,} lines • v{active_v}{diff_tag}{ts_str}"
    else:
        display_text = f"{icon} **{filename}**\n-# {lines:,} lines{ts_str}"

    section = Section(TextDisplay(display_text), accessory=preview_btn)
    container.add_item(section)

    if total_versions >= 2 and versions and not is_live_stream:
        history_options = []
        for v_entry in reversed(versions[:25]):
            v_num = v_entry.get("version", 1)
            v_summary = v_entry.get("summary", f"Version {v_num}")
            v_adds = v_entry.get("additions", 0)
            v_dels = v_entry.get("deletions", 0)
            diff_stat = f"(+{v_adds} -{v_dels}) " if (v_adds > 0 or v_dels > 0) else ""

            is_latest = (v_num == total_versions)
            v_label = f"Version {v_num} (Latest)" if is_latest else f"Version {v_num}"

            history_options.append(
                discord.SelectOption(
                    label=v_label,
                    value=str(v_num),
                    description=f"{diff_stat}{v_summary}"[:100],
                    emoji=icon,
                    default=(v_num == active_v)
                )
            )

        history_select = Select(
            custom_id=f"arthist:{message_id}:{artifact_id}",
            placeholder="Browse History...",
            options=history_options,
            disabled=is_live_stream
        )
        container.add_item(ActionRow(history_select))

    return [container]