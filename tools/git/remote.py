"""
Git remote operations - push, pull, fetch.
"""

from typing import Optional
from ._runner import run_git, _inject_token_in_url


def git_push(
    remote: str = "origin",
    branch: Optional[str] = None,
    force: bool = False,
    set_upstream: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Push commits to a remote repository.
    
    Args:
        remote: Remote name (default: origin)
        branch: Branch to push (default: current branch)
        force: Force push (use with caution!)
        set_upstream: Set upstream tracking (-u)
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['push']
    
    if set_upstream:
        args.append('-u')
    
    if force:
        args.append('--force')
    
    args.append(remote)
    
    if branch:
        args.append(branch)
    
    success, stdout, stderr = run_git(*args, cwd=cwd, inject_auth=True)
    
    output = stdout or stderr
    
    return {
        'success': success,
        'message': output.strip() if output else ("Push complete" if success else "Push failed")
    }


def git_pull(
    remote: str = "origin",
    branch: Optional[str] = None,
    rebase: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Pull changes from a remote repository.
    
    Args:
        remote: Remote name (default: origin)
        branch: Branch to pull (default: current tracking branch)
        rebase: Rebase instead of merge
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['pull']
    
    if rebase:
        args.append('--rebase')
    
    args.append(remote)
    
    if branch:
        args.append(branch)
    
    success, stdout, stderr = run_git(*args, cwd=cwd, inject_auth=True)
    
    output = stdout or stderr
    
    return {
        'success': success,
        'message': output.strip() if output else ("Pull complete" if success else "Pull failed")
    }


def git_fetch(
    remote: str = "origin",
    prune: bool = False,
    all_remotes: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Fetch refs from remote(s).
    
    Args:
        remote: Remote name (default: origin)
        prune: Remove deleted remote branches
        all_remotes: Fetch from all remotes
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['fetch']
    
    if prune:
        args.append('--prune')
    
    if all_remotes:
        args.append('--all')
    else:
        args.append(remote)
    
    success, stdout, stderr = run_git(*args, cwd=cwd, inject_auth=True)
    
    output = stdout or stderr
    
    return {
        'success': success,
        'message': output.strip() if output else ("Fetch complete" if success else "Fetch failed")
    }


def git_remote(
    action: str = "list",
    name: Optional[str] = None,
    url: Optional[str] = None,
    cwd: Optional[str] = None
) -> dict:
    """
    Manage remotes.
    
    Args:
        action: 'list', 'add', 'remove', or 'get-url'
        name: Remote name (for add/remove/get-url)
        url: Remote URL (for add)
        cwd: Repository directory
    
    Returns:
        dict with 'success' and action-specific data
    """
    if action == "list":
        success, stdout, stderr = run_git('remote', '-v', cwd=cwd)
        
        if not success:
            return {'success': False, 'remotes': [], 'message': stderr}
        
        remotes = {}
        for line in stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                remote_name = parts[0]
                remote_url = parts[1]
                if remote_name not in remotes:
                    remotes[remote_name] = remote_url
        
        return {'success': True, 'remotes': remotes}
    
    elif action == "add":
        if not name or not url:
            return {'success': False, 'message': "Name and URL required for 'add'"}
        
        success, stdout, stderr = run_git('remote', 'add', name, url, cwd=cwd)
        return {
            'success': success,
            'message': f"Added remote {name}" if success else stderr
        }
    
    elif action == "remove":
        if not name:
            return {'success': False, 'message': "Name required for 'remove'"}
        
        success, stdout, stderr = run_git('remote', 'remove', name, cwd=cwd)
        return {
            'success': success,
            'message': f"Removed remote {name}" if success else stderr
        }
    
    elif action == "get-url":
        if not name:
            return {'success': False, 'message': "Name required for 'get-url'"}
        
        success, stdout, stderr = run_git('remote', 'get-url', name, cwd=cwd)
        return {
            'success': success,
            'url': stdout.strip() if success else None,
            'message': stderr if not success else None
        }
    
    else:
        return {'success': False, 'message': f"Unknown action: {action}"}


# Tool definitions
TOOLS = [
    {
        "name": "git_push",
        "description": "Push commits to a remote repository. Automatically handles GitHub auth.",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "Remote name (default: origin)"
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to push (default: current)"
                },
                "force": {
                    "type": "boolean",
                    "description": "Force push (default: false)"
                },
                "set_upstream": {
                    "type": "boolean",
                    "description": "Set upstream tracking (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_push
    },
    {
        "name": "git_pull",
        "description": "Pull changes from a remote repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "Remote name (default: origin)"
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to pull"
                },
                "rebase": {
                    "type": "boolean",
                    "description": "Rebase instead of merge (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_pull
    },
    {
        "name": "git_fetch",
        "description": "Fetch refs from remote(s).",
        "parameters": {
            "type": "object",
            "properties": {
                "remote": {
                    "type": "string",
                    "description": "Remote name (default: origin)"
                },
                "prune": {
                    "type": "boolean",
                    "description": "Remove deleted remote branches"
                },
                "all_remotes": {
                    "type": "boolean",
                    "description": "Fetch from all remotes"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_fetch
    },
    {
        "name": "git_remote",
        "description": "Manage git remotes - list, add, remove.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove", "get-url"],
                    "description": "Action to perform (default: list)"
                },
                "name": {
                    "type": "string",
                    "description": "Remote name (for add/remove/get-url)"
                },
                "url": {
                    "type": "string",
                    "description": "Remote URL (for add)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_remote
    }
]
