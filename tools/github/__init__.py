"""GitHub API tools package.

This package provides comprehensive GitHub integration via the REST API.
Covers repositories, issues, pull requests, actions, gists, and more.

Requires: GITHUB_TOKEN env var (Personal Access Token)
"""

from __future__ import annotations

MODULE_NAME = "github"
MODULE_VERSION = "2.0.0"  # Modular rewrite

# Import from submodules
from ._client import is_configured, github_request, github_request_raw

from .users import TOOLS as USER_TOOLS
from .repositories import TOOLS as REPO_TOOLS
from .issues import TOOLS as ISSUE_TOOLS
from .pull_requests import TOOLS as PR_TOOLS
from .actions import TOOLS as ACTION_TOOLS
from .gists import TOOLS as GIST_TOOLS
from .releases import TOOLS as RELEASE_TOOLS
from .notifications import TOOLS as NOTIFICATION_TOOLS
from .stars import TOOLS as STAR_TOOLS

# Aggregate all tools
TOOLS = (
    USER_TOOLS
    + REPO_TOOLS
    + ISSUE_TOOLS
    + PR_TOOLS
    + ACTION_TOOLS
    + GIST_TOOLS
    + RELEASE_TOOLS
    + NOTIFICATION_TOOLS
    + STAR_TOOLS
)

# System prompt for LLM context
SYSTEM_PROMPT = """
## GitHub Integration
You can interact with GitHub repositories, issues, pull requests, and workflows.

**Repository Tools:**
- `github_search_repositories` - Search for repositories
- `github_get_repository` - Get repo details (stats, description, topics)
- `github_list_branches` / `github_list_tags` - List branches and tags
- `github_list_commits` / `github_get_commit` - View commit history

**Issues & PRs:**
- `github_list_issues` / `github_get_issue` / `github_create_issue` - Manage issues
- `github_list_pull_requests` / `github_get_pull_request` / `github_create_pull_request` - Manage PRs
- `github_list_pr_files` / `github_list_pr_commits` - View PR details

**PR Reviews (NEW):**
- `github_list_pr_reviews` / `github_list_pr_review_comments` - View reviews
- `github_create_pr_review` - Submit APPROVE/REQUEST_CHANGES/COMMENT
- `github_add_pr_comment` - Add conversation comment
- `github_add_pr_review_comment` - Add inline comment on specific line

**Code & Files:**
- `github_get_file_contents` - Read files from repos
- `github_create_or_update_file` - Create or update files
- `github_search_code` - Search code across GitHub

**Actions & Workflows:**
- `github_list_workflows` / `github_list_workflow_runs` - View workflows
- `github_run_workflow` - Trigger a workflow

**Other:**
- `github_get_me` - Get authenticated user info
- `github_list_gists` / `github_create_gist` - Manage gists
- `github_list_notifications` - View notifications
""".strip()


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize GitHub module."""
    if is_configured():
        print(f"[github] GitHub API configured (v{MODULE_VERSION})")
    else:
        print("[github] Not configured - GITHUB_TOKEN not set, tools will be disabled")
        global TOOLS
        TOOLS = []


async def cleanup() -> None:
    """Cleanup on module unload."""
    pass


# Re-export handler functions for direct use if needed
from .users import get_me, search_users
from .repositories import (
    search_repositories,
    get_repository,
    create_repository,
    fork_repository,
    get_file_contents,
    create_or_update_file,
    delete_file,
    list_branches,
    create_branch,
    list_commits,
    get_commit,
    search_code,
    get_repository_tree,
)
from .issues import (
    list_issues,
    get_issue,
    create_issue,
    update_issue,
    add_issue_comment,
    list_issue_comments,
    search_issues,
)
from .pull_requests import (
    list_pull_requests,
    get_pull_request,
    create_pull_request,
    update_pull_request,
    merge_pull_request,
    get_pull_request_diff,
    list_pull_request_files,
    list_pr_reviews,
    list_pr_review_comments,
    create_pr_review,
    add_pr_comment,
    add_pr_review_comment,
)
from .actions import (
    list_workflows,
    list_workflow_runs,
    get_workflow_run,
    run_workflow,
    cancel_workflow_run,
    rerun_workflow,
)
from .gists import list_gists, get_gist, create_gist, update_gist, delete_gist
from .releases import list_releases, get_latest_release, list_tags
from .notifications import list_notifications, mark_notifications_read
from .stars import list_starred_repos, star_repository, unstar_repository

__all__ = [
    # Module info
    "MODULE_NAME",
    "MODULE_VERSION",
    "SYSTEM_PROMPT",
    "TOOLS",
    # Lifecycle
    "initialize",
    "cleanup",
    # Client
    "is_configured",
    "github_request",
    "github_request_raw",
    # All handler functions
    "get_me",
    "search_users",
    "search_repositories",
    "get_repository",
    "create_repository",
    "fork_repository",
    "get_file_contents",
    "create_or_update_file",
    "delete_file",
    "list_branches",
    "create_branch",
    "list_commits",
    "get_commit",
    "search_code",
    "get_repository_tree",
    "list_issues",
    "get_issue",
    "create_issue",
    "update_issue",
    "add_issue_comment",
    "list_issue_comments",
    "search_issues",
    "list_pull_requests",
    "get_pull_request",
    "create_pull_request",
    "update_pull_request",
    "merge_pull_request",
    "get_pull_request_diff",
    "list_pull_request_files",
    "list_pr_reviews",
    "list_pr_review_comments",
    "create_pr_review",
    "add_pr_comment",
    "add_pr_review_comment",
    "list_workflows",
    "list_workflow_runs",
    "get_workflow_run",
    "run_workflow",
    "cancel_workflow_run",
    "rerun_workflow",
    "list_gists",
    "get_gist",
    "create_gist",
    "update_gist",
    "delete_gist",
    "list_releases",
    "get_latest_release",
    "list_tags",
    "list_notifications",
    "mark_notifications_read",
    "list_starred_repos",
    "star_repository",
    "unstar_repository",
]
