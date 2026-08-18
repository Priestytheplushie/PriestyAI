# -*- coding: utf-8 -*-
"""
Script writer and visual asset compilation orchestrator for PriestyAI News.
Coordinates Director and Editor passes using the Gemini API.
"""

import os
import io
import json
import asyncio
import urllib.parse
import random
import re
import aiohttp
import matplotlib.pyplot as plt
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont

try:
    from tools.news.utils import clean_display_name, clean_unicode_text
except ImportError:
    try:
        from .utils import clean_display_name, clean_unicode_text
    except ImportError:
        from utils import clean_display_name, clean_unicode_text


class MessageQuoteSchema(BaseModel):
    author: str = Field(
        description="Display name or username of the user being quoted."
    )
    text: str = Field(description="The exact message string being quoted.")


class DirectorSegmentOutline(BaseModel):
    segment_id: int = Field(description="Sequential ID from 1 up to length.")
    topic: str = Field(
        description="The narrow discussion topic focused in this segment."
    )
    frame_template: str = Field(
        description="The layout template: 'Solo Anchor', 'Standard Report', 'Full-Screen Media', 'Split-Screen' (continuous conversation card), or 'Guest Interview' (couch interview layout with profile transition)."
    )
    host_pose: str = Field(
        default="standard",
        description="The physical posture and facial expression for PriestyAI in this segment. Must be exactly one of: 'standard' (default talking/idle), 'pointing' (gesturing towards layouts/charts), 'thinking' (thoughtful look on chin for Q&A/mailbag), or 'sighing' (closed-eyes weary look for satirical monologues).",
    )
    source_channel: Optional[str] = Field(
        default="", description="The specific source channel name."
    )
    overlay_search_query: Optional[str] = Field(
        default="",
        description="A 2-4 word query for Pexels Photo API if using B-Roll.",
    )
    pexels_bg_search: Optional[str] = Field(
        default="", description="A 2-4 word query for Pexels Video API background loop."
    )
    stats_chart_type: Optional[str] = Field(
        default="",
        description="Chart type: 'activity_velocity', 'channel_volume', 'top_games', 'top_chatters', 'word_wall' if applicable.",
    )
    calendar_event_name: Optional[str] = Field(default="")
    calendar_date_iso: Optional[str] = Field(default="")
    award_recipient: Optional[str] = Field(default="")
    award_title: Optional[str] = Field(default="")
    mailbag_sender: Optional[str] = Field(default="")
    mailbag_question: Optional[str] = Field(default="")
    vibe_query: Optional[str] = Field(default="")
    quotes: Optional[List[MessageQuoteSchema]] = Field(default=[])


class DirectorBlueprintSchema(BaseModel):
    segments: List[DirectorSegmentOutline] = Field(
        description="The structural segment array planned by the Director."
    )


class EditorSegmentScript(BaseModel):
    segment_id: int
    script_text: str = Field(
        description="Dialogue script spoken by PriestyAI. Keep between 80 to 120 words."
    )
    banner_text: str = Field(
        description="Headline text displayed on the lower banner bar. Max 60 chars."
    )
    ticker_text: str = Field(
        description="Short update to scroll on the bottom ticker bar. Max 60 chars."
    )
    pause_duration: Optional[int] = Field(
        default=0,
        description="Silent reading delay added to end of segment in seconds (0-5).",
    )


class EditorShowSchema(BaseModel):
    segments: List[EditorSegmentScript] = Field(
        description="The ordered list of finalized segment scripts."
    )


async def fetch_avatar_bytes(url: str, output_path: str) -> bool:
    """Downloads avatar image bytes and saves them locally."""
    if not url:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 200:
                    img_bytes = await response.read()
                    with open(output_path, "wb") as f:
                        f.write(img_bytes)
                    return True
    except Exception:
        pass
    return False


