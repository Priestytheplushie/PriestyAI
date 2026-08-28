import io
import re
import asyncio
import logging
from typing import Any

logger = logging.getLogger("PriestyAI.ScreenshotService")

def wrap_react_component_to_html(raw_code: str, filename: str) -> str:
    code = raw_code

    code = re.sub(r"import\s+React\s*,\s*\{([^}]+)\}\s+from\s+['\"]react['\"];?", r"const { \1 } = React;", code)
    code = re.sub(r"import\s+\{([^}]+)\}\s+from\s+['\"]react['\"];?", r"const { \1 } = React;", code)
    code = re.sub(r"import\s+React\s+from\s+['\"]react['\"];?", "", code)
    code = re.sub(r"import\s+ReactDOM\s+from\s+['\"]react-dom(?:\/client)?['\"];?", "", code)
    code = re.sub(r"import\s+['\"][^'\"]+\.css['\"];?", "", code)
    code = re.sub(r"import\s+.*?\s+from\s+['\"][^'\"]+['\"];?", "", code)

    component_name = "App"
    export_fn_match = re.search(r"export\s+default\s+function\s+([a-zA-Z0-9_]+)", code)
    if export_fn_match:
        component_name = export_fn_match.group(1)
        code = re.sub(r"export\s+default\s+function\s+", "function ", code)
    else:
        export_ident_match = re.search(r"export\s+default\s+([a-zA-Z0-9_]+);?", code)
        if export_ident_match:
            component_name = export_ident_match.group(1)
            code = re.sub(r"export\s+default\s+[a-zA-Z0-9_]+;?", "", code)

    code = re.sub(r"export\s+default\s+", "", code)
    code = re.sub(r"export\s+const\s+", "const ", code)
    code = re.sub(r"export\s+function\s+", "function ", code)

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>
    body {{
      background-color: #313338;
      color: #dbdee1;
      margin: 0;
      padding: 24px;
      font-family: 'gg sans', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
  </style>
</head>
<body>
  <div id="root"></div>
  <script type="text/babel" data-presets="react,typescript">
    const {{ useState, useEffect, useRef, useMemo, useCallback, createContext, useContext, useReducer }} = React;
    try {{
      {code}
      const RootComp = typeof {component_name} !== 'undefined' ? {component_name} : (typeof App !== 'undefined' ? App : null);
      if (RootComp) {{
        ReactDOM.createRoot(document.getElementById('root')).render(<RootComp />);
      }}
    }} catch (err) {{
      document.getElementById('root').innerHTML = '<div style="padding:16px;background:#2b2d31;border:1px solid #f23f43;border-radius:8px;color:#f23f43;"><strong>React Error:</strong> ' + err.message + '</div>';
    }}
  </script>
</body>
</html>"""

class ScreenshotService:
    def __init__(self):
        self.playwright: Any = None
        self.browser: Any = None
        self.is_available: bool = False
        self._init_lock = asyncio.Lock()

    async def start(self):
        if self.is_available or self.browser:
            return

        async with self._init_lock:
            try:
                from playwright.async_api import async_playwright
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--disable-extensions",
                        "--disable-background-networking",
                        "--disable-default-apps"
                    ]
                )
                self.is_available = True
                logger.info("[ScreenshotService] Headless Chromium browser instance warm and ready.")
            except ImportError:
                logger.warning("[ScreenshotService] Playwright not installed. Install with: pip install playwright && playwright install chromium")
                self.is_available = False
            except Exception as e:
                logger.warning(f"[ScreenshotService] Failed to launch headless Chromium: {e}")
                self.is_available = False

    def bundle_artifact_html(self, files: list[dict[str, Any]], default_filename: str) -> str | None:
        if not files:
            return None

        clean_default = default_filename.lower()

        if clean_default.endswith(".svg") or (len(files) == 1 and files[0].get("filename", "").lower().endswith(".svg")):
            svg_content = files[0].get("content", "")
            return (
                "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                "<style>body{background:#313338;margin:0;display:flex;align-items:center;justify-content:center;height:100vh;overflow:hidden;}svg{max-width:90%;max-height:90%;}</style>"
                f"</head><body>{svg_content}</body></html>"
            )

        html_file = None
        for f in files:
            fn = f.get("filename", "").lower()
            if fn in ("index.html", "index.htm") or fn.endswith(("/index.html", "/index.htm")):
                html_file = f
                break

        if not html_file:
            for f in files:
                if f.get("filename", "").lower().endswith((".html", ".htm")):
                    html_file = f
                    break

        if html_file:
            html_code = html_file.get("content", "")

            for f in files:
                f_name = f.get("filename", "")
                if f_name.lower().endswith(".css"):
                    escaped = re.escape(f_name)
                    link_regex = re.compile(rf'<link[^>]*href=["\'](?:./)?{escaped}["\'][^>]*>', re.IGNORECASE)
                    css_tag = f"<style>\n/* Inlined {f_name} */\n{f.get('content', '')}\n</style>"
                    if link_regex.search(html_code):
                        html_code = link_regex.sub(css_tag, html_code)
                    elif "</head>" in html_code:
                        html_code = html_code.replace("</head>", f"{css_tag}\n</head>")

            for f in files:
                f_name = f.get("filename", "")
                if f_name.lower().endswith(".js") and not f_name.lower().endswith(".min.js"):
                    escaped = re.escape(f_name)
                    script_regex = re.compile(rf'<script[^>]*src=["\'](?:./)?{escaped}["\'][^>]*>\s*</script>', re.IGNORECASE)
                    js_tag = f"<script>\n/* Inlined {f_name} */\n{f.get('content', '')}\n</script>"
                    if script_regex.search(html_code):
                        html_code = script_regex.sub(js_tag, html_code)
                    elif "</body>" in html_code:
                        html_code = html_code.replace("</body>", f"{js_tag}\n</body>")

            return html_code

        react_file = None
        for f in files:
            fn = f.get("filename", "").lower()
            if fn in ("app.jsx", "app.tsx", "index.jsx", "index.tsx") or fn.endswith(("/app.jsx", "/app.tsx")):
                react_file = f
                break

        if not react_file:
            for f in files:
                if f.get("filename", "").lower().endswith((".jsx", ".tsx")):
                    react_file = f
                    break

        if react_file:
            return wrap_react_component_to_html(react_file.get("content", ""), react_file.get("filename", "App.jsx"))

        return None

    async def capture_html_preview(
        self,
        html_content: str,
        width: int = 1200,
        height: int = 750,
        timeout_ms: int = 4000
    ) -> bytes | None:
        if not self.is_available or not self.browser or not html_content:
            return None

        page = None
        try:
            page = await self.browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=1.5
            )
            
            await page.set_content(
                html_content,
                wait_until="domcontentloaded",
                timeout=timeout_ms
            )

            await page.wait_for_timeout(250)

            png_bytes = await page.screenshot(
                type="png",
                full_page=False
            )
            return png_bytes

        except Exception as e:
            logger.debug(f"[ScreenshotService] Snapshot capture skipped or timed out: {e}")
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass

    async def stop(self):
        try:
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception:
            pass
        self.is_available = False

screenshot_service = ScreenshotService()