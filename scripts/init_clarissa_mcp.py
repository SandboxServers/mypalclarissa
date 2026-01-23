#!/usr/bin/env python3
"""Initialize Clarissa MCP Server in the database.

This script adds the clarissa-mcp-server to the MCP servers database,
enabling Clarissa to use the native Rust tools.

Usage:
    poetry run python scripts/init_clarissa_mcp.py
"""

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db import SessionLocal
from clarissa_core.mcp.models import MCPServer


def get_clarissa_mcp_binary() -> str:
    """Find the clarissa-mcp-server binary."""
    import shutil

    # Check for system-wide binary first (Docker)
    system_binary = shutil.which("clarissa-mcp-server")
    if system_binary:
        return system_binary

    # Check for release binary in development
    project_root = Path(__file__).parent.parent
    release_binary = project_root / "clarissa-mcp-server" / "target" / "release" / "clarissa-mcp-server"
    debug_binary = project_root / "clarissa-mcp-server" / "target" / "debug" / "clarissa-mcp-server"

    if release_binary.exists():
        return str(release_binary)
    elif debug_binary.exists():
        return str(debug_binary)
    else:
        raise FileNotFoundError(
            "clarissa-mcp-server binary not found. Run: cd clarissa-mcp-server && cargo build --release"
        )


def init_clarissa_mcp_server():
    """Initialize the Clarissa MCP server entry in the database."""
    binary_path = get_clarissa_mcp_binary()
    print(f"Found clarissa-mcp-server binary: {binary_path}")

    with SessionLocal() as session:
        # Check if already exists
        existing = session.query(MCPServer).filter(MCPServer.name == "clarissa-tools").first()

        if existing:
            print(f"Updating existing clarissa-tools server entry...")
            existing.command = binary_path
            existing.source_type = "local"
            existing.transport = "stdio"
            existing.enabled = True
            existing.status = "stopped"
            existing.display_name = "Clarissa Native Tools"
        else:
            print("Creating new clarissa-tools server entry...")
            server = MCPServer(
                name="clarissa-tools",
                display_name="Clarissa Native Tools",
                source_type="local",
                source_url=binary_path,
                transport="stdio",
                command=binary_path,
                enabled=True,
                status="stopped",
            )
            session.add(server)

        session.commit()
        print("Clarissa MCP server initialized successfully!")

        # Verify
        server = session.query(MCPServer).filter(MCPServer.name == "clarissa-tools").first()
        print(f"  Name: {server.name}")
        print(f"  Command: {server.command}")
        print(f"  Transport: {server.transport}")
        print(f"  Enabled: {server.enabled}")


if __name__ == "__main__":
    init_clarissa_mcp_server()
