"""GitHub stars tools.

Tools for starring/unstarring repositories.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


async def list_starred_repos(args: dict[str, Any], ctx: ToolContext) -> str:
    """List repositories starred by the authenticated user."""
    per_page = min(args.get("per_page", 20), 100)
    sort = args.get("sort", "created")

    try:
        result = await github_request(
            "GET", "/user/starred", params={"per_page": per_page, "sort": sort}
        )
        repos = [
            {
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "stars": r["stargazers_count"],
                "url": r["html_url"],
            }
            for r in result
        ]
        return json.dumps(repos, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def star_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Star a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        await github_request("PUT", f"/user/starred/{owner}/{repo}")
        return json.dumps({"starred": True, "repository": f"{owner}/{repo}"}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def unstar_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Unstar a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        await github_request("DELETE", f"/user/starred/{owner}/{repo}")
        return json.dumps({"unstarred": True, "repository": f"{owner}/{repo}"}, indent=2)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    ToolDef(
        name="github_list_starred_repos",
        description="List repositories starred by the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Results per page"},
                "sort": {"type": "string", "enum": ["created", "updated"]},
            },
            "required": [],
        },
        handler=list_starred_repos,
    ),
    ToolDef(
        name="github_star_repository",
        description="Star a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=star_repository,
    ),
    ToolDef(
        name="github_unstar_repository",
        description="Unstar a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=unstar_repository,
    ),
]
