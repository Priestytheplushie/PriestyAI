import asyncio
import shutil
import time
import logging
from typing import Dict, Any

logger = logging.getLogger("PriestyAI.CodeTools")

DOCKER_IMAGES = {
    "python": "python:3.11-slim",
    "py": "python:3.11-slim",
    "javascript": "node:20-slim",
    "js": "node:20-slim",
    "bash": "debian:stable-slim",
    "sh": "debian:stable-slim"
}

EXECUTION_COMMANDS = {
    "python": ["python", "-c"],
    "py": ["python", "-c"],
    "javascript": ["node", "-e"],
    "js": ["node", "-e"],
    "bash": ["bash", "-c"],
    "sh": ["bash", "-c"]
}

async def execute_code(language: str, code: str, timeout_seconds: int = 12) -> Dict[str, Any]:
    lang = language.lower().strip()
    has_docker = shutil.which("docker") is not None
    start_time = time.time()

    if has_docker and lang in DOCKER_IMAGES:
        image = DOCKER_IMAGES[lang]
        cmd_args = EXECUTION_COMMANDS.get(lang, ["python", "-c"])

        docker_cmd = [
            "docker", "run", "--rm",
            "-i",
            "--memory=256m",
            "--cpus=1.0",
            "--pids-limit=64",
            image
        ] + cmd_args + [code]

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            elapsed = time.time() - start_time
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")

            return {
                "status": "success" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": stdout.strip(),
                "stderr": stderr.strip(),
                "execution_time": f"{elapsed:.2f}s",
                "runner": "docker"
            }
        except asyncio.TimeoutError:
            return {"status": "error", "error": f"Code execution timed out after {timeout_seconds}s.", "runner": "docker"}
        except Exception as e:
            logger.warning(f"Docker runner failed ({e}), attempting local fallback...")

    if lang in ("python", "py"):
        try:
            proc = await asyncio.create_subprocess_exec(
                "python", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
            elapsed = time.time() - start_time

            return {
                "status": "success" if proc.returncode == 0 else "error",
                "exit_code": proc.returncode,
                "stdout": stdout_b.decode("utf-8", errors="replace").strip(),
                "stderr": stderr_b.decode("utf-8", errors="replace").strip(),
                "execution_time": f"{elapsed:.2f}s",
                "runner": "local_fallback"
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "runner": "local_fallback"}

    return {
        "status": "error",
        "error": f"Execution for '{language}' is not supported without Docker.",
        "runner": "none"
    }