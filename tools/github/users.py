"""GitHub user tools.

Tools for working with GitHub users and profiles.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


# =============================================================================
# Handler Functions
# =============================================================================


async def get_me(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the authenticated user's profile."""
    try:
        user = await github_request("GET", "/user")
        return json.dumps(user, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def search_users(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for GitHub users."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await github_request(
            "GET", "/search/users", params={"q": query, "per_page": per_page}
        )
        users = [
            {"login": u["login"], "url": u["html_url"], "type": u["type"]}
            for u in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "users": users}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    ToolDef(
        name="github_get_me",
        description="Get the authenticated GitHub user's profile information.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=get_me,
    ),
    ToolDef(
        name="github_search_users",
        description="Search for GitHub users by username, name, or other criteria.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "per_page": {"type": "integer", "description": "Results per page (max 100)"},
            },
            "required": ["query"],
        },
        handler=search_users,
    ),
]
