import os
import re
import time
import asyncio
import logging
import urllib.parse
from typing import Any
from playwright.async_api import async_playwright, Browser, Playwright

logger = logging.getLogger("PriestyAI.BrowserManager")

CONSENT_COOKIES = [
    {
        "name": "SOCS",
        "value": "CAESHAgBEhJnd3NfMjAyNDA2MTAtMF9SQzIaAmVuIAEaBgiA_L20Bg",
        "domain": ".google.com",
        "path": "/"
    },
    {
        "name": "CONSENT",
        "value": "PENDING+999",
        "domain": ".google.com",
        "path": "/"
    }
]

class BrowserManager:
    def __init__(self):
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return

            try:
                logger.info("[BrowserManager] Starting background Chromium process...")
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--no-first-run",
                        "--no-default-browser-check"
                    ]
                )
                logger.info("[BrowserManager] Chromium browser instance started successfully.")
            except Exception as e:
                logger.error(f"[BrowserManager] Failed to launch Chromium browser: {e}")
                self._browser = None
                self._playwright = None

    async def ensure_running(self):
        if not self._browser or not self._browser.is_connected():
            await self.start()

    async def close(self):
        async with self._lock:
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    logger.debug(f"[BrowserManager] Error closing browser: {e}")
                self._browser = None

            if self._playwright:
                try:
                    await self._playwright.stop()
                except Exception as e:
                    logger.debug(f"[BrowserManager] Error stopping playwright: {e}")
                self._playwright = None

            logger.info("[BrowserManager] Chromium browser stopped.")

    async def scrape_google_web(self, query: str, limit: int = 4) -> list[dict[str, str]]:
        await self.ensure_running()
        if not self._browser:
            logger.error("[BrowserManager] Browser unavailable for web search.")
            return []

        encoded_q = urllib.parse.quote(query.strip())
        url = f"https://www.google.com/search?q={encoded_q}&hl=en&gl=us&safe=active"

        context = None
        results: list[dict[str, str]] = []

        try:
            context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US"
            )
            await context.add_cookies(CONSENT_COOKIES)

            page = await context.new_page()
            logger.info(f"[BrowserManager] Navigating to Google Web Search for: '{query}'")
            await page.goto(url, wait_until="domcontentloaded", timeout=8000)

            try:
                await page.wait_for_selector("#search, #rso, div.g", timeout=3500)
            except Exception:
                pass

            cards = page.locator("div.g, div[data-hveid]")
            card_count = await cards.count()

            for i in range(card_count):
                if len(results) >= limit:
                    break

                card = cards.nth(i)
                try:
                    link_el = card.locator("a[href^='http']").first
                    if not await link_el.count():
                        continue

                    href = await link_el.get_attribute("href")
                    if not href or any(bad in href for bad in ["google.com", "support.google", "policies.google"]):
                        continue

                    title_el = card.locator("h3").first
                    title = await title_el.inner_text() if await title_el.count() else ""
                    if not title or len(title.strip()) < 3:
                        continue

                    snippet_el = card.locator("div[style*='-webkit-line-clamp'], div.VwiC3b, div[data-snc]").first
                    snippet = await snippet_el.inner_text() if await snippet_el.count() else ""
                    if not snippet:
                        full_card_text = await card.inner_text()
                        lines = [line.strip() for line in full_card_text.splitlines() if line.strip() and line.strip() != title]
                        snippet = " ".join(lines[:2])

                    if not any(r["link"] == href for r in results):
                        results.append({
                            "title": title.strip(),
                            "link": href.strip(),
                            "snippet": snippet.strip()[:350]
                        })
                except Exception as ex:
                    logger.debug(f"[BrowserManager] Error extracting card #{i}: {ex}")

        except Exception as e:
            logger.error(f"[BrowserManager] Error executing web search for '{query}': {e}")
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

        logger.info(f"[BrowserManager] Google Web Search gathered {len(results)} results for '{query}'")
        return results

    async def scrape_google_images(self, query: str, limit: int = 4) -> list[dict[str, Any]]:
        await self.ensure_running()
        if not self._browser:
            logger.error("[BrowserManager] Browser unavailable for image search.")
            return []

        encoded_q = urllib.parse.quote(query.strip())
        url = f"https://www.google.com/search?q={encoded_q}&udm=2&hl=en&gl=us&safe=active"

        context = None
        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        try:
            context = await self._browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1440, "height": 900},
                locale="en-US"
            )
            await context.add_cookies(CONSENT_COOKIES)

            page = await context.new_page()
            logger.info(f"[BrowserManager] Navigating to Google Images for: '{query}'")
            await page.goto(url, wait_until="domcontentloaded", timeout=10000)

            try:
                await page.wait_for_selector("#rso, div[data-ri], div[jscontroller]", timeout=4000)
            except Exception:
                pass

            thumbnails = page.locator("#rso img, div[data-ri] img, a[jsname='hSRGPd'] img")
            total_thumbs = await thumbnails.count()
            logger.info(f"[BrowserManager] Found {total_thumbs} thumbnail cards in grid for '{query}'.")

            for idx in range(min(total_thumbs, limit * 3)):
                if len(results) >= limit:
                    break

                thumb = thumbnails.nth(idx)
                try:
                    await thumb.scroll_into_view_if_needed(timeout=1000)
                    await thumb.click(timeout=1500)
                    await page.wait_for_timeout(350)
                except Exception:
                    continue

                expanded_images = page.locator("div[role='dialog'] img, div[jsname] img[src^='http']")
                img_count = await expanded_images.count()

                for img_idx in range(img_count):
                    candidate = expanded_images.nth(img_idx)
                    src = await candidate.get_attribute("src")
                    alt = await candidate.get_attribute("alt") or query

                    if not src or not src.startswith("http"):
                        continue

                    if any(bad in src for bad in ["gstatic.com", "google.com", "googleusercontent.com"]):
                        continue

                    if src not in seen_urls:
                        seen_urls.add(src)
                        source_domain = src.split("/")[2] if "//" in src else "Web"
                        logger.info(f"[BrowserManager] ✨ Clicked #{idx+1} -> Grabbed high-res image from {source_domain}")
                        results.append({
                            "title": alt[:100].strip(),
                            "image_url": src,
                            "thumbnail_url": src,
                            "source": source_domain,
                            "engine": "Google Click-Scraper"
                        })
                        break

        except Exception as e:
            logger.error(f"[BrowserManager] Error scraping images for '{query}': {e}")
        finally:
            if context:
                try:
                    await context.close()
                except Exception:
                    pass

        logger.info(f"[BrowserManager] Google Image Search extracted {len(results)} candidate(s) for '{query}'")
        return results

browser_manager = BrowserManager()