#!/usr/bin/env python3
"""
Clarissa MCP Server - Expose Clarissa's tools via Model Context Protocol.

This server exposes Clarissa's tool system (code execution, file management,
web search, etc.) via the MCP protocol for use with Claude Desktop or
other MCP-compatible applications.

Usage:
    # Stdio mode (for Claude Desktop integration)
    python mcp_server.py

    # SSE mode (for HTTP access)
    python mcp_server.py --sse --port 8002

    # With hot-reload enabled
    python mcp_server.py --hot-reload

Claude Desktop Configuration:
    Add to your claude_desktop_config.json:
    {
        "mcpServers": {
            "clarissa": {
                "command": "python",
                "args": ["/path/to/mypalclarissa/mcp_server.py"]
            }
        }
    }
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Ensure we don't print to stdout in stdio mode (corrupts JSON-RPC)
# All logging goes to stderr


async def main() -> None:
    """Main entry point for the MCP server."""
    parser = argparse.ArgumentParser(
        description="Clarissa MCP Server - Expose tools via Model Context Protocol"
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Run in SSE (HTTP) mode instead of stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_SERVER_PORT", "8002")),
        help="Port for SSE mode (default: 8002)",
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host for SSE mode (default: localhost)",
    )
    parser.add_argument(
        "--hot-reload",
        action="store_true",
        help="Enable hot-reload of tool modules",
    )
    args = parser.parse_args()

    # Import tools system
    from tools import get_loader, get_registry, init_tools
    from tools._mcp_server import ClarissaMCPServer, is_mcp_available

    if not is_mcp_available():
        print("Error: MCP package not installed.", file=sys.stderr)
        print("Install with: pip install mcp", file=sys.stderr)
        sys.exit(1)

    # Load all tool modules
    print("[mcp] Loading tool modules...", file=sys.stderr)
    results = await init_tools(hot_reload=args.hot_reload)

    loaded = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    print(f"[mcp] Loaded {loaded} modules, {failed} failed", file=sys.stderr)

    if args.hot_reload:
        print("[mcp] Hot-reload enabled", file=sys.stderr)

    # Create and run MCP server
    registry = get_registry()
    server = ClarissaMCPServer(registry)

    try:
        if args.sse:
            await server.run_sse(host=args.host, port=args.port)
        else:
            await server.run_stdio()
    except KeyboardInterrupt:
        print("\n[mcp] Shutting down...", file=sys.stderr)
    finally:
        # Cleanup
        from tools import shutdown_tools

        await shutdown_tools()


if __name__ == "__main__":
    asyncio.run(main())
