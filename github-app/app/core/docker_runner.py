import asyncio
import logging
import os
from typing import Any, Dict, List
import docker

logger = logging.getLogger("priesty.docker")


class DockerRunner:

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

        try:
            container = self.client.containers.run(
                image=image,
                command=f'sh -c "{combined_script}"',
                volumes={abs_workspace: {"bind": "/workspace", "mode": "rw"}},
                working_dir="/workspace",
                detach=True,
                remove=False,
            )

            result = container.wait(timeout=timeout)
            exit_code = result.get("StatusCode", 1)
            stdout = container.logs(stdout=True, stderr=False).decode(
                "utf-8", errors="replace"
            )
            stderr = container.logs(stdout=False, stderr=True).decode(
                "utf-8", errors="replace"
            )
            container.remove(force=True)

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

    async def run_commands(
        self,
        workspace_dir: str,
        commands: List[str],
        image: str = "python:3.11-slim",
        timeout: int = 120,
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(
            self._run_commands_sync,
            workspace_dir=workspace_dir,
            commands=commands,
            image=image,
            timeout=timeout,
        )


docker_runner = DockerRunner()
