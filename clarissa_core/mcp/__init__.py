"""MCP (Model Context Protocol) Plugin System for Clarissa.

This package provides functionality for Clarissa to discover, install, and use
tools from external MCP servers.

Storage modes:
- JSON (default): Configs stored in .mcp_servers/{name}/config.json
- Database: Configs stored in mcp_servers table (set MCP_USE_DATABASE=true)

Usage:
    from clarissa_core.mcp import (
        MCPServerManager,
        MCPInstaller,
        init_mcp,
        get_mcp_manager,
    )

    # Initialize the MCP system at startup
    manager = await init_mcp()

    # Or get the singleton manager
    manager = get_mcp_manager()

    # Install a new server
    installer = MCPInstaller()
    result = await installer.install("@modelcontextprotocol/server-everything")

    # Call a tool
    result = await manager.call_tool("everything__echo", {"message": "Hello"})

    # Get tools in OpenAI format for API calls
    tools = manager.get_tools_openai_format()
"""

from __future__ import annotations

import logging
import os

from .client import MCPClient, MCPTool
from .installer import InstallResult, MCPInstaller
from .manager import MCPServerManager
from .models import MCPServer, MCPServerConfig, load_server_config, save_server_config

# Keep deprecated imports for backwards compatibility
from .registry_adapter import MCPRegistryAdapter, get_mcp_adapter, init_mcp_adapter

logger = logging.getLogger(__name__)

# Toggle for database vs JSON storage (JSON is default)
USE_DATABASE = os.getenv("MCP_USE_DATABASE", "").lower() in ("true", "1", "yes")

__all__ = [
    # Core classes
    "MCPClient",
    "MCPTool",
    "MCPServerManager",
    "MCPInstaller",
    "MCPServer",
    "MCPServerConfig",
    "InstallResult",
    # Initialization
    "init_mcp",
    "get_mcp_manager",
    # Storage functions
    "load_server_config",
    "save_server_config",
    # Deprecated (kept for backwards compatibility)
    "MCPRegistryAdapter",
    "get_mcp_adapter",
    "init_mcp_adapter",
]


# Global singleton references
_manager: MCPServerManager | None = None
_initialized: bool = False


def get_mcp_manager() -> MCPServerManager:
    """Get the global MCP server manager instance.

    Returns:
        MCPServerManager singleton
    """
    global _manager
    if _manager is None:
        _manager = MCPServerManager.get_instance()
    return _manager


async def init_mcp() -> MCPServerManager:
    """Initialize the MCP plugin system.

    This should be called at application startup. It will:
    1. Ensure clarissa-tools native MCP server is registered
    2. Create/get the MCPServerManager singleton
    3. Initialize all enabled MCP servers from storage

    MCP tools are now served directly through the manager's format conversion
    methods (get_tools_openai_format, get_tools_claude_format) rather than
    through the registry adapter bridge pattern.

    Returns:
        MCPServerManager singleton
    """
    global _manager, _initialized

    if _initialized:
        logger.debug("[MCP] Already initialized")
        return _manager

    logger.info("[MCP] Initializing MCP plugin system...")

    # Ensure clarissa-tools native MCP server is registered
    _ensure_clarissa_tools_server()

    # Get/create the manager
    _manager = get_mcp_manager()

    # Initialize all enabled servers
    init_results = await _manager.initialize()
    connected = sum(1 for v in init_results.values() if v)
    total = len(init_results)

    # Count total tools discovered
    tool_count = sum(
        len(client.get_tools())
        for client in _manager._clients.values()
        if client.is_connected
    )

    logger.info(
        f"[MCP] Initialized {connected}/{total} servers with {tool_count} tools"
    )

    _initialized = True
    return _manager


def _ensure_clarissa_tools_server() -> None:
    """Ensure the clarissa-tools native MCP server is registered."""
    import shutil
    from pathlib import Path

    # Find the clarissa-mcp-server binary
    binary_path = shutil.which("clarissa-mcp-server")
    if not binary_path:
        # Check local development paths
        project_root = Path(__file__).parent.parent.parent
        release = (
            project_root
            / "clarissa-mcp-server"
            / "target"
            / "release"
            / "clarissa-mcp-server"
        )
        debug = (
            project_root
            / "clarissa-mcp-server"
            / "target"
            / "debug"
            / "clarissa-mcp-server"
        )
        if release.exists():
            binary_path = str(release)
        elif debug.exists():
            binary_path = str(debug)

    if not binary_path:
        logger.warning(
            "[MCP] clarissa-mcp-server binary not found, skipping native tools"
        )
        return

    if USE_DATABASE:
        _ensure_clarissa_tools_server_db(binary_path)
    else:
        _ensure_clarissa_tools_server_json(binary_path)


def _ensure_clarissa_tools_server_json(binary_path: str) -> None:
    """Register clarissa-tools using JSON config."""
    existing = load_server_config("clarissa-tools")

    if existing:
        # Update binary path if changed
        if existing.command != binary_path:
            logger.info(f"[MCP] Updating clarissa-tools binary path: {binary_path}")
            existing.command = binary_path
            save_server_config(existing)
        return

    # Register new server
    logger.info(f"[MCP] Registering clarissa-tools native MCP server: {binary_path}")
    server = MCPServerConfig(
        name="clarissa-tools",
        source_type="local",
        display_name="Clarissa Native Tools",
        source_url=binary_path,
        transport="stdio",
        command=binary_path,
        enabled=True,
        status="stopped",
    )
    save_server_config(server)


def _ensure_clarissa_tools_server_db(binary_path: str) -> None:
    """Register clarissa-tools using database (legacy mode)."""
    try:
        from db import SessionLocal

        with SessionLocal() as session:
            # Check if exists
            result = session.execute(
                "SELECT command FROM mcp_servers WHERE name = :name",
                {"name": "clarissa-tools"},
            ).first()

            if result:
                # Update if path changed
                if result[0] != binary_path:
                    logger.info(
                        f"[MCP] Updating clarissa-tools binary path: {binary_path}"
                    )
                    session.execute(
                        "UPDATE mcp_servers SET command = :cmd WHERE name = :name",
                        {"cmd": binary_path, "name": "clarissa-tools"},
                    )
                    session.commit()
                return

            # Insert new
            logger.info(
                f"[MCP] Registering clarissa-tools native MCP server: {binary_path}"
            )
            session.execute(
                """
                INSERT INTO mcp_servers (name, display_name, source_type, source_url,
                    transport, command, enabled, status)
                VALUES (:name, :display_name, :source_type, :source_url,
                    :transport, :command, :enabled, :status)
                """,
                {
                    "name": "clarissa-tools",
                    "display_name": "Clarissa Native Tools",
                    "source_type": "local",
                    "source_url": binary_path,
                    "transport": "stdio",
                    "command": binary_path,
                    "enabled": True,
                    "status": "stopped",
                },
            )
            session.commit()

    except Exception as e:
        logger.warning(f"[MCP] Database registration failed, using JSON fallback: {e}")
        _ensure_clarissa_tools_server_json(binary_path)


async def shutdown_mcp() -> None:
    """Shutdown the MCP plugin system.

    Should be called on application shutdown to cleanly disconnect
    from all MCP servers.
    """
    global _manager, _initialized

    if _manager:
        await _manager.shutdown()

    _manager = None
    _initialized = False
    logger.info("[MCP] Shutdown complete")


def is_initialized() -> bool:
    """Check if the MCP system is initialized.

    Returns:
        True if initialized
    """
    return _initialized
