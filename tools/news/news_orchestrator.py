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

try:
    from tools.news.data_gatherer import NewsScraper
except ImportError:
    NewsScraper = None

from tools.news.video_generator import generate_full_news_video


def get_and_update_state(
    server_name: str, gemini_key: str, news_model: str
) -> tuple[int, str]:
    """
    Reads, increments, and saves local broadcast states.
    Dynamically generates a permanent, thematic News Show Name on first run.
    """
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

    if (
        not show_name
        or len(show_name) < 4
        or show_name.lower() in ["con", "none", "null"]
    ):
        print(
            f"Branding validation check... Generating premium news branding for '{server_name}'..."
        )
        client = genai.Client(api_key=gemini_key)

        prompt = (
            f"You are a professional television branding producer. The Discord server is named '{server_name}'. "
            f"Generate a single, highly creative, memorable, and thematic daily broadcast news show name "
            f"representing this server (e.g. 'Chaos Conquest Daily Chronicle', 'Chaos Conquest News Network'). "
            f"Do not write any introductory or explanatory text. Output ONLY the clean, final show name string itself."
        )

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=40, temperature=0.7
                ),
            )
            if response and response.text:
                show_name = response.text.strip().replace('"', "").replace("'", "")
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
            json.dump(
                {
                    "last_episode_number": episode,
                    "show_name": show_name,
                    "last_run_date": datetime.now().strftime("%Y-%m-%d"),
                },
                f,
                indent=4,
            )
    except Exception:
        pass

    return episode, show_name


async def write_news_script_with_rate_limits(
    edition: str,
    episode_number: int,
    date_str: str,
    time_str: str,
    show_name: str,
    guild_id: int = 0,
) -> list:
    from tools.news.script_writer import write_news_script

    return await write_news_script(
        edition=edition,
        episode_number=episode_number,
        date_str=date_str,
        time_str=time_str,
        show_name=show_name,
        length="Standard",
        guild_id=guild_id,
    )


def upload_to_streamable(video_path: str, title: str) -> str:
    """Uploads the video to Streamable via standard Basic Auth and returns the URL."""
    email = os.getenv("STREAMABLE_EMAIL")
    password = os.getenv("STREAMABLE_PASSWORD")

    if not email or not password:
        print(
            "⚠️ Warning: STREAMABLE_EMAIL or STREAMABLE_PASSWORD not set in environment."
        )
        return ""

    print("\n[PHASE 4: UPLOADING COMPILATION TO STREAMABLE HOSTING...]")
    url = "https://api.streamable.com/upload"
    auth = HTTPBasicAuth(email, password)

    try:
        with open(video_path, "rb") as f:
            files = {"file": f}
            data = {"title": title}
            response = requests.post(
                url, auth=auth, files=files, data=data, timeout=120
            )

        if response.status_code in (200, 201):
            res_json = response.json()
            shortcode = res_json.get("shortcode", "")
            return f"https://streamable.com/{shortcode}"
        else:
            print(
                f"⚠️ Streamable API rejected upload (Status {response.status_code}): {response.text}"
            )
    except Exception as e:
        print(f"⚠️ Exception during Streamable upload: {e}")

    return ""


async def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Compilation engine for PriestyAI Server News."
    )
    parser.add_argument(
        "--edition",
        type=str,
        choices=["morning", "night", "auto"],
        default="auto",
        help="Specify format. 'auto' detects based on current time.",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        required=True,
        help="Target Discord Guild ID to process.",
    )
    args = parser.parse_args()

    if args.edition == "auto":

        import zoneinfo

        try:
            tz = zoneinfo.ZoneInfo("America/New_York")
        except Exception:
            tz = timezone.utc

        local_now = datetime.now(tz)
        current_hour = local_now.hour

        if current_hour < 12:
            edition = "morning"
        else:
            edition = "night"
    else:
        edition = args.edition

    guild_id = args.guild_id

    if not NewsScraper:
        print("❌ Error: NewsScraper could not be imported from data_gatherer.")
        return

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

    print(
        "⚠️ Standing up stateless NewsScraper instance... For full operations, run through the bot client."
    )
    return


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    asyncio.run(main())
