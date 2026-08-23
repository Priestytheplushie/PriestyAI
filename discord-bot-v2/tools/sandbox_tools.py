import os
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
        "cmd": lambda pkgs: f"pip install --no-cache-dir {' '.join(pkgs)} && python main.py" if pkgs else "python main.py"
    },
    "py": {
        "image": "python:3.11-slim",
        "file": "main.py",
        "cmd": lambda pkgs: f"pip install --no-cache-dir {' '.join(pkgs)} && python main.py" if pkgs else "python main.py"
    },
    "javascript": {
        "image": "node:20-alpine",
        "file": "main.js",
        "cmd": lambda pkgs: f"npm install --no-audit {' '.join(pkgs)} && node main.js" if pkgs else "node main.js"
    },
    "js": {
        "image": "node:20-alpine",
        "file": "main.js",
        "cmd": lambda pkgs: f"npm install --no-audit {' '.join(pkgs)} && node main.js" if pkgs else "node main.js"
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
        "cmd": lambda pkgs: f"apk add --no-cache {' '.join(pkgs)} && sh main.sh" if pkgs else "sh main.sh"
    },
    "sh": {
        "image": "alpine:latest",
        "file": "main.sh",
        "cmd": lambda pkgs: f"apk add --no-cache {' '.join(pkgs)} && sh main.sh" if pkgs else "sh main.sh"
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

async def _run_in_docker(
    lang_key: str,
    code: str,
    packages: list[str],
    workspace_dir: str,
    timeout: float = 25.0
) -> dict[str, Any]:
    cfg = LANGUAGE_CONFIG[lang_key]
    script_path = os.path.join(workspace_dir, cfg["file"])

    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)

    exec_cmd = cfg["cmd"](packages)
    container_name = f"priesty_sandbox_{int(time.time() * 1000)}"

    docker_args = [
        "docker", "run",
        "--name", container_name,
        "--rm",
        "--memory=512m",
        "--memory-swap=512m",
        "--cpus=1.0",
        "--pids-limit=100",
        "-v", f"{workspace_dir}:/workspace",
        "-w", "/workspace",
        cfg["image"],
        "sh", "-c", exec_cmd
    ]

    logger.info(f"[Sandbox Docker Exec] Command: {exec_cmd}")
    start_time = time.perf_counter()

    try:
        proc = await asyncio.create_subprocess_exec(
            *docker_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        duration_ms = int((time.perf_counter() - start_time) * 1000)

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout or "(No stdout produced)",
            "stderr": stderr or None,
            "execution_time_ms": duration_ms,
            "installed_packages": packages
        }

    except asyncio.TimeoutError:
        try:
            kill_proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", container_name)
            await kill_proc.communicate()
        except Exception:
            pass
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

def _detect_and_stage_artifacts(workspace_dir: str, context: ToolExecutionContext | None) -> list[str]:
    if not context:
        return []

    found_artifacts = []
    image_patterns = [
        os.path.join(workspace_dir, "*.png"),
        os.path.join(workspace_dir, "*.jpg"),
        os.path.join(workspace_dir, "*.jpeg")
    ]

    for pattern in image_patterns:
        for filepath in glob.glob(pattern):
            try:
                with open(filepath, "rb") as f:
                    context.staged_image_bytes = f.read()
                    context.staged_image_filename = os.path.basename(filepath)
                    found_artifacts.append(os.path.basename(filepath))
                    logger.info(f"[Sandbox] Auto-staged generated image artifact: '{os.path.basename(filepath)}' ({len(context.staged_image_bytes)} bytes)")
                    break
            except Exception as e:
                logger.warning(f"Failed to read image artifact: {e}")

    return found_artifacts

@tool_registry.register(
    name="execute_code",
    description=(
        "Executes arbitrary code securely inside an isolated Docker sandbox container. "
        "Supports 'python', 'javascript', 'typescript', 'bash', 'cpp', 'c', 'rust', and 'go'. "
        "Can specify 'packages' to install (e.g. ['numpy', 'matplotlib']). "
        "Any generated plots (e.g. plt.savefig('plot.png')) will automatically be attached to Discord."
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
    logger.info(f"[execute_code] Running {lang_clean} code ({len(code)} chars, normalized packages: {pkgs})")

    workspace = tempfile.mkdtemp(prefix="priesty_exec_")

    try:
        result = await _run_in_docker(
            lang_key=lang_clean,
            code=code,
            packages=pkgs,
            workspace_dir=workspace,
            timeout=25.0
        )

        artifacts = _detect_and_stage_artifacts(workspace, context)
        if artifacts:
            result["generated_artifacts"] = artifacts
            result["artifact_note"] = "Image artifact detected and staged for native Discord attachment."

        return result

    finally:
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:
            pass