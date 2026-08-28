import os
import sys
import json
import time
import base64
import shutil
import asyncio
import re
import logging
from collections import defaultdict
from typing import Any
import httpx
from aiohttp import web
from core.branch_manager import branch_manager
from config.settings import AGENT_WORKSPACES_ROOT

logger = logging.getLogger("PriestyAI.PlaygroundServer")

PLAYGROUND_PORT = 8085

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(BASE_DIR, "web", "playground.html")

class PlaygroundServer:
    def __init__(self, port: int = PLAYGROUND_PORT):
        self.port = port
        self.public_url: str | None = None
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.tunnel_proc: asyncio.subprocess.Process | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/p/{artifact_id}", self.handle_playground_page)
        self.app.router.add_get("/p/{artifact_id}/{filename:.*}", self.handle_artifact_subfile)
        self.app.router.add_get("/raw/{artifact_id}", self.handle_raw_artifact)
        self.app.router.add_get("/api/artifact/{artifact_id}/versions", self.handle_artifact_versions_api)
        self.app.router.add_get("/ws/terminal/{artifact_id}", self.handle_terminal_ws)
        self.app.router.add_get("/favicon.ico", self.handle_favicon)

    def get_artifact_url(self, artifact_id: str, version: int = 1) -> str | None:
        if not self.public_url:
            return None
        return f"{self.public_url}/p/{artifact_id}?v={version}"

    async def handle_favicon(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    def _load_html_template(self) -> str:
        try:
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            logger.error(f"[PlaygroundServer] Failed to read template from {TEMPLATE_PATH}: {e}")
            return "<h1>PriestyAI Playground Template Missing</h1>"


    async def handle_terminal_ws(self, request: web.Request) -> web.WebSocketResponse:
        artifact_id = request.match_info.get("artifact_id", "")
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        workspace_dir = os.path.join(AGENT_WORKSPACES_ROOT, f"term_{artifact_id}")
        os.makedirs(workspace_dir, exist_ok=True)

        art_data = branch_manager.get_artifact(artifact_id)
        if art_data:
            versions = art_data.get("versions", [])
            v_data = versions[-1] if versions else {}
            files = v_data.get("files", [])
            for f in files:
                f_path = os.path.join(workspace_dir, f.get("filename", "script.py"))
                os.makedirs(os.path.dirname(f_path), exist_ok=True)
                with open(f_path, "w", encoding="utf-8", errors="replace") as out_f:
                    out_f.write(f.get("content", ""))

        container_name = f"priesty_term_{artifact_id}"
        has_docker = shutil.which("docker") is not None

        if has_docker:
            inspect_p = await asyncio.create_subprocess_exec(
                "docker", "inspect", "-f", "{{.State.Running}}", container_name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out_i, _ = await inspect_p.communicate()
            if inspect_p.returncode != 0 or "true" not in out_i.decode().lower():
                await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                await asyncio.create_subprocess_exec(
                    "docker", "run", "-d", "--name", container_name,
                    "--memory=512m", "--cpus=1.0", "--pids-limit=100",
                    "-v", f"{workspace_dir}:/workspace",
                    "-w", "/workspace",
                    "python:3.11-slim", "tail", "-f", "/dev/null",
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                )

        discord_banner = (
            "\r\n\x1b[38;2;88;101;242m●\x1b[0m \x1b[1;37mPriestyAI Terminal Sandbox\x1b[0m "
            "\x1b[38;2;148;155;164m(Python 3.11 • Node • Pip)\x1b[0m\r\n"
            "\x1b[38;2;148;155;164mType commands below or click ▶ Run to execute.\x1b[0m\r\n\r\n"
        )
        await ws.send_str(discord_banner)

        cwd_disp = "/workspace"
        prompt_str = (
            "\x1b[38;2;88;101;242mpriesty\x1b[0m"
            "\x1b[38;2;148;155;164m@\x1b[0m"
            "\x1b[38;2;35;165;90msandbox\x1b[0m"
            "\x1b[38;2;148;155;164m:\x1b[0m"
            f"\x1b[38;2;240;178;50m{cwd_disp}\x1b[0m"
            "\x1b[38;2;148;155;164m$ \x1b[0m"
        )
        await ws.send_str(prompt_str)

        cmd_buf = []

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    for ch in msg.data:
                        if ch in ("\r", "\n"):
                            full_cmd = "".join(cmd_buf).strip()
                            cmd_buf = []
                            await ws.send_str("\r\n")

                            if full_cmd:
                                if full_cmd in ("clear", "cls"):
                                    await ws.send_str("\x1b[2J\x1b[H")
                                else:
                                    if has_docker:
                                        exec_cmd = ["docker", "exec", "-w", "/workspace", container_name, "sh", "-c", full_cmd]
                                        run_cwd = None
                                    else:
                                        if sys.platform == "win32":
                                            exec_cmd = ["cmd.exe", "/c", full_cmd]
                                        else:
                                            exec_cmd = ["sh", "-c", full_cmd]
                                        run_cwd = workspace_dir

                                    try:
                                        proc = await asyncio.create_subprocess_exec(
                                            *exec_cmd,
                                            stdout=asyncio.subprocess.PIPE,
                                            stderr=asyncio.subprocess.STDOUT,
                                            cwd=run_cwd
                                        )
                                        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
                                        output_text = stdout_bytes.decode("utf-8", errors="replace")
                                        formatted_out = output_text.replace("\n", "\r\n")
                                        if formatted_out:
                                            await ws.send_str(formatted_out + ("\r\n" if not formatted_out.endswith("\r\n") else ""))
                                    except asyncio.TimeoutError:
                                        await ws.send_str("\x1b[38;2;242;63;67m[Command timed out after 45s]\x1b[0m\r\n")
                                    except Exception as e:
                                        await ws.send_str(f"\x1b[38;2;242;63;67mError: {e}\x1b[0m\r\n")

                            await ws.send_str(prompt_str)

                        elif ch in ("\x7f", "\x08"):
                            if cmd_buf:
                                cmd_buf.pop()
                                await ws.send_str("\b \b")
                        elif ch == "\x03":
                            cmd_buf = []
                            await ws.send_str(f"^C\r\n{prompt_str}")
                        elif len(ch) == 1 and ord(ch) >= 32:
                            cmd_buf.append(ch)
                            await ws.send_str(ch)

        except Exception as ex:
            logger.debug(f"[Terminal WS Error] {ex}")
        finally:
            if has_docker:
                try:
                    await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                except Exception:
                    pass

        return ws


    async def handle_artifact_versions_api(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.json_response({"error": "Artifact not found"}, status=404)

        versions = art_data.get("versions", [])
        return web.json_response({
            "artifact_id": artifact_id,
            "filename": art_data.get("filename", "artifact.txt"),
            "title": art_data.get("title", "Artifact"),
            "active_version": art_data.get("active_version", len(versions)),
            "latest_version": len(versions),
            "total_versions": len(versions),
            "versions": versions
        })

    async def handle_artifact_subfile(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        req_filename = request.match_info.get("filename", "")
        v_param = request.query.get("v", "latest")

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Artifact not found", status=404)

        versions = art_data.get("versions", [])
        total_v = len(versions)
        target_v = int(v_param) if (v_param.isdigit() and 1 <= int(v_param) <= total_v) else total_v
        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})

        files = target_v_data.get("files", [])
        clean_req = req_filename.strip().lower()

        for f in files:
            f_name = f.get("filename", "")
            if f_name.strip().lower() == clean_req or f_name.strip().lower().endswith("/" + clean_req):
                content = f.get("content", "")
                content_type = "text/plain"
                if clean_req.endswith(".js"):
                    content_type = "application/javascript"
                elif clean_req.endswith(".css"):
                    content_type = "text/css"
                elif clean_req.endswith(".html") or clean_req.endswith(".htm"):
                    content_type = "text/html"
                elif clean_req.endswith(".json"):
                    content_type = "application/json"
                elif clean_req.endswith(".svg"):
                    content_type = "image/svg+xml"

                return web.Response(text=content, content_type=content_type)

        return web.Response(text=f"File '{req_filename}' not found in artifact", status=404)

    async def handle_playground_page(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        v_param = request.query.get("v", "latest")

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Artifact not found or expired.", status=404)

        versions = art_data.get("versions", [])
        total_v = len(versions)
        
        if v_param == "latest" or not v_param.isdigit():
            target_v = total_v if total_v > 0 else 1
        else:
            target_v = int(v_param)

        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})

        filename = art_data.get("filename", "artifact.txt")
        files = target_v_data.get("files", [])

        if not files:
            content = target_v_data.get("content", "")
            files = [{"filename": filename, "content": content}]

        is_previewable = any(f.get("filename", "").lower().endswith((".html", ".htm", ".svg", ".md", ".markdown")) for f in files)

        adds = target_v_data.get("additions", 0)
        dels = target_v_data.get("deletions", 0)
        diff_badge = f"(+{adds} -{dels})" if (adds > 0 or dels > 0) else ""

        raw_files_b64 = base64.b64encode(json.dumps(files).encode("utf-8")).decode("utf-8")

        html = self._load_html_template()
        html = html.replace("{{ARTIFACT_ID}}", artifact_id)
        html = html.replace("{{FILENAME}}", filename)
        html = html.replace("{{VERSION}}", str(target_v))
        html = html.replace("{{DIFF_BADGE}}", diff_badge)
        html = html.replace("{{IS_PREVIEWABLE_BOOL}}", "true" if is_previewable else "false")
        html = html.replace("{{RAW_FILES_B64}}", raw_files_b64)
        
        has_multiple_files = len(files) > 1 or filename.endswith(".zip")
        html = html.replace("{{TABS_BAR_DISPLAY}}", "flex" if has_multiple_files else "none")
        html = html.replace("{{CENTER_CONTROLS_DISPLAY}}", "flex" if is_previewable else "none")
        html = html.replace("{{RIGHT_PANE_DISPLAY}}", "flex" if is_previewable else "none")
        html = html.replace("{{DIVIDER_DISPLAY}}", "block" if is_previewable else "none")

        return web.Response(text=html, content_type="text/html")

    async def handle_raw_artifact(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        v_param = request.query.get("v", "1")
        target_v = int(v_param) if v_param.isdigit() else 1

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Not found", status=404)

        versions = art_data.get("versions", [])
        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})
        content = target_v_data.get("content", "")

        return web.Response(text=content, content_type="text/plain")

    async def start(self):
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
            await self.site.start()
            logger.info(f"[Playground Server] Local HTTP server listening on http://127.0.0.1:{self.port}")
            
            self._watchdog_task = asyncio.create_task(self._start_tunnel_watchdog())
        except Exception as e:
            logger.warning(f"[Playground Server] Failed to start local HTTP server: {e}")

    async def _start_tunnel_watchdog(self):
        while True:
            try:
                await self._start_tunnel()
                if self.tunnel_proc:
                    await self.tunnel_proc.wait()
                logger.warning("[Playground Server] Cloudflare Tunnel disconnected. Reconnecting in 3s...")
                await asyncio.sleep(3.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Playground Server] Tunnel watchdog error: {e}")
                await asyncio.sleep(5.0)

    async def _start_tunnel(self):
        has_native = shutil.which("cloudflared") is not None
        has_docker = shutil.which("docker") is not None

        if not has_native and not has_docker:
            logger.warning("[Playground Server] Neither 'cloudflared' nor 'docker' found. Live tunnel disabled.")
            return

        cmd = []
        if has_native:
            logger.info("[Playground Server] Starting tunnel using native cloudflared binary...")
            cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{self.port}"]
        else:
            logger.info("[Playground Server] Cleaning old tunnel containers and starting Docker cloudflared...")
            try:
                cleanup_proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", "priesty_cf_tunnel",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await cleanup_proc.communicate()
            except Exception:
                pass

            target_host = "http://host.docker.internal" if sys.platform in ("win32", "darwin") else "http://127.0.0.1"
            cmd = [
                "docker", "run", "--name", "priesty_cf_tunnel", "--rm",
                "cloudflare/cloudflared:latest",
                "tunnel", "--url", f"{target_host}:{self.port}"
            ]

        try:
            self.tunnel_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            while True:
                line_bytes = await self.tunnel_proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace")
                
                match = re.search(r'https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if match:
                    self.public_url = match.group(0)
                    logger.info(f"✨ [Playground Server] Live Tunnel Online: {self.public_url}")
                    break

            async def drain():
                try:
                    while True:
                        line = await self.tunnel_proc.stdout.readline()
                        if not line:
                            break
                except Exception:
                    pass

            asyncio.create_task(drain())

        except Exception as e:
            logger.warning(f"[Playground Server] Cloudflare Tunnel failed: {e}")

    async def stop(self):
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
            except Exception:
                pass
        if shutil.which("docker"):
            try:
                kill_proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", "priesty_cf_tunnel")
                await kill_proc.communicate()
            except Exception:
                pass
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

playground_server = PlaygroundServer()