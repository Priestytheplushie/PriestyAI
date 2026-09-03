import os
import json
import base64
import asyncio
import httpx
from typing import Any
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN is missing from .env")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_ROOT = os.path.join(BASE_DIR, "assets", "emojis")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
EMOJI_JSON_PATH = os.path.join(CONFIG_DIR, "emojis.json")
DISCORD_API_BASE = "https://discord.com/api/v10"

HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "PriestyAI-EmojiSetup/2.0"
}

async def get_application_id(client: httpx.AsyncClient) -> tuple[str, str]:
    resp = await client.get(f"{DISCORD_API_BASE}/oauth2/applications/@me", headers=HEADERS)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to fetch application info ({resp.status_code}): {resp.text}")
    data = resp.json()
    return data["id"], data.get("name", "Application")

async def list_application_emojis(client: httpx.AsyncClient, app_id: str) -> dict[str, dict[str, Any]]:
    resp = await client.get(f"{DISCORD_API_BASE}/applications/{app_id}/emojis", headers=HEADERS)
    if resp.status_code != 200:
        return {}
    items = resp.json().get("items", [])
    return {e["name"]: e for e in items}

async def upload_emoji(client: httpx.AsyncClient, app_id: str, name: str, filepath: str) -> dict[str, Any] | None:
    is_animated = filepath.endswith(".gif")
    mime = "image/gif" if is_animated else "image/png"

    with open(filepath, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("utf-8")
    data_uri = f"data:{mime};base64,{b64_data}"

    payload = {
        "name": name,
        "image": data_uri
    }

    try:
        resp = await client.post(
            f"{DISCORD_API_BASE}/applications/{app_id}/emojis",
            headers=HEADERS,
            json=payload
        )
        if resp.status_code in (200, 201):
            return resp.json()
        elif resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 2.0)
            await asyncio.sleep(retry_after)
            return await upload_emoji(client, app_id, name, filepath)
        else:
            return None
    except Exception:
        return None

def print_progress(current: int, total: int, uploaded: int, existing: int):
    bar_length = 20
    progress = current / max(1, total)
    filled = int(bar_length * progress)
    bar = "█" * filled + "░" * (bar_length - filled)
    print(f"\r[{bar}] {current}/{total} Emojis processed ({uploaded} uploaded, {existing} existing)", end="", flush=True)

async def main():
    print("=== PriestyAI Application Emoji Setup ===")
    if not os.path.exists(ASSETS_ROOT):
        print(f"Error: Assets folder not found at '{ASSETS_ROOT}'.")
        return

    os.makedirs(CONFIG_DIR, exist_ok=True)

    async with httpx.AsyncClient(timeout=30.0) as client:
        app_id, app_name = await get_application_id(client)
        print(f"Application: {app_name} (ID: {app_id})")

        existing_emojis = await list_application_emojis(client, app_id)

        files_to_process = []
        for root, _, files in os.walk(ASSETS_ROOT):
            for file in files:
                if file.endswith((".png", ".gif")):
                    name = os.path.splitext(file)[0]
                    full_path = os.path.join(root, file)
                    files_to_process.append((name, full_path, file.endswith(".gif")))

        total_files = len(files_to_process)
        if total_files == 0:
            print(f"No emoji image files found in '{ASSETS_ROOT}'.")
            return

        uploaded_count = 0
        existing_count = 0
        emoji_map = {}

        for idx, (name, path, is_anim) in enumerate(files_to_process, start=1):
            if name in existing_emojis:
                e_obj = existing_emojis[name]
                e_id = e_obj["id"]
                prefix = "a" if e_obj.get("animated") else ""
                emoji_map[name] = f"<{prefix}:{name}:{e_id}>"
                existing_count += 1
            else:
                uploaded_obj = await upload_emoji(client, app_id, name, path)
                if uploaded_obj:
                    e_id = uploaded_obj["id"]
                    prefix = "a" if is_anim else ""
                    emoji_map[name] = f"<{prefix}:{name}:{e_id}>"
                    uploaded_count += 1
                await asyncio.sleep(0.4)

            print_progress(idx, total_files, uploaded_count, existing_count)

        with open(EMOJI_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(emoji_map, f, indent=2)

        print(f"\n✓ Completed: {uploaded_count} uploaded, {existing_count} existing. Mapped to config/emojis.json\n")

if __name__ == "__main__":
    asyncio.run(main())