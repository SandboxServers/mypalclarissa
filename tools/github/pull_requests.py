"""GitHub pull request tools.

Tools for working with pull requests, reviews, and comments.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request, github_request_raw


# =============================================================================
# Handler Functions
# =============================================================================


async def list_pull_requests(args: dict[str, Any], ctx: ToolContext) -> str:
    """List pull requests in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {
        "state": args.get("state", "open"),
        "per_page": min(args.get("per_page", 20), 100),
    }
    if args.get("base"):
        params["base"] = args["base"]
    if args.get("head"):
        params["head"] = args["head"]

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/pulls", params=params
        )
        prs = [
            {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "user": pr["user"]["login"],
                "head": pr["head"]["ref"],
                "base": pr["base"]["ref"],
                "draft": pr.get("draft", False),
                "created_at": pr["created_at"],
            }
            for pr in result
        ]
        return json.dumps(prs, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}"
        )
        return json.dumps(
            {
                "number": result["number"],
                "title": result["title"],
                "state": result["state"],
                "body": result.get("body", ""),
                "user": result["user"]["login"],
                "head": {"ref": result["head"]["ref"], "sha": result["head"]["sha"]},
                "base": {"ref": result["base"]["ref"]},
                "mergeable": result.get("mergeable"),
                "merged": result.get("merged", False),
                "draft": result.get("draft", False),
                "additions": result.get("additions"),
                "deletions": result.get("deletions"),
                "changed_files": result.get("changed_files"),
                "created_at": result["created_at"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def create_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    title = args.get("title", "")
    head = args.get("head", "")
    base = args.get("base", "main")

    if not all([owner, repo, title, head]):
        return "Error: owner, repo, title, and head are required"

    data = {"title": title, "head": head, "base": base}
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("draft"):
        data["draft"] = args["draft"]

    try:
        result = await github_request(
            "POST", f"/repos/{owner}/{repo}/pulls", json_data=data
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


async def update_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update an existing pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    data = {}
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("state"):
        data["state"] = args["state"]
    if args.get("base"):
        data["base"] = args["base"]

    if not data:
        return "Error: at least one field to update is required"

    try:
        result = await github_request(
            "PATCH", f"/repos/{owner}/{repo}/pulls/{pull_number}", json_data=data
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


async def merge_pull_request(args: dict[str, Any], ctx: ToolContext) -> str:
    """Merge a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    data = {}
    if args.get("commit_title"):
        data["commit_title"] = args["commit_title"]
    if args.get("commit_message"):
        data["commit_message"] = args["commit_message"]
    if args.get("merge_method"):
        data["merge_method"] = args["merge_method"]  # merge, squash, rebase

    try:
        result = await github_request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{pull_number}/merge", json_data=data
        )
        return json.dumps(
            {
                "merged": result.get("merged", True),
                "sha": result.get("sha"),
                "message": result.get("message"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_pull_request_diff(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the diff of a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        diff = await github_request_raw(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pull_number}",
            accept="application/vnd.github.diff",
        )
        return diff
    except Exception as e:
        return f"Error: {e}"


async def list_pull_request_files(args: dict[str, Any], ctx: ToolContext) -> str:
    """List files changed in a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/files"
        )
        files = [
            {
                "filename": f["filename"],
                "status": f["status"],
                "additions": f["additions"],
                "deletions": f["deletions"],
            }
            for f in result
        ]
        return json.dumps(files, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# PR Review Functions (NEW!)
# =============================================================================


async def list_pr_reviews(args: dict[str, Any], ctx: ToolContext) -> str:
    """List reviews on a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews"
        )
        reviews = [
            {
                "id": r["id"],
                "user": r["user"]["login"],
                "state": r["state"],
                "body": r.get("body", ""),
                "submitted_at": r.get("submitted_at"),
            }
            for r in result
        ]
        return json.dumps(reviews, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_pr_review_comments(args: dict[str, Any], ctx: ToolContext) -> str:
    """List review comments on a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{pull_number}/comments"
        )
        comments = [
            {
                "id": c["id"],
                "user": c["user"]["login"],
                "body": c["body"],
                "path": c.get("path"),
                "line": c.get("line"),
                "created_at": c["created_at"],
            }
            for c in result
        ]
        return json.dumps(comments, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_pr_review(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a review on a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    event = args.get("event", "COMMENT")  # APPROVE, REQUEST_CHANGES, COMMENT

    if not all([owner, repo, pull_number]):
        return "Error: owner, repo, and pull_number are required"

    if event not in ["APPROVE", "REQUEST_CHANGES", "COMMENT"]:
        return "Error: event must be APPROVE, REQUEST_CHANGES, or COMMENT"

    data = {"event": event}
    if args.get("body"):
        data["body"] = args["body"]
    if args.get("comments"):
        data["comments"] = args["comments"]

    try:
        result = await github_request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            json_data=data,
        )
        return json.dumps(
            {
                "created": True,
                "id": result["id"],
                "state": result["state"],
                "url": result.get("html_url"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def add_pr_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a general comment to a pull request (conversation tab)."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    body = args.get("body", "")

    if not all([owner, repo, pull_number, body]):
        return "Error: owner, repo, pull_number, and body are required"

    try:
        # PRs are issues, so we use the issues endpoint for general comments
        result = await github_request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{pull_number}/comments",
            json_data={"body": body},
        )
        return json.dumps(
            {"created": True, "id": result["id"], "url": result["html_url"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def add_pr_review_comment(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add an inline review comment on a specific file/line in a pull request."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    pull_number = args.get("pull_number")
    body = args.get("body", "")
    path = args.get("path", "")
    
    if not all([owner, repo, pull_number, body, path]):
        return "Error: owner, repo, pull_number, body, and path are required"

    data = {"body": body, "path": path}
    
    # Either line (for single-line) or start_line + line (for multi-line)
    if args.get("line"):
        data["line"] = args["line"]
    if args.get("start_line"):
        data["start_line"] = args["start_line"]
    if args.get("side"):
        data["side"] = args["side"]  # LEFT or RIGHT
    
    # commit_id is required
    if args.get("commit_id"):
        data["commit_id"] = args["commit_id"]
    else:
        # Get the latest commit on the PR
        try:
            pr = await github_request("GET", f"/repos/{owner}/{repo}/pulls/{pull_number}")
            data["commit_id"] = pr["head"]["sha"]
        except Exception as e:
            return f"Error getting PR head commit: {e}"

    try:
        result = await github_request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pull_number}/comments",
            json_data=data,
        )
        return json.dumps(
            {
                "created": True,
                "id": result["id"],
                "url": result["html_url"],
                "path": result["path"],
                "line": result.get("line"),
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    ToolDef(
        name="github_list_pull_requests",
        description="List pull requests in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "base": {"type": "string", "description": "Filter by base branch"},
                "head": {"type": "string", "description": "Filter by head branch"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_pull_requests,
    ),
    ToolDef(
        name="github_get_pull_request",
        description="Get details of a specific pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=get_pull_request,
    ),
    ToolDef(
        name="github_create_pull_request",
        description="Create a new pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "PR title"},
                "head": {"type": "string", "description": "Head branch (source)"},
                "base": {"type": "string", "description": "Base branch (target, default: main)"},
                "body": {"type": "string", "description": "PR description"},
                "draft": {"type": "boolean", "description": "Create as draft PR"},
            },
            "required": ["owner", "repo", "title", "head"],
        },
        handler=create_pull_request,
    ),
    ToolDef(
        name="github_update_pull_request",
        description="Update an existing pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body"},
                "state": {"type": "string", "enum": ["open", "closed"]},
                "base": {"type": "string", "description": "New base branch"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=update_pull_request,
    ),
    ToolDef(
        name="github_merge_pull_request",
        description="Merge a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "commit_title": {"type": "string", "description": "Title for merge commit"},
                "commit_message": {"type": "string", "description": "Message for merge commit"},
                "merge_method": {"type": "string", "enum": ["merge", "squash", "rebase"]},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=merge_pull_request,
    ),
    ToolDef(
        name="github_get_pull_request_diff",
        description="Get the diff of a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=get_pull_request_diff,
    ),
    ToolDef(
        name="github_list_pull_request_files",
        description="List files changed in a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=list_pull_request_files,
    ),
    # NEW PR Review Tools
    ToolDef(
        name="github_list_pr_reviews",
        description="List reviews on a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=list_pr_reviews,
    ),
    ToolDef(
        name="github_list_pr_review_comments",
        description="List inline review comments on a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
            },
            "required": ["owner", "repo", "pull_number"],
        },
        handler=list_pr_review_comments,
    ),
    ToolDef(
        name="github_create_pr_review",
        description="Create a review on a pull request (APPROVE, REQUEST_CHANGES, or COMMENT).",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "event": {"type": "string", "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"], "description": "Review action"},
                "body": {"type": "string", "description": "Review body/comment"},
                "comments": {"type": "array", "description": "Inline comments [{path, line, body}]"},
            },
            "required": ["owner", "repo", "pull_number", "event"],
        },
        handler=create_pr_review,
    ),
    ToolDef(
        name="github_add_pr_comment",
        description="Add a general comment to a pull request conversation.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "body": {"type": "string", "description": "Comment body"},
            },
            "required": ["owner", "repo", "pull_number", "body"],
        },
        handler=add_pr_comment,
    ),
    ToolDef(
        name="github_add_pr_review_comment",
        description="Add an inline comment on a specific file/line in a pull request.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pull_number": {"type": "integer", "description": "Pull request number"},
                "body": {"type": "string", "description": "Comment body"},
                "path": {"type": "string", "description": "File path to comment on"},
                "line": {"type": "integer", "description": "Line number to comment on"},
                "start_line": {"type": "integer", "description": "Start line for multi-line comment"},
                "side": {"type": "string", "enum": ["LEFT", "RIGHT"], "description": "Side of diff"},
                "commit_id": {"type": "string", "description": "Commit SHA (defaults to PR head)"},
            },
            "required": ["owner", "repo", "pull_number", "body", "path"],
        },
        handler=add_pr_review_comment,
    ),
]
