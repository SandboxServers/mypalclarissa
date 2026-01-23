"""Remote sandbox client for Clara.

HTTP client that communicates with the VPS sandbox service.
Provides the same interface as DockerSandboxManager for seamless switching.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

# Configuration
SANDBOX_API_URL = os.getenv("SANDBOX_API_URL")  # e.g., https://sandbox.example.com
SANDBOX_API_KEY = os.getenv("SANDBOX_API_KEY")
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "60"))


@dataclass
class ExecutionResult:
    """Result of code execution (matches sandbox/docker.py interface)."""

    success: bool
    output: str
    error: str | None = None
    files: list[dict] = field(default_factory=list)
    execution_time: float = 0.0


class RemoteSandboxClient:
    """HTTP client for remote sandbox API.

    Provides the same interface as DockerSandboxManager for seamless switching.
    All methods return ExecutionResult for consistency.

    Usage:
        client = RemoteSandboxClient()
        result = await client.execute_code("user123", "print('hello')")
        if result.success:
            print(result.output)
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = (base_url or SANDBOX_API_URL or "").rstrip("/")
        self.api_key = api_key or SANDBOX_API_KEY
        self.timeout = timeout or SANDBOX_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"X-API-Key": self.api_key or ""},
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def is_available(self) -> bool:
        """Check if remote sandbox is configured."""
        return bool(self.base_url and self.api_key)

    async def health_check(self) -> bool:
        """Check if remote service is healthy."""
        if not self.is_available():
            return False
        try:
            client = await self._get_client()
            response = await client.get("/health")
            data = response.json()
            return data.get("status") == "healthy" and data.get("docker", False)
        except Exception:
            return False

    async def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Make API request and return ExecutionResult."""
        try:
            client = await self._get_client()
            request_timeout = httpx.Timeout(timeout or self.timeout)
            response = await client.request(
                method, path, json=json, timeout=request_timeout
            )

            if response.status_code == 401:
                return ExecutionResult(
                    success=False,
                    output="",
                    error="Authentication failed: Invalid API key",
                )
            elif response.status_code == 404:
                return ExecutionResult(
                    success=False,
                    output="",
                    error="Sandbox not found",
                )
            elif response.status_code == 503:
                return ExecutionResult(
                    success=False,
                    output="",
                    error="Service unavailable: " + response.text,
                )
            elif response.status_code >= 400:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", response.text)
                except Exception:
                    error_msg = response.text
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"API error ({response.status_code}): {error_msg}",
                )

            data = response.json()
            return ExecutionResult(
                success=data.get("success", True),
                output=data.get("output", ""),
                error=data.get("error"),
                execution_time=data.get("execution_time", 0.0),
            )

        except httpx.TimeoutException:
            return ExecutionResult(
                success=False,
                output="",
                error="Request timed out",
            )
        except httpx.ConnectError:
            return ExecutionResult(
                success=False,
                output="",
                error="Failed to connect to sandbox service",
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Request failed: {str(e)}",
            )

    # =========================================================================
    # Sandbox Lifecycle
    # =========================================================================

    async def get_sandbox(self, user_id: str) -> bool:
        """Ensure sandbox exists for user. Returns True if available."""
        result = await self._request("POST", f"/sandbox/{user_id}/create")
        return result.success

    async def destroy_sandbox(self, user_id: str) -> ExecutionResult:
        """Destroy user's sandbox."""
        return await self._request("DELETE", f"/sandbox/{user_id}")

    # =========================================================================
    # Code Execution (matches DockerSandboxManager interface)
    # =========================================================================

    async def execute_code(
        self,
        user_id: str,
        code: str,
        description: str = "",
        timeout: int = 30,
    ) -> ExecutionResult:
        """Execute Python code in sandbox."""
        return await self._request(
            "POST",
            f"/sandbox/{user_id}/execute",
            {"code": code, "description": description, "timeout": timeout},
            timeout=timeout + 10,  # Extra time for network
        )

    async def run_shell(
        self,
        user_id: str,
        command: str,
        timeout: int = 60,
    ) -> ExecutionResult:
        """Run shell command in sandbox."""
        return await self._request(
            "POST",
            f"/sandbox/{user_id}/shell",
            {"command": command, "timeout": timeout},
            timeout=timeout + 10,
        )

    async def install_package(
        self,
        user_id: str,
        package: str,
        timeout: int = 120,
    ) -> ExecutionResult:
        """Install pip package in sandbox."""
        return await self._request(
            "POST",
            f"/sandbox/{user_id}/pip/install",
            {"package": package, "timeout": timeout},
            timeout=timeout + 10,
        )

    # =========================================================================
    # File Operations
    # =========================================================================

    async def read_file(self, user_id: str, path: str) -> ExecutionResult:
        """Read file from sandbox."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"/sandbox/{user_id}/file",
                params={"path": path},
            )

            if response.status_code != 200:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Failed to read file: {response.status_code}",
                )

            data = response.json()
            content = data.get("content", "")

            # Decode base64 if needed
            if data.get("encoding") == "base64":
                content = base64.b64decode(content).decode("utf-8", errors="replace")

            return ExecutionResult(
                success=True,
                output=content,
            )

        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
            )

    async def write_file(
        self,
        user_id: str,
        path: str,
        content: str | bytes,
    ) -> ExecutionResult:
        """Write file to sandbox."""
        encoding = "utf-8"
        if isinstance(content, bytes):
            content = base64.b64encode(content).decode("ascii")
            encoding = "base64"

        return await self._request(
            "POST",
            f"/sandbox/{user_id}/file",
            {"path": path, "content": content, "encoding": encoding},
        )

    async def list_files(
        self,
        user_id: str,
        path: str = "/workspace",
    ) -> ExecutionResult:
        """List files in sandbox directory."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"/sandbox/{user_id}/files",
                params={"path": path},
            )

            if response.status_code != 200:
                return ExecutionResult(
                    success=False,
                    output="",
                    error=f"Failed to list files: {response.status_code}",
                )

            data = response.json()
            return ExecutionResult(
                success=data.get("success", True),
                output=data.get("output", ""),
                error=data.get("error"),
            )

        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=str(e),
            )

    async def unzip_file(
        self,
        user_id: str,
        path: str,
        destination: str | None = None,
    ) -> ExecutionResult:
        """Extract archive in sandbox."""
        return await self._request(
            "POST",
            f"/sandbox/{user_id}/unzip",
            {"path": path, "destination": destination},
            timeout=120,
        )

    # =========================================================================
    # Web Search (delegated to local implementation if available)
    # =========================================================================

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> ExecutionResult:
        """Web search is not available via remote sandbox.

        Use the local web_search tool instead.
        """
        return ExecutionResult(
            success=False,
            output="",
            error="Web search is not available via remote sandbox. Use local web_search tool.",
        )

    # =========================================================================
    # Tool Call Handler (matches DockerSandboxManager interface)
    # =========================================================================

    async def handle_tool_call(
        self,
        user_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ExecutionResult:
        """Handle tool call (same interface as DockerSandboxManager)."""
        # Route to appropriate method based on tool name
        if tool_name == "execute_python":
            code = (
                arguments.get("code")
                or arguments.get("python_code")
                or arguments.get("script", "")
            )
            return await self.execute_code(
                user_id,
                code,
                arguments.get("description", ""),
            )
        elif tool_name == "install_package":
            return await self.install_package(
                user_id,
                arguments.get("package", ""),
            )
        elif tool_name == "read_file":
            return await self.read_file(user_id, arguments.get("path", ""))
        elif tool_name == "write_file":
            return await self.write_file(
                user_id,
                arguments.get("path", ""),
                arguments.get("content", ""),
            )
        elif tool_name == "list_files":
            return await self.list_files(
                user_id,
                arguments.get("path", "/workspace"),
            )
        elif tool_name == "run_shell":
            return await self.run_shell(user_id, arguments.get("command", ""))
        elif tool_name == "unzip_file":
            return await self.unzip_file(
                user_id,
                arguments.get("path", ""),
                arguments.get("destination"),
            )
        elif tool_name == "web_search":
            return await self.web_search(
                arguments.get("query", ""),
                arguments.get("max_results", 5),
                arguments.get("search_depth", "basic"),
            )
        else:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Unknown tool: {tool_name}",
            )

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return {
            "available": self.is_available(),
            "base_url": self.base_url,
            "timeout": self.timeout,
            "mode": "remote",
        }


# =============================================================================
# Global Singleton
# =============================================================================

_remote_client: RemoteSandboxClient | None = None


def get_remote_client() -> RemoteSandboxClient:
    """Get the global remote sandbox client."""
    global _remote_client
    if _remote_client is None:
        _remote_client = RemoteSandboxClient()
    return _remote_client
