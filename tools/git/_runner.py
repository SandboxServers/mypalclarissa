"""
Git command runner with authentication injection.

This module provides the core execution layer for git commands,
handling token injection for authenticated operations.
"""

import subprocess
import os
import re
from typing import Optional, Tuple
from config import config


def _inject_token_in_url(url: str) -> str:
    """Inject GitHub token into HTTPS URL for authentication."""
    token = getattr(config, 'GITHUB_TOKEN', None)
    if not token:
        return url
    
    # Handle https://github.com/... URLs
    if url.startswith("https://github.com/"):
        return url.replace("https://github.com/", f"https://{token}@github.com/")
    
    # Handle https://TOKEN@github.com/... URLs (already has token)
    if "@github.com/" in url:
        return re.sub(r'https://[^@]+@github\.com/', f'https://{token}@github.com/', url)
    
    return url


def _mask_token_in_output(text: str) -> str:
    """Remove any token from output to avoid leaking secrets."""
    token = getattr(config, 'GITHUB_TOKEN', None)
    if token and token in text:
        text = text.replace(token, '***TOKEN***')
    return text


def run_git(
    *args: str,
    cwd: Optional[str] = None,
    inject_auth: bool = False
) -> Tuple[bool, str, str]:
    """
    Run a git command and return (success, stdout, stderr).
    
    Args:
        *args: Git command arguments (e.g., 'status', '-s')
        cwd: Working directory (default: current directory)
        inject_auth: Whether to inject auth token for remote operations
    
    Returns:
        Tuple of (success: bool, stdout: str, stderr: str)
    """
    cmd = ['git'] + list(args)
    
    # Set up environment with token if needed
    env = os.environ.copy()
    token = getattr(config, 'GITHUB_TOKEN', None)
    
    if inject_auth and token:
        # Use credential helper to inject token
        env['GIT_ASKPASS'] = 'echo'
        env['GIT_USERNAME'] = 'x-access-token'
        env['GIT_PASSWORD'] = token
    
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
            env=env
        )
        
        stdout = _mask_token_in_output(result.stdout)
        stderr = _mask_token_in_output(result.stderr)
        
        return result.returncode == 0, stdout, stderr
        
    except subprocess.TimeoutExpired:
        return False, "", "Command timed out after 120 seconds"
    except FileNotFoundError:
        return False, "", "Git is not installed or not in PATH"
    except Exception as e:
        return False, "", f"Error running git: {str(e)}"


def get_repo_root(cwd: Optional[str] = None) -> Optional[str]:
    """Get the root directory of the current git repository."""
    success, stdout, _ = run_git('rev-parse', '--show-toplevel', cwd=cwd)
    if success:
        return stdout.strip()
    return None


def is_git_repo(path: str) -> bool:
    """Check if a path is inside a git repository."""
    success, _, _ = run_git('rev-parse', '--git-dir', cwd=path)
    return success
