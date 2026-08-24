import re
import asyncio
import logging
from typing import Any
import httpx
import trafilatura
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from tools.registry import tool_registry

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
        "Fetches and extracts the full readable text content and markdown from a specific URL. "
        "Use this after search_web to read detailed articles, documentation, or links provided by users."
    )
)
async def read_link(url: str) -> dict[str, Any]:
    logger.info(f"[read_link] Fetching URL: {url}")
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

            cleaned_text = extracted_text[:3000].strip() if extracted_text else "Unable to extract main content."

            return {
                "url": url,
                "length": len(cleaned_text),
                "content": cleaned_text
            }

    except Exception as e:
        logger.error(f"[read_link] Failed to fetch {url}: {e}")
        return {"url": url, "error": f"Failed to load URL: {str(e)}"}