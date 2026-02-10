"""GitHub notifications tools.

Tools for working with GitHub notifications.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


async def list_notifications(args: dict[str, Any], ctx: ToolContext) -> str:
    """List notifications for the authenticated user."""
    params = {
        "all": args.get("all", False),
        "per_page": min(args.get("per_page", 20), 100),
    }

    try:
        result = await github_request("GET", "/notifications", params=params)
        notifications = [
            {
                "id": n["id"],
                "reason": n["reason"],
                "unread": n["unread"],
                "subject": {
                    "title": n["subject"]["title"],
                    "type": n["subject"]["type"],
                },
                "repository": n["repository"]["full_name"],
                "updated_at": n["updated_at"],
            }
            for n in result
        ]
        return json.dumps(notifications, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def mark_notifications_read(args: dict[str, Any], ctx: ToolContext) -> str:
    """Mark all notifications as read."""
    try:
        await github_request("PUT", "/notifications")
        return json.dumps({"marked_read": True}, indent=2)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    ToolDef(
        name="github_list_notifications",
        description="List notifications for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "all": {"type": "boolean", "description": "Include read notifications"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": [],
        },
        handler=list_notifications,
    ),
    ToolDef(
        name="github_mark_notifications_read",
        description="Mark all notifications as read.",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=mark_notifications_read,
    ),
]
