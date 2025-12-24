"""
Git clone operations.
"""

from typing import Optional
from ._runner import run_git, _inject_token_in_url


def git_clone(
    repo_url: str,
    directory: Optional[str] = None,
    branch: Optional[str] = None,
    depth: Optional[int] = None,
    cwd: Optional[str] = None
) -> dict:
    """
    Clone a git repository.
    
    Args:
        repo_url: Repository URL (HTTPS or SSH)
        directory: Target directory name (default: repo name)
        branch: Specific branch to clone
        depth: Create shallow clone with N commits of history
        cwd: Working directory to clone into
    
    Returns:
        dict with 'success', 'directory', 'message'
    """
    # Inject token for GitHub HTTPS URLs
    auth_url = _inject_token_in_url(repo_url)
    
    args = ['clone']
    
    if branch:
        args.extend(['--branch', branch])
    
    if depth:
        args.extend(['--depth', str(depth)])
    
    args.append(auth_url)
    
    if directory:
        args.append(directory)
    
    success, stdout, stderr = run_git(*args, cwd=cwd)
    
    # Determine actual directory name
    if directory:
        target_dir = directory
    else:
        # Extract from URL: https://github.com/owner/repo.git -> repo
        target_dir = repo_url.rstrip('/').rstrip('.git').split('/')[-1]
    
    if success:
        return {
            'success': True,
            'directory': target_dir,
            'message': f"Cloned {repo_url} to {target_dir}"
        }
    else:
        return {
            'success': False,
            'directory': None,
            'message': stderr or "Clone failed"
        }


# Tool definition for the tools system
TOOLS = [
    {
        "name": "git_clone",
        "description": "Clone a git repository to the sandbox. Automatically handles GitHub authentication.",
        "parameters": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "Repository URL (e.g., https://github.com/owner/repo)"
                },
                "directory": {
                    "type": "string",
                    "description": "Target directory name (default: repository name)"
                },
                "branch": {
                    "type": "string",
                    "description": "Specific branch to clone"
                },
                "depth": {
                    "type": "integer",
                    "description": "Shallow clone depth (number of commits)"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory to clone into (default: /home/user)"
                }
            },
            "required": ["repo_url"]
        },
        "function": git_clone
    }
]
