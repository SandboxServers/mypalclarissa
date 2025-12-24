"""GitHub issues tools.

Tools for working with GitHub issues and comments.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


# =============================================================================
# Handler Functions
# =============================================================================


async def list_issues(args: dict[str, Any], ctx: ToolContext) -> str:
    """List issues in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {
        "state": args.get("state", "open"),
        "per_page": min(args.get("per_page", 20), 100),
    }
    if args.get("labels"):
        params["labels"] = args["labels"]
    if args.get("assignee"):
        params["assignee"] = args["assignee"]

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/issues", params=params
        )
        issues = [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "user": i["user"]["login"],
                "labels": [l["name"] for l in i.get("labels", [])],
                "created_at": i["created_at"],
            }
            for i in result
            if "pull_request" not in i  # Exclude PRs
        ]
        return json.dumps(issues, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/issues/{issue_number}"
        )
        return json.dumps(
            {
                "number": result["number"],
                "title": result["title"],
                "state": result["state"],
                "body": result.get("body", ""),
                "user": result["user"]["login"],
                "labels": [l["name"] for l in result.get("labels", [])],
                "assignees": [a["login"] for a in result.get("assignees", [])],
                "created_at": result["created_at"],
                "updated_at": result["updated_at"],
                "comments": result["comments"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    title = args.get("title", "")
    if not all([owner, repo, title]):
        return "Error: owner, repo, and title are required"

    data = {"title": title}
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("labels"):
        data["labels"] = args["labels"]
    if args.get("assignees"):
        data["assignees"] = args["assignees"]

    try:
        result = await github_request(
            "POST", f"/repos/{owner}/{repo}/issues", json_data=data
        )
        return json.dumps(
            {
                "created": True,
                "number": result["number"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def update_issue(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    data = {}
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("state"):
        data["state"] = args["state"]
    if args.get("labels"):
        data["labels"] = args["labels"]
    if args.get("assignees"):
        data["assignees"] = args["assignees"]

    if not data:
        return "Error: at least one field to update is required"

    try:
        result = await github_request(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json_data=data
        )
        return json.dumps(
            {
                "updated": True,
                "number": result["number"],
                "state": result["state"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def add_issue_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a comment to an issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    body = args.get("body", "")
    if not all([owner, repo, issue_number, body]):
        return "Error: owner, repo, issue_number, and body are required"

    try:
        result = await github_request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json_data={"body": body},
        )
        return json.dumps(
            {"created": True, "id": result["id"], "url": result["html_url"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def list_issue_comments(args: dict[str, Any], ctx: ToolContext) -> str:
    """List comments on an issue."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    issue_number = args.get("issue_number")
    if not all([owner, repo, issue_number]):
        return "Error: owner, repo, and issue_number are required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await github_request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            params={"per_page": per_page},
        )
        comments = [
            {
                "id": c["id"],
                "user": c["user"]["login"],
                "body": c["body"],
                "created_at": c["created_at"],
            }
            for c in result
        ]
        return json.dumps(comments, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def search_issues(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for issues and pull requests."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 20), 100)

    try:
        result = await github_request(
            "GET", "/search/issues", params={"q": query, "per_page": per_page}
        )
        items = [
            {
                "number": i["number"],
                "title": i["title"],
                "state": i["state"],
                "repository": i["repository_url"].split("/")[-2:]
                if "repository_url" in i
                else None,
                "url": i["html_url"],
                "is_pr": "pull_request" in i,
            }
            for i in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "items": items}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    ToolDef(
        name="github_list_issues",
        description="List issues in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "labels": {"type": "string", "description": "Comma-separated label names"},
                "assignee": {"type": "string", "description": "Filter by assignee"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_issues,
    ),
    ToolDef(
        name="github_get_issue",
        description="Get details of a specific issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=get_issue,
    ),
    ToolDef(
        name="github_create_issue",
        description="Create a new issue in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels to add"},
                "assignees": {"type": "array", "items": {"type": "string"}, "description": "Assignees"},
            },
            "required": ["owner", "repo", "title"],
        },
        handler=create_issue,
    ),
    ToolDef(
        name="github_update_issue",
        description="Update an existing issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body"},
                "state": {"type": "string", "enum": ["open", "closed"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "assignees": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=update_issue,
    ),
    ToolDef(
        name="github_add_issue_comment",
        description="Add a comment to an issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "body": {"type": "string", "description": "Comment body"},
            },
            "required": ["owner", "repo", "issue_number", "body"],
        },
        handler=add_issue_comment,
    ),
    ToolDef(
        name="github_list_issue_comments",
        description="List comments on an issue.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo", "issue_number"],
        },
        handler=list_issue_comments,
    ),
    ToolDef(
        name="github_search_issues",
        description="Search for issues and pull requests across GitHub.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'is:open is:issue repo:owner/repo')"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
        handler=search_issues,
    ),
]
