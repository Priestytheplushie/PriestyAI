
import os
import json
import asyncio
import urllib.parse
import random
import aiohttp
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont
from tools.news.utils import clean_display_name

class MessageQuoteSchema(BaseModel):
    author: str = Field(description="Display name or username of the user being quoted.")
    text: str = Field(description="The exact message string being quoted.")

class NewsSegmentSchema(BaseModel):
    script_text: str = Field(description="Dialogue script spoken by PriestyAI. Keep between 80 to 120 words.")
    banner_text: str = Field(description="Headline text displayed on the lower banner bar. Max 60 chars.")
    ticker_text: str = Field(description="Short update to scroll on the bottom ticker bar. Max 60 chars.")
    frame_template: str = Field(description="The layout template: 'Solo Anchor', 'Standard Report', 'Full-Screen Media', 'Split-Screen'.")
    overlay_search_query: Optional[str] = Field(default="", description="A 2-4 word query for Pexels Photo API. Keep empty if using a quote list, event card, or global news.")
    quotes: Optional[List[MessageQuoteSchema]] = Field(default=[], description="List of consecutive message objects. Keep empty if not quoting a conversation thread.")
    calendar_event_name: Optional[str] = Field(default="", description="Name of scheduled server event. Keep empty if not an event highlight.")
    calendar_date_iso: Optional[str] = Field(default="", description="ISO start time string for highlighted event. Keep empty if not an event highlight.")
    pexels_bg_search: Optional[str] = Field(default="", description="A 2-4 word query for Pexels Video API background loop.")
    award_recipient: Optional[str] = Field(default="", description="The username/display name of the award winner. Keep empty if not an awards segment.")
    award_title: Optional[str] = Field(default="", description="The custom comedic award title. Keep empty if not an awards segment.")
    mailbag_sender: Optional[str] = Field(default="", description="The display name of the user asking the mailbag question. Keep empty if not a Q&A segment.")
    mailbag_question: Optional[str] = Field(default="", description="The clean text of the user's question. Keep empty if not a Q&A segment.")
    vibe_query: Optional[str] = Field(default="", description="If Segment 2 (Vibe Check), write a 3-5 word descriptive prompt for the AI art generator. Keep empty otherwise.")

class NewsShowSchema(BaseModel):
    segments: List[NewsSegmentSchema] = Field(description="The ordered list of chronological segments comprising the full news episode broadcast.")

async def fetch_avatar_bytes(url: str, output_path: str) -> bool:
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


def render_stacked_conversation_card(quotes: List[dict], avatar_paths: List[str], output_path: str):
    card_width = 650
    card_height = 80 + (len(quotes) * 85)
    
    bg_color = (49, 51, 56, 245)
    username_color = (242, 243, 245)
    text_color = (219, 222, 225)
    timestamp_color = (148, 155, 164)
    avatar_bg = (88, 101, 242)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle([(0, 0), (card_width, card_height)], radius=14, fill=bg_color)

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
        author = q.get("author", "User")
        text = q.get("text", "")
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
            draw.ellipse([(av_x, current_y), (av_x + av_size, current_y + av_size)], fill=avatar_bg)
            letter = author[0].upper() if author else "U"
            draw.text((av_x + 15, current_y + 11), letter, fill=(255, 255, 255), font=font_avatar)

        text_x = 85
        draw.text((text_x, current_y + 2), author[:25], fill=username_color, font=font_name)
        
        name_len_px = len(author[:25]) * 9 + 10
        draw.text((text_x + name_len_px, current_y + 5), "Today at 2:45 PM", fill=timestamp_color, font=font_muted)

        clean_msg = clean_display_name(text)
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


