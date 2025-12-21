"""
E2B Code Interpreter integration for Clara.

Provides sandboxed code execution capabilities via E2B's cloud sandboxes.
Supports Python code execution with file operations and package installation.

Usage:
    from e2b_tools import E2BSandboxManager, E2B_TOOLS

    # Get or create a sandbox for a user
    manager = E2BSandboxManager()
    sandbox = await manager.get_sandbox(user_id)

    # Execute code
    result = await manager.execute_code(user_id, "print('Hello!')")

Environment variables:
    E2B_API_KEY - E2B API key (required for E2B features)
    E2B_TIMEOUT - Sandbox timeout in seconds (default: 300 = 5 minutes)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

# E2B imports - optional dependency
try:
    from e2b_code_interpreter import Sandbox as CodeInterpreterSandbox
    from e2b import Sandbox as BaseSandbox

    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False
    CodeInterpreterSandbox = None
    BaseSandbox = None

# Configuration
E2B_API_KEY = os.getenv("E2B_API_KEY")
E2B_TIMEOUT = int(os.getenv("E2B_TIMEOUT", "300"))  # 5 minutes default
SANDBOX_IDLE_TIMEOUT = 600  # 10 minutes before cleanup
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


# Tool definitions for OpenAI-compatible APIs
E2B_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_python",
            "description": (
                "Execute Python code in a secure cloud sandbox. "
                "The sandbox has internet access and can install packages with pip. "
                "Code execution is stateful - variables persist across calls. "
                "Use this for: calculations, data analysis, file generation, "
                "web requests, package installation, and any Python code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "The Python code to execute. Can be multi-line. "
                            "Use print() to output results. "
                            "Variables persist across executions."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Brief description of what this code does "
                            "(for logging/display purposes)"
                        ),
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": (
                "Install a Python package using pip in the sandbox. "
                "Use this before importing non-standard-library packages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": (
                            "The package name to install (e.g., 'requests', "
                            "'pandas', 'numpy'). Can include version specifiers."
                        ),
                    },
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file from the sandbox filesystem. "
                "Useful for checking generated files or reading uploaded content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "The file path to read (e.g., '/home/user/output.txt')"
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file in the sandbox filesystem. "
                "Useful for creating files that can be executed or downloaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The file path to write to",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories in a path within the sandbox. "
                "Useful for exploring the filesystem or checking generated files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "The directory path to list (default: '/home/user')"
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Run a shell command in the sandbox. "
                "Useful for system operations, git, curl, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unzip_file",
            "description": (
                "Extract a zip archive in the sandbox. "
                "Supports .zip, .tar, .tar.gz, .tgz, .tar.bz2 formats. "
                "Useful after downloading or receiving compressed files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the archive file to extract "
                            "(e.g., '/home/user/archive.zip')"
                        ),
                    },
                    "destination": {
                        "type": "string",
                        "description": (
                            "Directory to extract to (default: same directory as archive)"
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web using Tavily API. "
                "Returns relevant search results with snippets and URLs. "
                "Use this to find current information, research topics, "
                "look up documentation, find news, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "Maximum number of results to return (default: 5, max: 10)"
                        ),
                    },
                    "search_depth": {
                        "type": "string",
                        "enum": ["basic", "advanced"],
                        "description": (
                            "Search depth: 'basic' for quick results, "
                            "'advanced' for more thorough search (default: basic)"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_claude_code",
            "description": (
                "Spawn an autonomous Claude Code agent in a sandbox for complex coding tasks. "
                "Use this for multi-step coding projects like: building applications, "
                "creating complex scripts, debugging codebases, or any task that requires "
                "iterative development. The agent can write files, run code, install packages, "
                "and iterate on its work autonomously. Returns the final result and any files created. "
                "More powerful but slower than execute_python - use for substantial coding work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Clear description of the coding task. Be specific about requirements, "
                            "expected outputs, and any constraints. Example: 'Create a Python script "
                            "that fetches weather data from an API and saves it to a CSV file.'"
                        ),
                    },
                    "timeout_minutes": {
                        "type": "integer",
                        "description": (
                            "Maximum time for the agent to work (default: 5, max: 15). "
                            "Complex tasks may need more time."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
]

# Anthropic API key for Claude Code sandbox (optional - only needed for run_claude_code)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


@dataclass
class SandboxSession:
    """Tracks a user's sandbox session."""

    sandbox: Any  # Sandbox instance
    user_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_used: datetime = field(default_factory=lambda: datetime.now(UTC))
    execution_count: int = 0


