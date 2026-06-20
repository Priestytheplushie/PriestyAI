
import os
import asyncio
import random
from PIL import Image, ImageDraw, ImageFont
import edge_tts

from moviepy import ImageClip, AudioFileClip, concatenate_videoclips

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

BANNER_COLOR = (200, 30, 30)
TICKER_COLOR = (30, 30, 30)
TEXT_COLOR = (255, 255, 255)
TEXT_DARK = (10, 10, 10)

async def generate_tts_audio(text: str, output_path: str, voice: str = "en-US-AndrewNeural"):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def render_scene_frame(
    background_path: str, 
    character_path: str, 
    overlay_image_path: str, 
    banner_text: str, 
    ticker_text: str, 
    output_frame_path: str
):
    canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 255))
    draw = ImageDraw.Draw(canvas)

    if os.path.exists(background_path):
        bg = Image.open(background_path).convert("RGBA")
        bg_resized = bg.resize((VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS)
        canvas.paste(bg_resized, (0, 0), bg_resized)
    else:
        draw.rectangle([(0, 0), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=(20, 30, 48))

    if overlay_image_path and os.path.exists(overlay_image_path):
        overlay = Image.open(overlay_image_path).convert("RGBA")
        max_size = (500, 350)
        overlay.thumbnail(max_size, Image.Resampling.LANCZOS)
        canvas.paste(overlay, (80, 120), overlay)

    if os.path.exists(character_path):
        host = Image.open(character_path).convert("RGBA")
        scale_height = int(VIDEO_HEIGHT * 0.65)
        aspect_ratio = host.width / host.height
        scale_width = int(scale_height * aspect_ratio)
        host_resized = host.resize((scale_width, scale_height), Image.Resampling.LANCZOS)
        
        host_x = VIDEO_WIDTH - scale_width - 80
        host_y = VIDEO_HEIGHT - scale_height - 150
        canvas.paste(host_resized, (host_x, host_y), host_resized)

    banner_height = 80
    banner_y = VIDEO_HEIGHT - 130
    draw.rectangle([(0, banner_y), (VIDEO_WIDTH, banner_y + banner_height)], fill=BANNER_COLOR)
    
    ticker_height = 50
    ticker_y = VIDEO_HEIGHT - 50
    draw.rectangle([(0, ticker_y), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=TICKER_COLOR)

    try:
        font_title = ImageFont.truetype("arial.ttf", 24)
        font_ticker = ImageFont.truetype("arial.ttf", 18)
    except IOError:
        font_title = ImageFont.load_default()
        font_ticker = ImageFont.load_default()

    draw.text((30, banner_y + 10), "🔴 BREAKING NEWS", fill=(255, 255, 0), font=font_title)
    draw.text((270, banner_y + 10), banner_text[:80], fill=TEXT_COLOR, font=font_title)

    draw.text((30, ticker_y + 15), f"🕒 TIME: {ticker_text}", fill=(100, 200, 255), font=font_ticker)

    canvas.convert("RGB").save(output_frame_path, "JPEG")


async def compile_news_segment(segment_data: dict, segment_index: int) -> ImageClip:
    os.makedirs("temp", exist_ok=True)
    audio_path = f"temp/audio_{segment_index}.mp3"
    frame_path = f"temp/frame_{segment_index}.jpg"

    print(f"Generating TTS for segment {segment_index}...")
    await generate_tts_audio(segment_data["script_text"], audio_path)

    print(f"Rendering layout frame for segment {segment_index}...")
    render_scene_frame(
        background_path=segment_data.get("background_path", ""),
        character_path=segment_data.get("character_path", "assets/character.png"),
        overlay_image_path=segment_data.get("overlay_path", ""),
        banner_text=segment_data.get("banner_text", "LIVE NEWS UPDATE"),
        ticker_text=segment_data.get("ticker_text", "STATION CALM"),
        output_frame_path=frame_path
    )

    audio_clip = AudioFileClip(audio_path)
    video_clip = ImageClip(frame_path).with_duration(audio_clip.duration)
    video_clip = video_clip.with_audio(audio_clip)

    return video_clip


async def generate_full_news_video(segments: list, output_filepath: str):
    clips = []
    for idx, seg in enumerate(segments):
        clip = await compile_news_segment(seg, idx)
        clips.append(clip)

    print("Stitching segments together and compiling final video...")
    final_video = concatenate_videoclips(clips, method="compose")
    
    final_video.write_videofile(
        output_filepath, 
        fps=24, 
        codec="libx264", 
        audio_codec="aac"
    )

    for clip in clips:
        clip.close()
    final_video.close()
    print(f"Compilation Complete! Video saved to: {output_filepath}")


if __name__ == "__main__":
    os.makedirs("assets", exist_ok=True)
    
    mock_segments = [
        {
            "script_text": "Good morning server! I'm your host, your digital companion. Today is looking like an incredibly active day inside the guild. Grab your coffee, and let's check in on the schedule.",
            "background_path": "",
            "character_path": "assets/character.png",
            "overlay_path": "", 
            "banner_text": "BROADCAST ACTIVE: Good Morning Server!",
            "ticker_text": "9:00 AM | #general activity spiking"
        },
        {
            "script_text": "Over in global news, tech developers continue to run video rendering prototypes directly inside Python without using ImageMagick, completely eliminating configuration errors.",
            "background_path": "",
            "character_path": "assets/character.png",
            "overlay_path": "assets/character.png",
            "banner_text": "TECH NEWS: Python Video Pipelines Stable",
            "ticker_text": "9:05 AM | Free API keys are holding steady"
        }
    ]

    print("Starting news compilation prototype...")
    if not os.path.exists("assets/character.png"):
        print("⚠️ Warning: 'assets/character.png' was not found! The frame will render with a blank host area. Please add your host PNG to assets/.")
    
    asyncio.run(generate_full_news_video(mock_segments, "temp_news_broadcast.mp4"))