def render_stacked_conversation_card(
    quotes: List[dict], avatar_paths: List[str], output_path: str
):
    """Renders a Discord-style conversation block with stacked user messages."""
    card_width = 650
    card_height = 80 + (len(quotes) * 85)

    bg_color = (49, 51, 56, 245)
    username_color = (242, 243, 245)
    text_color = (219, 222, 225)
    timestamp_color = (148, 155, 164)
    avatar_bg = (88, 101, 242)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)], radius=14, fill=bg_color
    )

    try:
        font_avatar = ImageFont.truetype("arial.ttf", 20)
        font_name = ImageFont.truetype("arial.ttf", 16)
        font_text = ImageFont.truetype("arial.ttf", 15)
        font_muted = ImageFont.truetype("arial.ttf", 12)
    except IOError:
        font_avatar = ImageFont.load_default()
        font_name = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_muted = ImageFont.load_default()

    current_y = 25
    for idx, q in enumerate(quotes):
        author = clean_display_name(q.get("author", "User"))
        text = clean_unicode_text(q.get("text", ""))
        av_local = avatar_paths[idx] if idx < len(avatar_paths) else ""

        av_x, av_size = 25, 45
        has_av = False
        if av_local and os.path.exists(av_local):
            try:
                av_img = Image.open(av_local).convert("RGBA")
                av_resized = av_img.resize((av_size, av_size), Image.Resampling.LANCZOS)
                mask = Image.new("L", (av_size, av_size), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse([(0, 0), (av_size, av_size)], fill=255)
                card.paste(av_resized, (av_x, current_y), mask)
                has_av = True
            except Exception:
                pass

        if not has_av:
            draw.ellipse(
                [(av_x, current_y), (av_x + av_size, current_y + av_size)],
                fill=avatar_bg,
            )
            letter = author[0].upper() if author else "U"
            draw.text(
                (av_x + 15, current_y + 11),
                letter,
                fill=(255, 255, 255),
                font=font_avatar,
            )

        text_x = 85
        draw.text(
            (text_x, current_y + 2), author[:25], fill=username_color, font=font_name
        )

        name_len_px = len(author[:25]) * 9 + 10
        draw.text(
            (text_x + name_len_px, current_y + 5),
            "Today at 2:45 PM",
            fill=timestamp_color,
            font=font_muted,
        )

        clean_msg = text
        wrapped_lines = []
        words = clean_msg.split()
        current_line = []
        for word in words:
            if len(" ".join(current_line + [word])) > 55:
                wrapped_lines.append(" ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        wrapped_lines.append(" ".join(current_line))

        line_y = current_y + 24
        for line in wrapped_lines[:2]:
            draw.text((text_x, line_y), line, fill=text_color, font=font_text)
            line_y += 20

        current_y += 85

    card.save(output_path, "PNG")


def render_quote_wall_card(quotes: List[dict], output_path: str):
    """Generates a sticky-note pin board card for server quote roundups."""
    card_width = 650
    card_height = 420

    backing_color = (24, 24, 35, 245)
    note_colors = [
        (254, 240, 138, 255),
        (191, 219, 254, 255),
        (254, 205, 211, 255),
        (187, 247, 208, 255),
    ]
    text_color = (15, 23, 42)
    tag_color = (225, 29, 72)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)], radius=16, fill=backing_color
    )

    try:
        font_header = ImageFont.truetype("arial.ttf", 18)
        font_text = ImageFont.truetype("arial.ttf", 15)
        font_author = ImageFont.truetype("arial.ttf", 13)
    except IOError:
        font_header = ImageFont.load_default()
        font_text = ImageFont.load_default()
        font_author = ImageFont.load_default()

    draw.text(
        (30, 25), "📌  SERVER QUOTE BOARD", fill=(255, 255, 255), font=font_header
    )

    note_coords = [
        (40, 75, 260, 215),
        (340, 95, 260, 215),
        (190, 235, 260, 155),
    ]

    for idx, q in enumerate(quotes[:3]):
        x, y, w, h = note_coords[idx]
        author = clean_display_name(q.get("author", "User"))
        text = clean_unicode_text(q.get("text", ""))

        note_color = note_colors[idx % len(note_colors)]

        draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=10, fill=note_color)

        draw.ellipse([(x + w // 2 - 6, y - 4), (x + w // 2 + 6, y + 8)], fill=tag_color)

        draw.text((x + 15, y + 20), f"@{author[:18]}", fill=tag_color, font=font_author)

        wrapped_lines = []
        words = text.split()
        current_line = []
        for word in words:
            if len(" ".join(current_line + [word])) > 28:
                wrapped_lines.append(" ".join(current_line))
                current_line = [word]
            else:
                current_line.append(word)
        wrapped_lines.append(" ".join(current_line))

        text_y = y + 42
        for line in wrapped_lines[:4]:
            draw.text((x + 15, text_y), f'"{line}"', fill=text_color, font=font_text)
            text_y += 22

    card.save(output_path, "PNG")


def render_server_stats_card(chart_type: str, stats_data: dict, output_path: str):
    """Plots and compiles active server statistics using Matplotlib."""
    plt.style.use("dark_background")
    fig, ax = plt.subplots(figsize=(5.5, 3.4), dpi=100)

    neon_cyan = "#00d2ff"
    neon_magenta = "#db2777"

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#475569")
    ax.spines["bottom"].set_color("#475569")
    ax.tick_params(colors="#94a3b8", labelsize=9)

    has_data = False

    if chart_type == "activity_velocity" and stats_data.get("velocity"):
        velocity = stats_data["velocity"]
        hours = list(velocity.keys())
        counts = list(velocity.values())
        ax.plot(hours, counts, color=neon_cyan, marker="o", linewidth=2, markersize=5)
        ax.fill_between(hours, counts, color=neon_cyan, alpha=0.15)
        ax.set_title(
            "MESSAGE VELOCITY (CHRONOLOGICAL)", color="#f8fafc", fontsize=11, pad=10
        )
        plt.xticks(rotation=45)
        has_data = True

    elif chart_type == "channel_volume" and stats_data.get("channel_volume"):
        volume = stats_data["channel_volume"]
        channels = list(volume.keys())
        counts = list(volume.values())
        y_pos = range(len(channels))
        ax.barh(y_pos, counts, color=neon_magenta, height=0.6)
        ax.set_yticks(y_pos)

        cleaned_labels = [f"#{clean_unicode_text(c)}" for c in channels]
        ax.set_yticklabels(cleaned_labels)

        ax.invert_yaxis()
        ax.set_title(
            "TOP ACTIVE CHANNELS (VOLUME)", color="#f8fafc", fontsize=11, pad=10
        )
        has_data = True

    elif chart_type == "top_chatters" and stats_data.get("top_chatters"):
        chatters = stats_data["top_chatters"]
        names = list(chatters.keys())
        counts = list(chatters.values())
        y_pos = range(len(names))
        ax.barh(y_pos, counts, color=neon_cyan, height=0.6)
        ax.set_yticks(y_pos)

        cleaned_labels = [clean_display_name(n)[:15] for n in names]
        ax.set_yticklabels(cleaned_labels)

        ax.invert_yaxis()
        ax.set_title(
            "TOP ACTIVE CONTRIBUTORS (VOLUME)", color="#f8fafc", fontsize=11, pad=10
        )
        has_data = True

    elif chart_type == "word_wall" and stats_data.get("word_frequencies"):
        words_dict = stats_data["word_frequencies"]
        ax.set_xlim(0, 10)
        ax.set_ylim(0, 10)
        ax.axis("off")
        colors = [
            "#00d2ff",
            "#db2777",
            "#38bdf8",
            "#fb7185",
            "#a78bfa",
            "#f472b6",
            "#34d399",
        ]
        for word, freq in list(words_dict.items())[:12]:
            x = random.uniform(1.5, 8.5)
            y = random.uniform(1.5, 8.5)
            size = max(11, min(25, 11 + (freq * 2.5)))
            color = random.choice(colors)
            ax.text(
                x,
                y,
                word,
                fontsize=size,
                color=color,
                ha="center",
                va="center",
                weight="bold",
            )
        ax.set_title(
            "SERVER WORD WALL (POPULAR DAILY TERMS)",
            color="#f8fafc",
            fontsize=11,
            pad=10,
        )
        has_data = True

    elif chart_type == "top_games" and stats_data.get("top_games"):
        games = stats_data["top_games"]
        game_names = list(games.keys())
        counts = list(games.values())
        x_pos = range(len(game_names))
        ax.bar(x_pos, counts, color=neon_cyan, width=0.5)
        ax.set_xticks(x_pos)

        cleaned_labels = [clean_unicode_text(g)[:10] for g in game_names]
        ax.set_xticklabels(cleaned_labels, rotation=15)

        ax.set_title(
            "POPULAR PRESENCE / DESKTOP GAMES", color="#f8fafc", fontsize=11, pad=10
        )
        has_data = True

    if not has_data:
        ax.text(
            0.5,
            0.5,
            "STATISTICS STAGED\nFOR LATER METRIC LOOPS",
            color="#94a3b8",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=12,
        )
        ax.set_title(
            "SERVER PERFORMANCE METRIC INDEX", color="#f8fafc", fontsize=11, pad=10
        )

    fig.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", bbox_inches="tight", transparent=True)
    buf.seek(0)
    plot_img = Image.open(buf).convert("RGBA")
    plt.close(fig)

    card_width = 650
    card_height = 420
    backing_color = (15, 23, 42, 245)
    border_color = (0, 210, 255)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)],
        radius=16,
        fill=backing_color,
        outline=border_color,
        width=2,
    )

    card.paste(plot_img, (50, 45), plot_img)
    card.save(output_path, "PNG")


