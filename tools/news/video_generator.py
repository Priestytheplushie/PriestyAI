
import os
import asyncio
import subprocess
import imageio_ffmpeg
import edge_tts
from PIL import Image, ImageDraw, ImageFont

from moviepy import ImageClip, AudioFileClip, VideoFileClip, CompositeVideoClip, concatenate_videoclips
from moviepy.video.fx import Loop 

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

BANNER_STATUS_RED = (200, 30, 30)
BANNER_HEADLINE_BG = (25, 25, 30)
BANNER_TICKER_BG = (15, 15, 18)
DIVIDER_CYAN = (0, 210, 255)
TEXT_WHITE = (255, 255, 255)
TEXT_SHADOW = (10, 10, 12)

async def generate_tts_audio(text: str, output_path: str, voice: str = "en-US-AndrewNeural"):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def draw_rich_gradient(draw, width, height, edition="morning"):
    if edition.lower() == "night":
        color_start = (15, 10, 25)
        color_end = (30, 15, 45)
    else:
        color_start = (11, 16, 29)
        color_end = (23, 37, 65)
        
    for y in range(height):
        ratio = y / height
        r = int(color_start[0] * (1 - ratio) + color_end[0] * ratio)
        g = int(color_start[1] * (1 - ratio) + color_end[1] * ratio)
        b = int(color_start[2] * (1 - ratio) + color_end[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def render_scene_base_layout(
    background_path: str,
    character_path: str,
    overlay_image_path: str,
    banner_text: str,
    frame_template: str,
    output_frame_path: str,
    edition: str = "morning"
):
    has_video_bg = background_path.endswith(".mp4") and os.path.exists(background_path)
    
    if has_video_bg:
        canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 255))
        
    draw = ImageDraw.Draw(canvas)

    if not has_video_bg:
        if background_path and os.path.exists(background_path):
            bg = Image.open(background_path).convert("RGBA")
            bg_resized = bg.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
            canvas.paste(bg_resized, (0, 0), bg_resized)
        else:
            draw_rich_gradient(draw, VIDEO_WIDTH, VIDEO_HEIGHT, edition=edition)

    try:
        font_status = ImageFont.truetype("arial.ttf", 16)
        font_headline = ImageFont.truetype("arial.ttf", 26)
    except IOError:
        font_status = ImageFont.load_default()
        font_headline = ImageFont.load_default()

    if edition.lower() == "night":
        status_bar_color = (147, 51, 234)
        headline_bg_color = (15, 15, 20)
        divider_color = (219, 39, 119)
        
        if frame_template == "Solo Anchor":
            status_label = "LATE NIGHT MONOLOGUE"
        elif frame_template == "Full-Screen Media":
            status_label = "LATE NIGHT EXCLUSIVE"
        else:
            status_label = "LATE NIGHT SPECIAL"
    else:
        status_bar_color = BANNER_STATUS_RED
        headline_bg_color = BANNER_HEADLINE_BG
        divider_color = DIVIDER_CYAN
        status_label = "LIVE BROADCAST"

    has_overlay = overlay_image_path and os.path.exists(overlay_image_path)
    has_host = os.path.exists(character_path)

    overlay_coords = (80, 120)
    host_scale_height = int(VIDEO_HEIGHT * 0.65)

    if frame_template == "Solo Anchor":
        host_scale_height = int(VIDEO_HEIGHT * 0.72)
        has_overlay = False

    elif frame_template == "Full-Screen Media":
        overlay_coords = (VIDEO_WIDTH // 2 - 240, 60)
        host_scale_height = int(VIDEO_HEIGHT * 0.40)

    if has_overlay:
        overlay = Image.open(overlay_image_path).convert("RGBA")
        if frame_template == "Full-Screen Media":
            overlay.thumbnail((500, 420), Image.Resampling.LANCZOS)
        else:
            overlay.thumbnail((450, 320), Image.Resampling.LANCZOS)
            
        ox, oy = overlay_coords
        draw.rectangle([(ox - 4, oy - 4), (ox + overlay.width + 4, oy + overlay.height + 4)], fill=divider_color)
        canvas.paste(overlay, (ox, oy), overlay)

    if has_host:
        host = Image.open(character_path).convert("RGBA")
        aspect_ratio = host.width / host.height
        scale_width = int(host_scale_height * aspect_ratio)
        host_resized = host.resize((scale_width, host_scale_height), Image.Resampling.LANCZOS)
        
        if frame_template == "Solo Anchor":
            host_x = (VIDEO_WIDTH - scale_width) // 2
            host_y = 540 - host_scale_height + 40
        elif frame_template == "Full-Screen Media":
            host_x = VIDEO_WIDTH - scale_width - 40
            host_y = 540 - host_scale_height + 40
        else:
            host_x = VIDEO_WIDTH - scale_width - 80
            host_y = 540 - host_scale_height + 40

        canvas.paste(host_resized, (host_x, host_y), host_resized)

    draw.rectangle([(0, 540), (VIDEO_WIDTH, 570)], fill=status_bar_color)
    draw.text((30, 546), status_label, fill=TEXT_WHITE, font=font_status)

    draw.rectangle([(0, 570), (VIDEO_WIDTH, 640)], fill=headline_bg_color)
    draw.text((30 + 1, 585 + 1), banner_text[:75], fill=TEXT_SHADOW, font=font_headline)
    draw.text((30, 585), banner_text[:75], fill=TEXT_WHITE, font=font_headline)

    draw.rectangle([(0, 640), (VIDEO_WIDTH, 645)], fill=divider_color)

    draw.rectangle([(0, 645), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=BANNER_TICKER_BG)

    canvas.convert("RGBA").save(output_frame_path, "PNG")


def render_ticker_text_image(master_ticker: str, duration: float, output_path: str):
    speed = 110
    scroll_distance = int(duration * speed)
    canvas_w = max(4500, 1280 + scroll_distance + 2500)
    canvas_h = 50
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    
    try:
        font_ticker = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_ticker = ImageFont.load_default()
        
    base_text = f"   •   {master_ticker}   "
    repeated_text = base_text
    while len(repeated_text) * 11 < canvas_w:
        repeated_text += base_text
        
    draw.text((10, 15), repeated_text, fill=(180, 220, 255), font=font_ticker)
    canvas.save(output_path, "PNG")


def render_live_tag_cover(output_path: str, edition: str = "morning"):
    width, height = 100, 75
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    if edition.lower() == "night":
        status_bar_color = (147, 51, 234)
    else:
        status_bar_color = BANNER_STATUS_RED
        
    draw.rectangle([(0, 0), (width, height)], fill=status_bar_color)
    
    try:
        font_ticker_static = ImageFont.truetype("arial.ttf", 16)
    except IOError:
        font_ticker_static = ImageFont.load_default()
        
    draw.text((50, 37), "LIVE", fill=TEXT_WHITE, font=font_ticker_static, anchor="mm")
    img.save(output_path, "PNG")


async def compile_news_segment(segment_data: dict, segment_index: int, master_ticker: str, edition: str = "morning", guild_id: int = 0) -> CompositeVideoClip:
    os.makedirs("temp", exist_ok=True)
    audio_path = f"temp/audio_{guild_id}_{segment_index}.mp3"
    frame_path = f"temp/frame_{guild_id}_{segment_index}.png"
    ticker_path = f"temp/ticker_{guild_id}_{segment_index}.png"
    live_tag_path = f"temp/live_tag_cover_{guild_id}_{edition}.png"

    print(f"Generating voice narrative track for segment {segment_index}...")
    await generate_tts_audio(segment_data["script_text"], audio_path)

    audio_clip = AudioFileClip(audio_path)
    duration = audio_clip.duration
    audio_clip.close()

    bg_path = segment_data.get("background_path", "")
    render_scene_base_layout(
        background_path=bg_path,
        character_path=segment_data.get("character_path", "assets/character.png"),
        overlay_image_path=segment_data.get("overlay_path", ""),
        banner_text=segment_data.get("banner_text", "LIVE NEWS UPDATE"),
        frame_template=segment_data.get("frame_template", "Standard Report"),
        output_frame_path=frame_path,
        edition=edition
    )

    render_ticker_text_image(master_ticker, duration, ticker_path)

    if not os.path.exists(live_tag_path):
        render_live_tag_cover(live_tag_path, edition=edition)

    layout_clip = ImageClip(frame_path).with_duration(duration)
    
    ticker_clip = (ImageClip(ticker_path)
                   .with_duration(duration)
                   .with_position(lambda t: (120 - (t * 110), 656)))

    live_cover_clip = ImageClip(live_tag_path).with_duration(duration).with_position((0, 645))

    if bg_path.endswith(".mp4") and os.path.exists(bg_path):
        try:
            bg_video = VideoFileClip(bg_path)
            bg_video = bg_video.resized((VIDEO_WIDTH, VIDEO_HEIGHT))
            bg_video = bg_video.with_effects([Loop(duration=duration)])
            bg_video = bg_video.with_position("center")
            
            composite_segment = CompositeVideoClip([bg_video, layout_clip, ticker_clip, live_cover_clip]).with_duration(duration)
            return composite_segment
        except Exception as e:
            print(f"      ⚠️ Failed to loop video backdrop for segment {segment_index}: {e}")

    composite_segment = CompositeVideoClip([layout_clip, ticker_clip, live_cover_clip]).with_duration(duration)
    return composite_segment

def generate_full_news_video(segments: list, output_filepath: str, music_path: str = "", edition: str = "morning", guild_id: int = 0):
    os.makedirs("temp", exist_ok=True)
    silent_video_path = f"temp/silent_video_draft_{guild_id}.mp4"
    master_audio_path = f"temp/master_audio_track_{guild_id}.mp3"

    ticker_list = []
    for s in segments:
        t_text = s.get("ticker_text", "").strip()
        if t_text:
            ticker_list.append(t_text)
            
    if not ticker_list:
        ticker_list = ["CHAOS CONQUEST LIVE BROADCAST", "ALL SYSTEMS RUNNING", "STATION ONLINE"]
    
    master_ticker = "   •   ".join(ticker_list)
    print(f"Compiled Master Ticker Feed: \"{master_ticker[:80]}...\"")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    video_clips = []
    try:
        for idx, seg in enumerate(segments):
            v_clip = loop.run_until_complete(
                compile_news_segment(seg, idx, master_ticker, edition=edition, guild_id=guild_id)
            )
            video_clips.append(v_clip)
    finally:
        loop.close()

    print("Concatenating visual segments...")
    final_silent_video = concatenate_videoclips(video_clips, method="compose")
    
    print("Writing silent high-fidelity draft...")
    final_silent_video.write_videofile(
        silent_video_path,
        fps=24,
        codec="libx264",
        audio=False, 
        logger="bar"
    )
    
    video_duration = final_silent_video.duration
    
    for v in video_clips: 
        v.close()
    final_silent_video.close()

    print("Muxing audio track...")
    with open(master_audio_path, "wb") as outfile:
        for idx in range(len(segments)):
            path_chunk = f"temp/audio_{guild_id}_{idx}.mp3"
            if os.path.exists(path_chunk):
                with open(path_chunk, "rb") as infile:
                    outfile.write(infile.read())

    mixed_audio_output_path = f"temp/master_mixed_audio_output_{guild_id}.mp3"
    
    if music_path and os.path.exists(music_path):
        print(f"Sourcing background music backplate: '{music_path}'...")
        try:
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            mix_cmd = [
                ffmpeg_exe, "-y",
                "-i", master_audio_path,
                "-stream_loop", "-1", "-i", music_path,
                "-filter_complex", "[1:a]volume=0.07[bg_music];[0:a][bg_music]amix=inputs=2:duration=first:dropout_transition=2[out]",
                "-map", "[out]",
                "-c:a", "libmp3lame",
                "-q:a", "2",
                mixed_audio_output_path
            ]
            
            process_mix = subprocess.run(mix_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if process_mix.returncode == 0:
                print("🎉 Successfully compiled high-fidelity mixed audio track with ducked background music!")
                master_audio_path = mixed_audio_output_path
            else:
                print(f"⚠️ Audio mixing failed. Falling back to clean voice narrative only: {process_mix.stderr.decode()}")
        except Exception as e:
            print(f"⚠️ Background audio mixing failed: {e}. Falling back to clean voice narrative only.")

    print("Executing standard high-fidelity layout mux via FFmpeg...")
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    
    cmd = [
        ffmpeg_exe, "-y",
        "-i", silent_video_path,
        "-i", master_audio_path,
        "-c:v", "libx264",
        "-crf", "21",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        output_filepath
    ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    if process.returncode == 0:
        print(f"🎉 Success! High-quality video compiled: {output_filepath}")
        
        for idx in range(len(segments)):
            temp_path = f"temp/audio_{guild_id}_{idx}.mp3"
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except Exception: pass
        if os.path.exists(silent_video_path):
            try: os.remove(silent_video_path)
            except Exception: pass
        if os.path.exists(master_audio_path):
            try: os.remove(master_audio_path)
            except Exception: pass
        if os.path.exists(mixed_audio_output_path):
            try: os.remove(mixed_audio_output_path)
            except Exception: pass
    else:
        print(f"❌ Muxing error: {process.stderr.decode()}")