"""
Git branch operations.
"""

from typing import Optional, List
from ._runner import run_git


def git_branch(
    list_all: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    List branches in the repository.
    
    Args:
        list_all: Include remote branches
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'current', 'branches'
    """
    args = ['branch']
    if list_all:
        args.append('-a')
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    if not success:
        return {'success': False, 'current': None, 'branches': [], 'message': stderr}
    
    branches = []
    current = None
    
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue
        
        is_current = line.startswith('*')
        branch_name = line.lstrip('* ').strip()
        
        if is_current:
            current = branch_name
        
        branches.append(branch_name)
    
    return {
        'success': True,
        'current': current,
        'branches': branches
    }


def git_checkout(
    branch: str,
    create: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Switch to a branch, optionally creating it.
    
    Args:
        branch: Branch name to switch to
        create: Create the branch if it doesn't exist
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'branch', 'message'
    """
    args = ['checkout']
    
    if create:
        args.append('-b')
    
    args.append(branch)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    output = stdout or stderr
    
    return {
        'success': success,
        'branch': branch if success else None,
        'message': output.strip() if output else ("Switched to " + branch if success else "Checkout failed")
    }


def git_create_branch(
    branch: str,
    start_point: Optional[str] = None,
    cwd: Optional[str] = None
) -> dict:
    """
    Create a new branch without switching to it.
    
    Args:
        branch: New branch name
        start_point: Starting commit/branch (default: HEAD)
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'branch', 'message'
    """
    args = ['branch', branch]
    
    if start_point:
        args.append(start_point)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    return {
        'success': success,
        'branch': branch if success else None,
        'message': f"Created branch {branch}" if success else (stderr or "Failed to create branch")
    }


# Tool definitions
TOOLS = [
    {
        "name": "git_branch",
        "description": "List branches in the repository.",
        "parameters": {
            "type": "object",
            "properties": {
                "list_all": {
                    "type": "boolean",
                    "description": "Include remote branches (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_branch
    },
    {
        "name": "git_checkout",
        "description": "Switch to a branch, optionally creating it.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Branch name to switch to"
                },
                "create": {
                    "type": "boolean",
                    "description": "Create branch if it doesn't exist (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": ["branch"]
        },
        "function": git_checkout
    },
    {
        "name": "git_create_branch",
        "description": "Create a new branch without switching to it.",
        "parameters": {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "New branch name"
                },
                "start_point": {
                    "type": "string",
                    "description": "Starting commit or branch (default: HEAD)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": ["branch"]
        },
        "function": git_create_branch
    }
]
