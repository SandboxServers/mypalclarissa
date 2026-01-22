"""Sandbox execution environments for Clarissa.

Provides sandboxed code execution via Docker, E2B, or remote VPS.
"""

from sandbox.docker import DOCKER_AVAILABLE, DOCKER_TOOLS, DockerSandboxManager
from sandbox.manager import (
    UnifiedSandboxManager,
    get_sandbox_manager,
    reset_sandbox_manager,
)
from sandbox.remote_client import RemoteSandboxClient, get_remote_client

__all__ = [
    # Docker sandbox
    "DockerSandboxManager",
    "DOCKER_TOOLS",
    "DOCKER_AVAILABLE",
    # Remote sandbox
    "RemoteSandboxClient",
    "get_remote_client",
    # Unified manager
    "UnifiedSandboxManager",
    "get_sandbox_manager",
    "reset_sandbox_manager",
]
