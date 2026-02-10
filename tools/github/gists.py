"""GitHub gists tools.

Tools for working with GitHub Gists.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


async def list_gists(args: dict[str, Any], ctx: ToolContext) -> str:
    """List gists for the authenticated user."""
    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await github_request("GET", "/gists", params={"per_page": per_page})
        gists = [
            {
                "id": g["id"],
                "description": g.get("description", ""),
                "public": g["public"],
                "files": list(g["files"].keys()),
                "url": g["html_url"],
                "created_at": g["created_at"],
            }
            for g in result
        ]
        return json.dumps(gists, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get a specific gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    try:
        result = await github_request("GET", f"/gists/{gist_id}")
        files = {
            name: {"content": f.get("content", ""), "language": f.get("language")}
            for name, f in result.get("files", {}).items()
        }
        return json.dumps(
            {
                "id": result["id"],
                "description": result.get("description", ""),
                "public": result["public"],
                "files": files,
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new gist."""
    files = args.get("files", {})
    if not files:
        return "Error: files is required (dict of filename: content)"

    data = {
        "files": {name: {"content": content} for name, content in files.items()},
        "public": args.get("public", False),
    }
    if args.get("description"):
        data["description"] = args["description"]

    try:
        result = await github_request("POST", "/gists", json_data=data)
        return json.dumps(
            {
                "created": True,
                "id": result["id"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    data = {}
    if args.get("description"):
        data["description"] = args["description"]
    if args.get("files"):
        data["files"] = {
            name: {"content": content} for name, content in args["files"].items()
        }

    if not data:
        return "Error: at least description or files is required"

    try:
        result = await github_request("PATCH", f"/gists/{gist_id}", json_data=data)
        return json.dumps(
            {"updated": True, "id": result["id"], "url": result["html_url"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def delete_gist(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a gist."""
    gist_id = args.get("gist_id", "")
    if not gist_id:
        return "Error: gist_id is required"

    try:
        await github_request("DELETE", f"/gists/{gist_id}")
        return json.dumps({"deleted": True, "gist_id": gist_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    ToolDef(
        name="github_list_gists",
        description="List gists for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": [],
        },
        handler=list_gists,
    ),
    ToolDef(
        name="github_get_gist",
        description="Get a specific gist with its contents.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
            },
            "required": ["gist_id"],
        },
        handler=get_gist,
    ),
    ToolDef(
        name="github_create_gist",
        description="Create a new gist.",
        parameters={
            "type": "object",
            "properties": {
                "files": {"type": "object", "description": "Object mapping filename to content"},
                "description": {"type": "string", "description": "Gist description"},
                "public": {"type": "boolean", "description": "Whether the gist is public"},
            },
            "required": ["files"],
        },
        handler=create_gist,
    ),
    ToolDef(
        name="github_update_gist",
        description="Update an existing gist.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
                "files": {"type": "object", "description": "Object mapping filename to content"},
                "description": {"type": "string", "description": "New description"},
            },
            "required": ["gist_id"],
        },
        handler=update_gist,
    ),
    ToolDef(
        name="github_delete_gist",
        description="Delete a gist.",
        parameters={
            "type": "object",
            "properties": {
                "gist_id": {"type": "string", "description": "Gist ID"},
            },
            "required": ["gist_id"],
        },
        handler=delete_gist,
    ),
]
