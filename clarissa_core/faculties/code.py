"""Code execution faculty for Clarissa.

Wraps the existing tools/docker_sandbox.py tools with intent-based orchestration.
In the future, this will use Bubblewrap sandbox instead of Docker.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clarissa_core.faculty import Faculty, FacultyResult

logger = logging.getLogger(__name__)


class CodeFaculty(Faculty):
    """Code execution orchestrator.

    Wraps the existing Docker sandbox tools with intent-based execution.
    Clarissa can say "run this Python code" and this faculty will
    figure out which tool(s) to call.

    Future: Will transition to Bubblewrap sandbox for lighter execution.
    """

    name = "code"
    description = "Code execution and file operations in sandbox (Python, shell, files)"
    available_actions = [
        "execute_python",
        "run_shell",
        "install_package",
        "read_file",
        "write_file",
        "list_files",
        "unzip_file",
    ]

    def __init__(self):
        self._configured = self._check_configuration()

    def _check_configuration(self) -> bool:
        """Check if sandbox is available."""
        try:
            from tools.docker_sandbox import is_available

            return is_available()
        except Exception:
            return False

    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute code operations based on intent."""
        if not self._configured:
            return FacultyResult(
                success=False,
                summary="Code sandbox is not available. Docker may not be running.",
                error="Sandbox not available",
            )

        logger.info(f"[faculty:code] Intent: {intent}")

        try:
            # Parse intent to determine action
            action, params = self._parse_intent(intent, constraints)
            logger.info(f"[faculty:code] Action: {action}, Params: {params}")

            # Execute the action
            result = await self._execute_action(action, params, user_id, channel_id)
            return result

        except Exception as e:
            logger.exception(f"[faculty:code] Error: {e}")
            return FacultyResult(
                success=False,
                summary=f"Code execution failed: {str(e)}",
                error=str(e),
            )

    def _parse_intent(
        self, intent: str, constraints: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Parse natural language intent into action and parameters."""
        intent_lower = intent.lower()

        # Execute Python code
        if any(
            x in intent_lower for x in ["run python", "execute python", "python code"]
        ):
            code = self._extract_code(intent)
            return "execute_python", {"code": code}

        # Run shell command
        if any(
            x in intent_lower
            for x in ["run shell", "execute shell", "run command", "shell command"]
        ):
            command = self._extract_command(intent)
            return "run_shell", {"command": command}

        # Install package
        if any(
            x in intent_lower for x in ["install package", "pip install", "install"]
        ):
            package = self._extract_package(intent)
            return "install_package", {"package": package}

        # Read file
        if any(
            x in intent_lower
            for x in ["read file", "show file", "cat file", "get file"]
        ):
            path = self._extract_path(intent)
            return "read_file", {"path": path}

        # Write file
        if any(x in intent_lower for x in ["write file", "save file", "create file"]):
            path = self._extract_path(intent)
            content = self._extract_content(intent)
            return "write_file", {"path": path, "content": content}

        # List files
        if any(
            x in intent_lower for x in ["list files", "show files", "ls", "directory"]
        ):
            path = self._extract_path(intent) or "."
            return "list_files", {"path": path}

        # Unzip file
        if any(x in intent_lower for x in ["unzip", "extract", "uncompress"]):
            path = self._extract_path(intent)
            return "unzip_file", {"path": path}

        # Default: try to detect code blocks
        code = self._extract_code(intent)
        if code:
            return "execute_python", {"code": code}

        # Fallback: treat as shell command
        return "run_shell", {"command": intent}

    async def _execute_action(
        self,
        action: str,
        params: dict[str, Any],
        user_id: str | None,
        channel_id: str | None,
    ) -> FacultyResult:
        """Execute a code action via the underlying tools."""
        from tools._base import ToolContext

        # Create a context for tool execution
        ctx = ToolContext(user_id=user_id or "faculty", channel_id=channel_id)

        # Import the Docker sandbox tools module
        from tools import docker_sandbox as sandbox

        try:
            if action == "execute_python":
                result = await sandbox.execute_python(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Python execution result:\n```\n{result[:1500]}\n```",
                    data={"output": result},
                )

            elif action == "run_shell":
                result = await sandbox.run_shell(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Shell output:\n```\n{result[:1500]}\n```",
                    data={"output": result},
                )

            elif action == "install_package":
                result = await sandbox.install_package(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Package installation:\n{result}",
                    data={"output": result},
                )

            elif action == "read_file":
                result = await sandbox.read_file(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"File contents:\n```\n{result[:1500]}\n```",
                    data={"contents": result},
                )

            elif action == "write_file":
                result = await sandbox.write_file(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"File written: {result}",
                    data={"result": result},
                )

            elif action == "list_files":
                result = await sandbox.list_files(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Files:\n{result}",
                    data={"files": result},
                )

            elif action == "unzip_file":
                result = await sandbox.unzip_file(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Unzip result:\n{result}",
                    data={"result": result},
                )

            else:
                return FacultyResult(
                    success=False,
                    summary=f"Unknown code action: {action}",
                    error=f"Action '{action}' not implemented",
                )

        except Exception as e:
            return FacultyResult(
                success=False,
                summary=f"Sandbox error: {str(e)}",
                error=str(e),
            )

    # Helper methods for intent parsing
    def _extract_code(self, intent: str) -> str:
        """Extract code from intent (typically in code blocks)."""
        # Look for code blocks
        match = re.search(r"```(?:python)?\n?(.*?)```", intent, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Look for inline code
        match = re.search(r"`([^`]+)`", intent)
        if match:
            return match.group(1)
        return ""

    def _extract_command(self, intent: str) -> str:
        """Extract shell command from intent."""
        # Look for command in code blocks
        match = re.search(r"```(?:bash|sh|shell)?\n?(.*?)```", intent, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Look for command after keywords
        match = re.search(
            r"(?:run|execute)\s+[\"']?(.+?)[\"']?$", intent, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_package(self, intent: str) -> str:
        """Extract package name from intent."""
        # Look for package name after install keywords
        match = re.search(
            r"install\s+(?:package\s+)?[\"']?(\S+)[\"']?", intent, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_path(self, intent: str) -> str:
        """Extract file path from intent."""
        # Look for path-like strings
        match = re.search(
            r"(?:path|file)[:\s]+[\"']?(\S+)[\"']?", intent, re.IGNORECASE
        )
        if match:
            return match.group(1)
        # Look for anything that looks like a path
        match = re.search(r"[\"']?(\S+\.\w+)[\"']?", intent)
        if match:
            return match.group(1)
        return ""

    def _extract_content(self, intent: str) -> str:
        """Extract file content from intent."""
        # Look for content in code blocks
        match = re.search(r"```\n?(.*?)```", intent, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Look for content after keywords
        match = re.search(
            r"content[:\s]+[\"'](.+?)[\"']", intent, re.IGNORECASE | re.DOTALL
        )
        if match:
            return match.group(1)
        return ""
