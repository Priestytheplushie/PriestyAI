import os
import asyncio
import subprocess
import imageio_ffmpeg
import edge_tts
from PIL import Image, ImageDraw, ImageFont
from moviepy import (
    ImageClip,
    AudioFileClip,
    VideoFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
    ImageSequenceClip,
)
from moviepy.video.fx import Loop

try:
    from tools.news.utils import clean_unicode_text
except ImportError:
    try:
        from .utils import clean_unicode_text
    except ImportError:
        from utils import clean_unicode_text

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720

BANNER_STATUS_RED = (200, 30, 30)
BANNER_HEADLINE_BG = (25, 25, 30)
BANNER_TICKER_BG = (15, 15, 18)
DIVIDER_CYAN = (0, 210, 255)
TEXT_WHITE = (255, 255, 255)
TEXT_SHADOW = (10, 10, 12)


async def generate_tts_audio(
    text: str, output_path: str, voice: str = "en-US-AndrewNeural"
):
    """Synthesizes high-fidelity speech from script text utilizing Edge-TTS."""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def draw_rich_gradient(draw, width, height, edition="morning"):
    """Draws a premium studio backdrop gradient across the viewport canvas."""
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
    output_banner_path: str,
    edition: str = "morning",
):
    """Renders the standard broadcast layout frame including lower-thirds and banners."""
    has_video_bg = background_path.endswith(".mp4") and os.path.exists(background_path)

    if has_video_bg:
        canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    else:
        canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 255))

    draw = ImageDraw.Draw(canvas)

    if not has_video_bg:
        if background_path and os.path.exists(background_path):
            bg = Image.open(background_path).convert("RGBA")
            bg_resized = bg.resize(
                (VIDEO_WIDTH, VIDEO_HEIGHT), Image.Resampling.LANCZOS
            )
            canvas.paste(bg_resized, (0, 0), bg_resized)
        else:
            draw_rich_gradient(draw, VIDEO_WIDTH, VIDEO_HEIGHT, edition=edition)

    has_overlay = overlay_image_path and os.path.exists(overlay_image_path)
    overlay_coords = (80, 120)

    if frame_template == "Solo Anchor":
        has_overlay = False
    elif frame_template == "Full-Screen Media":
        overlay_coords = (VIDEO_WIDTH // 2 - 240, 60)

    if has_overlay:
        overlay = Image.open(overlay_image_path).convert("RGBA")
        if frame_template == "Full-Screen Media":
            overlay.thumbnail((500, 420), Image.Resampling.LANCZOS)
        else:
            overlay.thumbnail((450, 320), Image.Resampling.LANCZOS)

        ox, oy = overlay_coords

        if edition.lower() == "night":
            divider_color = (219, 39, 119)
        else:
            divider_color = DIVIDER_CYAN

        draw.rectangle(
            [(ox - 4, oy - 4), (ox + overlay.width + 4, oy + overlay.height + 4)],
            fill=divider_color,
        )
        canvas.paste(overlay, (ox, oy), overlay)

    canvas.convert("RGBA").save(output_frame_path, "PNG")

    banner_canvas = Image.new("RGBA", (VIDEO_WIDTH, VIDEO_HEIGHT), (0, 0, 0, 0))
    banner_draw = ImageDraw.Draw(banner_canvas)

    try:
        font_status = ImageFont.truetype("arial.ttf", 16)
        font_headline = ImageFont.truetype("arial.ttf", 26)
    except IOError:
        font_status = ImageFont.load_default()
        font_headline = ImageFont.load_default()

    if edition.lower() == "night":
        status_bar_color = (147, 51, 234)
        headline_bg_color = (12, 10, 15)
        divider_color = (219, 39, 119)
        text_headline_color = TEXT_WHITE
        text_shadow_color = (3, 1, 5)

        if frame_template == "Solo Anchor":
            status_label = "LATE NIGHT MONOLOGUE"
        elif frame_template == "Full-Screen Media":
            status_label = "LATE NIGHT EXCLUSIVE"
        elif frame_template == "Guest Interview":
            status_label = "LATE NIGHT INTERVIEW"
        else:
            status_label = "LATE NIGHT SPECIAL"
    else:
        status_bar_color = BANNER_STATUS_RED
        headline_bg_color = (255, 255, 255)
        divider_color = DIVIDER_CYAN
        text_headline_color = (0, 0, 0)
        text_shadow_color = (226, 232, 240)
        status_label = "LIVE BROADCAST"

    banner_text = clean_unicode_text(banner_text)

    banner_draw.rectangle([(0, 540), (VIDEO_WIDTH, 570)], fill=status_bar_color)
    banner_draw.text((30, 546), status_label, fill=TEXT_WHITE, font=font_status)

    banner_draw.rectangle([(0, 570), (VIDEO_WIDTH, 640)], fill=headline_bg_color)
    banner_draw.text(
        (30 + 1, 585 + 1), banner_text[:75], fill=text_shadow_color, font=font_headline
    )
    banner_draw.text(
        (30, 585), banner_text[:75], fill=text_headline_color, font=font_headline
    )

    banner_draw.rectangle([(0, 640), (VIDEO_WIDTH, 645)], fill=divider_color)
    banner_draw.rectangle(
        [(0, 645), (VIDEO_WIDTH, VIDEO_HEIGHT)], fill=BANNER_TICKER_BG
    )

    banner_canvas.convert("RGBA").save(output_banner_path, "PNG")


def render_ticker_text_image(master_ticker: str, duration: float, output_path: str):
    """Generates an elongated horizontal ribbon containing the crawling ticker feed."""
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

    master_ticker = clean_unicode_text(master_ticker)

    base_text = f"   •   {master_ticker}   "
    repeated_text = base_text
    while len(repeated_text) * 11 < canvas_w:
        repeated_text += base_text

    draw.text((10, 15), repeated_text, fill=(180, 220, 255), font=font_ticker)
    canvas.save(output_path, "PNG")


def render_live_tag_cover(output_path: str, edition: str = "morning"):
    """Generates a static red banner overlay indicating active live connection status."""
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


async def compile_news_segment(
    segment_data: dict,
    segment_index: int,
    master_ticker: str,
    edition: str = "morning",
    guild_id: int = 0,
) -> CompositeVideoClip:
    """Combines voice tracks, base overlays, scrolling banners, and live tags into a MoviePy clip."""
    os.makedirs("temp", exist_ok=True)
    audio_path = f"temp/audio_{guild_id}_{segment_index}.mp3"
    frame_path = f"temp/frame_{guild_id}_{segment_index}.png"
    banner_path = f"temp/banner_{guild_id}_{segment_index}.png"
    ticker_path = f"temp/ticker_{guild_id}_{segment_index}.png"
    live_tag_path = f"temp/live_tag_cover_{guild_id}_{edition}.png"

    clean_script = clean_unicode_text(segment_data["script_text"])

    print(f"Generating voice narrative track for segment {segment_index}...")
    await generate_tts_audio(clean_script, audio_path)

    audio_clip = AudioFileClip(audio_path)
    voice_duration = audio_clip.duration
    audio_clip.close()

    pause_duration = segment_data.get("pause_duration") or 0
    segment_duration = voice_duration + pause_duration

    if pause_duration > 0:
        padded_audio_path = f"temp/audio_padded_{guild_id}_{segment_index}.mp3"
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe,
            "-y",
            "-i",
            audio_path,
            "-af",
            f"apad=pad_dur={pause_duration}",
            padded_audio_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode == 0 and os.path.exists(padded_audio_path):
            try:
                os.remove(audio_path)
                os.rename(padded_audio_path, audio_path)
            except Exception as e:
                print(f"      ⚠️ Failed to overwrite audio with silent pad: {e}")
        else:
            print(f"      ⚠️ FFmpeg apad filter failed: {proc.stderr.decode()}")

    bg_path = segment_data.get("background_path", "")
    secondary_overlay_path = segment_data.get("secondary_overlay_path", "")
    has_secondary = secondary_overlay_path and os.path.exists(secondary_overlay_path)

    render_scene_base_layout(
        background_path=bg_path,
        character_path="",
        overlay_image_path=segment_data.get("overlay_path", ""),
        banner_text=segment_data.get("banner_text", "LIVE NEWS UPDATE"),
        frame_template=segment_data.get("frame_template", "Standard Report"),
        output_frame_path=frame_path,
        output_banner_path=banner_path,
        edition=edition,
    )

    render_ticker_text_image(master_ticker, segment_duration, ticker_path)

    if not os.path.exists(live_tag_path):
        render_live_tag_cover(live_tag_path, edition=edition)

    if has_secondary:
        transition_time = min(5.0, max(3.0, segment_duration * 0.35))
        if transition_time >= segment_duration:
            transition_time = segment_duration * 0.5

        layout_clip_1 = ImageClip(frame_path).with_duration(transition_time)

        secondary_frame_path = f"temp/secondary_frame_{guild_id}_{segment_index}.png"
        secondary_banner_path = f"temp/secondary_banner_{guild_id}_{segment_index}.png"
        render_scene_base_layout(
            background_path=bg_path,
            character_path="",
            overlay_image_path=secondary_overlay_path,
            banner_text=segment_data.get("banner_text", "LIVE NEWS UPDATE"),
            frame_template=segment_data.get("frame_template", "Standard Report"),
            output_frame_path=secondary_frame_path,
            output_banner_path=secondary_banner_path,
            edition=edition,
        )
        layout_clip_2 = (
            ImageClip(secondary_frame_path)
            .with_duration(segment_duration - transition_time)
            .with_start(transition_time)
        )

        visual_layers = [layout_clip_1, layout_clip_2]
        banner_clip_1 = ImageClip(banner_path).with_duration(transition_time)
        banner_clip_2 = (
            ImageClip(secondary_banner_path)
            .with_duration(segment_duration - transition_time)
            .with_start(transition_time)
        )
        banner_layers = [banner_clip_1, banner_clip_2]
    else:
        visual_layers = [ImageClip(frame_path).with_duration(segment_duration)]
        banner_layers = [ImageClip(banner_path).with_duration(segment_duration)]

    ticker_clip = (
        ImageClip(ticker_path)
        .with_duration(segment_duration)
        .with_position(lambda t: (120 - (t * 110), 656))
    )

    live_cover_clip = (
        ImageClip(live_tag_path).with_duration(segment_duration).with_position((0, 645))
    )

    host_pose = segment_data.get("host_pose", "standard")
    host_clip = None

    if host_pose == "pointing":
        frame = "assets/host_point.png"
        if not os.path.exists(frame):
            frame = "assets/host_talk_1.png"
        if os.path.exists(frame):
            host_clip = ImageClip(frame).with_duration(segment_duration)

    elif host_pose == "thinking":
        frame = "assets/host_think.png"
        if not os.path.exists(frame):
            frame = "assets/host_talk_1.png"
        if os.path.exists(frame):
            host_clip = ImageClip(frame).with_duration(segment_duration)

    elif host_pose == "sighing":
        frame = "assets/host_talk_4.png"
        if not os.path.exists(frame):
            frame = "assets/host_talk_1.png"
        if os.path.exists(frame):
            host_clip = ImageClip(frame).with_duration(segment_duration)

    else:

        fps = 2
        num_frames = int(voice_duration * fps)
        if num_frames == 0:
            num_frames = 1

        frames_cycle = [
            "assets/host_talk_1.png",
            "assets/host_talk_1.png",
            "assets/host_talk_3.png",
        ]
        valid_cycle = [f for f in frames_cycle if os.path.exists(f)]
        if not valid_cycle and os.path.exists("assets/character.png"):
            valid_cycle = ["assets/character.png"]

        if valid_cycle:
            actual_frames = []
            for i in range(num_frames):
                actual_frames.append(valid_cycle[i % len(valid_cycle)])

            talking_clip = ImageSequenceClip(actual_frames, fps=fps)

            idle_frame = "assets/host_talk_1.png"
            if not os.path.exists(idle_frame):
                idle_frame = (
                    "assets/character.png"
                    if os.path.exists("assets/character.png")
                    else ""
                )

            if idle_frame and (segment_duration - voice_duration) > 0:
                idle_clip = ImageClip(idle_frame).with_duration(
                    segment_duration - voice_duration
                )
                host_clip = concatenate_videoclips(
                    [talking_clip, idle_clip], method="compose"
                )
            else:
                host_clip = talking_clip

    if host_clip:
        frame_template = segment_data.get("frame_template", "Standard Report")
        host_scale_height = int(VIDEO_HEIGHT * 0.65)

        if frame_template == "Solo Anchor":
            host_scale_height = int(VIDEO_HEIGHT * 0.72)
        elif frame_template == "Full-Screen Media":
            host_scale_height = int(VIDEO_HEIGHT * 0.40)

        ref_path = "assets/host_talk_1.png"
        if not os.path.exists(ref_path):
            ref_path = "assets/character.png"

        aspect_ratio = 1.0
        if os.path.exists(ref_path):
            try:
                with Image.open(ref_path) as img:
                    aspect_ratio = img.width / img.height
            except Exception:
                pass

        scale_width = int(host_scale_height * aspect_ratio)
        host_y = 540 - host_scale_height + 55

        if frame_template == "Solo Anchor":
            host_x = (VIDEO_WIDTH - scale_width) // 2
        elif frame_template == "Full-Screen Media":
            host_x = VIDEO_WIDTH - scale_width - 40
        else:
            host_x = VIDEO_WIDTH - scale_width - 80

        host_clip = host_clip.resized(height=host_scale_height)
        host_clip = host_clip.with_position((host_x, host_y))

    extra_clips = []
    if host_clip:
        extra_clips.append(host_clip)
    extra_clips.extend(banner_layers)
    extra_clips.append(ticker_clip)
    extra_clips.append(live_cover_clip)

    if bg_path.endswith(".mp4") and os.path.exists(bg_path):
        try:
            bg_video = VideoFileClip(bg_path)
            bg_video = bg_video.resized((VIDEO_WIDTH, VIDEO_HEIGHT))
            bg_video = bg_video.with_effects([Loop(duration=segment_duration)])
            bg_video = bg_video.with_position("center")

            composite_segment = CompositeVideoClip(
                [bg_video] + visual_layers + extra_clips
            ).with_duration(segment_duration)
            return composite_segment
        except Exception as e:
            print(
                f"      ⚠️ Failed to loop video backdrop for segment {segment_index}: {e}"
            )

    composite_segment = CompositeVideoClip(visual_layers + extra_clips).with_duration(
        segment_duration
    )
    return composite_segment


def generate_full_news_video(
    segments: list,
    output_filepath: str,
    music_path: str = "",
    edition: str = "morning",
    guild_id: int = 0,
):
    """Sews multiple visual elements, voices, and backplate background audio tracks into the final MP4 video."""
    os.makedirs("temp", exist_ok=True)
    silent_video_path = f"temp/silent_video_draft_{guild_id}.mp4"
    master_audio_path = f"temp/master_audio_track_{guild_id}.mp3"

    ticker_list = []
    server_name = (
        segments[0].get("server_name", "Community Server") if segments else "Local News"
    )

    for s in segments:
        t_text = s.get("ticker_text", "").strip()
        if t_text:
            ticker_list.append(t_text)

    if not ticker_list:
        ticker_list = [
            f"{server_name.upper()} LIVE BROADCAST",
            "ALL SYSTEMS RUNNING",
            "STATION ONLINE",
        ]

    master_ticker = "   •   ".join(ticker_list)
    print(f'Compiled Master Ticker Feed: "{master_ticker[:80]}..."')

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    video_clips = []
    try:
        for idx, seg in enumerate(segments):
            v_clip = loop.run_until_complete(
                compile_news_segment(
                    seg, idx, master_ticker, edition=edition, guild_id=guild_id
                )
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
        logger="bar",
        threads=os.cpu_count() or 3,
        preset="ultrafast",
    )

    for v in video_clips:
        v.close()
    final_silent_video.close()

    print("Muxing audio track...")
    concat_list_path = f"temp/audio_list_{guild_id}.txt"
    with open(concat_list_path, "w", encoding="utf-8") as f_list:
        for idx in range(len(segments)):
            path_chunk = f"temp/audio_{guild_id}_{idx}.mp3"
            if os.path.exists(path_chunk):
                abs_path = os.path.abspath(path_chunk).replace("\\", "/")
                f_list.write(f"file '{abs_path}'\n")

    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    concat_cmd = [
        ffmpeg_exe,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list_path,
        "-c",
        "copy",
        master_audio_path,
    ]
    subprocess.run(concat_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    mixed_audio_output_path = f"temp/master_mixed_audio_output_{guild_id}.mp3"

    if music_path and os.path.exists(music_path):
        print(f"Sourcing background music backplate: '{music_path}'...")
        try:
            mix_cmd = [
                ffmpeg_exe,
                "-y",
                "-i",
                master_audio_path,
                "-stream_loop",
                "-1",
                "-i",
                music_path,
                "-filter_complex",
                "[1:a]volume=0.07[bg_music];[0:a][bg_music]amix=inputs=2:duration=first:dropout_transition=2[out]",
                "-map",
                "[out]",
                "-c:a",
                "libmp3lame",
                "-q:a",
                "2",
                mixed_audio_output_path,
            ]

            process_mix = subprocess.run(
                mix_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            if process_mix.returncode == 0:
                print(
                    "🎉 Successfully compiled mixed audio track with ducked background music!"
                )
                master_audio_path = mixed_audio_output_path
            else:
                print(
                    f"⚠️ Audio mixing failed. Falling back to voice narrative only: {process_mix.stderr.decode()}"
                )
        except Exception as e:
            print(
                f"⚠️ Background audio mixing failed: {e}. Falling back to voice narrative only."
            )

    print("Executing standard high-fidelity layout mux via FFmpeg...")

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        silent_video_path,
        "-i",
        master_audio_path,
        "-c:v",
        "libx264",
        "-crf",
        "21",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        output_filepath,
    ]

    process = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    if process.returncode == 0:
        print(f"🎉 Success! High-quality video compiled: {output_filepath}")

        for idx in range(len(segments)):
            temp_path = f"temp/audio_{guild_id}_{idx}.mp3"
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
        if os.path.exists(silent_video_path):
            try:
                os.remove(silent_video_path)
            except Exception:
                pass
        if os.path.exists(master_audio_path):
            try:
                os.remove(master_audio_path)
            except Exception:
                pass
        if os.path.exists(mixed_audio_output_path):
            try:
                os.remove(mixed_audio_output_path)
            except Exception:
                pass
        if os.path.exists(concat_list_path):
            try:
                os.remove(concat_list_path)
            except Exception:
                pass
    else:
        print(f"❌ Muxing error: {process.stderr.decode()}")
