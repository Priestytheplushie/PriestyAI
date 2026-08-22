import re
import urllib.parse
import aiohttp
import asyncio
import logging
from typing import Dict, Any, List

logger = logging.getLogger("PriestyAI.WebTools")

async def execute_search_web(query: str, num_results: int = 5) -> Dict[str, Any]:
    clean_query = query.strip()
    results: List[Dict[str, str]] = []

    try:
        from duckduckgo_search import DDGS
        ddgs = DDGS()
        raw_results = await asyncio.to_thread(ddgs.text, clean_query, max_results=num_results)
        if raw_results:
            for item in raw_results:
                title = item.get("title", "")
                url = item.get("href", "")
                body = item.get("body", "")
                if title and url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": body
                    })
            if results:
                logger.info(f"DDGS search for '{clean_query}' returned {len(results)} results.")
                return {
                    "status": "success",
                    "query": clean_query,
                    "total_results": len(results),
                    "results": results
                }
    except Exception as e:
        logger.debug(f"DDGS tier failed ({e}), attempting SearXNG fallback...")

    searx_instances = [
        "https://search.ononoki.org/search",
        "https://searx.be/search",
        "https://searx.bndkt.io/search"
    ]
    for instance in searx_instances:
        if results:
            break
        try:
            params = {"q": clean_query, "format": "json", "categories": "general"}
            async with aiohttp.ClientSession() as session:
                async with session.get(instance, params=params, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for item in data.get("results", [])[:num_results]:
                            results.append({
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "snippet": item.get("content", "")
                            })
        except Exception as e:
            logger.debug(f"SearXNG instance {instance} failed: {e}")

    if results:
        logger.info(f"SearXNG search for '{clean_query}' returned {len(results)} results.")
        return {
            "status": "success",
            "query": clean_query,
            "total_results": len(results),
            "results": results
        }

    try:
        wiki_url = f"https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote_plus(clean_query)}&utf8=&format=json"
        async with aiohttp.ClientSession() as session:
            async with session.get(wiki_url, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    search_items = data.get("query", {}).get("search", [])
                    for item in search_items[:num_results]:
                        page_title = item.get("title", "")
                        page_id = item.get("pageid", "")
                        snippet = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
                        page_url = f"https://en.wikipedia.org/?curid={page_id}"
                        results.append({
                            "title": page_title,
                            "url": page_url,
                            "snippet": snippet
                        })
    except Exception as e:
        logger.error(f"Wikipedia search fallback error: {e}")

    logger.info(f"Web search for '{clean_query}' completed with {len(results)} results.")
    return {
        "status": "success" if results else "no_results",
        "query": clean_query,
        "total_results": len(results),
        "results": results
    }