"""GitHub Actions tools.

Tools for working with GitHub Actions workflows and runs.
"""

from __future__ import annotations

import json
from typing import Any

from .._base import ToolContext, ToolDef
from ._client import github_request


async def list_workflows(args: dict[str, Any], ctx: ToolContext) -> str:
    """List workflows in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    try:
        result = await github_request("GET", f"/repos/{owner}/{repo}/actions/workflows")
        workflows = [
            {
                "id": w["id"],
                "name": w["name"],
                "path": w["path"],
                "state": w["state"],
            }
            for w in result.get("workflows", [])
        ]
        return json.dumps(workflows, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def list_workflow_runs(args: dict[str, Any], ctx: ToolContext) -> str:
    """List workflow runs in a repository."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    if not owner or not repo:
        return "Error: owner and repo are required"

    params = {"per_page": min(args.get("per_page", 10), 100)}
    if args.get("workflow_id"):
        params["workflow_id"] = args["workflow_id"]
    if args.get("branch"):
        params["branch"] = args["branch"]
    if args.get("status"):
        params["status"] = args["status"]

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/actions/runs", params=params
        )
        runs = [
            {
                "id": r["id"],
                "name": r["name"],
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "branch": r["head_branch"],
                "event": r["event"],
                "created_at": r["created_at"],
                "url": r["html_url"],
            }
            for r in result.get("workflow_runs", [])
        ]
        return json.dumps({"total_count": result.get("total_count", 0), "runs": runs}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def get_workflow_run(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get details of a specific workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        result = await github_request(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}"
        )
        return json.dumps(
            {
                "id": result["id"],
                "name": result["name"],
                "status": result["status"],
                "conclusion": result.get("conclusion"),
                "branch": result["head_branch"],
                "sha": result["head_sha"][:7],
                "event": result["event"],
                "created_at": result["created_at"],
                "updated_at": result["updated_at"],
                "url": result["html_url"],
            },
            indent=2,
        )
    except Exception as e:
        return f"Error: {e}"


async def run_workflow(args: dict[str, Any], ctx: ToolContext) -> str:
    """Trigger a workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    workflow_id = args.get("workflow_id", "")
    ref = args.get("ref", "main")

    if not all([owner, repo, workflow_id]):
        return "Error: owner, repo, and workflow_id are required"

    data = {"ref": ref}
    if args.get("inputs"):
        data["inputs"] = args["inputs"]

    try:
        await github_request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
            json_data=data,
        )
        return json.dumps({"triggered": True, "workflow_id": workflow_id, "ref": ref}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def cancel_workflow_run(args: dict[str, Any], ctx: ToolContext) -> str:
    """Cancel a workflow run."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        await github_request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel"
        )
        return json.dumps({"cancelled": True, "run_id": run_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


async def rerun_workflow(args: dict[str, Any], ctx: ToolContext) -> str:
    """Re-run a workflow."""
    owner = args.get("owner", "")
    repo = args.get("repo", "")
    run_id = args.get("run_id")
    if not all([owner, repo, run_id]):
        return "Error: owner, repo, and run_id are required"

    try:
        await github_request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun"
        )
        return json.dumps({"rerun": True, "run_id": run_id}, indent=2)
    except Exception as e:
        return f"Error: {e}"


TOOLS = [
    ToolDef(
        name="github_list_workflows",
        description="List workflows in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_workflows,
    ),
    ToolDef(
        name="github_list_workflow_runs",
        description="List workflow runs in a repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "workflow_id": {"type": "string", "description": "Filter by workflow ID or filename"},
                "branch": {"type": "string", "description": "Filter by branch"},
                "status": {"type": "string", "enum": ["queued", "in_progress", "completed"]},
                "per_page": {"type": "integer", "description": "Results per page"},
            },
            "required": ["owner", "repo"],
        },
        handler=list_workflow_runs,
    ),
    ToolDef(
        name="github_get_workflow_run",
        description="Get details of a specific workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=get_workflow_run,
    ),
    ToolDef(
        name="github_run_workflow",
        description="Trigger a workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "workflow_id": {"type": "string", "description": "Workflow ID or filename"},
                "ref": {"type": "string", "description": "Git ref to run on (default: main)"},
                "inputs": {"type": "object", "description": "Workflow inputs"},
            },
            "required": ["owner", "repo", "workflow_id"],
        },
        handler=run_workflow,
    ),
    ToolDef(
        name="github_cancel_workflow_run",
        description="Cancel a workflow run.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=cancel_workflow_run,
    ),
    ToolDef(
        name="github_rerun_workflow",
        description="Re-run a workflow.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {"type": "integer", "description": "Workflow run ID"},
            },
            "required": ["owner", "repo", "run_id"],
        },
        handler=rerun_workflow,
    ),
]
