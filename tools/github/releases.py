"""GitHub releases and tags tools.

Tools for working with releases and tags.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


async def list_releases(args: dict[str, Any], ctx: ToolContext) -> str:
    """List releases in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/releases", params={"per_page": per_page}
        )
        releases = [
            {
                "id": r["id"],
                "tag_name": r["tag_name"],
                "name": r.get("name", ""),
                "draft": r["draft"],
                "prerelease": r["prerelease"],
                "created_at": r["created_at"],
                "url": r["html_url"],
            }
            for r in result
        ]
        return json.dumps(releases, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_latest_release(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the latest release of a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/releases/latest"
        )
        return json.dumps(
            {
                "tag_name": result["tag_name"],
                "name": result.get("name", ""),
                "body": result.get("body", ""),
                "draft": result["draft"],
                "prerelease": result["prerelease"],
                "created_at": result["created_at"],
                "url": result["html_url"],
                "assets": [
                    {"name": a["name"], "download_url": a["browser_download_url"]}
                    for a in result.get("assets", [])
                ],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def list_tags(args: dict[str, Any], ctx: ToolContext) -> str:
    """List tags in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/tags", params={"per_page": per_page}
        )
        tags = [{"name": t["name"], "sha": t["commit"]["sha"][:7]} for t in result]
        return json.dumps(tags, indent=2)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    ToolDef(
        name="github_list_releases",
        description="List releases in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_releases,
    ),
    ToolDef(
        name="github_get_latest_release",
        description="Get the latest release of a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_latest_release,
    ),
    ToolDef(
        name="github_list_tags",
        description="List tags in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_tags,
    ),
]
