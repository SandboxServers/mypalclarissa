"""
Git tools package for sandbox operations.

This package provides git CLI wrappers that run in the sandbox,
with automatic GitHub authentication injection for remote operations.

Tools:
    git_clone       - Clone a repository
    git_branch      - List branches  
    git_checkout    - Switch/create branches
    git_create_branch - Create branch without switching
    git_status      - Working tree status
    git_diff        - Show changes
    git_show        - Show commits or files
    git_add         - Stage files
    git_reset       - Unstage or reset
    git_restore     - Restore files
    git_commit      - Commit changes
    git_log         - View history
    git_rev_parse   - Resolve refs
    git_push        - Push to remote
    git_pull        - Pull from remote
    git_fetch       - Fetch refs
    git_remote      - Manage remotes
"""

from .clone import git_clone, TOOLS as CLONE_TOOLS
from .branch import git_branch, git_checkout, git_create_branch, TOOLS as BRANCH_TOOLS
from .status import git_status, git_diff, git_show, TOOLS as STATUS_TOOLS
from .staging import git_add, git_reset, git_restore, TOOLS as STAGING_TOOLS
from .commit import git_commit, git_log, git_rev_parse, TOOLS as COMMIT_TOOLS
from .remote import git_push, git_pull, git_fetch, git_remote, TOOLS as REMOTE_TOOLS

# Module metadata (required by loader)
MODULE_NAME = "git"
MODULE_VERSION = "1.0.0"

# System prompt for the git tools
SYSTEM_PROMPT = """## Git CLI Tools
You have access to git CLI tools that run in your sandbox with automatic GitHub authentication.

**Workflow:**
1. `git_clone` - Clone a repo to your sandbox
2. `git_checkout` - Switch to or create a branch
3. Make changes with `write_file` or `execute_python`
4. `git_add` - Stage your changes
5. `git_commit` - Commit with a message
6. `git_push` - Push to remote (auth is automatic)

**Available Tools:**
- **Clone/Remote:** `git_clone`, `git_push`, `git_pull`, `git_fetch`, `git_remote`
- **Branches:** `git_branch`, `git_checkout`, `git_create_branch`
- **Status:** `git_status`, `git_diff`, `git_show`, `git_log`
- **Staging:** `git_add`, `git_reset`, `git_restore`
- **Commits:** `git_commit`, `git_rev_parse`

**Notes:**
- GitHub auth is injected automatically for push/pull/fetch
- Repos are cloned to `/home/user/repos/` by default
- Use this for multi-file commits to avoid API truncation limits
"""

# Aggregate all tools
TOOLS = (
    CLONE_TOOLS +
    BRANCH_TOOLS +
    STATUS_TOOLS +
    STAGING_TOOLS +
    COMMIT_TOOLS +
    REMOTE_TOOLS
)

# Export all functions
__all__ = [
    # Metadata
    'MODULE_NAME',
    'MODULE_VERSION',
    'SYSTEM_PROMPT',
    # Clone
    'git_clone',
    # Branch
    'git_branch',
    'git_checkout', 
    'git_create_branch',
    # Status
    'git_status',
    'git_diff',
    'git_show',
    # Staging
    'git_add',
    'git_reset',
    'git_restore',
    # Commit
    'git_commit',
    'git_log',
    'git_rev_parse',
    # Remote
    'git_push',
    'git_pull',
    'git_fetch',
    'git_remote',
    # Tool list
    'TOOLS',
]
