import os
import io
import tarfile
import glob
import json
import time
import shutil
import asyncio
import logging
import tempfile
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.Sandbox")

LANGUAGE_CONFIG = {
    "python": {
        "image": "python:3.11-slim",
        "file": "main.py",
        "cmd": lambda pkgs: f"pip install --no-cache-dir {' '.join(pkgs)} >/dev/null 2>&1 && python main.py" if pkgs else "python main.py"
    },
    "py": {
        "image": "python:3.11-slim",
        "file": "main.py",
        "cmd": lambda pkgs: f"pip install --no-cache-dir {' '.join(pkgs)} >/dev/null 2>&1 && python main.py" if pkgs else "python main.py"
    },
    "javascript": {
        "image": "node:20-alpine",
        "file": "main.js",
        "cmd": lambda pkgs: f"npm install --no-audit {' '.join(pkgs)} >/dev/null 2>&1 && node main.js" if pkgs else "node main.js"
    },
    "js": {
        "image": "node:20-alpine",
        "file": "main.js",
        "cmd": lambda pkgs: f"npm install --no-audit {' '.join(pkgs)} >/dev/null 2>&1 && node main.js" if pkgs else "node main.js"
    },
    "typescript": {
        "image": "node:20-alpine",
        "file": "main.ts",
        "cmd": lambda pkgs: "npx -y tsx main.ts"
    },
    "ts": {
        "image": "node:20-alpine",
        "file": "main.ts",
        "cmd": lambda pkgs: "npx -y tsx main.ts"
    },
    "bash": {
        "image": "alpine:latest",
        "file": "main.sh",
        "cmd": lambda pkgs: f"apk add --no-cache {' '.join(pkgs)} >/dev/null 2>&1 && sh main.sh" if pkgs else "sh main.sh"
    },
    "sh": {
        "image": "alpine:latest",
        "file": "main.sh",
        "cmd": lambda pkgs: f"apk add --no-cache {' '.join(pkgs)} >/dev/null 2>&1 && sh main.sh" if pkgs else "sh main.sh"
    },
    "cpp": {
        "image": "gcc:latest",
        "file": "main.cpp",
        "cmd": lambda pkgs: "g++ -O3 main.cpp -o app && ./app"
    },
    "c": {
        "image": "gcc:latest",
        "file": "main.c",
        "cmd": lambda pkgs: "gcc -O3 main.c -o app && ./app"
    },
    "rust": {
        "image": "rust:alpine",
        "file": "main.rs",
        "cmd": lambda pkgs: "rustc main.rs -o app && ./app"
    },
    "go": {
        "image": "golang:alpine",
        "file": "main.go",
        "cmd": lambda pkgs: "go run main.go"
    }
}

def normalize_packages(packages: Any) -> list[str]:
    if not packages:
        return []
    if isinstance(packages, str):
        cleaned = packages.replace("[", " ").replace("]", " ").replace("'", " ").replace('"', " ").replace(",", " ")
        return [p.strip() for p in cleaned.split() if p.strip()]
    if isinstance(packages, list):
        return [str(p).strip().strip("'\"") for p in packages if str(p).strip().strip("'\"")]
    return []

def _create_code_tar(filename: str, code: str) -> bytes:
    """Packs code into an in-memory tar archive for streaming to Docker."""
    tar_stream = io.BytesIO()
    code_bytes = code.encode("utf-8")

    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        tarinfo = tarfile.TarInfo(name=filename)
        tarinfo.size = len(code_bytes)
        tarinfo.mtime = int(time.time())
        tar.addfile(tarinfo, io.BytesIO(code_bytes))

    return tar_stream.getvalue()