def render_calendar_card(event_name: str, date_iso_str: str, output_path: str):
    """Renders a physical calendar page indicating schedule details."""
    card_width = 480
    card_height = 320

    backing_color = (245, 245, 247)
    header_color = (200, 30, 30)
    text_dark = (15, 23, 42)
    text_muted = (71, 85, 105)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)], radius=16, fill=backing_color
    )
    draw.rounded_rectangle([(0, 0), (card_width, 60)], radius=16, fill=header_color)
    draw.rectangle([(0, 40), (card_width, 60)], fill=header_color)

    try:
        font_header = ImageFont.truetype("arial.ttf", 28)
        font_day_name = ImageFont.truetype("arial.ttf", 20)
        font_num = ImageFont.truetype("arial.ttf", 100)
        font_title = ImageFont.truetype("arial.ttf", 22)
        font_time = ImageFont.truetype("arial.ttf", 16)
    except IOError:
        font_header = ImageFont.load_default()
        font_day_name = ImageFont.load_default()
        font_num = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_time = ImageFont.load_default()

    try:
        dt = datetime.fromisoformat(date_iso_str.replace("Z", "+00:00"))
        month_str = dt.strftime("%B").upper()
        day_name = dt.strftime("%A").upper()
        day_num = str(dt.day)
        time_str = dt.strftime("%I:%M %p UTC")
    except Exception:
        month_str = "EVENT"
        day_name = "CALENDAR"
        day_num = "!!"
        time_str = "TBA"

    event_name = clean_unicode_text(event_name)
    draw.text((240, 15), month_str, fill=(255, 255, 255), font=font_header, anchor="mm")
    draw.text((120, 155), day_num, fill=text_dark, font=font_num, anchor="mm")
    draw.text((120, 225), day_name, fill=text_muted, font=font_day_name, anchor="mm")
    draw.line([(240, 80), (240, 280)], fill=(200, 200, 200), width=3)

    wrapped_title_lines = []
    words = event_name.split()
    current_line = []
    for word in words:
        if len(" ".join(current_line + [word])) > 18:
            wrapped_title_lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    wrapped_title_lines.append(" ".join(current_line))

    title_y = 100
    for line in wrapped_title_lines[:3]:
        draw.text((260, title_y), line, fill=text_dark, font=font_title)
        title_y += 28

    draw.text((260, 240), f"🕒 {time_str}", fill=header_color, font=font_time)
    card.save(output_path, "PNG")


