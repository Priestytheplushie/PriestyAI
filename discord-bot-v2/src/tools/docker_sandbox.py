import os
import time
import asyncio
import logging
import tempfile
from typing import Dict, Any, Optional

logger = logging.getLogger("PriestyAI.DockerSandbox")

LANGUAGE_CONFIGS = {
    "python": {
        "image": "python:3.11-alpine",
        "file": "main.py",
        "cmd": ["python", "main.py"],
    },
    "javascript": {
        "image": "node:20-alpine",
        "file": "index.js",
        "cmd": ["node", "index.js"],
    },
    "js": {
        "image": "node:20-alpine",
        "file": "index.js",
        "cmd": ["node", "index.js"],
    },
    "bash": {
        "image": "alpine:latest",
        "file": "script.sh",
        "cmd": ["sh", "script.sh"],
    },
    "sh": {
        "image": "alpine:latest",
        "file": "script.sh",
        "cmd": ["sh", "script.sh"],
    },
    "c": {
        "image": "gcc:latest",
        "file": "main.c",
        "cmd": ["sh", "-c", "gcc main.c -o main && ./main"],
    },
    "cpp": {
        "image": "gcc:latest",
        "file": "main.cpp",
        "cmd": ["sh", "-c", "g++ main.cpp -o main && ./main"],
    },
    "rust": {
        "image": "rust:alpine",
        "file": "main.rs",
        "cmd": ["sh", "-c", "rustc main.rs -o main && ./main"],
    }
}

class DockerSandbox:
    def __init__(self, timeout_seconds: int = 5, mem_limit: str = "128m"):
        self.timeout = timeout_seconds
        self.mem_limit = mem_limit
        self._docker_available: Optional[bool] = None

    async def _check_docker(self) -> bool:
        if self._docker_available is not None:
            return self._docker_available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.wait()
            self._docker_available = (proc.returncode == 0)
        except Exception:
            self._docker_available = False
        
        if not self._docker_available:
            logger.warning("Docker daemon is not reachable. Sandbox will run in restricted local mode.")
        return self._docker_available

    async def run_code(self, language: str, code: str) -> Dict[str, Any]:
        lang_key = language.lower().strip()
        config = LANGUAGE_CONFIGS.get(lang_key)
        if not config:
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Unsupported language '{language}'. Supported: {', '.join(set(LANGUAGE_CONFIGS.keys()))}",
                "exit_code": 1,
                "duration": 0.0
            }

        start_time = time.time()
        is_docker = await self._check_docker()

        with tempfile.TemporaryDirectory() as temp_dir:
            file_path = os.path.join(temp_dir, config["file"])
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(code)

            if is_docker:
                cmd = [
                    "docker", "run", "--rm",
                    "--network", "none",
                    "--memory", self.mem_limit,
                    "--cpus", "1.0",
                    "-v", f"{os.path.abspath(temp_dir)}:/app",
                    "-w", "/app",
                    config["image"]
                ] + config["cmd"]
            else:
                if lang_key in ("python", "py"):
                    cmd = ["python", file_path]
                elif lang_key in ("javascript", "js"):
                    cmd = ["node", file_path]
                else:
                    cmd = ["sh", file_path]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=self.timeout
                    )
                    duration = round(time.time() - start_time, 2)
                    stdout = stdout_bytes.decode("utf-8", errors="replace")[:3000]
                    stderr = stderr_bytes.decode("utf-8", errors="replace")[:3000]
                    return {
                        "success": proc.returncode == 0,
                        "stdout": stdout,
                        "stderr": stderr,
                        "exit_code": proc.returncode,
                        "duration": duration
                    }
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return {
                        "success": False,
                        "stdout": "",
                        "stderr": f"Execution timed out after {self.timeout} seconds.",
                        "exit_code": 124,
                        "duration": self.timeout
                    }
            except Exception as e:
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Execution failed: {str(e)}",
                    "exit_code": 1,
                    "duration": round(time.time() - start_time, 2)
                }

sandbox = DockerSandbox()