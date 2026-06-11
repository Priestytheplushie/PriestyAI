
import os
import tempfile
import zipfile
import subprocess
import logging
import asyncio
from typing import Dict, Optional, Tuple

logger = logging.getLogger("SandboxExecutor")

class SandboxExecutor:
    def __init__(self, image_name: str = "discordfriend-sandbox"):
        self.image_name = image_name
        self.timeout_seconds = 10

    async def execute_code(self, files: Dict[str, str], run_command: str) -> Tuple[str, Optional[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Setting up secure sandbox workspace in temp folder: {temp_dir}")
            
            for filename, content in files.items():
                safe_name = os.path.basename(filename)
                file_path = os.path.join(temp_dir, safe_name)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                    
            docker_cmd = [
                "docker", "run", "--rm",
                "--network", "none",
                "--memory", "512m",
                "--cpus", "1.0",
                "-v", f"{temp_dir}:/workspace",
                self.image_name,
                "timeout", str(self.timeout_seconds), "bash", "-c", run_command
            ]
            
            logger.info(f"Dispatching execution command inside Docker: {run_command}")
            
            try:
                proc = await asyncio.create_subprocess_exec(
                    *docker_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                
                stdout, stderr = await proc.communicate()
                console_output = (stdout.decode("utf-8", errors="replace") + stderr.decode("utf-8", errors="replace")).strip()
            except Exception as run_err:
                logger.error(f"Docker execution failed: {run_err}")
                return f"[Sandbox Execution Error: {run_err}]", None

            if not console_output:
                console_output = "[Sandbox completed execution with no terminal outputs returned.]"

            zip_path = None
            generated_files = []
            
            for root, dirs, filenames in os.walk(temp_dir):
                for f_name in filenames:
                    generated_files.append(os.path.join(root, f_name))

            if len(generated_files) > 1:
                logger.info(f"Compiling {len(generated_files)} artifacts into a ZIP archive...")
                zip_fd, temp_zip_path = tempfile.mkstemp(suffix=".zip")
                os.close(zip_fd)
                
                try:
                    with zipfile.ZipFile(temp_zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        for file_to_zip in generated_files:
                            arc_name = os.path.relpath(file_to_zip, temp_dir)
                            zip_file.write(file_to_zip, arcname=arc_name)
                    zip_path = temp_zip_path
                except Exception as zip_err:
                    logger.error(f"Failed compiling ZIP package: {zip_err}")
                    if os.path.exists(temp_zip_path):
                        os.remove(temp_zip_path)
                    zip_path = None

            return console_output, zip_path