def render_calendar_card(event_name: str, date_iso_str: str, output_path: str):
    card_width = 480
    card_height = 320
    
    backing_color = (245, 245, 247)
    header_color = (200, 30, 30)
    text_dark = (15, 23, 42)
    text_muted = (71, 85, 105)

    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)

    draw.rounded_rectangle([(0, 0), (card_width, card_height)], radius=16, fill=backing_color)
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
    card_width = 450
    card_height = 320
    bg_color = (24, 24, 35, 245)
    border_color = (236, 72, 153)
    
    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    
    draw.rounded_rectangle([(0, 0), (card_width, card_height)], radius=16, fill=bg_color, outline=border_color, width=3)
    
    try:
        font_q = ImageFont.truetype("arial.ttf", 100)
        font_sub = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_q = ImageFont.load_default()
        font_sub = ImageFont.load_default()
        
    draw.text((card_width // 2, card_height // 2 - 20), "?", fill=border_color, font=font_q, anchor="mm")
    
    draw.text((card_width // 2, card_height - 50), "VIBE CHECK: CLASSIFIED", fill=(255, 255, 255), font=font_sub, anchor="mm")
    draw.text((card_width // 2, card_height - 25), "GENERATING ARTWORK...", fill=(147, 51, 234), font=font_sub, anchor="mm")
    
    card.save(output_path, "PNG")


def render_award_plaque_card(recipient_name: str, award_title: str, avatar_path: str, output_path: str):
    card_width = 480
    card_height = 320
    
    mahogany_dark = (50, 20, 10)
    mahogany_light = (90, 40, 25)
    gold_border = (212, 175, 55)
    text_color = (255, 255, 255)
    
    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    
    draw.rounded_rectangle([(0, 0), (card_width, card_height)], radius=18, fill=mahogany_dark)
    draw.rounded_rectangle([(8, 8), (card_width - 8, card_height - 8)], radius=14, fill=mahogany_light)
    draw.rounded_rectangle([(16, 16), (card_width - 16, card_height - 16)], radius=12, fill=mahogany_dark)
    
    plaque_bg = (30, 30, 35)
    draw.rounded_rectangle([(24, 24), (card_width - 24, card_height - 24)], radius=8, fill=plaque_bg, outline=gold_border, width=3)
    
    try:
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_title = ImageFont.truetype("arial.ttf", 22)
        font_winner = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_header = ImageFont.load_default()
        font_title = ImageFont.load_default()
        font_winner = ImageFont.load_default()
        
    draw.text((card_width // 2, 45), "CONQUEST SPECIAL COMMENDATION", fill=gold_border, font=font_header, anchor="mm")
    
    av_x, av_y, av_size = 50, 95, 110
    draw.ellipse([(av_x - 6, av_y - 6), (av_x + av_size + 6, av_y + av_size + 6)], fill=gold_border)
    draw.ellipse([(av_x - 3, av_y - 3), (av_x + av_size + 3, av_y + av_size + 3)], fill=(20, 20, 25))
    
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
        draw.ellipse([(av_x, av_y), (av_x + av_size, av_y + av_size)], fill=(88, 101, 242))
        letter = recipient_name[0].upper() if recipient_name else "U"
        font_avatar_fallback = ImageFont.truetype("arial.ttf", 40) if os.path.exists("arial.ttf") else font_header
        draw.text((av_x + av_size // 2, av_y + av_size // 2), letter, fill=(255, 255, 255), font=font_avatar_fallback, anchor="mm")
        
    text_start_x = 190
    draw.text((text_start_x, 100), "AWARD CATEGORY:", fill=(148, 155, 164), font=font_header)
    
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
    draw.polygon([(rib_x + 10, rib_y + 30), (rib_x + 5, rib_y + 55), (rib_x + 15, rib_y + 45)], fill=gold_border)
    draw.polygon([(rib_x + 25, rib_y + 30), (rib_x + 30, rib_y + 55), (rib_x + 20, rib_y + 45)], fill=gold_border)
    
    card.save(output_path, "PNG")


def render_community_mailbag_card(sender_name: str, question_text: str, output_path: str):
    card_width = 480
    card_height = 320
    
    post_it_yellow = (254, 240, 138, 255)
    tape_gray = (203, 213, 225, 180)
    text_color = (15, 23, 42)
    
    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    
    draw.rounded_rectangle([(10, 10), (card_width - 10, card_height - 10)], radius=12, fill=(10, 10, 15, 100))
    draw.rounded_rectangle([(0, 0), (card_width - 15, card_height - 15)], radius=12, fill=post_it_yellow)
    
    draw.rectangle([(card_width // 2 - 50, -10), (card_width // 2 + 50, 25)], fill=tape_gray)
    
    try:
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_winner = ImageFont.truetype("arial.ttf", 18)
        font_text = ImageFont.truetype("arial.ttf", 20)
    except IOError:
        font_header = ImageFont.load_default()
        font_winner = ImageFont.load_default()
        font_text = ImageFont.load_default()
        
    draw.text((30, 45), "📬  SERVER MAILBAG:", fill=(100, 116, 139), font=font_header)
    
    clean_q = clean_display_name(question_text)
    wrapped_lines = []
    words = clean_q.split()
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
    card_width = 450
    card_height = 320
    bg_color = (20, 20, 25, 245)
    border_color = (219, 39, 119)
    
    card = Image.new("RGBA", (card_width, card_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    
    draw.rounded_rectangle([(0, 0), (card_width, card_height)], radius=16, fill=bg_color, outline=border_color, width=3)
    
    try:
        font_header = ImageFont.truetype("arial.ttf", 16)
        font_name = ImageFont.truetype("arial.ttf", 24)
    except IOError:
        font_header = ImageFont.load_default()
        font_name = ImageFont.load_default()
        
    draw.rounded_rectangle([(card_width // 2 - 80, 25), (card_width // 2 + 80, 55)], radius=8, fill=border_color)
    draw.text((card_width // 2, 40), "SPECIAL GUEST", fill=(255, 255, 255), font=font_header, anchor="mm")
    
    av_size = 120
    av_x = (card_width - av_size) // 2
    av_y = 85
    
    draw.ellipse([(av_x - 4, av_y - 4), (av_x + av_size + 4, av_y + av_size + 4)], fill=border_color)
    draw.ellipse([(av_x - 2, av_y - 2), (av_x + av_size + 2, av_y + av_size + 2)], fill=(20, 20, 25))
    
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
        draw.ellipse([(av_x, av_y), (av_x + av_size, av_y + av_size)], fill=(88, 101, 242))
        letter = guest_name[0].upper() if guest_name else "G"
        font_av_fallback = ImageFont.truetype("arial.ttf", 45) if os.path.exists("arial.ttf") else font_name
        draw.text((av_x + av_size // 2, av_y + av_size // 2), letter, fill=(255, 255, 255), font=font_av_fallback, anchor="mm")
        
    draw.text((card_width // 2, 240), f"@{guest_name}", fill=(255, 255, 255), font=font_name, anchor="mm")
    draw.text((card_width // 2, 275), "JOINING US ON THE COUCH", fill=(148, 155, 164), font=font_header, anchor="mm")
    
    card.save(output_path, "PNG")

async def download_pollinations_vibe_art(prompt: str, output_path: str) -> bool:
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
        print(f"      ⚠️ Failed to compile vibe art via Pollinations AI: {e}")
    return False

async def download_pexels_background_loop(query: str, output_path: str, pexels_key: str) -> bool:
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


async def download_pexels_photo_overlay(query: str, output_path: str, pexels_key: str) -> bool:
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

MORNING_PROMPT = """
You are a highly professional, encouraging, and clear Morning Show news host for a Discord server. 
Your name is PriestyAI, the digital anchor identity of this bot.

Analyze the raw server JSON log data provided. Do NOT follow a rigid, hardcoded list of assumptions. Instead, inspect the data keys and build a fluid, data-driven morning broadcast following these flexible guidelines:

1. THE OPENER: Greet the server warmly, citing the active Episode Number, Date, and Broadcast Time from the metadata. Introduce the specific show name. Frame Template: 'Solo Anchor'.
2. ANNOUNCEMENTS SCAN: Dynamically check the 'announcements' array or look for any channel entries containing 'news', 'announcements', or updates. If you find real posts, report on them accurately. If no active announcement posts exist, skip this topic or smoothly transition to server schedules. Frame Template: 'Standard Report'.
3. SCHEDULED EVENTS: Check the 'scheduled_events' list. If upcoming events are scheduled, read their titles, explain what they are, and encourage RSVPs (populate calendar_event_name and calendar_date_iso). If the list is empty, completely omit this segment.
4. REAL WORLD CONTEXT: Read the 'real_world_news' feed. Deliver 1-2 actual key world news headlines to keep the community informed. Frame Template: 'Solo Anchor'.
5. CHAT WEATHER REPORT: Analyze the message velocity and active conversations across any keys inside 'public_discussions'. Choose one highly prominent active user from the log and deliver a custom, lighthearted fictionalized 'vibe weather forecast' tailored specifically to their active discussion topic. Frame Template: 'Standard Report'.
6. RECAP MAIN DISCUSSIONS: Identify the 1 or 2 most active discussion channels within the JSON data. Synthesize what the community was talking about or debating. If there is an active back-and-forth between specific members, quote them accurately using the 'quotes' property array. Frame Template: 'Full-Screen Media' if quoting a multi-line conversation thread, or 'Split-Screen'/'Standard Report' for a single-channel summary.
7. THE OUTRO: Close the show gracefully by wishing everyone a great, productive day ahead. Frame Template: 'Solo Anchor'.

QUIET DAY MODULAR SWAP: If the server is extremely quiet (less than 5 chat messages), replace the standard discussion recap segments with a detailed, creative 'Server Lore Explainer' segment where you deep-dive into the funny origins of a running joke, a specific server channel, or an obscure role found in the metadata.

CRITICAL STAFF IDENTITY RULE: You are PriestyAI, the digital talk-show host. You are NOT the human server developer/owner Priesty, nor are you any other admin or moderator. When discussing messages, updates, or announcements made by Priesty or other administrators, refer to them in the third person as 'our administrator, [Name]', 'the staff team', or 'our moderation crew'. Never use first-person pronouns ('I', 'me', 'my', 'our developer') to represent their statements or logs.
CRITICAL DESIGN RULE: Each generated segment's script_text must be written in detail (80 to 120 words). Never use filler phrases or make up members not present in the logs.
"""

NIGHT_PROMPT = """
You are an exceptionally funny, satirical, and fast-paced Late-Night Talk Show host named PriestyAI.
You are witty, slightly sarcastic, and thrive on calling out server-side drama, hot takes, and memes.

Analyze the raw server JSON log data provided. Your late-night broadcast must strictly follow this 10-step segment layout and be highly detailed:
1. THE COLD OPEN / MONOLOGUE: Roast today's biggest server drama, funniest argument, or weirdest quote. Introduce the show, Date, and Episode. Frame Template: 'Solo Anchor'.
2. SERVER VIBE REVEAL: Determine a dynamic "Server Vibe Name" representing the chat activity (e.g. 'Salty Rage-Quitting'). 
   CRITICAL SURPRISE RULE: You must NOT leak, write, or speak the vibe name or description in Segment 2's dialogue, banner, or ticker. Keep the name a complete secret to build late-night suspense! The host must only tease that they have locked in a vibe and that the render engines are currently compiling the artwork in the background. Define the vibe prompt in the 'vibe_query' property so we can download it. Frame Template: 'Standard Report' (this will display a generic Classified Mystery card).
3. RAPID-FIRE CHAT ROASTS: Roast and critique 2-3 specific server chat events or quotes. Group them inside the 'quotes' list. Frame Template: 'Full-Screen Media'.
4. THE DECREE ON MODERATION: Find rules updates, remarks, or statements from moderators/admin, and treat them as absurd tyrannical government laws or overreaches. Frame Template: 'Standard Report'.
5. SPECIAL GUEST COUCH (INTERVIEW): Pick an active user, call them "the special guest of the night", ask a highly formal/dramatic interview question, and reply using their past messages as the guest answers (making fun of how they are completely out-of-context!). Put their messages in the 'quotes' list. Frame Template: 'Split-Screen'.
6. REAL LIFE SATIRE: Provide 1-2 real-life stories from 'real_world_news' and give a sharp, opinionated, satirical opinion. Frame Template: 'Solo Anchor'.
7. THE WEATHER FORECAST: Select a random active user and make a personalized, satirical weather forecast based on their gaming habits/chat vibe. Frame Template: 'Standard Report'.
8. THE COMMUNITY AWARDS: Award a user with a funny comedic award. Fill out the 'award_recipient' and 'award_title' fields so Pillow renders their plaque. Read some of their chat logs aloud to explain why they earned it. Frame Template: 'Standard Report'.
9. THE SERVER VIBE UNVEILING: Officially showcase the finalized unique Pollinations art compiled in the background! 
   CRITICAL SURPRISE REVEAL: This is where you reveal BOTH the Vibe Name and the image. The banner_text must show the vibe name (e.g. 'Tonight's Vibe Revealed: Salty Gaming!'). The host must read out the vibe name and analyze/joke about the completed artwork. Copy the exact same 'vibe_query' from Segment 2 here. Frame Template: 'Full-Screen Media'.
10. SLEEP OUTRO: End with a funny closing quote telling everyone to close their games, touch real-world grass, and go to sleep because it's 3:00 AM. Frame Template: 'Solo Anchor'.

QUIET DAY MODULAR SWAPS:
* If the server is quiet, replace the global vibe check with a 'User Dossier' segment. Select one prominent active member from the logs, declare that you are running a 'Classification Study' on them, and assign them a personalized, funny Vibe Name. Put a 3-5 word portrait prompt representing their personality into the 'vibe_query' property (this will display a mystery '?' card in Segment 2 and unveil their portrait in Segment 8).
* If the server is extremely quiet (less than 5 chat messages), replace the standard awards segment with a subtle, self-aware comedy block. The host will pull a random trivial keyword or message from the quiet logs, tell three increasingly terrible, groan-worthy puns or situational jokes about it, and sardonically roast their own rendering code for generating such awkward humor. Do NOT use the phrase 'dad jokes'—keep the humor delivery subtle, dry, and self-effacing.

CRITICAL TRANSITION RULE: You are a sharp, seamless late-night host. Never write robotic transitions like 'And now onto the daily roasts', 'The next segment is...', or 'Moving on to awards'. Slide into topics naturally and colloquially (e.g., 'Meanwhile, sanity was officially outlawed in the lounge yesterday...', 'Speaking of questionable life choices, let us look at...', or 'I received some anonymous letters in our inbox...'). Transitions must feel smooth, witty, and conversational.

CRITICAL STAFF IDENTITY RULE: You are PriestyAI, the digital talk-show host. You are NOT the human server developer/owner Priesty, nor are you any other admin or moderator. When discussing messages, updates, or announcements made by Priesty or other administrators, refer to them in the third person as 'our administrator, [Name]', 'the staff team', or 'our moderation crew'. Never use first-person pronouns ('I', 'me', 'my', 'our developer') to represent their statements or logs.
"""

async def write_news_script(edition: str = "morning", episode_number: int = 1, date_str: str = "", time_str: str = "", show_name: str = "PriestyAI News", length: str = "Standard", guild_id: int = 0) -> list:
    load_dotenv()
    gemini_key = os.getenv("GEMINI_API_KEY")
    news_model = os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash")
    pexels_key = os.getenv("PEXELS_KEY", "")
    
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY not found in environment variables.")

    raw_data_path = f"temp/raw_news_data_{guild_id}.json"
    if not os.path.exists(raw_data_path):
        raise FileNotFoundError(f"'{raw_data_path}' not found. Run Scraper first.")
        
    with open(raw_data_path, "r", encoding="utf-8") as f:
        raw_server_data_json = json.load(f)

    length_instruction = f"Your show length configuration is: {length.upper()}. "
    if length.lower() == "brief":
        length_instruction += "You must generate exactly 4 short, high-velocity segments."
    elif length.lower() == "extended":
        length_instruction += "You must expand the coverage to compile exactly 12 segments including Server Lore, detailed Q&As, and deep-dives."
    else:
        length_instruction += "You must generate the standard amount of segments."

    system_instruction = MORNING_PROMPT if edition.lower() == "morning" else NIGHT_PROMPT
    system_instruction += f"\n\n=== LENGTH ADJUSTMENT RULES ===\n{length_instruction}"
    
    total_messages = 0
    for channel_name, chan_data in raw_server_data_json.get("public_discussions", {}).items():
        total_messages += len(chan_data.get("messages", []))
    
    is_quiet_day = total_messages < 15
    if is_quiet_day:
        print(f"⚠️ Slow News Day detected (Total messages: {total_messages}). Activating quiet-day modular overrides...")

    print(f"Sending context to Gemini ({news_model}). Writing {edition.upper()} Edition script ({length})...")
    client = genai.Client(api_key=gemini_key)
    
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.8,
        response_mime_type="application/json",
        response_schema=NewsShowSchema
    )
    
    metadata_block = (
        f"=== METADATA CONTEXT ===\n"
        f"This is Episode {episode_number} of the {show_name}.\n"
        f"Today's Date: {date_str}.\n"
        f"Broadcast Time: {time_str}.\n"
        f"The broadcast edition is {edition.upper()}.\n\n"
        f"=== SERVER ACTIVITY STATUS ===\n"
        f"Total public discussion messages gathered today: {total_messages}\n"
        f"Is Quiet/Slow Day: {'TRUE (You MUST trigger the Quiet Day Modular Swaps as instructed in your system guidelines!)' if is_quiet_day else 'FALSE (Standard Broadcast)'}\n\n"
    )
    
    response = await client.aio.models.generate_content(
        model=news_model,
        contents=f"{metadata_block}--- RAW DISCORD CONTEXT DATA FOR TODAY ---\n{json.dumps(raw_server_data_json, indent=2)}",
        config=config
    )

    if not response or not response.text:
        raise ValueError("Error: The Gemini model returned an empty script response. Verify API keys and network parameters.")

    show_payload = json.loads(response.text)
    raw_segments = show_payload.get("segments", [])
    
    final_segments = []
    print(f"Successfully wrote {len(raw_segments)} script segments. Fetching dynamic resources...")
    
    def find_user_avatar_url(name_to_search: str) -> str:
        if not name_to_search or not isinstance(name_to_search, str):
            return ""
        name_clean = name_to_search.lower().strip()
        for channel_name, chan_data in raw_server_data_json.get("public_discussions", {}).items():
            for m in chan_data.get("messages", []):
                if name_clean in m.get("author", "").lower() or name_clean in m.get("username", "").lower():
                    return m.get("author_avatar_url", "")
        for ann in raw_server_data_json.get("announcements", []):
            for m in ann.get("messages", []):
                if name_clean in m.get("author", "").lower() or name_clean in m.get("username", "").lower():
                    return m.get("author_avatar_url", "")
        return ""

    active_vibe_query = ""
    for s in raw_segments:
        q_vibe = s.get("vibe_query")
        if q_vibe:
            active_vibe_query = q_vibe
            break

    if active_vibe_query:
        os.makedirs("temp", exist_ok=True)
        vibe_img_path = f"temp/compiled_vibe_art_{guild_id}.jpg"
        print(f" -> Sourcing dynamic Server Vibe artwork via Pollinations AI for: '{active_vibe_query}'...")
        await download_pollinations_vibe_art(active_vibe_query, vibe_img_path)

    for idx, seg in enumerate(raw_segments):
        overlay_search = seg.get("overlay_search_query") or ""
        quotes_list = seg.get("quotes") or []
        cal_event_name = seg.get("calendar_event_name") or ""
        cal_date_iso = seg.get("calendar_date_iso") or ""
        pexels_bg_search = seg.get("pexels_bg_search") or "dark abstract loop"
        frame_template = seg.get("frame_template") or "Standard Report"
        
        award_recipient = seg.get("award_recipient") or ""
        award_title = seg.get("award_title") or ""
        mailbag_sender = seg.get("mailbag_sender") or ""
        mailbag_question = seg.get("mailbag_question") or ""
        vibe_query = seg.get("vibe_query") or ""

        overlay_path = ""

        if frame_template == "Split-Screen" or "couch" in pexels_bg_search.lower():
            pexels_bg_search = "midnight neon city loop"

        bg_video_path = f"temp/pexels_bg_{guild_id}_{idx}.mp4"
        bg_video_success = await download_pexels_background_loop(pexels_bg_search, bg_video_path, pexels_key)
        final_bg_path = bg_video_path if bg_video_success else ""

        if vibe_query and idx == 1:
            os.makedirs("temp", exist_ok=True)
            output_mystery_path = f"temp/mystery_card_{guild_id}_{idx}.png"
            render_mystery_placeholder_card(output_mystery_path)
            overlay_path = output_mystery_path

        elif idx == len(raw_segments) - 2 and active_vibe_query:
            vibe_img_path = f"temp/compiled_vibe_art_{guild_id}.jpg"
            if os.path.exists(vibe_img_path):
                overlay_path = vibe_img_path

        elif quotes_list and frame_template == "Split-Screen":
            os.makedirs("temp", exist_ok=True)
            output_guest_path = f"temp/guest_card_{guild_id}_{idx}.png"
            output_avatar_path = f"temp/guest_avatar_{guild_id}_{idx}.png"
            
            guest_name = quotes_list[0].get("author") or "Special Guest"
            av_url = find_user_avatar_url(guest_name)
            avatar_local_path = ""
            if av_url:
                av_success = await fetch_avatar_bytes(av_url, output_avatar_path)
                if av_success:
                    avatar_local_path = output_avatar_path
            
            print(f" -> Compiling late-night Guest Couch card for guest '{guest_name}'...")
            render_guest_interview_card(guest_name, avatar_local_path, output_guest_path)
            overlay_path = output_guest_path

        elif award_recipient and award_title:
            os.makedirs("temp", exist_ok=True)
            output_award_path = f"temp/award_plaque_{guild_id}_{idx}.png"
            output_avatar_path = f"temp/award_avatar_{guild_id}_{idx}.png"
            
            av_url = find_user_avatar_url(award_recipient)
            avatar_local_path = ""
            if av_url:
                av_success = await fetch_avatar_bytes(av_url, output_award_path)
                if av_success:
                    avatar_local_path = output_award_path
            
            print(f" -> Compiling dynamic golden plaque card for '{award_recipient}'...")
            render_award_plaque_card(award_recipient, award_title, avatar_local_path, output_award_path)
            overlay_path = output_award_path

        elif mailbag_sender and mailbag_question:
            os.makedirs("temp", exist_ok=True)
            output_mailbag_path = f"temp/mailbag_{guild_id}_{idx}.png"
            print(f" -> Compiling community mailbag card from '{mailbag_sender}'...")
            render_community_mailbag_card(mailbag_sender, mailbag_question, output_mailbag_path)
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
            
            print(f" -> Compiling stacked conversation layout with {len(quotes_list)} messages...")
            render_stacked_conversation_card(quotes_list, avatar_local_paths, output_card_path)
            
            attachment_found = False
            for q_obj in quotes_list:
                author_name = q_obj.get("author") or ""
                quote_text = q_obj.get("text") or ""
                
                for channel_name, chan_data in raw_server_data_json.get("public_discussions", {}).items():
                    for m in chan_data.get("messages", []):
                        if (m.get("author") == author_name and quote_text in m.get("content", "")) or (m.get("username") == author_name and quote_text in m.get("content", "")):
                            attachments = m.get("attachments") or []
                            if attachments:
                                os.makedirs("temp", exist_ok=True)
                                output_att_path = f"temp/quote_attachment_{guild_id}_{idx}.png"
                                print(f" -> Dynamic Visual Meme Match! Downloading Discord attachment from '{author_name}'...")
                                async with aiohttp.ClientSession() as session:
                                    async with session.get(attachments[0], timeout=20) as resp:
                                        if resp.status == 200:
                                            with open(output_att_path, "wb") as f:
                                                f.write(await resp.read())
                                            overlay_path = output_att_path
                                            attachment_found = True
                                            break
                    if attachment_found:
                        break
                if attachment_found:
                    break
            
            if not attachment_found:
                overlay_path = output_card_path
            
        elif overlay_search:
            os.makedirs("temp", exist_ok=True)
            output_img_path = f"temp/pexels_photo_overlay_{guild_id}_{idx}.jpg"
            success = await download_pexels_photo_overlay(overlay_search, output_img_path, pexels_key)
            if success:
                overlay_path = output_img_path

        final_segments.append({
            "script_text": clean_display_name(seg.get("script_text") or "Live News Update"),
            "background_path": final_bg_path,
            "character_path": "assets/character.png",
            "overlay_path": overlay_path,
            "banner_text": clean_display_name(seg.get("banner_text") or "BREAKING NEWS"), 
            "ticker_text": clean_display_name(seg.get("ticker_text") or "LIVE UPDATE"),
            "frame_template": frame_template
        })
        
    os.makedirs("temp", exist_ok=True)
    with open(f"temp/news_segments_draft_{guild_id}.json", "w", encoding="utf-8") as f:
        json.dump(final_segments, f, indent=4, ensure_ascii=False)
        
    return final_segments