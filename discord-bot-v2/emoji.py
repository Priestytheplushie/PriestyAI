import io
import os
import base64
import asyncio
import logging
import aiohttp
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("EmojiUploader")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN not found in environment or .env file.")

REMAINING_CATALOG = {
    "ext_zip": [
        "https://img.icons8.com/color/144/zip.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/zip.png"
    ],

    "ext_html": [
        "https://img.icons8.com/color/144/html-5--v1.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/html5.png"
    ],
    "ext_css": [
        "https://img.icons8.com/color/144/css3.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/css3.png"
    ],
    "ext_react": [
        "https://img.icons8.com/color/144/react-native.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/react.png"
    ],
    "ext_vue": [
        "https://img.icons8.com/color/144/vue-js.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/vue.png"
    ],

    "ext_cpp": [
        "https://img.icons8.com/color/144/c-plus-plus-logo.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/cplusplus.png"
    ],
    "ext_sh": [
        "https://img.icons8.com/color/144/bash.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/bash.png"
    ],

    "ext_yaml": [
        "https://img.icons8.com/color/144/yaml.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/yaml.png"
    ],
    "ext_toml": [
        "https://img.icons8.com/color/144/settings--v1.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/toml.png"
    ],
    "ext_env": [
        "https://img.icons8.com/color/144/settings-gears.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/env.png"
    ],

    "ext_md": [
        "https://img.icons8.com/color/144/markdown.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/markdown.png"
    ],
    "ext_csv": [
        "https://img.icons8.com/color/144/csv.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/csv.png"
    ],
    "ext_pdf": [
        "https://img.icons8.com/color/144/pdf.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/pdf.png"
    ],
    "ext_txt": [
        "https://img.icons8.com/color/144/txt.png",
        "https://raw.githubusercontent.com/walkxcode/dashboard-icons/main/png/text.png"
    ]
}

BASE_API_URL = "https://discord.com/api/v10"

DISCORD_HEADERS = {
    "Authorization": f"Bot {DISCORD_TOKEN}",
    "Content-Type": "application/json"
}

DOWNLOAD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "image/png,image/webp,image/*,*/*;q=0.8"
}

async def get_application_id(session: aiohttp.ClientSession) -> str:
    async with session.get(f"{BASE_API_URL}/oauth2/applications/@me", headers=DISCORD_HEADERS) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Failed to fetch application details ({resp.status}): {text}")
        data = await resp.json()
        return data["id"]

async def get_existing_emojis(session: aiohttp.ClientSession, app_id: str) -> dict[str, dict]:
    async with session.get(f"{BASE_API_URL}/applications/{app_id}/emojis", headers=DISCORD_HEADERS) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Failed to fetch application emojis ({resp.status}): {text}")
        data = await resp.json()
        return {e["name"]: e for e in data.get("items", [])}

async def download_image_b64(session: aiohttp.ClientSession, url_candidates: list[str]) -> str:
    last_error = "No URLs provided"
    for url in url_candidates:
        try:
            async with session.get(url, headers=DOWNLOAD_HEADERS, timeout=8.0) as resp:
                if resp.status == 200:
                    raw_bytes = await resp.read()
                    if len(raw_bytes) > 200:
                        b64_str = base64.b64encode(raw_bytes).decode("utf-8")
                        return f"data:image/png;base64,{b64_str}"
                else:
                    last_error = f"Status {resp.status} from {url}"
        except Exception as e:
            last_error = f"Error from {url}: {e}"

    raise RuntimeError(f"All candidate URLs failed: {last_error}")

async def upload_emoji(session: aiohttp.ClientSession, app_id: str, name: str, image_b64: str) -> dict:
    payload = {"name": name, "image": image_b64}
    async with session.post(f"{BASE_API_URL}/applications/{app_id}/emojis", headers=DISCORD_HEADERS, json=payload) as resp:
        if resp.status not in [200, 201]:
            err_text = await resp.text()
            raise RuntimeError(f"Failed to upload '{name}' ({resp.status}): {err_text}")
        return await resp.json()

async def main():
    logger.info("Starting Application Emoji Uploader (Pass 2)...")

    async with aiohttp.ClientSession() as session:
        app_id = await get_application_id(session)
        logger.info(f"Connected to Application ID: {app_id}")

        existing_emojis = await get_existing_emojis(session, app_id)
        logger.info(f"Found {len(existing_emojis)} existing Application Emoji(s).")

        results = {}

        for name, url_list in REMAINING_CATALOG.items():
            if name in existing_emojis:
                emoji_obj = existing_emojis[name]
                emoji_tag = f"<:{emoji_obj['name']}:{emoji_obj['id']}>"
                logger.info(f"[EXISTS] '{name}' -> {emoji_tag}")
                results[name] = emoji_tag
                continue

            logger.info(f"[UPLOADING] '{name}'...")
            try:
                img_data_uri = await download_image_b64(session, url_list)
                created_emoji = await upload_emoji(session, app_id, name, img_data_uri)
                emoji_tag = f"<:{created_emoji['name']}:{created_emoji['id']}>"
                logger.info(f"[SUCCESS] Uploaded '{name}' -> {emoji_tag}")
                results[name] = emoji_tag
                await asyncio.sleep(1.2)
            except Exception as e:
                logger.error(f"[FAILED] Could not upload '{name}': {e}")

        all_emojis = await get_existing_emojis(session, app_id)
        final_map = {name: f"<:{obj['name']}:{obj['id']}>" for name, obj in all_emojis.items() if name.startswith("ext_")}

        print("\n" + "=" * 60)
        print("COPIED OUTPUT — COMPLETE APPLICATION EMOJIS MAPPING:")
        print("=" * 60 + "\n")
        print("FILE_ICON_MAP = {")
        for k, v in sorted(final_map.items()):
            print(f'    "{k}": "{v}",')
        print("}\n")
        print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())