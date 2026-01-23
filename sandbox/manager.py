"""Unified sandbox manager for Clara.

Provides a single interface that can use either local Docker or remote sandbox.
Automatically selects the appropriate backend based on configuration.
"""

from __future__ import annotations

import os
from typing import Any

from .docker import DOCKER_AVAILABLE, DockerSandboxManager, ExecutionResult
from .remote_client import RemoteSandboxClient, get_remote_client

# Mode configuration
# - "local": Only use local Docker
# - "remote": Only use remote sandbox API
# - "auto": Use remote if configured, fall back to local
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "auto")


class UnifiedSandboxManager:
    """Unified sandbox manager that switches between local and remote.

    Mode selection:
    - "local": Only use local Docker
    - "remote": Only use remote sandbox API
    - "auto" (default): Use remote if configured, fall back to local

    Usage:
        manager = get_sandbox_manager()
        result = await manager.execute_code("user123", "print('hello')")

    Environment variables:
        SANDBOX_MODE: "local", "remote", or "auto"
        SANDBOX_API_URL: Remote sandbox URL (required for remote mode)
        SANDBOX_API_KEY: Remote sandbox API key (required for remote mode)
    """

    def __init__(self, mode: str | None = None):
        self.mode = mode or SANDBOX_MODE
        self._local: DockerSandboxManager | None = None
        self._remote: RemoteSandboxClient | None = None
        self._active_backend: str | None = None

    @property
    def local(self) -> DockerSandboxManager:
        """Get local Docker manager (lazy initialization)."""
        if self._local is None:
            self._local = DockerSandboxManager()
        return self._local

    @property
    def remote(self) -> RemoteSandboxClient:
        """Get remote client (lazy initialization)."""
        if self._remote is None:
            self._remote = get_remote_client()
        return self._remote

    def _select_backend(self) -> str:
        """Select which backend to use based on mode and availability."""
        if self.mode == "local":
            if DOCKER_AVAILABLE and self.local.is_available():
                return "local"
            return "none"

        elif self.mode == "remote":
            if self.remote.is_available():
                return "remote"
            return "none"

        else:  # auto
            # Prefer remote if configured
            if self.remote.is_available():
                return "remote"
            # Fall back to local Docker
            if DOCKER_AVAILABLE and self.local.is_available():
                return "local"
            return "none"

    def is_available(self) -> bool:
        """Check if any sandbox backend is available."""
        return self._select_backend() != "none"

    async def health_check(self) -> bool:
        """Check if the active backend is healthy."""
        backend = self._select_backend()
        if backend == "remote":
            return await self.remote.health_check()
        elif backend == "local":
            return self.local.is_available()
        return False

    def _get_manager(self) -> DockerSandboxManager | RemoteSandboxClient | None:
        """Get the active manager based on mode."""
        backend = self._select_backend()
        self._active_backend = backend
        if backend == "remote":
            return self.remote
        elif backend == "local":
            return self.local
        return None

    # =========================================================================
    # Forward all methods to the active backend
    # =========================================================================

    async def execute_code(
        self,
        user_id: str,
        code: str,
        description: str = "",
    ) -> ExecutionResult:
        """Execute Python code in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.execute_code(user_id, code, description)

    async def run_shell(
        self,
        user_id: str,
        command: str,
    ) -> ExecutionResult:
        """Run shell command in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.run_shell(user_id, command)

    async def install_package(
        self,
        user_id: str,
        package: str,
    ) -> ExecutionResult:
        """Install pip package in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.install_package(user_id, package)

    async def ensure_packages(
        self,
        user_id: str,
        packages: list[str],
    ) -> ExecutionResult:
        """Ensure multiple packages are installed (batch install).

        Only installs packages that aren't already tracked.
        """
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        # ensure_packages is only available on local backend
        if hasattr(manager, "ensure_packages"):
            return await manager.ensure_packages(user_id, packages)
        # Fall back to individual installs for remote
        for pkg in packages:
            result = await manager.install_package(user_id, pkg)
            if not result.success:
                return result
        return ExecutionResult(
            success=True,
            output=f"All {len(packages)} packages installed",
        )

    async def read_file(
        self,
        user_id: str,
        path: str,
    ) -> ExecutionResult:
        """Read file from the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.read_file(user_id, path)

    async def write_file(
        self,
        user_id: str,
        path: str,
        content: str | bytes,
    ) -> ExecutionResult:
        """Write file to the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.write_file(user_id, path, content)

    async def list_files(
        self,
        user_id: str,
        path: str = "/home/user",
    ) -> ExecutionResult:
        """List files in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.list_files(user_id, path)

    async def unzip_file(
        self,
        user_id: str,
        path: str,
        destination: str | None = None,
    ) -> ExecutionResult:
        """Extract archive in the sandbox."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.unzip_file(user_id, path, destination)

    async def web_search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> ExecutionResult:
        """Web search (only available on local backend)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )

        # Web search is only available on local Docker sandbox
        if self._active_backend == "local" and hasattr(manager, "web_search"):
            return await manager.web_search(query, max_results, search_depth)
        else:
            return ExecutionResult(
                success=False,
                output="",
                error="Web search not available on remote sandbox.",
            )

    async def handle_tool_call(
        self,
        user_id: str,
        tool_name: str,
        arguments: dict,
    ) -> ExecutionResult:
        """Handle tool call (unified interface)."""
        manager = self._get_manager()
        if not manager:
            return ExecutionResult(
                success=False,
                output="",
                error="No sandbox backend available",
            )
        return await manager.handle_tool_call(user_id, tool_name, arguments)

    async def get_sandbox(self, user_id: str) -> Any:
        """Get or create sandbox for user."""
        manager = self._get_manager()
        if not manager:
            return None
        return await manager.get_sandbox(user_id)

    # =========================================================================
    # Lifecycle & Statistics
    # =========================================================================

    async def cleanup_all(self) -> None:
        """Cleanup all sandbox sessions."""
        if self._local:
            await self._local.cleanup_all()
        if self._remote:
            await self._remote.close()

    async def cleanup_idle_sessions(self) -> int:
        """Cleanup idle sessions (local only)."""
        if self._active_backend == "local" and self._local:
            return await self._local.cleanup_idle_sessions()
        return 0

    def get_stats(self) -> dict[str, Any]:
        """Get combined statistics."""
        backend = self._select_backend()
        manager = self._get_manager()
        stats = manager.get_stats() if manager else {"available": False}
        stats["mode"] = self.mode
        stats["active_backend"] = backend
        return stats


# =============================================================================
# Global Singleton
# =============================================================================

_unified_manager: UnifiedSandboxManager | None = None


def get_sandbox_manager() -> UnifiedSandboxManager:
    """Get the global unified sandbox manager.

    This replaces the previous get_sandbox_manager() from docker.py.
    Uses SANDBOX_MODE environment variable to determine backend.
    """
    global _unified_manager
    if _unified_manager is None:
        _unified_manager = UnifiedSandboxManager()
    return _unified_manager


def reset_sandbox_manager() -> None:
    """Reset the global sandbox manager (for testing)."""
    global _unified_manager
    _unified_manager = None
