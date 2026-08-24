import re
import asyncio
import logging
from typing import Any
import httpx
import trafilatura
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from google.genai import types
from tools.registry import tool_registry, ToolExecutionContext
from core.client_manager import client_manager

logger = logging.getLogger("PriestyAI.SearchTools")

def sanitize_query(query: str) -> str:
    query = re.sub(r'(?i)^(can you (please )?search for|search (for|about)|find me|look up|what is in)\s+', '', query)
    return query.strip()

def _sync_ddg_search(query: str, max_results: int) -> list[dict[str, str]]:
    results = []
    try:
        with DDGS() as ddgs:
            raw = ddgs.text(keywords=query, max_results=max_results)
            if raw:
                for item in raw:
                    results.append({
                        "title": item.get("title", "No title"),
                        "link": item.get("href", item.get("link", "")),
                        "snippet": item.get("body", item.get("snippet", ""))
                    })
    except Exception as e:
        logger.warning(f"DDGS primary search error: {e}")
    return results

async def _fetch_ddg_lite(query: str, max_results: int) -> list[dict[str, str]]:
    results = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://lite.duckduckgo.com/"
        }
        async with httpx.AsyncClient(timeout=7.0, follow_redirects=True) as client:
            resp = await client.post(
                "https://lite.duckduckgo.com/lite/",
                data={"q": query},
                headers=headers
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                rows = soup.find_all("tr")
                for row in rows:
                    link_tag = row.find("a", class_="result-link")
                    snippet_tag = row.find("td", class_="result-snippet")
                    if link_tag:
                        title = link_tag.get_text(strip=True)
                        href = link_tag.get("href", "")
                        snippet = snippet_tag.get_text(strip=True) if snippet_tag else title
                        results.append({"title": title, "link": href, "snippet": snippet})
                        if len(results) >= max_results:
                            break
    except Exception as e:
        logger.error(f"DDG Lite fallback error: {e}")
    return results

def is_youtube_url(url: str) -> bool:
    return bool(re.search(r'(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)', url, re.IGNORECASE))

@tool_registry.register(
    name="search_web",
    description=(
        "MANDATORY search engine tool. You MUST invoke this whenever answering questions "
        "about current events, real-time facts, game updates/seasons, or latest documentation. "
        "DO NOT guess or assume something does not exist without searching."
    )
)
async def search_web(query: str, max_results: int = 3) -> dict[str, Any]:
    clean_q = sanitize_query(query)
    logger.info(f"[search_web] Query: '{clean_q}' (max: {max_results})")

    results = await asyncio.to_thread(_sync_ddg_search, clean_q, max_results)

    if not results:
        logger.info(f"[search_web] Primary returned 0. Triggering DDG Lite for '{clean_q}'...")
        results = await _fetch_ddg_lite(clean_q, max_results)

    if not results and len(clean_q.split()) > 3:
        simplified_q = " ".join([w for w in clean_q.split() if w.lower() not in ["content", "leaks", "predictions", "details", "update"]])
        logger.info(f"[search_web] Retrying with simplified query: '{simplified_q}'...")
        results = await _fetch_ddg_lite(simplified_q, max_results)

    if not results:
        return {"query": clean_q, "results": [], "message": "No search results found for this query."}

    logger.info(f"[search_web] Successfully gathered {len(results)} results for '{clean_q}'")
    return {"query": clean_q, "result_count": len(results), "results": results}

@tool_registry.register(
    name="read_link",
    description=(
        "Fetches and analyzes web pages, articles, documentation, or YouTube video links.\n"
        "Supports direct native video and audio understanding for public YouTube URLs!\n"
        "Use this after search_web or whenever users provide URLs."
    )
)
async def read_link(url: str) -> dict[str, Any]:
    logger.info(f"[read_link] Fetching URL: {url}")

    if is_youtube_url(url):
        logger.info(f"[read_link] Detected YouTube link: '{url}'. Running native video comprehension...")
        client, key_idx, active_model = client_manager.get_client_for_model("gemini-3.5-flash-lite")
        if client:
            try:
                prompt_instruction = (
                    "Analyze this YouTube video thoroughly. Provide a structured, comprehensive summary with key takeaways, "
                    "timestamps for major topic transitions, visual context, and conclusions."
                )
                response = await client.aio.models.generate_content(
                    model=active_model,
                    contents=[
                        types.Part.from_uri(file_uri=url, mime_type="video/mp4"),
                        prompt_instruction
                    ]
                )
                if response.text:
                    return {
                        "url": url,
                        "type": "youtube_video_summary",
                        "model": active_model,
                        "content": response.text.strip()
                    }
            except Exception as e:
                client_manager.report_error(key_idx, active_model, e)
                logger.warning(f"Native YouTube parsing failed: {e}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return {"url": url, "error": f"HTTP status {resp.status_code}"}

            extracted_text = trafilatura.extract(
                resp.text,
                include_links=True,
                include_tables=True,
                output_format="markdown"
            )

            if not extracted_text or len(extracted_text.strip()) < 100:
                soup = BeautifulSoup(resp.text, "html.parser")
                for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                    tag.decompose()
                extracted_text = soup.get_text(separator="\n", strip=True)

            cleaned_text = extracted_text[:3500].strip() if extracted_text else "Unable to extract main content."

            return {
                "url": url,
                "length": len(cleaned_text),
                "content": cleaned_text
            }

    except Exception as e:
        logger.error(f"[read_link] Failed to fetch {url}: {e}")
        return {"url": url, "error": f"Failed to load URL: {str(e)}"}

@tool_registry.register(
    name="fetch_github",
    description=(
        "Fetches the directory structure and full source files of any public GitHub repository.\n"
        "- repo_url: Full GitHub URL (e.g. 'https://github.com/torvalds/linux' or 'pallets/flask')\n"
        "- subpath: Optional folder or file filter (e.g. 'src/' or 'app.py')"
    )
)
async def fetch_github(repo_url: str, subpath: str = "") -> dict[str, Any]:
    clean_url = repo_url.strip().rstrip("/")
    match = re.search(r'github\.com\/([^\/]+)\/([^\/\?#]+)', clean_url)
    if match:
        owner, repo = match.group(1), match.group(2)
    elif "/" in clean_url and not clean_url.startswith("http"):
        parts = clean_url.split("/")
        owner, repo = parts[0], parts[1]
    else:
        return {"error": f"Invalid GitHub repository URL: '{repo_url}'"}

    api_url = f"https://gitingest.com/api/{owner}/{repo}"
    if subpath.strip():
        api_url += f"?pattern={subpath.strip()}"

    logger.info(f"[fetch_github] Fetching digest for {owner}/{repo} via GitIngest API...")
    try:
        headers = {
            "User-Agent": "PriestyAI-DiscordBot",
            "Accept": "application/json"
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                digest = data.get("digest") or data.get("content") or str(data)
                return {
                    "status": "success",
                    "repo": f"{owner}/{repo}",
                    "subpath": subpath or "root",
                    "digest": digest[:4500]
                }
            else:
                logger.warning(f"GitIngest returned status {resp.status_code}. Fallback to README scrape...")
                readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"
                r_resp = await client.get(readme_url)
                if r_resp.status_code == 200:
                    return {
                        "status": "partial",
                        "repo": f"{owner}/{repo}",
                        "note": "Fetched README.md as fallback",
                        "digest": r_resp.text[:3500]
                    }

    except Exception as e:
        logger.error(f"[fetch_github] Error querying repo {owner}/{repo}: {e}")

    return {"error": f"Unable to fetch repository data for '{owner}/{repo}'."}