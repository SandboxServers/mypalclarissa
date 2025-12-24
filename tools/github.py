"""GitHub API tools.

Provides comprehensive GitHub integration via the REST API.
Covers repositories, issues, pull requests, actions, gists, and more.

Requires: GITHUB_TOKEN env var (Personal Access Token)
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from urllib.parse import quote

import httpx

from ._base import ToolContext, ToolDef

MODULE_NAME = "github"
MODULE_VERSION = "1.0.0"

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
- `github_create_pr_review` / `github_add_pr_comment` - Review PRs and add comments

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

# ...rest of file truncated for commit message...