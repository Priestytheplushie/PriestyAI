
import os
import sys
import argparse
import asyncio
import json
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from google import genai
from google.genai import types

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from tools.news.data_gatherer import NewsDataGatherer, TARGET_GUILD_ID
from tools.news.video_generator import generate_full_news_video


def get_and_update_state(server_name: str, gemini_key: str, news_model: str) -> tuple[int, str]:
    os.makedirs("temp", exist_ok=True)
    state_path = "temp/news_state.json"
    
    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            state = {}

    episode = state.get("last_episode_number", 0) + 1
    show_name = state.get("show_name", "").strip()

    if not show_name or len(show_name) < 4 or show_name.lower() in ["con", "none", "null"]:
        print(f"Branding validation check... Generating premium news branding for '{server_name}'...")
        client = genai.Client(api_key=gemini_key)
        
        prompt = (
            f"You are a professional television branding producer. The Discord server is named '{server_name}'. "
            f"Generate a single, highly creative, memorable, and thematic daily broadcast news show name "
            f"representing this server (e.g. 'Chaos Conquest Daily Chronicle', 'Chaos Conquest News Network'). "
            f"Do not write any introductory or explanatory text. Output ONLY the clean, final show name string itself."
        )
        
        try:
            response = client.models.generate_content(
                model=news_model,
                contents=prompt,
                config=types.GenerateContentConfig(max_output_tokens=40, temperature=0.7)
            )
            if response and response.text:
                show_name = response.text.strip().replace('"', '').replace("'", "")
                if len(show_name) < 4 or show_name.lower() in ["con", "none", "null"]:
                    show_name = f"{server_name} Daily Chronicle"
                print(f"🎉 Generated permanent show branding: '{show_name}'")
            else:
                show_name = f"{server_name} Daily Chronicle"
        except Exception as e:
            print(f"⚠️ Failed to generate show branding: {e}. Falling back to default.")
            show_name = f"{server_name} Daily Chronicle"

    try:
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({
                "last_episode_number": episode, 
                "show_name": show_name,
                "last_run_date": datetime.now().strftime("%Y-%m-%d")
            }, f, indent=4)
    except Exception:
        pass
        
    return episode, show_name


async def write_news_script_with_rate_limits(edition: str, episode_number: int, date_str: str, time_str: str, show_name: str) -> list:
    from tools.news.script_writer import write_news_script
    return await write_news_script(edition, episode_number, date_str, time_str, show_name)


def upload_to_streamable(video_path: str, title: str) -> str:
    email = os.getenv("STREAMABLE_EMAIL")
    password = os.getenv("STREAMABLE_PASSWORD")
    
    if not email or not password:
        print("⚠️ Warning: STREAMABLE_EMAIL or STREAMABLE_PASSWORD not set in environment.")
        return ""

    print("\n[PHASE 4: UPLOADING COMPILATION TO STREAMABLE HOSTING...]")
    url = "https://api.streamable.com/upload"
    auth = HTTPBasicAuth(email, password)
    
    try:
        with open(video_path, "rb") as f:
            files = {"file": f}
            data = {"title": title}
            response = requests.post(url, auth=auth, files=files, data=data, timeout=120)
            
        if response.status_code in (200, 201):
            res_json = response.json()
            shortcode = res_json.get("shortcode", "")
            return f"https://streamable.com/{shortcode}"
        else:
            print(f"⚠️ Streamable API rejected upload (Status {response.status_code}): {response.text}")
    except Exception as e:
        print(f"⚠️ Exception during Streamable upload: {e}")
        
    return ""


