"""Sandbox execution environments for Clarissa.

Provides sandboxed code execution via:
- Local Docker containers
- Remote sandbox API (self-hosted VPS)

Usage:
    from sandbox import get_sandbox_manager

    manager = get_sandbox_manager()
    result = await manager.execute_code("user123", "print('hello')")

The manager automatically selects the appropriate backend based on SANDBOX_MODE:
- "local": Use local Docker only
- "remote": Use remote sandbox API only
- "auto" (default): Use remote if configured, fall back to local
"""

from sandbox.docker import DOCKER_AVAILABLE, DOCKER_TOOLS, DockerSandboxManager
from sandbox.manager import (
    UnifiedSandboxManager,
    get_sandbox_manager,
    reset_sandbox_manager,
)
from sandbox.remote_client import RemoteSandboxClient, get_remote_client

__all__ = [
    # Legacy exports (for backward compatibility)
    "DockerSandboxManager",
    "DOCKER_TOOLS",
    "DOCKER_AVAILABLE",
    # New unified interface
    "UnifiedSandboxManager",
    "get_sandbox_manager",
    "reset_sandbox_manager",
    # Remote client
    "RemoteSandboxClient",
    "get_remote_client",
]
