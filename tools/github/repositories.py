"""GitHub repository tools.

Tools for working with repositories, files, branches, and commits.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


# =============================================================================
# Handler Functions
# =============================================================================


async def search_repositories(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for repositories."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)
    sort = args.get("sort", "best-match")

    try:
        result = await github_request(
            "GET",
            "/search/repositories",
            params={"q": query, "per_page": per_page, "sort": sort},
        )
        repos = [
            {
                "full_name": r["full_name"],
                "description": r.get("description", ""),
                "url": r["html_url"],
                "stars": r["stargazers_count"],
                "language": r.get("language"),
            }
            for r in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "repositories": repos},
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get repository details."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await github_request("GET", f"/repos/{owner}/{repo}")
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new repository."""
    name = args.get("name", "")
    if not name:
        return "Error: name is required"

    data = {
        "name": name,
        "description": args.get("description", ""),
        "private": args.get("private", False),
        "auto_init": args.get("auto_init", False),
    }

    try:
        result = await github_request("POST", "/user/repos", json_data=data)
        return json.dumps(
            {
                "created": True,
                "full_name": result["full_name"],
                "url": result["html_url"],
                "clone_url": result["clone_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def fork_repository(args: dict[str, Any], ctx: ToolContext) -> str:
    """Fork a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    data = {}
    if args.get("organization"):
        data["organization"] = args["organization"]

    try:
        result = await github_request(
            "POST", f"/repos/{owner}/{repo}/forks", json_data=data
        )
        return json.dumps(
            {
                "forked": True,
                "full_name": result["full_name"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def get_file_contents(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get file or directory contents from a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {}
    if args.get("ref"):
        params["ref"] = args["ref"]

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}", params=params
        )

        if isinstance(result, list):
            # Directory listing
            items = [
                {"name": item["name"], "type": item["type"], "path": item["path"]}
                for item in result
            ]
            return json.dumps({"type": "directory", "items": items}, indent=2)
        else:
            # File content
            if result.get("encoding") == "base64" and result.get("content"):
                content = base64.b64decode(result["content"]).decode("utf-8")
                return json.dumps(
                    {
                        "type": "file",
                        "path": result["path"],
                        "size": result["size"],
                        "content": content,
                    },
                    indent=2,
                )
            return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_or_update_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create or update a file in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    content = args.get("content", "")
    message = args.get("message", "")

    if not all([owner, repo, path, message]):
        return "Error: owner, repo, path, and message are required"

    data = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
    }

    if args.get("branch"):
        data["branch"] = args["branch"]

    # Check if file exists to get SHA
    if args.get("sha"):
        data["sha"] = args["sha"]
    else:
        try:
            existing = await github_request(
                "GET", f"/repos/{owner}/{repo}/contents/{path}"
            )
            if isinstance(existing, dict) and existing.get("sha"):
                data["sha"] = existing["sha"]
        except Exception:
            pass  # File doesn't exist, creating new

    try:
        result = await github_request(
            "PUT", f"/repos/{owner}/{repo}/contents/{path}", json_data=data
        )
        return json.dumps(
            {
                "success": True,
                "path": result["content"]["path"],
                "sha": result["content"]["sha"],
                "commit_sha": result["commit"]["sha"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def delete_file(args: dict[str, Any], ctx: ToolContext) -> str:
    """Delete a file from a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    path = args.get("path", "")
    message = args.get("message", "")
    sha = args.get("sha", "")

    if not all([owner, repo, path, message, sha]):
        return "Error: owner, repo, path, message, and sha are required"

    data = {"message": message, "sha": sha}
    if args.get("branch"):
        data["branch"] = args["branch"]

    try:
        result = await github_request(
            "DELETE", f"/repos/{owner}/{repo}/contents/{path}", json_data=data
        )
        return json.dumps(
            {"deleted": True, "commit_sha": result["commit"]["sha"]}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def list_branches(args: dict[str, Any], ctx: ToolContext) -> str:
    """List branches in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    per_page = min(args.get("per_page", 30), 100)

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/branches", params={"per_page": per_page}
        )
        branches = [
            {"name": b["name"], "protected": b.get("protected", False)} for b in result
        ]
        return json.dumps(branches, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def create_branch(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new branch."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    branch = args.get("branch", "")
    from_branch = args.get("from_branch", "main")

    if not all([owner, repo, branch]):
        return "Error: owner, repo, and branch are required"

    try:
        # Get the SHA of the source branch
        ref_result = await github_request(
            "GET", f"/repos/{owner}/{repo}/git/refs/heads/{from_branch}"
        )
        sha = ref_result["object"]["sha"]

        # Create the new branch
        result = await github_request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json_data={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        return json.dumps(
            {"created": True, "branch": branch, "sha": result["object"]["sha"]},
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def list_commits(args: dict[str, Any], ctx: ToolContext) -> str:
    """List commits in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {"per_page": min(args.get("per_page", 20), 100)}
    if args.get("sha"):
        params["sha"] = args["sha"]
    if args.get("path"):
        params["path"] = args["path"]

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/commits", params=params
        )
        commits = [
            {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"].split("\n")[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in result
        ]
        return json.dumps(commits, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_commit(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific commit."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    sha = args.get("sha", "")
    if not all([owner, repo, sha]):
        return "Error: owner, repo, and sha are required"

    try:
        result = await github_request("GET", f"/repos/{owner}/{repo}/commits/{sha}")
        return json.dumps(
            {
                "sha": result["sha"],
                "message": result["commit"]["message"],
                "author": result["commit"]["author"],
                "stats": result.get("stats"),
                "files": [
                    {"filename": f["filename"], "status": f["status"]}
                    for f in result.get("files", [])
                ],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def search_code(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search for code across repositories."""
    query = args.get("query", "")
    if not query:
        return "Error: query is required"

    per_page = min(args.get("per_page", 10), 100)

    try:
        result = await github_request(
            "GET", "/search/code", params={"q": query, "per_page": per_page}
        )
        items = [
            {
                "name": item["name"],
                "path": item["path"],
                "repository": item["repository"]["full_name"],
                "url": item["html_url"],
            }
            for item in result.get("items", [])
        ]
        return json.dumps(
            {"total_count": result.get("total_count", 0), "items": items}, indent=2
        )
    except Exception as e:
        return f"Error: {e}"


async def get_repository_tree(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the file tree of a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    tree_sha = args.get("tree_sha", "HEAD")
    recursive = args.get("recursive", True)

    try:
        params = {}
        if recursive:
            params["recursive"] = "1"

        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}", params=params
        )
        tree = [
            {"path": item["path"], "type": item["type"], "size": item.get("size")}
            for item in result.get("tree", [])
        ]
        return json.dumps({"sha": result["sha"], "tree": tree}, indent=2)
    except Exception as e:
        return f"Error: {e}"


# =============================================================================
# Tool Definitions
# =============================================================================

TOOLS = [
    ToolDef(
        name="github_search_repositories",
        description="Search for GitHub repositories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'language:python stars:>1000')"},
                "per_page": {"type": "integer", "description": "Results per page (max 100)"},
                "sort": {"type": "string", "enum": ["stars", "forks", "updated", "best-match"]},
            },
            "required": ["query"],
        },
        handler=search_repositories,
    ),
    ToolDef(
        name="github_get_repository",
        description="Get detailed information about a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_repository,
    ),
    ToolDef(
        name="github_create_repository",
        description="Create a new repository for the authenticated user.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Repository name"},
                "description": {"type": "string", "description": "Repository description"},
                "private": {"type": "boolean", "description": "Whether the repo is private"},
                "auto_init": {"type": "boolean", "description": "Initialize with README"},
            },
            "required": ["name"],
        },
        handler=create_repository,
    ),
    ToolDef(
        name="github_fork_repository",
        description="Fork a repository to your account or an organization.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "organization": {"type": "string", "description": "Organization to fork to (optional)"},
            },
            "required": ["owner", "repo"],
        },
        handler=fork_repository,
    ),
    ToolDef(
        name="github_get_file_contents",
        description="Get the contents of a file or directory from a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file or directory"},
                "ref": {"type": "string", "description": "Git ref (branch, tag, or SHA)"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_file_contents,
    ),
    ToolDef(
        name="github_create_or_update_file",
        description="Create or update a file in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file"},
                "content": {"type": "string", "description": "File content"},
                "message": {"type": "string", "description": "Commit message"},
                "branch": {"type": "string", "description": "Branch name"},
                "sha": {"type": "string", "description": "SHA of file being replaced (for updates)"},
            },
            "required": ["owner", "repo", "path", "content", "message"],
        },
        handler=create_or_update_file,
    ),
    ToolDef(
        name="github_delete_file",
        description="Delete a file from a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file"},
                "message": {"type": "string", "description": "Commit message"},
                "sha": {"type": "string", "description": "SHA of file to delete"},
                "branch": {"type": "string", "description": "Branch name"},
            },
            "required": ["owner", "repo", "path", "message", "sha"],
        },
        handler=delete_file,
    ),
    ToolDef(
        name="github_list_branches",
        description="List branches in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_branches,
    ),
    ToolDef(
        name="github_create_branch",
        description="Create a new branch in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "New branch name"},
                "from_branch": {"type": "string", "description": "Source branch (default: main)"},
            },
            "required": ["owner", "repo", "branch"],
        },
        handler=create_branch,
    ),
    ToolDef(
        name="github_list_commits",
        description="List commits in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "sha": {"type": "string", "description": "Branch or SHA to list commits from"},
                "path": {"type": "string", "description": "Only commits containing this path"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_commits,
    ),
    ToolDef(
        name="github_get_commit",
        description="Get details of a specific commit.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "sha": {"type": "string", "description": "Commit SHA"},
            },
            "required": ["owner", "repo", "sha"],
        },
        handler=get_commit,
    ),
    ToolDef(
        name="github_search_code",
        description="Search for code across GitHub repositories.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (e.g., 'addClass repo:jquery/jquery')"},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["query"],
        },
        handler=search_code,
    ),
    ToolDef(
        name="github_get_repository_tree",
        description="Get the file tree of a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "tree_sha": {"type": "string", "description": "Tree SHA or 'HEAD' (default)"},
                "recursive": {"type": "boolean", "description": "Get full tree recursively"},
            },
            "required": ["owner", "repo"],
        },
        handler=get_repository_tree,
    ),
]