async def main():
    load_dotenv()
    
    parser = argparse.ArgumentParser(description="Compilation engine for PriestyAI Server News.")
    parser.add_argument(
        "--edition", 
        type=str, 
        choices=["morning", "night", "auto"], 
        default="auto",
        help="Specify format. 'auto' detects based on current time."
    )
    args = parser.parse_args()

    if args.edition == "auto":
        current_hour = datetime.now().hour
        if current_hour < 12:
            edition = "morning"
        else:
            edition = "night"
    else:
        edition = args.edition

    print("[PHASE 1: GATHERING SERVER DATA]")
    token = os.getenv("DISCORD_TOKEN")
    gemini_key = os.getenv("GEMINI_API_KEY")
    news_model = os.getenv("GEMINI_NEWS_MODEL", "gemini-2.5-flash")
    
    if not token:
        print("❌ Error: DISCORD_TOKEN not found in environment variables.")
        return
    if not gemini_key:
        print("❌ Error: GEMINI_API_KEY not found in environment variables.")
        return

    gatherer = NewsDataGatherer()
    try:
        await gatherer.start(token)
    except Exception as e:
        print(f"❌ Data gathering stage failed: {e}")
        return

    raw_data_path = "temp/raw_news_data.json"
    if not os.path.exists(raw_data_path):
        print("❌ Error: Staging data was not generated. Pipeline aborted.")
        return

    with open(raw_data_path, "r", encoding="utf-8") as f:
        raw_server_data = json.load(f)
    server_name = raw_server_data.get("server_name", "Cool Server")

    episode_number, show_name = get_and_update_state(server_name, gemini_key, news_model)
    
    utc_now = datetime.now(timezone.utc)
    local_now = utc_now - timedelta(hours=4)
    
    formatted_date = local_now.strftime("%A, %B %d, %Y")
    formatted_time = "9:00 AM" if edition == "morning" else "8:00 PM"

    print("=====================================================================")
    print(f"🎬 STARTING PRIESTYAI NEWS PIPELINE: {edition.upper()} EDITION")
    print(f"📣 Show: '{show_name}' | Episode: {episode_number}")
    print(f"🕒 Date: {formatted_date} | target time: {formatted_time}")
    print("=====================================================================\n")

    print("[PHASE 2: COMPOSING AUDIO SCRIPTS & GRAPHICAL OVERLAYS]")
    try:
        segments = await write_news_script_with_rate_limits(edition, episode_number, formatted_date, formatted_time, show_name)
    except Exception as e:
        print(f"❌ Script composition stage failed: {e}")
        import traceback
        traceback.print_exc()
        return

    music_path = "assets/late_night_jazz.mp3" if edition == "night" else "assets/morning_acoustic.mp3"

    print("[PHASE 3: RENDERING HIGH-FIDELITY LAYOUTS & COMPILING BROADCAST]")
    local_output_filename = f"temp_{edition}_edition_broadcast.mp4"
    try:
        await generate_full_news_video(segments, local_output_filename, music_path=music_path, edition=edition)
    except Exception as e:
        print(f"❌ Video compilation stage failed: {e}")
        import traceback
        traceback.print_exc()
        return

    title = f"{show_name} - Ep. {episode_number} ({edition.capitalize()})"
    streamable_url = upload_to_streamable(local_output_filename, title)

    os.makedirs("archive", exist_ok=True)
    archive_filename = f"archive/{show_name.replace(' ', '_')}_Ep_{episode_number}_{edition}.mp4"
    try:
        import shutil
        shutil.copyfile(local_output_filename, archive_filename)
        print(f"💾 Raw video backed up to permanent archive location: {archive_filename}")
    except Exception as e:
        print(f"⚠️ Failed to archive file: {e}")

    print("\n=====================================================================")
    print("🎉 VIDEO COMPILATION PROCESS COMPLETE!")
    if streamable_url:
        print(f"📺 STREAMABLE BROADCAST LINK: {streamable_url}")
    else:
        print(f"📁 Local File Location: {os.path.abspath(local_output_filename)}")
        print("⚠️ Streamable upload skipped or failed. Verify credentials in .env file.")
    print("=====================================================================\n")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
    asyncio.run(main())