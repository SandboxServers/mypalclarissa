"""
Git staging operations.
"""

from typing import Optional, List, Union
from ._runner import run_git


def git_add(
    files: Union[str, List[str]] = ".",
    cwd: Optional[str] = None
) -> dict:
    """
    Stage files for commit.
    
    Args:
        files: File(s) to stage - string or list (default: "." for all)
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['add']
    
    if isinstance(files, list):
        args.extend(files)
    else:
        args.append(files)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    return {
        'success': success,
        'message': "Files staged" if success else (stderr or "Failed to stage files")
    }


def git_reset(
    files: Optional[Union[str, List[str]]] = None,
    hard: bool = False,
    cwd: Optional[str] = None
) -> dict:
    """
    Unstage files or reset to a commit.
    
    Args:
        files: File(s) to unstage (default: all staged files)
        hard: Hard reset (WARNING: discards changes)
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['reset']
    
    if hard:
        args.append('--hard')
    
    if files:
        args.append('--')
        if isinstance(files, list):
            args.extend(files)
        else:
            args.append(files)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    output = stdout or stderr
    
    return {
        'success': success,
        'message': output.strip() if output else ("Reset complete" if success else "Reset failed")
    }


def git_restore(
    files: Union[str, List[str]],
    staged: bool = False,
    source: Optional[str] = None,
    cwd: Optional[str] = None
) -> dict:
    """
    Restore working tree files or unstage.
    
    Args:
        files: File(s) to restore
        staged: Unstage files (keep changes in working tree)
        source: Restore from specific commit/branch
        cwd: Repository directory
    
    Returns:
        dict with 'success', 'message'
    """
    args = ['restore']
    
    if staged:
        args.append('--staged')
    
    if source:
        args.extend(['--source', source])
    
    args.append('--')
    
    if isinstance(files, list):
        args.extend(files)
    else:
        args.append(files)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    return {
        'success': success,
        'message': "Files restored" if success else (stderr or "Restore failed")
    }


# Tool definitions
TOOLS = [
    {
        "name": "git_add",
        "description": "Stage files for commit.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "string",
                    "description": "File or pattern to stage (default: '.' for all)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_add
    },
    {
        "name": "git_reset",
        "description": "Unstage files or reset to a commit.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "string",
                    "description": "File to unstage (default: all)"
                },
                "hard": {
                    "type": "boolean",
                    "description": "Hard reset - DISCARDS CHANGES (default: false)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": []
        },
        "function": git_reset
    },
    {
        "name": "git_restore",
        "description": "Restore working tree files or unstage changes.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "string",
                    "description": "File(s) to restore"
                },
                "staged": {
                    "type": "boolean",
                    "description": "Unstage files (default: false)"
                },
                "source": {
                    "type": "string",
                    "description": "Restore from specific commit/branch"
                },
                "cwd": {
                    "type": "string",
                    "description": "Repository directory path"
                }
            },
            "required": ["files"]
        },
        "function": git_restore
    }
]