@dataclass
class ExecutionResult:
    """Result of code execution."""

    success: bool
    output: str
    error: str | None = None
    files: list[dict] = field(default_factory=list)  # Generated files
    execution_time: float = 0.0


class E2BSandboxManager:
    """Manages E2B sandbox sessions for users."""

    def __init__(self):
        self.sessions: dict[str, SandboxSession] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None

    def is_available(self) -> bool:
        """Check if E2B is available and configured."""
        return E2B_AVAILABLE and bool(E2B_API_KEY)

    async def get_sandbox(self, user_id: str) -> Sandbox | None:
        """Get or create a sandbox for a user."""
        if not self.is_available():
            return None

        async with self._lock:
            # Check for existing session
            if user_id in self.sessions:
                session = self.sessions[user_id]
                session.last_used = datetime.now(UTC)
                return session.sandbox

            # Create new sandbox
            try:
                # Run sandbox creation in executor (it's sync)
                loop = asyncio.get_event_loop()
                sandbox = await loop.run_in_executor(
                    None, lambda: CodeInterpreterSandbox(timeout=E2B_TIMEOUT)
                )

                self.sessions[user_id] = SandboxSession(
                    sandbox=sandbox,
                    user_id=user_id,
                )
                print(f"[e2b] Created sandbox for {user_id}: {sandbox.sandbox_id}")
                return sandbox

            except Exception as e:
                print(f"[e2b] Failed to create sandbox for {user_id}: {e}")
                return None

    async def execute_code(
        self, user_id: str, code: str, description: str = ""
    ) -> ExecutionResult:
        """Execute Python code in a user's sandbox."""
        start_time = datetime.now(UTC)

        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False,
                output="",
                error="E2B sandbox not available. Set E2B_API_KEY to enable.",
            )

        try:
            # Execute code
            loop = asyncio.get_event_loop()
            execution = await loop.run_in_executor(
                None, lambda: sandbox.run_code(code)
            )

            # Update session stats
            if user_id in self.sessions:
                self.sessions[user_id].execution_count += 1
                self.sessions[user_id].last_used = datetime.now(UTC)

            # Process results
            elapsed = (datetime.now(UTC) - start_time).total_seconds()

            if execution.error:
                return ExecutionResult(
                    success=False,
                    output=execution.logs.stdout if execution.logs else "",
                    error=(
                        f"{execution.error.name}: {execution.error.value}\n"
                        f"{execution.error.traceback}"
                    ),
                    execution_time=elapsed,
                )

            # Combine outputs
            output_parts = []
            if execution.logs and execution.logs.stdout:
                output_parts.append(execution.logs.stdout)
            if execution.results:
                for result in execution.results:
                    if hasattr(result, "text") and result.text:
                        output_parts.append(str(result.text))
                    elif hasattr(result, "png") and result.png:
                        output_parts.append("[Generated image/chart]")

            return ExecutionResult(
                success=True,
                output="\n".join(output_parts) if output_parts else "(no output)",
                execution_time=elapsed,
            )

        except Exception as e:
            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
                execution_time=elapsed,
            )

    async def install_package(self, user_id: str, package: str) -> ExecutionResult:
        """Install a pip package in a user's sandbox."""
        install_code = (
            f"import subprocess; "
            f"subprocess.run(['pip', 'install', '{package}'], "
            f"capture_output=True, text=True)"
        )
        result = await self.execute_code(user_id, install_code, f"Installing {package}")

        # Also run a simple pip install via shell for better output
        sandbox = await self.get_sandbox(user_id)
        if sandbox:
            try:
                loop = asyncio.get_event_loop()
                cmd_result = await loop.run_in_executor(
                    None, lambda: sandbox.commands.run(f"pip install {package}")
                )
                return ExecutionResult(
                    success=cmd_result.exit_code == 0,
                    output=cmd_result.stdout or "",
                    error=cmd_result.stderr if cmd_result.exit_code != 0 else None,
                )
            except Exception as e:
                return ExecutionResult(success=False, output="", error=str(e))

        return result

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        """Read a file from a user's sandbox."""
        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False, output="", error="Sandbox not available"
            )

        try:
            loop = asyncio.get_event_loop()
            content = await loop.run_in_executor(
                None, lambda: sandbox.files.read(path)
            )
            # content is bytes
            text = content.decode("utf-8") if isinstance(content, bytes) else content
            return ExecutionResult(success=True, output=text)
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def write_file(
        self, user_id: str, path: str, content: str | bytes
    ) -> ExecutionResult:
        """Write a file to a user's sandbox. Accepts both text and binary content."""
        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False, output="", error="Sandbox not available"
            )

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, lambda: sandbox.files.write(path, content)
            )
            return ExecutionResult(success=True, output=f"File written to {path}")
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def list_files(
        self, user_id: str, path: str = "/home/user"
    ) -> ExecutionResult:
        """List files in a directory in a user's sandbox."""
        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False, output="", error="Sandbox not available"
            )

        try:
            loop = asyncio.get_event_loop()
            files = await loop.run_in_executor(
                None, lambda: sandbox.files.list(path)
            )
            file_list = "\n".join(
                f"{'[dir]' if f.is_dir else '[file]'} {f.name}"
                for f in files
            )
            return ExecutionResult(
                success=True, output=file_list or "(empty directory)"
            )
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def run_shell(self, user_id: str, command: str) -> ExecutionResult:
        """Run a shell command in a user's sandbox."""
        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False, output="", error="Sandbox not available"
            )

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: sandbox.commands.run(command)
            )
            output = result.stdout or ""
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            exit_ok = result.exit_code == 0
            return ExecutionResult(
                success=exit_ok,
                output=output,
                error=None if exit_ok else f"Exit code: {result.exit_code}",
            )
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def unzip_file(
        self, user_id: str, path: str, destination: str | None = None
    ) -> ExecutionResult:
        """Extract an archive in a user's sandbox."""
        sandbox = await self.get_sandbox(user_id)
        if not sandbox:
            return ExecutionResult(
                success=False, output="", error="Sandbox not available"
            )

        # Determine destination directory
        if not destination:
            # Extract to same directory as the archive
            destination = "/".join(path.split("/")[:-1]) or "/home/user"

        # Build extraction command based on file extension
        path_lower = path.lower()
        if path_lower.endswith(".zip"):
            cmd = f"unzip -o '{path}' -d '{destination}'"
        elif path_lower.endswith(".tar.gz") or path_lower.endswith(".tgz"):
            cmd = f"tar -xzf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar.bz2"):
            cmd = f"tar -xjf '{path}' -C '{destination}'"
        elif path_lower.endswith(".tar"):
            cmd = f"tar -xf '{path}' -C '{destination}'"
        elif path_lower.endswith(".gz"):
            # Single gzipped file
            cmd = f"gunzip -k '{path}'"
        elif path_lower.endswith(".7z"):
            cmd = f"7z x '{path}' -o'{destination}'"
        elif path_lower.endswith(".rar"):
            cmd = f"unrar x '{path}' '{destination}/'"
        else:
            # Try to detect format and extract
            cmd = f"unzip -o '{path}' -d '{destination}' 2>/dev/null || tar -xf '{path}' -C '{destination}'"

        try:
            loop = asyncio.get_event_loop()

            # Ensure destination exists
            await loop.run_in_executor(
                None, lambda: sandbox.commands.run(f"mkdir -p '{destination}'")
            )

            # Run extraction
            result = await loop.run_in_executor(
                None, lambda: sandbox.commands.run(cmd)
            )

            output = result.stdout or ""
            if result.stderr and result.exit_code == 0:
                # Some tools output to stderr even on success
                output += f"\n{result.stderr}"

            if result.exit_code == 0:
                # List extracted files
                ls_result = await loop.run_in_executor(
                    None, lambda: sandbox.commands.run(f"ls -la '{destination}'")
                )
                output += f"\n\nExtracted to {destination}:\n{ls_result.stdout}"
                return ExecutionResult(success=True, output=output)
            else:
                return ExecutionResult(
                    success=False,
                    output=output,
                    error=f"Extraction failed: {result.stderr or 'Unknown error'}",
                )
        except Exception as e:
            return ExecutionResult(success=False, output="", error=str(e))

    async def web_search(
        self, query: str, max_results: int = 5, search_depth: str = "basic"
    ) -> ExecutionResult:
        """Search the web using Tavily API."""
        if not TAVILY_API_KEY:
            return ExecutionResult(
                success=False,
                output="",
                error="TAVILY_API_KEY not set. Web search unavailable.",
            )

        try:
            import httpx

            max_results = min(max(1, max_results), 10)  # Clamp to 1-10

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": TAVILY_API_KEY,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": search_depth,
                        "include_answer": True,
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
                data = response.json()

            # Format results
            output_parts = []

            # Include Tavily's AI answer if available
            if data.get("answer"):
                output_parts.append(f"**Summary:** {data['answer']}\n")

            output_parts.append("**Search Results:**\n")

            for i, result in enumerate(data.get("results", []), 1):
                title = result.get("title", "Untitled")
                url = result.get("url", "")
                snippet = result.get("content", "")[:300]
                output_parts.append(f"{i}. **{title}**\n   {url}\n   {snippet}\n")

            return ExecutionResult(
                success=True,
                output="\n".join(output_parts) if output_parts else "No results found.",
            )

        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Web search failed: {str(e)}",
            )

    async def run_claude_code(
        self, task: str, timeout_minutes: int = 5
    ) -> ExecutionResult:
        """Spawn an autonomous Claude Code agent in a sandbox for complex coding tasks.

        Uses the official E2B Claude Code template which has Claude Code pre-installed.
        The agent can write files, execute code, install packages, and iterate on work.

        Args:
            task: Description of the coding task
            timeout_minutes: Max time for the agent (default 5, max 15)

        Returns:
            ExecutionResult with agent output and list of created files
        """
        if not ANTHROPIC_API_KEY:
            return ExecutionResult(
                success=False,
                output="",
                error="ANTHROPIC_API_KEY not set. Claude Code agent unavailable.",
            )

        if not E2B_AVAILABLE or not BaseSandbox:
            return ExecutionResult(
                success=False,
                output="",
                error="E2B not available. Set E2B_API_KEY to enable.",
            )

        # Clamp timeout
        timeout_minutes = min(max(1, timeout_minutes), 15)
        timeout_seconds = timeout_minutes * 60

        start_time = datetime.now(UTC)

        try:
            loop = asyncio.get_event_loop()

            # Create a dedicated sandbox with Claude Code template
            # This sandbox is separate from the user's code interpreter sandbox
            print(f"[e2b] Creating Claude Code sandbox (timeout: {timeout_minutes}m)...")
            sandbox = await loop.run_in_executor(
                None,
                lambda: BaseSandbox(
                    template="anthropic-claude-code",
                    timeout=timeout_seconds,
                    envs={"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY},
                ),
            )

            try:
                # Escape the task for shell (replace single quotes)
                escaped_task = task.replace("'", "'\"'\"'")

                # Run Claude Code with the task via pipe
                # Using -p flag for non-interactive mode
                print(f"[e2b] Running Claude Code agent...")
                cmd = f"echo '{escaped_task}' | claude -p"

                result = await loop.run_in_executor(
                    None,
                    lambda: sandbox.commands.run(
                        cmd,
                        timeout=timeout_seconds,
                    ),
                )

                # Capture output
                output = result.stdout or ""
                if result.stderr:
                    output += f"\n\n[stderr]:\n{result.stderr}"

                # List files created in /home/user
                print(f"[e2b] Listing created files...")
                files_result = await loop.run_in_executor(
                    None,
                    lambda: sandbox.commands.run(
                        "find /home/user -type f -newer /tmp/.sandbox_start 2>/dev/null || "
                        "ls -la /home/user"
                    ),
                )
                if files_result.stdout:
                    output += f"\n\n**Files in workspace:**\n{files_result.stdout}"

                elapsed = (datetime.now(UTC) - start_time).total_seconds()

                if result.exit_code == 0:
                    return ExecutionResult(
                        success=True,
                        output=output,
                        execution_time=elapsed,
                    )
                else:
                    return ExecutionResult(
                        success=False,
                        output=output,
                        error=f"Claude Code exited with code {result.exit_code}",
                        execution_time=elapsed,
                    )

            finally:
                # Always clean up the Claude Code sandbox
                try:
                    await loop.run_in_executor(None, sandbox.kill)
                    print("[e2b] Claude Code sandbox cleaned up")
                except Exception as e:
                    print(f"[e2b] Error cleaning up Claude Code sandbox: {e}")

        except Exception as e:
            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            return ExecutionResult(
                success=False,
                output="",
                error=f"Claude Code agent failed: {str(e)}",
                execution_time=elapsed,
            )

    async def handle_tool_call(
        self, user_id: str, tool_name: str, arguments: dict
    ) -> ExecutionResult:
        """Handle a tool call from the LLM."""
        if tool_name == "execute_python":
            return await self.execute_code(
                user_id,
                arguments["code"],
                arguments.get("description", ""),
            )
        elif tool_name == "install_package":
            return await self.install_package(user_id, arguments["package"])
        elif tool_name == "read_file":
            return await self.read_file(user_id, arguments["path"])
        elif tool_name == "write_file":
            return await self.write_file(
                user_id, arguments["path"], arguments["content"]
            )
        elif tool_name == "list_files":
            return await self.list_files(
                user_id, arguments.get("path", "/home/user")
            )
        elif tool_name == "run_shell":
            return await self.run_shell(user_id, arguments["command"])
        elif tool_name == "unzip_file":
            return await self.unzip_file(
                user_id, arguments["path"], arguments.get("destination")
            )
        elif tool_name == "web_search":
            return await self.web_search(
                arguments["query"],
                arguments.get("max_results", 5),
                arguments.get("search_depth", "basic"),
            )
        elif tool_name == "run_claude_code":
            return await self.run_claude_code(
                arguments["task"],
                arguments.get("timeout_minutes", 5),
            )
        else:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Unknown tool: {tool_name}",
            )

    async def cleanup_idle_sessions(self):
        """Clean up sessions that have been idle too long."""
        async with self._lock:
            now = datetime.now(UTC)
            idle_threshold = timedelta(seconds=SANDBOX_IDLE_TIMEOUT)

            to_remove = []
            for user_id, session in self.sessions.items():
                if now - session.last_used > idle_threshold:
                    to_remove.append(user_id)

            for user_id in to_remove:
                session = self.sessions.pop(user_id)
                try:
                    session.sandbox.kill()
                    print(f"[e2b] Cleaned up idle sandbox for {user_id}")
                except Exception as e:
                    print(f"[e2b] Error cleaning up sandbox for {user_id}: {e}")

    async def cleanup_all(self):
        """Clean up all sandbox sessions."""
        async with self._lock:
            for user_id, session in list(self.sessions.items()):
                try:
                    session.sandbox.kill()
                    print(f"[e2b] Cleaned up sandbox for {user_id}")
                except Exception as e:
                    print(f"[e2b] Error cleaning up sandbox for {user_id}: {e}")
            self.sessions.clear()

    def get_stats(self) -> dict:
        """Get sandbox manager statistics."""
        return {
            "available": self.is_available(),
            "active_sessions": len(self.sessions),
            "sessions": {
                user_id: {
                    "sandbox_id": session.sandbox.sandbox_id
                    if hasattr(session.sandbox, "sandbox_id")
                    else "unknown",
                    "created_at": session.created_at.isoformat(),
                    "last_used": session.last_used.isoformat(),
                    "execution_count": session.execution_count,
                }
                for user_id, session in self.sessions.items()
            },
        }


# Global singleton instance
_sandbox_manager: E2BSandboxManager | None = None


def get_sandbox_manager() -> E2BSandboxManager:
    """Get the global sandbox manager instance."""
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = E2BSandboxManager()
    return _sandbox_manager


def format_tool_result(result: ExecutionResult, tool_name: str) -> str:
    """Format an execution result for display."""
    if result.success:
        output = result.output or "(no output)"
        timing = f" [{result.execution_time:.2f}s]" if result.execution_time else ""
        return f"**{tool_name}** succeeded{timing}:\n```\n{output}\n```"
    else:
        error = result.error or "Unknown error"
        return f"**{tool_name}** failed:\n```\n{error}\n```"
