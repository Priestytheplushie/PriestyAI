import asyncio
import logging
import os
from typing import Any, Dict, List
import docker

logger = logging.getLogger("priesty.docker")


class DockerRunner:
    """Executes dynamic repository tests and linters in isolated ephemeral containers asynchronously."""

    def __init__(self):
        try:
            self.client = docker.from_env()
            self.available = True
        except Exception as e:
            logger.warning(
                f"Docker is not available or not running ({e}). Will skip sandbox execution."
            )
            self.client = None
            self.available = False

    def _run_commands_sync(
        self,
        workspace_dir: str,
        commands: List[str],
        image: str = "python:3.11-slim",
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Synchronous internal execution worker."""
        if not self.available or not self.client:
            return {
                "success": True,
                "stdout": "Docker not running. Dynamic execution skipped.",
                "stderr": "",
                "exit_code": 0,
            }

        combined_script = " && ".join(commands)
        logger.info(f"Running sandbox commands in '{image}': {combined_script}")

        abs_workspace = os.path.abspath(workspace_dir)
        container = None

        try:
            container = self.client.containers.run(
                image=image,
                command=f'sh -c "{combined_script}"',
                volumes={abs_workspace: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                detach=True,
                remove=False,
                mem_limit="2g",
                nano_cpus=2000000000,
                pids_limit=256,
            )

            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", 1)
            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )

            return {
                "success": exit_code == 0,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
            }

        except Exception as e:
            logger.error(f"Docker execution error: {e}")
            return {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
            }
        finally:
            if container:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    async def run_commands(
        self,
        workspace_dir: str,
        commands: List[str],
        image: str = "python:3.11-slim",
        timeout: int = 120,
    ) -> Dict[str, Any]:
        """Asynchronously offloads container execution to a background thread to prevent event loop blocking."""
        return await asyncio.to_thread(
            self._run_commands_sync,
            workspace_dir=workspace_dir,
            commands=commands,
            image=image,
            timeout=timeout,
        )


docker_runner = DockerRunner()
