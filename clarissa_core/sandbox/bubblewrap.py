"""Bubblewrap sandbox for Clarissa's code execution.

Bubblewrap (bwrap) provides lightweight Linux sandboxing through
namespaces and bind mounts. It's faster than Docker and doesn't
require a daemon.

v0.8 Feature: Bubblewrap sandbox for lighter, faster code execution
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SandboxResult:
    """Result of a sandbox execution."""

    success: bool
    output: str
    error: str | None = None
    exit_code: int = 0


class BubblewrapSandbox:
    """Single persistent bubblewrap sandbox for code/shell/claude execution.

    Unlike Docker (one container per execution), this maintains a single
    sandboxed environment that persists across calls. Files written in
    one execution are available in subsequent executions.

    Bubblewrap uses Linux namespaces to create an isolated environment:
    - Mount namespace: Only whitelisted paths are visible
    - Network namespace: Can optionally isolate network
    - PID namespace: Processes can't see host processes
    - User namespace: Runs as non-root inside sandbox

    Note: Bubblewrap is Linux-only. On macOS, this class falls back to
    direct execution with limited sandboxing.
    """

    def __init__(
        self,
        work_dir: str | Path | None = None,
        timeout: int = 30,
        allow_network: bool = True,
    ):
        """Initialize the sandbox.

        Args:
            work_dir: Directory for sandbox workspace. Defaults to ~/.clarissa/sandbox
            timeout: Default execution timeout in seconds
            allow_network: Whether to allow network access in sandbox
        """
        self.work_dir = Path(work_dir or "~/.clarissa/sandbox").expanduser()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.allow_network = allow_network
        self._bwrap_available = self._check_bwrap()

        if self._bwrap_available:
            logger.info(f"[sandbox] Bubblewrap sandbox initialized at {self.work_dir}")
        else:
            logger.warning("[sandbox] Bubblewrap not available, using direct execution")

    def _check_bwrap(self) -> bool:
        """Check if bubblewrap is available."""
        return shutil.which("bwrap") is not None

    def _bwrap_command(self, cmd: list[str]) -> list[str]:
        """Wrap command in bubblewrap with appropriate isolation.

        Args:
            cmd: Command to execute

        Returns:
            Full bwrap command with isolation flags
        """
        bwrap_args = [
            "bwrap",
            # Read-only bind mounts for system directories
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/bin",
            "/bin",
            # DNS resolution
            "--ro-bind",
            "/etc/resolv.conf",
            "/etc/resolv.conf",
            # Writable workspace
            "--bind",
            str(self.work_dir),
            "/workspace",
            "--chdir",
            "/workspace",
            # Namespace isolation
            "--unshare-all",
            # Process management
            "--die-with-parent",
            "--new-session",
        ]

        # Handle /lib64 if it exists (common on 64-bit Linux)
        if Path("/lib64").exists():
            bwrap_args.extend(["--ro-bind", "/lib64", "/lib64"])

        # Allow network if configured
        if self.allow_network:
            bwrap_args.append("--share-net")

        # Add the actual command
        bwrap_args.extend(cmd)

        return bwrap_args

    async def execute_python(
        self,
        code: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Execute Python code in sandbox.

        Args:
            code: Python code to execute
            timeout: Execution timeout in seconds (defaults to self.timeout)

        Returns:
            SandboxResult with output and status
        """
        timeout = timeout or self.timeout

        # Write code to script file
        script_path = self.work_dir / "script.py"
        script_path.write_text(code)

        if self._bwrap_available:
            cmd = self._bwrap_command(["python3", "/workspace/script.py"])
        else:
            # Fallback: direct execution
            cmd = ["python3", str(script_path)]

        return await self._execute_command(cmd, timeout)

    async def execute_shell(
        self,
        command: str,
        timeout: int | None = None,
    ) -> SandboxResult:
        """Execute shell command in sandbox.

        Args:
            command: Shell command to execute
            timeout: Execution timeout in seconds (defaults to self.timeout)

        Returns:
            SandboxResult with output and status
        """
        timeout = timeout or self.timeout

        if self._bwrap_available:
            cmd = self._bwrap_command(["sh", "-c", command])
        else:
            # Fallback: direct execution in workspace
            cmd = ["sh", "-c", f"cd {self.work_dir} && {command}"]

        return await self._execute_command(cmd, timeout)

    async def install_package(
        self,
        package: str,
        timeout: int = 120,
    ) -> SandboxResult:
        """Install Python package in sandbox.

        Args:
            package: Package name to install
            timeout: Installation timeout in seconds

        Returns:
            SandboxResult with output and status
        """
        if self._bwrap_available:
            cmd = self._bwrap_command(["pip", "install", "--user", package])
        else:
            cmd = ["pip", "install", "--user", package]

        return await self._execute_command(cmd, timeout)

    async def _execute_command(
        self,
        cmd: list[str],
        timeout: int,
    ) -> SandboxResult:
        """Execute a command with timeout.

        Args:
            cmd: Command to execute
            timeout: Timeout in seconds

        Returns:
            SandboxResult with output and status
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
                exit_code = process.returncode or 0

                output = stdout.decode("utf-8", errors="replace")
                error_output = stderr.decode("utf-8", errors="replace")

                if exit_code == 0:
                    return SandboxResult(
                        success=True,
                        output=output,
                        exit_code=exit_code,
                    )
                else:
                    return SandboxResult(
                        success=False,
                        output=output,
                        error=error_output,
                        exit_code=exit_code,
                    )

            except TimeoutError:
                process.kill()
                await process.wait()
                return SandboxResult(
                    success=False,
                    output="",
                    error=f"Execution timed out after {timeout}s",
                    exit_code=-1,
                )

        except Exception as e:
            logger.exception(f"[sandbox] Execution error: {e}")
            return SandboxResult(
                success=False,
                output="",
                error=str(e),
                exit_code=-1,
            )

    def read_file(self, path: str) -> str:
        """Read file from sandbox workspace.

        Args:
            path: Relative path within workspace

        Returns:
            File contents as string
        """
        file_path = self.work_dir / path
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path.read_text()

    def write_file(self, path: str, content: str) -> None:
        """Write file to sandbox workspace.

        Args:
            path: Relative path within workspace
            content: File content to write
        """
        file_path = self.work_dir / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content)

    def list_files(self, path: str = ".") -> list[str]:
        """List files in sandbox workspace.

        Args:
            path: Relative path within workspace

        Returns:
            List of file/directory names
        """
        target = self.work_dir / path
        if not target.exists():
            return []
        return [f.name for f in target.iterdir()]

    def delete_file(self, path: str) -> bool:
        """Delete file from sandbox workspace.

        Args:
            path: Relative path within workspace

        Returns:
            True if deleted, False if not found
        """
        file_path = self.work_dir / path
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    def clean_workspace(self) -> None:
        """Remove all files from workspace."""
        for item in self.work_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)

    @property
    def is_available(self) -> bool:
        """Check if sandboxing is available.

        Returns True even on non-Linux systems (fallback to direct execution).
        """
        return True

    @property
    def is_secure(self) -> bool:
        """Check if full sandboxing is available.

        Returns False on non-Linux systems where only direct execution is available.
        """
        return self._bwrap_available
