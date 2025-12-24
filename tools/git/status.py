"""
Git status and diff operations.
"""

from typing import Optional, List
from ._runner import run_git


def git_status(
    short: bool = True,
    cwd: Optional[str] = None
) -> dict:
    """
    Get working tree status.
    
    Args:
        short: Use short format (default: True)
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'clean', 'files', 'raw'
    """
    args = ['status']
    if short:
        args.append('-s')
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    if not success:
        return {'success': False, 'clean': None, 'files': [], 'raw': stderr}
    
    files = []
    for line in stdout.strip().split('\n'):
        if not line.strip():
            continue
        
        if short:
            # Short format: XY filename
            status = line[:2]
            filename = line[3:]
            files.append({'status': status, 'file': filename})
        else:
            files.append(line)
    
    return {
        'success': True,
        'clean': len(files) == 0,
        'files': files,
        'raw': stdout
    }


def git_diff(
    file: Optional[str] = None,
    staged: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Show changes in working tree or staging area.
    
    Args:
        file: Specific file to diff (default: all files)
        staged: Show staged changes instead of unstaged
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'diff', 'has_changes'
    """
    args = ['diff']
    
    if staged:
        args.append('--cached')
    
    if file:
        args.extend(['--', file])
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    return {
        'success': success,
        'diff': stdout if success else stderr,
        'has_changes': bool(stdout.strip()) if success else None
    }


def git_show(
    ref: str = "HEAD",
    file: Optional[str] = None,
    stat_only: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Show commit details or file contents at a ref.
    
    Args:
        ref: Commit SHA, branch, or tag (default: HEAD)
        file: Show specific file at that ref
        stat_only: Only show diffstat, not full diff
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'output'
    """
    if file:
        # Show file contents at ref
        args = ['show', f'{ref}:{file}']
    else:
        # Show commit
        args = ['show', ref]
        if stat_only:
            args.append('--stat')
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    return {
        'success': success,
        'output': stdout if success else stderr
    }


# Tool definitions
TOOLS = [
    {
        "name": "git_status",
        "description": "Get the working tree status - shows modified, staged, and untracked files.",
        "parameters": {
            "type": "object",
            "properties": {
                "short": {
                    "type": "boolean",
                    "description": "Use short format (default: true)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_status
    },
    {
        "name": "git_diff",
        "description": "Show changes between working tree and index, or staged changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Specific file to diff"
                },
                "staged": {
                    "type": "boolean",
                    "description": "Show staged changes (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_diff
    },
    {
        "name": "git_show",
        "description": "Show commit details or file contents at a specific ref.",
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "Commit SHA, branch, or tag (default: HEAD)"
                },
                "file": {
                    "type": "string",
                    "description": "Show specific file at that ref"
                },
                "stat_only": {
                    "type": "boolean",
                    "description": "Only show diffstat (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_show
    }
]
