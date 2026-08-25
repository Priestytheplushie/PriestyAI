import io
import logging
from typing import Any
import discord
import time
import zipfile
from discord import ui
from discord.ui import (
    LayoutView,
    Container,
    Section,
    TextDisplay,
    Separator,
    ActionRow,
    Button,
    Select
)
from config.settings import LOADING_EMOJI
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

    ext = (
        filename.rsplit(".", 1)[-1].lower()
        if "." in filename
        else filename.lower()
    )

    return FILE_ICON_MAP.get(ext, DEFAULT_FILE_ICON)


def format_size(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"

    return f"{bytes_count / (1024 * 1024):.1f} MB"


def prepare_artifact_download_payload(
    artifact: dict[str, Any],
    target_version: int = 1
) -> tuple[str, list[discord.File]]:

    filename = artifact.get("filename", "download.txt")
    versions = artifact.get("versions", [])
    files_data = artifact.get("files", [])

    target_v_data = None

    if versions and 1 <= target_version <= len(versions):
        target_v_data = versions[target_version - 1]

    v_files = (
        target_v_data.get("files", files_data)
        if target_v_data
        else files_data
    )

    is_multi = filename.endswith(".zip") or len(v_files) > 1

    discord_files: list[discord.File] = []
    text_lines: list[str] = []

    if is_multi:
        zip_buf = io.BytesIO()

        with zipfile.ZipFile(
            zip_buf,
            "w",
            zipfile.ZIP_DEFLATED
        ) as zf:
            for f in v_files:
                f_name = f.get("filename", "file.txt")
                f_content = f.get("content", "")
                zf.writestr(f_name, f_content)

        zip_buf.seek(0)
        zip_bytes = zip_buf.getvalue()

        zip_fn = (
            filename
            if filename.endswith(".zip")
            else f"{filename.rsplit('.', 1)[0]}.zip"
        )

        discord_files.append(
            discord.File(
                io.BytesIO(zip_bytes),
                filename=zip_fn
            )
        )

        text_lines.append(
            f"📦 **Your project is ready!**"
        )
        text_lines.append(
            f"• `{zip_fn}` — Full project"
        )

        for f in v_files[:9]:
            f_name = f.get("filename", "file.txt")
            f_content = f.get("content", "")

            f_bytes = f_content.encode("utf-8")

            discord_files.append(
                discord.File(
                    io.BytesIO(f_bytes),
                    filename=f_name
                )
            )

            text_lines.append(
                f"• `{f_name}`"
            )

        if len(v_files) > 9:
            text_lines.append(
                f"-# Plus {len(v_files) - 9} more files in the project archive"
            )

    else:
        content = (
            target_v_data.get("content", "")
            if target_v_data
            else (
                v_files[0].get("content", "")
                if v_files
                else ""
            )
        )

        raw_bytes = content.encode("utf-8")

        f_name = (
            filename
            if not filename.endswith(".zip")
            else (
                v_files[0].get("filename", "script.txt")
                if v_files
                else "script.txt"
            )
        )

        discord_files.append(
            discord.File(
                io.BytesIO(raw_bytes),
                filename=f_name
            )
        )

        text_lines.append(
            f"📥 **Your file is ready:** `{f_name}`"
        )

    msg_text = "\n".join(text_lines)

    return msg_text, discord_files


def build_code_preview_modal(
    filename: str,
    raw_code: str,
    channel_id: str | int = 0,
    message_id: str | int = 0,
    attachment_url: str | None = None,
    on_submit_callback: Any = None
) -> DynamicModalV2:

    ext = (
        filename.rsplit(".", 1)[-1].lower()
        if "." in filename
        else ""
    )

    icon = get_file_icon(filename)
    lines = len(raw_code.splitlines())
    size_b = len(raw_code.encode("utf-8"))

    header_parts = [
        f"**{icon} {filename}** ({lines:,} lines • {format_size(size_b)})",
        "-# Click **Submit** below to download this file."
    ]

    if attachment_url and attachment_url.startswith("http"):
        header_parts.append(
            f"[Download directly]({attachment_url})"
        )

    header_text = "\n".join(header_parts)

    formatted_code = (
        f"```{ext}\n"
        f"{raw_code[:3600]}\n"
        f"```"
    )

    fields = [
        {
            "type": "text_display",
            "content": f"{header_text}\n\n{formatted_code}"[:4000]
        }
    ]

    async def default_submit(
        interaction: discord.Interaction,
        data: dict[str, Any]
    ):
        if on_submit_callback:
            await on_submit_callback(interaction, data)
            return

        raw_bytes = raw_code.encode("utf-8")

        f_file = discord.File(
            io.BytesIO(raw_bytes),
            filename=filename
        )

        msg_content = (
            f"📥 **Your file is ready:** `{filename}`"
        )

        if not interaction.response.is_done():
            await interaction.response.send_message(
                content=msg_content,
                file=f_file,
                ephemeral=True
            )

    return DynamicModalV2(
        title=f"{filename}"[:45],
        custom_id=f"modal_preview_{filename[:30]}",
        fields_schema=fields,
        on_submit_callback=default_submit
    )


def build_artifact_open_modal(
    artifact: dict[str, Any],
    target_version: int = 1,
    channel_id: str | int = 0,
    message_id: str | int = 0,
    attachment_url: str | None = None,
    on_submit_callback: Any = None
) -> DynamicModalV2:

    filename = artifact.get("filename", "project.zip")
    title = artifact.get("title", filename)
    files = artifact.get("files", [])
    versions = artifact.get("versions", [])

    icon = get_file_icon(filename)

    target_v_data = None

    if versions and 1 <= target_version <= len(versions):
        target_v_data = versions[target_version - 1]

    is_multi_file = (
        filename.endswith(".zip")
        or len(files) > 1
    )

    fields = []

    if is_multi_file:
        file_list = (
            target_v_data.get("files", files)
            if target_v_data
            else files
        )

        total_lines = sum(
            f.get(
                "lines",
                len(f.get("content", "").splitlines())
            )
            for f in file_list
        )

        total_size = sum(
            f.get(
                "size_bytes",
                len(f.get("content", "").encode("utf-8"))
            )
            for f in file_list
        )

        tree_lines = [
            (
                f"**{icon} {filename}** "
                f"({len(file_list)} files • "
                f"{total_lines:,} lines • "
                f"{format_size(total_size)})"
            ),
            "-# Click **Submit** below to download the project."
        ]

        if attachment_url and attachment_url.startswith("http"):
            tree_lines.append(
                f"[Download directly]({attachment_url})"
            )

        tree_lines.append("\n**Files in this project:**")

        for f in file_list[:12]:
            f_name = f.get("filename", "file.txt")
            f_icon = get_file_icon(f_name)

            f_lines = f.get(
                "lines",
                len(f.get("content", "").splitlines())
            )

            tree_lines.append(
                f"• {f_icon} `{f_name}` — {f_lines:,} lines"
            )

        if len(file_list) > 12:
            tree_lines.append(
                f"-# Plus {len(file_list) - 12} more files"
            )

        fields.append(
            {
                "type": "text_display",
                "content": "\n".join(tree_lines)[:1800]
            }
        )

        if file_list:
            first_f = file_list[0]

            first_name = first_f.get(
                "filename",
                "index.html"
            )

            first_code = first_f.get(
                "content",
                ""
            )

            first_icon = get_file_icon(first_name)

            ext = (
                first_name.rsplit(".", 1)[-1].lower()
                if "." in first_name
                else ""
            )

            fields.append(
                {
                    "type": "text_display",
                    "content": (
                        f"**Preview: {first_icon} `{first_name}`**\n"
                        f"```{ext}\n"
                        f"{first_code[:1800]}\n"
                        f"```"
                    )
                }
            )

    else:
        content = (
            target_v_data.get("content", "")
            if target_v_data
            else (
                files[0].get("content", "")
                if files
                else ""
            )
        )

        lines = len(content.splitlines())
        size_b = len(content.encode("utf-8"))

        ext = (
            filename.rsplit(".", 1)[-1].lower()
            if "." in filename
            else ""
        )

        header_parts = [
            (
                f"**{icon} {filename}** "
                f"({lines:,} lines • {format_size(size_b)})"
            ),
            (
                "-# This file is too large to show in full here. "
                "Click **Submit** to download it."
            )
        ]

        if attachment_url and attachment_url.startswith("http"):
            header_parts.append(
                f"[Download directly]({attachment_url})"
            )

        fields.append(
            {
                "type": "text_display",
                "content": "\n".join(header_parts)
            }
        )

        fields.append(
            {
                "type": "text_display",
                "content": (
                    f"```{ext}\n"
                    f"{content[:3400]}\n"
                    f"```"
                )
            }
        )

    async def default_submit(
        interaction: discord.Interaction,
        data: dict[str, Any]
    ):
        if on_submit_callback:
            await on_submit_callback(interaction, data)
            return

        msg_content, discord_files = (
            prepare_artifact_download_payload(
                artifact,
                target_version
            )
        )

        if not interaction.response.is_done():
            await interaction.response.send_message(
                content=msg_content,
                files=discord_files,
                ephemeral=True
            )

    return DynamicModalV2(
        title=f"{title}"[:45],
        custom_id=f"modal_open_{filename[:30]}",
        fields_schema=fields,
        on_submit_callback=default_submit
    )


def build_artifact_components_for_message(
    artifact: dict[str, Any],
    message_id: str | int = "temp",
    selected_version: int | None = None,
    is_live_stream: bool = False
) -> list[Any]:
    filename = artifact.get(
        "filename",
        "project.zip"
    )

    artifact_id = artifact.get(
        "artifact_id",
        "art_0"
    )

    files = artifact.get(
        "files",
        []
    )

    icon = get_file_icon(filename)

    versions = artifact.get(
        "versions",
        []
    )

    total_versions = max(
        1,
        len(versions)
        if versions
        else artifact.get("total_versions", 1)
    )

    status = artifact.get(
        "status",
        "ready"
    )

    if (
        status == "generating"
        or artifact.get("is_generating")
    ):
        container = Container()

        fn = (
            filename
            or artifact.get("title")
            or "artifact.txt"
        )

        icon = get_file_icon(fn)

        start_t = artifact.get(
            "start_time"
        )

        if start_t is None or start_t <= 0:
            start_t = time.time()
            artifact["start_time"] = start_t

        elapsed = max(
            0,
            int(time.time() - start_t)
        )

        display_text = (
            f"{icon} **{fn}**\n"
            f"-# {LOADING_EMOJI} Creating your file... ({elapsed}s)"
        )

        open_btn = Button(
            label="Open",
            style=discord.ButtonStyle.secondary,
            disabled=True
        )

        container.add_item(
            Section(
                TextDisplay(display_text),
                accessory=open_btn
            )
        )

        return [container]

    is_multi_file = (
        filename.endswith(".zip")
        or len(files) > 1
    )

    active_v = (
        selected_version
        or artifact.get(
            "active_version",
            total_versions
        )
    )

    target_v_data = None

    if versions and 1 <= active_v <= len(versions):
        target_v_data = versions[active_v - 1]

    ts = (
        target_v_data.get("timestamp")
        if target_v_data
        else None
    )

    ts_str = (
        f" • <t:{int(ts)}:R>"
        if ts
        else ""
    )

    container = Container()

    if is_multi_file:
        file_count = (
            len(files)
            if files
            else artifact.get(
                "file_count",
                1
            )
        )

        display_text = (
            f"{icon} **{filename}**\n"
            f"-# {file_count} files • v{active_v}{ts_str}"
        )

        open_btn = Button(
            label="Open",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                f"artopen:"
                f"{message_id}:"
                f"{artifact_id}:"
                f"{active_v}"
            ),
            disabled=is_live_stream
        )

        container.add_item(
            Section(
                TextDisplay(display_text),
                accessory=open_btn
            )
        )

    else:
        content = (
            target_v_data.get("content", "")
            if target_v_data
            else (
                files[0].get("content", "")
                if files
                else ""
            )
        )

        lines = (
            target_v_data.get(
                "lines",
                len(content.splitlines())
            )
            if target_v_data
            else len(content.splitlines())
        )

        adds = (
            target_v_data.get(
                "additions",
                artifact.get("additions", 0)
            )
            if target_v_data
            else 0
        )

        dels = (
            target_v_data.get(
                "deletions",
                artifact.get("deletions", 0)
            )
            if target_v_data
            else 0
        )

        diff_tag = (
            f" (+{adds} -{dels})"
            if adds > 0 or dels > 0
            else ""
        )

        is_large = len(content) > 3800

        if is_large:
            display_text = (
                f"{icon} **{filename}**\n"
                f"-# {lines:,} lines • Large file • "
                f"v{active_v}{diff_tag}{ts_str}"
            )

            btn = Button(
                label="Open",
                style=discord.ButtonStyle.secondary,
                custom_id=(
                    f"artopen:"
                    f"{message_id}:"
                    f"{artifact_id}:"
                    f"{active_v}"
                ),
                disabled=is_live_stream
            )

        else:
            if total_versions >= 2:
                display_text = (
                    f"{icon} **{filename}**\n"
                    f"-# {lines:,} lines • "
                    f"v{active_v}{diff_tag}{ts_str}"
                )
            else:
                display_text = (
                    f"{icon} **{filename}**\n"
                    f"-# {lines:,} lines{ts_str}"
                )

            btn = Button(
                label="Preview",
                style=discord.ButtonStyle.secondary,
                custom_id=(
                    f"artprev:"
                    f"{message_id}:"
                    f"{artifact_id}:"
                    f"{active_v}"
                ),
                disabled=is_live_stream
            )

        container.add_item(
            Section(
                TextDisplay(display_text),
                accessory=btn
            )
        )

    if (
        total_versions >= 2
        and versions
        and not is_live_stream
    ):
        history_options = []

        for v_entry in reversed(versions[:25]):
            v_num = v_entry.get(
                "version",
                1
            )

            v_summary = v_entry.get(
                "summary",
                f"Version {v_num}"
            )

            v_adds = v_entry.get(
                "additions",
                0
            )

            v_dels = v_entry.get(
                "deletions",
                0
            )

            diff_stat = (
                f"(+{v_adds} -{v_dels}) "
                if v_adds > 0 or v_dels > 0
                else ""
            )

            is_latest = (
                v_num == total_versions
            )

            v_label = (
                f"Version {v_num} (Latest)"
                if is_latest
                else f"Version {v_num}"
            )

            history_options.append(
                discord.SelectOption(
                    label=v_label,
                    value=str(v_num),
                    description=(
                        f"{diff_stat}{v_summary}"
                    )[:100],
                    emoji=icon,
                    default=(
                        v_num == active_v
                    )
                )
            )

        history_select = Select(
            custom_id=(
                f"arthist:"
                f"{message_id}:"
                f"{artifact_id}"
            ),
            placeholder="Browse History...",
            options=history_options,
            disabled=is_live_stream
        )

        container.add_item(
            ActionRow(history_select)
        )

    return [container]
