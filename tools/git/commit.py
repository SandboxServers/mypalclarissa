"""
Git commit and log operations.
"""

import json
import re
from typing import Any, Optional

from tools._base import ToolContext, ToolDef

from ._runner import run_git


def git_commit(
    message: str,
    all_changes: bool = False,
    amend: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Commit staged changes.

    Args:
        message: Commit message
        all_changes: Stage all modified files before commit (-a)
        amend: Amend the last commit
        cwd: Repository directory

    Returns:
        dict with 'success', 'sha', 'message'
    """
    args = ['commit', '-m', message]

    if all_changes:
        args.insert(1, '-a')

    if amend:
        args.insert(1, '--amend')

    success, stdout, stderr = run_git(*args, cwd=cwd)

    output = stdout or stderr

    # Try to extract commit SHA from output
    sha = None
    if success and output:
        # Output usually contains: [branch sha] message
        match = re.search(r'\[[\w/-]+ ([a-f0-9]+)\]', output)
        if match:
            sha = match.group(1)

    return {
        'success': success,
        'sha': sha,
        'message': output.strip() if output else ("Committed" if success else "Commit failed")
    }


def git_log(
    n: int = 10,
    oneline: bool = True,
    file: Optional[str] = None,
    cwd: Optional[str] = None
) -> dict:
    """
    Show commit history.

    Args:
        n: Number of commits to show
        oneline: One line per commit format
        file: Show commits affecting this file
        cwd: Repository directory

    Returns:
        dict with 'success', 'commits'
    """
    args = ['log', f'-{n}']

    if oneline:
        args.append('--oneline')
    else:
        args.append('--format=%H|%an|%ae|%s|%ci')

    if file:
        args.extend(['--', file])

    success, stdout, stderr = run_git(*args, cwd=cwd)

    if not success:
        return {'success': False, 'commits': [], 'message': stderr}

    commits = []
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue

        if oneline:
            parts = line.split(' ', 1)
            commits.append({
                'sha': parts[0],
                'message': parts[1] if len(parts) > 1 else ''
            })
        else:
            parts = line.split('|')
            if len(parts) >= 5:
                commits.append({
                    'sha': parts[0],
                    'author': parts[1],
                    'email': parts[2],
                    'message': parts[3],
                    'date': parts[4]
                })

    return {
        'success': True,
        'commits': commits
    }


def git_rev_parse(
    ref: str = "HEAD",
    short: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Get the SHA for a ref.

    Args:
        ref: Reference to resolve (branch, tag, HEAD, etc.)
        short: Return short SHA
        cwd: Repository directory

    Returns:
        dict with 'success', 'sha'
    """
    args = ['rev-parse']

    if short:
        args.append('--short')

    args.append(ref)

    success, stdout, stderr = run_git(*args, cwd=cwd)

    return {
        'success': success,
        'sha': stdout.strip() if success else None,
        'message': stderr if not success else None
    }


# Async handler wrappers for tool system
async def _handle_git_commit(arguments: dict[str, Any], context: ToolContext) -> str:
    result = git_commit(
        message=arguments["message"],
        all_changes=arguments.get("all_changes", False),
        amend=arguments.get("amend", False),
        cwd=arguments.get("cwd"),
    )
    return json.dumps(result)


async def _handle_git_log(arguments: dict[str, Any], context: ToolContext) -> str:
    result = git_log(
        n=arguments.get("n", 10),
        oneline=arguments.get("oneline", True),
        file=arguments.get("file"),
        cwd=arguments.get("cwd"),
    )
    return json.dumps(result)


async def _handle_git_rev_parse(arguments: dict[str, Any], context: ToolContext) -> str:
    result = git_rev_parse(
        ref=arguments.get("ref", "HEAD"),
        short=arguments.get("short", False),
        cwd=arguments.get("cwd"),
    )
    return json.dumps(result)


# Tool definitions
TOOLS = [
    ToolDef(
        name="git_commit",
        description="Commit staged changes with a message.",
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message"
                },
                "all_changes": {
                    "type": "boolean",
                    "description": "Stage all modified files (-a) (default: false)"
                },
                "amend": {
                    "type": "boolean",
                    "description": "Amend the last commit (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": ["message"]
        },
        handler=_handle_git_commit,
    ),
    ToolDef(
        name="git_log",
        description="Show commit history.",
        parameters={
            "type": "object",
            "properties": {
                "n": {
                    "type": "integer",
                    "description": "Number of commits (default: 10)"
                },
                "oneline": {
                    "type": "boolean",
                    "description": "One line format (default: true)"
                },
                "file": {
                    "type": "string",
                    "description": "Show commits for specific file"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        handler=_handle_git_log,
    ),
    ToolDef(
        name="git_rev_parse",
        description="Get the SHA for a ref (branch, tag, HEAD).",
        parameters={
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Reference to resolve (default: HEAD)"
                },
                "short": {
                    "type": "boolean",
                    "description": "Return short SHA (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        handler=_handle_git_rev_parse,
    ),
]