async def _extract_artifacts_from_container(container_name: str, context: ToolExecutionContext | None) -> list[str]:
    """Retrieves generated plots/images directly from the container via docker cp without needing local volume mounts."""
    if not context:
        return []

    found_artifacts = []
    tmp_dir = tempfile.mkdtemp(prefix="priesty_art_")

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "cp", f"{container_name}:/workspace/.", tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        image_patterns = [
            os.path.join(tmp_dir, "*.png"),
            os.path.join(tmp_dir, "*.jpg"),
            os.path.join(tmp_dir, "*.jpeg")
        ]

        for pattern in image_patterns:
            for filepath in glob.glob(pattern):
                try:
                    with open(filepath, "rb") as f:
                        context.staged_image_bytes = f.read()
                        context.staged_image_filename = os.path.basename(filepath)
                        found_artifacts.append(os.path.basename(filepath))
                        logger.info(f"[Sandbox] Found plot artifact: '{os.path.basename(filepath)}'")
                        break
                except Exception as e:
                    logger.warning(f"Failed to read image artifact: {e}")

    except Exception as e:
        logger.warning(f"Error copying artifacts from container: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return found_artifacts

async def _run_in_docker(
    lang_key: str,
    code: str,
    packages: list[str],
    context: ToolExecutionContext | None = None,
    timeout: float = 25.0
) -> dict[str, Any]:
    cfg = LANGUAGE_CONFIG[lang_key]
    exec_cmd = cfg["cmd"](packages)
    filename = cfg["file"]
    container_name = f"priesty_sandbox_{int(time.time() * 1000)}"

    # Create container in background without mounting disk directories
    create_proc = await asyncio.create_subprocess_exec(
        "docker", "create",
        "-i",
        "--name", container_name,
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1.0",
        "--pids-limit=100",
        "-w", "/workspace",
        cfg["image"],
        "sh", "-c", exec_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await create_proc.communicate()

    if create_proc.returncode != 0:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "Failed to initialize sandbox container.",
            "execution_time_ms": 0,
            "installed_packages": packages
        }

    tar_bytes = _create_code_tar(filename, code)

    # Pipe code archive directly into container memory via docker cp
    cp_proc = await asyncio.create_subprocess_exec(
        "docker", "cp", "-", f"{container_name}:/workspace",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await cp_proc.communicate(input=tar_bytes)

    logger.info(f"[Sandbox Docker Exec] Command: {exec_cmd}")
    start_time = time.perf_counter()

    try:
        start_proc = await asyncio.create_subprocess_exec(
            "docker", "start", "-a", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(start_proc.communicate(), timeout=timeout)
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        # Extract generated image plots before removing container
        artifacts = await _extract_artifacts_from_container(container_name, context)

        result = {
            "success": start_proc.returncode == 0,
            "exit_code": start_proc.returncode,
            "stdout": stdout or "(No output produced)",
            "stderr": stderr or None,
            "execution_time_ms": duration_ms,
            "installed_packages": packages
        }

        if artifacts:
            result["generated_artifacts"] = artifacts

        return result

    except asyncio.TimeoutError:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Execution timed out after {timeout} seconds.",
            "execution_time_ms": int(timeout * 1000),
            "installed_packages": packages
        }

    except Exception as e:
        return {
            "success": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": f"Docker engine error: {str(e)}",
            "execution_time_ms": 0,
            "installed_packages": packages
        }

    finally:
        # Cleanup container asynchronously
        try:
            rm_proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name)
            await rm_proc.communicate()
        except Exception:
            pass

@tool_registry.register(
    name="execute_code",
    description=(
        "Executes code securely inside an isolated Docker sandbox container to test logic, verify regex, or compute results.\n"
        "Supports 'python', 'javascript', 'typescript', 'bash', 'cpp', 'c', 'rust', and 'go'.\n"
        "Can specify 'packages' to install (e.g. ['numpy', 'matplotlib']).\n"
        "Generated plots (e.g. plt.savefig('plot.png')) will automatically be attached to chat.\n"
        "IMPORTANT: This tool is strictly for running and testing code. It DOES NOT create downloadable files or scripts for the user. "
        "To provide a script, program, or file for the user, you MUST use create_artifact instead."
    )
)
async def execute_code(
    language: str,
    code: str,
    packages: Any = None,
    context: ToolExecutionContext = None
) -> dict[str, Any]:
    lang_clean = language.strip().lower()
    if lang_clean not in LANGUAGE_CONFIG:
        supported = ", ".join(list(LANGUAGE_CONFIG.keys())[:8])
        return {
            "error": f"Language '{language}' is not supported. Supported runtimes: {supported}"
        }

    pkgs = normalize_packages(packages)
    logger.info(f"[execute_code] Running {lang_clean} code ({len(code)} chars, packages: {pkgs})")

    return await _run_in_docker(
        lang_key=lang_clean,
        code=code,
        packages=pkgs,
        context=context,
        timeout=25.0
    )