def render_mystery_placeholder_card(output_path: str):
    """Renders a late-night teaser preview box before visual generation reveals."""
    card_width = 450
    card_height = 320
    bg_color = (24, 24, 35, 245)
    border_color = (236, 72, 153)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)],
        radius=16,
        fill=bg_color,
        outline=border_color,
        width=3,
    )

    try:
        font_q = ImageFont.truetype("arial.ttf", 100)
        font_sub = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_q = ImageFont.load_default()
        font_sub = ImageFont.load_default()

    draw.text(
        (card_width // 2, card_height // 2 - 20),
        "?",
        fill=border_color,
        font=font_q,
        anchor="mm",
    )

    draw.text(
        (card_width // 2, card_height - 50),
        "VIBE CHECK: CLASSIFIED",
        fill=(255, 255, 255),
        font=font_sub,
        anchor="mm",
    )
    draw.text(
        (card_width // 2, card_height - 25),
        "GENERATING ARTWORK...",
        fill=(147, 51, 234),
        font=font_sub,
        anchor="mm",
    )

    card.save(output_path, "PNG")


def render_award_plaque_card(
    recipient_name: str,
    award_title: str,
    avatar_path: str,
    server_name: str,
    output_path: str,
):
    """Generates a high-quality commemorative commendation award plaque."""
    card_width = 480
    card_height = 320

    mahogany_dark = (50, 20, 10)
    mahogany_light = (90, 40, 25)
    gold_border = (212, 175, 55)
    text_color = (255, 255, 255)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)], radius=18, fill=mahogany_dark
    )
    draw.rounded_rectangle(
        [(8, 8), (card_width - 8, card_height - 8)], radius=14, fill=mahogany_light
    )
    draw.rounded_rectangle(
        [(16, 16), (card_width - 16, card_height - 16)], radius=12, fill=mahogany_dark
    )

    plaque_bg = (30, 30, 35)
    draw.rounded_rectangle(
        [(24, 24), (card_width - 24, card_height - 24)],
        radius=8,
        fill=plaque_bg,
        outline=gold_border,
        width=3,
    )

    try:
        font_header = ImageFont.truetype("arial.ttf", 15)
        font_title = ImageFont.truetype("arial.ttf", 22)
        font_winner = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_header = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_winner = ImageFont.load_default()

    recipient_name = clean_display_name(recipient_name)
    award_title = clean_unicode_text(award_title)
    server_name = clean_unicode_text(server_name).upper()

    draw.text(
        (card_width // 2, 45),
        f"{server_name} SPECIAL COMMENDATION",
        fill=gold_border,
        font=font_header,
        anchor="mm",
    )

    av_x, av_y, av_size = 50, 95, 110
    draw.ellipse(
        [(av_x - 6, av_y - 6), (av_x + av_size + 6, av_y + av_size + 6)],
        fill=gold_border,
    )
    draw.ellipse(
        [(av_x - 3, av_y - 3), (av_x + av_size + 3, av_y + av_size + 3)],
        fill=(20, 20, 25),
    )

    has_av = False
    if avatar_path and os.path.exists(avatar_path):
        try:
            av_img = Image.open(avatar_path).convert("RGBA")
            av_resized = av_img.resize((av_size, av_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (av_size, av_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse([(0, 0), (av_size, av_size)], fill=255)
            card.paste(av_resized, (av_x, av_y), mask)
            has_av = True
        except Exception:
            pass

    if not has_av:
        draw.ellipse(
            [(av_x, av_y), (av_x + av_size, av_y + av_size)], fill=(88, 101, 242)
        )
        letter = recipient_name[0].upper() if recipient_name else "U"
        font_avatar_fallback = (
            ImageFont.truetype("arial.ttf", 40)
            if os.path.exists("arial.ttf")
            else font_header
        )
        draw.text(
            (av_x + av_size // 2, av_y + av_size // 2),
            letter,
            fill=(255, 255, 255),
            font=font_avatar_fallback,
            anchor="mm",
        )

    text_start_x = 190
    draw.text(
        (text_start_x, 100), "AWARD CATEGORY:", fill=(148, 155, 164), font=font_header
    )

    wrapped_title_lines = []
    words = award_title.split()
    current_line = []
    for word in words:
        if len(" ".join(current_line + [word])) > 20:
            wrapped_title_lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    wrapped_title_lines.append(" ".join(current_line))

    title_y = 125
    for line in wrapped_title_lines[:2]:
        draw.text((text_start_x, title_y), line, fill=text_color, font=font_title)
        title_y += 24

    draw.text((text_start_x, 215), "PRESENTED TO:", fill=gold_border, font=font_header)
    draw.text((text_start_x, 235), recipient_name, fill=text_color, font=font_winner)

    rib_x, rib_y = card_width - 70, card_height - 70
    draw.ellipse([(rib_x, rib_y), (rib_x + 35, rib_y + 35)], fill=gold_border)
    draw.polygon(
        [(rib_x + 10, rib_y + 30), (rib_x + 5, rib_y + 55), (rib_x + 15, rib_y + 45)],
        fill=gold_border,
    )
    draw.polygon(
        [(rib_x + 25, rib_y + 30), (rib_x + 30, rib_y + 55), (rib_x + 20, rib_y + 45)],
        fill=gold_border,
    )

    card.save(output_path, "PNG")


def render_community_mailbag_card(
    sender_name: str, question_text: str, output_path: str
):
    """Renders a sticky mailbag post-it note graphic containing a user's question."""
    card_width = 480
    card_height = 320

    post_it_yellow = (254, 240, 138, 255)
    tape_gray = (203, 213, 225, 180)
    text_color = (15, 23, 42)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [(10, 10), (card_width - 10, card_height - 10)],
        radius=12,
        fill=(10, 10, 15, 100),
    )

    draw.rounded_rectangle(
        [(0, 0), (card_width - 15, card_height - 15)], radius=12, fill=post_it_yellow
    )

    draw.rectangle(
        [(card_width // 2 - 50, -10), (card_width // 2 + 50, 25)], fill=tape_gray
    )

    try:
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_winner = ImageFont.truetype("arial.ttf", 18)
        font_text = ImageFont.truetype("arial.ttf", 20)
    except IOError:
        font_header = ImageFont.load_default()
        font_winner = ImageFont.load_default()
        font_text = ImageFont.load_default()

    sender_name = clean_display_name(sender_name)
    question_text = clean_unicode_text(question_text)

    draw.text((30, 45), "📬  SERVER MAILBAG:", fill=(100, 116, 139), font=font_header)

    wrapped_lines = []
    words = question_text.split()
    current_line = []
    for word in words:
        if len(" ".join(current_line + [word])) > 32:
            wrapped_lines.append(" ".join(current_line))
            current_line = [word]
        else:
            current_line.append(word)
    wrapped_lines.append(" ".join(current_line))

    text_y = 80
    for line in wrapped_lines[:5]:
        draw.text((30, text_y), f'"{line}"', fill=text_color, font=font_text)
        text_y += 28

    draw.text((30, 245), "ASKED BY:", fill=(100, 116, 139), font=font_header)
    draw.text((30, 265), f"@{sender_name}", fill=(225, 29, 72), font=font_winner)

    card.save(output_path, "PNG")


def render_guest_interview_card(guest_name: str, avatar_path: str, output_path: str):
    """Renders a talk-show host couch profile intro block for guests."""
    card_width = 450
    card_height = 320
    bg_color = (20, 20, 25, 245)
    border_color = (219, 39, 119)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle(
        [(0, 0), (card_width, card_height)],
        radius=16,
        fill=bg_color,
        outline=border_color,
        width=3,
    )

    try:
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_name = ImageFont.truetype("arial.ttf", 24)
    except IOError:
        font_header = ImageFont.load_default()
        font_name = ImageFont.load_default()

    guest_name = clean_display_name(guest_name)

    draw.rounded_rectangle(
        [(card_width // 2 - 80, 25), (card_width // 2 + 80, 55)],
        radius=8,
        fill=border_color,
    )
    draw.text(
        (card_width // 2, 40),
        "SPECIAL GUEST",
        fill=(255, 255, 255),
        font=font_header,
        anchor="mm",
    )

    av_size = 120
    av_x = (card_width - av_size) // 2
    av_y = 85

    draw.ellipse(
        [(av_x - 4, av_y - 4), (av_x + av_size + 4, av_y + av_size + 4)],
        fill=border_color,
    )
    draw.ellipse(
        [(av_x - 2, av_y - 2), (av_x + av_size + 2, av_y + av_size + 2)],
        fill=(20, 20, 25),
    )

    has_av = False
    if avatar_path and os.path.exists(avatar_path):
        try:
            av_img = Image.open(avatar_path).convert("RGBA")
            av_resized = av_img.resize((av_size, av_size), Image.Resampling.LANCZOS)
            mask = Image.new("L", (av_size, av_size), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse([(0, 0), (av_size, av_size)], fill=255)
            card.paste(av_resized, (av_x, av_y), mask)
            has_av = True
        except Exception:
            pass

    if not has_av:
        draw.ellipse(
            [(av_x, av_y), (av_x + av_size, av_y + av_size)], fill=(88, 101, 242)
        )
        letter = guest_name[0].upper() if guest_name else "G"
        font_av_fallback = (
            ImageFont.truetype("arial.ttf", 45)
            if os.path.exists("arial.ttf")
            else font_name
        )
        draw.text(
            (av_x + av_size // 2, av_y + av_size // 2),
            letter,
            fill=(255, 255, 255),
            font=font_av_fallback,
            anchor="mm",
        )

    draw.text(
        (card_width // 2, 240),
        f"@{guest_name}",
        fill=(255, 255, 255),
        font=font_name,
        anchor="mm",
    )
    draw.text(
        (card_width // 2, 275),
        "JOINING US ON THE COUCH",
        fill=(147, 51, 234),
        font=font_header,
        anchor="mm",
    )

    card.save(output_path, "PNG")


async def download_pollinations_vibe_art(prompt: str, output_path: str) -> bool:
    """Invokes the Pollinations AI generator to produce themed server background paintings."""
    if not prompt:
        return False
    encoded_prompt = urllib.parse.quote(prompt.strip())
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={random.randint(1, 99999)}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=30) as response:
                if response.status == 200:
                    img_bytes = await response.read()
                    with open(output_path, "wb") as f:
                        f.write(img_bytes)
                    return True
    except Exception as e:
        print(f"      ⚠️ Failed to compile vibe art: {e}")
    return False


async def download_pexels_background_loop(
    query: str, output_path: str, pexels_key: str
) -> bool:
    """Queries Pexels Video API to search and retrieve matching high-fidelity backdrop loops."""
    if not pexels_key or not query or not isinstance(query, str) or not query.strip():
        return False
    encoded_query = urllib.parse.quote(query.strip())
    url = f"https://api.pexels.com/videos/search?query={encoded_query}&per_page=1&orientation=landscape"
    headers = {"Authorization": pexels_key}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    videos = data.get("videos", [])
                    if videos:
                        video_files = videos[0].get("video_files", [])
                        best_link = None
                        for vf in video_files:
                            if vf.get("width") == 1280 or vf.get("height") == 720:
                                best_link = vf.get("link")
                                break
                        if not best_link and video_files:
                            best_link = video_files[0].get("link")
                        if best_link:
                            async with session.get(best_link, timeout=30) as vid_resp:
                                if vid_resp.status == 200:
                                    img_bytes = await vid_resp.read()
                                    with open(output_path, "wb") as f_out:
                                        f_out.write(img_bytes)
                                    return True
    except Exception as e:
        print(f"      ⚠️ Failed to fetch Pexels Video: {e}")
    return False


async def download_pexels_photo_overlay(
    query: str, output_path: str, pexels_key: str
) -> bool:
    """Retrieves high-fidelity thematic photos from Pexels for B-Roll layout inserts."""
    if not pexels_key or not query or not isinstance(query, str) or not query.strip():
        return False
    encoded_query = urllib.parse.quote(query.strip())
    url = f"https://api.pexels.com/v1/search?query={encoded_query}&per_page=1"
    headers = {"Authorization": pexels_key}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15) as response:
                if response.status == 200:
                    data = await response.json()
                    photos = data.get("photos", [])
                    if photos:
                        photo_url = photos[0].get("src", {}).get("large")
                        if photo_url:
                            async with session.get(photo_url, timeout=20) as img_resp:
                                if img_resp.status == 200:
                                    img_bytes = await img_resp.read()
                                    with open(output_path, "wb") as f:
                                        f.write(img_bytes)
                                    return True
    except Exception as e:
        print(f"      ⚠️ Failed to fetch Pexels Photo: {e}")
    return False


DIRECTOR_MORNING_PROMPT = """
You are the Technical Director for a professional, encouraging, and clear Morning News Show on a Discord server named {server_name}.
Your job is to allocate exactly 7 segments for a fluid, data-driven morning broadcast. 

Enforce these strict blueprints and guidelines:
1. THE ANCHOR SLOTS:
   - Slot 1: Opener (Solo Anchor). Greet the server warmly, citing episode, date, and broadcast time.
   - Slot 3: Server Performance Metric (Full-Screen Media). Choose a stats_chart_type (e.g. 'activity_velocity', 'channel_volume', 'top_games').
   - Slot 4: Calendar / Upcoming Events (Standard Report). Populate calendar_event_name and calendar_date_iso. If upcoming events are empty, dynamically swap this with a real-world news headline from the 'real_world_news' list.
   - Slot 7: Outro (Solo Anchor). Warm closing wishing everyone a productive day, generically prompting users to answer the upcoming Question of the Day (QOTD) poll below the video.
2. CHAT RECAPS & ANNOUNCEMENTS (Slots 2, 5, 6):
   - Allocate these slots dynamically based on active local channels. Inspect the 'announcements' array and the 'public_discussions' conversations.
   - Use 'Split-Screen' or 'Standard Report' to show clean conversation cards containing active member quotes. 
   - CRITICAL LAYOUT EXCLUSION: You are strictly forbidden from choosing 'Guest Interview' frame templates or assigning couch cards to morning segments. The morning show must never feature couch interview formats.
3. CRITICAL TARGETING RULE: Do not focus the show on just one or two prominent members. Highlight as many different active users as possible across the local segments (User A != User B != User C).
4. STAFF IDENTITY RULE: You are PriestyAI, the talk-show host. You are NOT the human developer 'Priesty'. Refer to human staff, developers, or admins in the third person as 'our administrator, [Name]', 'our developer, [Name]', or 'the mod crew'. Never use first-person pronouns ('I', 'me', 'my') to represent their statements.
5. QUIET DAY MODULAR SWAP: If total server discussions are quiet (under 15 messages), replace the chat recaps with a creative, detailed 'Server Lore Explainer' segment highlighting a fun running joke, role history, or channel origin from the logs.
6. HOST POSE SELECTION:
   - Choose 'pointing' when a stats chart (stats_chart_type) or a calendar event is on screen (e.g., Slot 3, Slot 4).
   - Choose 'thinking' when presenting chat roundups, mailbags, or analyzing conversations.
   - Choose 'standard' for host openings, closings, and generic dialogue segments.
"""

DIRECTOR_NIGHT_PROMPT = """
You are the Late-Night Show Director for an exceptionally funny, satirical, and fast-paced talk show on a Discord server named {server_name}.
Your job is to plan a highly detailed 10-step late-night blueprint using these non-negotiable step guidelines:

1. THE STEP-BY-STEP BLUEPRINT LAYOUT (Slots 1 to 10):
   - Slot 1: Monologue / Cold Open (Solo Anchor). Roast today's weirdest quote or server drama.
   - Slot 2: Server Vibe Secret (Standard Report). Tease that the render engine is compiling the secret vibe painting. Do NOT leak the vibe name or description in dialogue; keep it a total secret. Provide vibe_query.
   - Slot 3: Rapid-Fire Chat Roasts (Full-Screen Media). Group 3-4 funny server quotes in 'quotes'.
   - Slot 4: Decree on Moderation (Standard Report). Roast moderator warnings or admin updates as tyrannical laws.
     CRITICAL EMPTY FALLBACK SUB-IN: If no moderation updates or admin posts exist in the local logs, you MUST substitute this segment with a 'World News Decree'. Select one prominent geopolitical or global headline from the 'World' news category of real_world_news (do NOT use technology or science feeds), and treat it as a tyrannical Global Decree issued by a corporate or political council.
   - Slot 5: Special Guest Couch (Guest Interview). Pick an active user, call them "the special guest of the night", ask a funny question, and quote their past messages as their guest answers.
   - Slot 6: Real Life Satire (Standard Report). Satirize a prominent story from 'real_world_news'.
   - Slot 7: Weather Forecast (Standard Report). Select a random active member and make a funny weather report based on their desktop presence gaming habits.
   - Slot 8: Community Awards (Standard Report). Present a funny, highly unique custom trophy award to an active user. Fill 'award_recipient' and 'award_title'.
   - Slot 9: Server Vibe Unveiling (Full-Screen Media). Officially reveal the completed Pollinations Vibe Art and announce the Vibe Name. Use the same vibe_query.
   - Slot 10: Sleep Outro (Solo Anchor). Tell everyone to close their IDEs, go to sleep, and touch real-world grass.
2. CRITICAL CO-OCCURRENCE TARGETING RULES: 
   - Enforce strict spotlight distribution so the show does not focus on only one or two people. 
   - If a member is selected as the guest on the Couch Interview (Slot 5), they are strictly INELIGIBLE to receive the Community Award (Slot 8) or the Weather Forecast roast (Slot 7) (Couch Guest != Award Winner != Weather Target). Include at least 3-4 different active members across the program.
3. STAFF IDENTITY RULE: You are PriestyAI, the bot talk-show host. You are NOT the human developer 'Priesty'. Refer to human staff, developers, or admins in the third person as 'our administrator, [Name]', 'our developer, [Name]', or 'the mod crew'. Never use first-person pronouns ('I', 'me', 'my') to represent their statements.
4. QUIET DAY MODULAR SWAPS:
   - If the server discussions are extremely quiet (under 15 messages), swap Slot 2's vibe teaser with a 'User Dossier' segment displaying a mystery portrait card, and swap Slot 8's award with a self-aware, subtle stand-up comedy block roasting your own rendering code and pulling a trivial keyword from logs to tell groan-worthy, dry puns.
5. HOST POSE SELECTION:
   - Choose 'pointing' when revealing the stats, the final vibe art painting, or handing out the custom trophy award (e.g., Slot 8, Slot 9).
   - Choose 'thinking' when exploring the classified vibe secrets or the Q&A mailbag (e.g., Slot 2, Slot 5).
   - Choose 'sighing' for self-aware roasts, tyrannical mod decrees, and the monologue or closing sequences (e.g., Slot 1, Slot 4, Slot 10).
   - Choose 'standard' for general commentary and interview segments.
"""

EDITOR_MORNING_PROMPT = """
You are the Head Scriptwriter and Editor for a professional, encouraging Daily Morning News Show on a Discord server named {server_name}.
Your job is to write the final broadcast script based on the show blueprint provided by the Technical Director.

For each pre-allocated segment in the blueprint, you must write:
- The script dialogue spoken by the host (80 to 120 words). Keep it highly engaging, clear, and professional.
- The lower-third banner text.
- The scrolling bottom ticker update.

CRITICAL RULE FOR THE OUTRO (SEGMENT 7):
Because the exact Question of the Day (QOTD) is generated dynamically and posted below this video after rendering completes, the host does not know the specific question text yet. Under no circumstances should the host hallucinate, make up, or guess a specific question. Instead, write the dialogue to invite users to respond to the upcoming poll, instructing the host to tell everyone to generically "check it out" below the video once the broadcast ends.

You are strictly forbidden from modifying the segment structure, omitting segments, or merging topics. Write exactly one script block for each segment ID in the blueprint.
"""

EDITOR_NIGHT_PROMPT = """
You are the Late-Night Scriptwriter and Lead Editor for an exceptionally witty, satirical talk show on a Discord server named {server_name}.
Your job is to write the final broadcast scripts based on the blueprint provided by the Technical Director.

For each pre-allocated segment in the blueprint, write:
- The script dialogue spoken by the host (80 to 120 words). Keep it fast-paced, highly sarcastic, and witty.
- The lower-third banner text.
- The scrolling bottom ticker update.

You are strictly forbidden from modifying the segment structure, omitting segments, or merging topics. Write exactly one script block for each segment ID in the blueprint.
"""


async def execute_generation_with_failover(
    prompt_func, edition: str, max_retries: int = 5
):
    """
    Executes a generative step with key rotation and exponential backoff.
    If a 429 rate limit is hit on a secondary key, waits 60s and swaps to the main key.
    On failure of a sub-part, only that sub-part is retried.
    """
    primary_key = (
        os.getenv("GEMINI_NEWS_KEY_MORNING")
        if edition.lower() == "morning"
        else os.getenv("GEMINI_NEWS_KEY_EVENING")
    )

    if not primary_key:
        primary_key = os.getenv("GEMINI_API_KEY")

    backup_key = os.getenv("GEMINI_API_KEY")
    current_key = primary_key
    using_backup = current_key == backup_key

    for attempt in range(1, max_retries + 1):
        try:
            client = genai.Client(api_key=current_key)
            return await prompt_func(client)
        except Exception as e:
            err_str = str(e).lower()
            is_429 = "429" in err_str or "exhausted" in err_str or "quota" in err_str

            if is_429:
                if not using_backup:
                    print(
                        f"      ⚠️ [Rate Limit 429] Hit primary news key threshold. "
                        f"Waiting 60 seconds, then swapping to backup main key..."
                    )
                    await asyncio.sleep(60)
                    current_key = backup_key
                    using_backup = True
                    continue
                else:
                    print(
                        "      ⚠️ [Rate Limit 429] Hit backup main key rate limit. Applying exponential backoff..."
                    )

            if attempt == max_retries:
                raise e

            backoff_delay = 5 * (2 ** (attempt - 1))
            print(
                f"      ⚠️ [Stage Failure] Part failed: {e}. Retrying this specific stage in {backoff_delay} seconds..."
            )
            await asyncio.sleep(backoff_delay)


async def write_news_script(
    edition: str = "morning",
    episode_number: int = 1,
    date_str: str = "",
    time_str: str = "",
    show_name: str = "PriestyAI News",
    length: str = "Standard",
    guild_id: int = 0,
) -> list:
    """Coordinates Director and Editor passes to compile visual cards, download background assets, and write the final news script."""
    load_dotenv()
    news_model = os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash")
    pexels_key = os.getenv("PEXELS_KEY", "")

    raw_data_path = f"temp/raw_news_data_{guild_id}.json"
    if not os.path.exists(raw_data_path):
        raise FileNotFoundError(f"'{raw_data_path}' not found. Run Scraper first.")

    with open(raw_data_path, "r", encoding="utf-8") as f:
        raw_server_data_json = json.load(f)

    server_name = raw_server_data_json.get("server_name", "Community Server")

    director_system = (
        DIRECTOR_MORNING_PROMPT.format(server_name=server_name)
        if edition.lower() == "morning"
        else DIRECTOR_NIGHT_PROMPT.format(server_name=server_name)
    )

    total_messages = 0
    for channel_name, chan_data in raw_server_data_json.get(
        "public_discussions", {}
    ).items():
        total_messages += len(chan_data.get("messages", []))

    metadata_block = (
        f"=== METADATA CONTEXT ===\n"
        f"This is Episode {episode_number} of the {show_name}.\n"
        f"Today's Date: {date_str}.\n"
        f"Broadcast Time: {time_str}.\n"
        f"The broadcast edition is {edition.upper()}.\n"
        f"Requested Format Length: {length.upper()}.\n\n"
        f"=== SERVER ACTIVITY STATUS ===\n"
        f"Total public discussion messages gathered today: {total_messages}\n\n"
    )

    print(
        f"[Pass 1/2: DIRECTOR] Planning {edition.upper()} show blueprint layout structure..."
    )

    director_config = types.GenerateContentConfig(
        system_instruction=director_system,
        temperature=0.6,
        response_mime_type="application/json",
        response_schema=DirectorBlueprintSchema,
    )

    async def run_director_pass(target_client):
        return await target_client.aio.models.generate_content(
            model=news_model,
            contents=f"{metadata_block}--- RAW DISCORD CONTEXT DATA FOR TODAY ---\n{json.dumps(raw_server_data_json, indent=2)}",
            config=director_config,
        )

    response_dir = await execute_generation_with_failover(run_director_pass, edition)

    if response_dir and response_dir.text:
        blueprint_payload = json.loads(response_dir.text.strip())
    else:
        raise ValueError(
            "Director blueprint generation returned an empty or invalid response payload."
        )

    outlines = blueprint_payload.get("segments", [])

    print(
        f"[Pass 2/2: EDITOR] Writing final script dialogue for {len(outlines)} segments..."
    )

    editor_system = (
        EDITOR_MORNING_PROMPT.format(server_name=server_name)
        if edition.lower() == "morning"
        else EDITOR_NIGHT_PROMPT.format(server_name=server_name)
    )

    editor_config = types.GenerateContentConfig(
        system_instruction=editor_system,
        temperature=0.8,
        response_mime_type="application/json",
        response_schema=EditorShowSchema,
    )

    async def run_editor_pass(target_client):
        return await target_client.aio.models.generate_content(
            model=news_model,
            contents=f"{metadata_block}=== THE DIRECTOR SHOW BLUEPRINT ===\n{json.dumps(blueprint_payload, indent=2)}\n\n=== RAW SERVER DATA CONTEXT ===\n{json.dumps(raw_server_data_json, indent=2)}",
            config=editor_config,
        )

    response_ed = await execute_generation_with_failover(run_editor_pass, edition)

    if response_ed and response_ed.text:
        editor_payload = json.loads(response_ed.text.strip())
        scripts = editor_payload.get("segments", [])
    else:
        raise ValueError(
            "Editor script writer returned an empty or invalid script payload."
        )

    scripts_map = {s["segment_id"]: s for s in scripts}

    final_segments = []
    print("Muxing structural blueprints with scripts & downloading assets...")

    def find_user_avatar_url(name_to_search: str) -> str:
        if not name_to_search or not isinstance(name_to_search, str):
            return ""

        clean_search = re.sub(r"[^a-zA-Z0-9]", "", name_to_search).lower().strip()
        if not clean_search:
            return ""

        for channel_name, chan_data in raw_server_data_json.get(
            "public_discussions", {}
        ).items():
            for m in chan_data.get("messages", []):
                m_author = re.sub(r"[^a-zA-Z0-9]", "", m.get("author", "")).lower()
                m_user = re.sub(r"[^a-zA-Z0-9]", "", m.get("username", "")).lower()
                if (
                    clean_search in m_author
                    or clean_search in m_user
                    or m_author in clean_search
                ):
                    return m.get("author_avatar_url", "")

        for ann in raw_server_data_json.get("announcements", []):
            for m in ann.get("messages", []):
                m_author = re.sub(r"[^a-zA-Z0-9]", "", m.get("author", "")).lower()
                m_user = re.sub(r"[^a-zA-Z0-9]", "", m.get("username", "")).lower()
                if (
                    clean_search in m_author
                    or clean_search in m_user
                    or m_author in clean_search
                ):
                    return m.get("author_avatar_url", "")
        return ""

    active_vibe_query = ""
    for out in outlines:
        q_vibe = out.get("vibe_query")
        if q_vibe:
            active_vibe_query = q_vibe
            break

    if active_vibe_query:
        os.makedirs("temp", exist_ok=True)
        vibe_img_path = f"temp/compiled_vibe_art_{guild_id}.jpg"
        print(
            f" -> Sourcing vibe artwork via Pollinations AI: '{active_vibe_query}'..."
        )
        await download_pollinations_vibe_art(active_vibe_query, vibe_img_path)

    for idx, out in enumerate(outlines):
        segment_id = out.get("segment_id", idx + 1)
        script_obj = scripts_map.get(segment_id, {})

        script_text = script_obj.get("script_text") or "Live News Update"
        banner_text = script_obj.get("banner_text") or "BREAKING NEWS"
        ticker_text = script_obj.get("ticker_text") or "LIVE UPDATE"
        pause_duration = script_obj.get("pause_duration") or 0

        overlay_search = out.get("overlay_search_query") or ""
        quotes_list = out.get("quotes") or []
        cal_event_name = out.get("calendar_event_name") or ""
        cal_date_iso = out.get("calendar_date_iso") or ""
        pexels_bg_search = out.get("pexels_bg_search") or "dark abstract loop"
        frame_template = out.get("frame_template") or "Standard Report"
        host_pose = out.get("host_pose") or "standard"

        award_recipient = out.get("award_recipient") or ""
        award_title = out.get("award_title") or ""
        mailbag_sender = out.get("mailbag_sender") or ""
        mailbag_question = out.get("mailbag_question") or ""
        vibe_query = out.get("vibe_query") or ""
        stats_chart_type = out.get("stats_chart_type") or ""

        overlay_path = ""
        secondary_overlay_path = ""

        if frame_template == "Guest Interview" or "couch" in pexels_bg_search.lower():
            pexels_bg_search = "midnight neon city loop"

        bg_video_path = f"temp/pexels_bg_{guild_id}_{idx}.mp4"
        bg_video_success = await download_pexels_background_loop(
            pexels_bg_search, bg_video_path, pexels_key
        )
        final_bg_path = bg_video_path if bg_video_success else ""

        if vibe_query and idx == 1 and edition.lower() == "night":
            os.makedirs("temp", exist_ok=True)
            output_mystery_path = f"temp/mystery_card_{guild_id}_{idx}.png"
            render_mystery_placeholder_card(output_mystery_path)
            overlay_path = output_mystery_path

        elif idx == len(outlines) - 2 and active_vibe_query:
            vibe_img_path = f"temp/compiled_vibe_art_{guild_id}.jpg"
            if os.path.exists(vibe_img_path):
                overlay_path = vibe_img_path

        elif stats_chart_type:
            os.makedirs("temp", exist_ok=True)
            output_stats_path = f"temp/server_stats_{guild_id}_{idx}.png"
            print(
                f" -> Rendering statistics card ({stats_chart_type}) via Matplotlib..."
            )
            render_server_stats_card(
                stats_chart_type,
                raw_server_data_json.get("server_stats", {}),
                output_stats_path,
            )
            overlay_path = output_stats_path

        elif award_recipient and award_title:
            os.makedirs("temp", exist_ok=True)
            output_award_path = f"temp/award_plaque_{guild_id}_{idx}.png"
            output_avatar_path = f"temp/award_avatar_{guild_id}_{idx}.png"

            av_url = find_user_avatar_url(award_recipient)
            avatar_local_path = ""
            if av_url:
                av_success = await fetch_avatar_bytes(av_url, output_avatar_path)
                if av_success:
                    avatar_local_path = output_avatar_path

            print(f" -> Rendering Award Plaque representing '{server_name}'...")
            render_award_plaque_card(
                award_recipient,
                award_title,
                avatar_local_path,
                server_name,
                output_award_path,
            )
            overlay_path = output_award_path

        elif mailbag_sender and mailbag_question:
            os.makedirs("temp", exist_ok=True)
            output_mailbag_path = f"temp/mailbag_{guild_id}_{idx}.png"
            print(f" -> Compiling community mailbag card from '{mailbag_sender}'...")
            render_community_mailbag_card(
                mailbag_sender, mailbag_question, output_mailbag_path
            )
            overlay_path = output_mailbag_path

        elif cal_event_name and cal_date_iso:
            os.makedirs("temp", exist_ok=True)
            output_cal_path = f"temp/calendar_card_{guild_id}_{idx}.png"
            render_calendar_card(cal_event_name, cal_date_iso, output_cal_path)
            overlay_path = output_cal_path

        elif quotes_list:
            os.makedirs("temp", exist_ok=True)
            output_card_path = f"temp/conversation_card_{guild_id}_{idx}.png"

            avatar_local_paths = []
            for q_idx, quote_obj in enumerate(quotes_list):
                author_name = quote_obj.get("author") or "User"
                av_url = find_user_avatar_url(author_name)
                local_av_path = f"temp/avatar_raw_{guild_id}_{idx}_{q_idx}.png"
                av_success = False
                if av_url:
                    av_success = await fetch_avatar_bytes(av_url, local_av_path)
                avatar_local_paths.append(local_av_path if av_success else "")

            render_stacked_conversation_card(
                quotes_list, avatar_local_paths, output_card_path
            )

            if frame_template == "Guest Interview":
                output_guest_path = f"temp/guest_card_{guild_id}_{idx}.png"
                output_avatar_path = f"temp/guest_avatar_{guild_id}_{idx}.png"
                guest_name = quotes_list[0].get("author") or "Special Guest"
                av_url = find_user_avatar_url(guest_name)
                avatar_local_path = ""
                if av_url:
                    av_success = await fetch_avatar_bytes(av_url, output_avatar_path)
                    if av_success:
                        avatar_local_path = output_avatar_path

                print(
                    f" -> Rendering B-Roll Card 1 (Guest Couch Profile) for '{guest_name}'..."
                )
                render_guest_interview_card(
                    guest_name, avatar_local_path, output_guest_path
                )
                secondary_overlay_path = output_card_path
                overlay_path = output_guest_path
            else:
                overlay_path = output_card_path
                secondary_overlay_path = ""

        elif frame_template == "Guest Interview":
            os.makedirs("temp", exist_ok=True)
            output_guest_path = f"temp/guest_card_{guild_id}_{idx}.png"
            output_avatar_path = f"temp/guest_avatar_{guild_id}_{idx}.png"

            guest_name = "Special Guest"
            av_url = find_user_avatar_url(guest_name)
            avatar_local_path = ""
            if av_url:
                av_success = await fetch_avatar_bytes(av_url, output_avatar_path)
                if av_success:
                    avatar_local_path = output_avatar_path

            print(
                f" -> Rendering B-Roll Card 1 (Guest Couch Profile) for '{guest_name}'..."
            )
            render_guest_interview_card(
                guest_name, avatar_local_path, output_guest_path
            )
            overlay_path = output_guest_path

        elif quotes_list and frame_template == "Full-Screen Media":
            os.makedirs("temp", exist_ok=True)
            output_wall_path = f"temp/quote_wall_{guild_id}_{idx}.png"
            print(
                f" -> Compiling dynamic server quote board for {len(quotes_list)} messages..."
            )
            render_quote_wall_card(quotes_list, output_wall_path)
            overlay_path = output_wall_path

        elif overlay_search:
            os.makedirs("temp", exist_ok=True)
            output_img_path = f"temp/pexels_photo_overlay_{guild_id}_{idx}.jpg"
            success = await download_pexels_photo_overlay(
                overlay_search, output_img_path, pexels_key
            )
            if success:
                overlay_path = output_img_path

        clean_script = clean_unicode_text(script_text)
        clean_banner = clean_unicode_text(banner_text)
        clean_ticker = clean_unicode_text(ticker_text)

        final_segments.append(
            {
                "script_text": clean_script,
                "background_path": final_bg_path,
                "character_path": "assets/character.png",
                "host_pose": host_pose,
                "overlay_path": overlay_path,
                "secondary_overlay_path": secondary_overlay_path,
                "banner_text": clean_banner,
                "ticker_text": clean_ticker,
                "frame_template": frame_template,
                "pause_duration": pause_duration,
                "server_name": server_name,
            }
        )

    os.makedirs("temp", exist_ok=True)
    with open(f"temp/news_segments_draft_{guild_id}.json", "w", encoding="utf-8") as f:
        json.dump(final_segments, f, indent=4, ensure_ascii=False)

    return final_segments
