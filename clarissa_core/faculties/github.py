"""GitHub faculty for Clarissa.

Wraps the existing tools/github.py tools with intent-based orchestration.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clarissa_core.faculty import Faculty, FacultyResult

logger = logging.getLogger(__name__)


class GitHubFaculty(Faculty):
    """GitHub operations orchestrator.

    Wraps the existing GitHub tools with intent-based execution.
    Clarissa can say "list my open issues" and this faculty will
    figure out which tool(s) to call.
    """

    name = "github"
    description = "GitHub API operations (repos, issues, PRs, workflows, gists)"
    available_actions = [
        "get_me",
        "search_repos",
        "search_users",
        "list_issues",
        "create_issue",
        "list_pull_requests",
        "create_pull_request",
        "list_commits",
        "get_file",
        "list_workflows",
        "run_workflow",
        "list_gists",
        "create_gist",
        "list_notifications",
        "list_starred",
        "star_repo",
    ]

    def __init__(self):
        self._configured = self._check_configuration()

    def _check_configuration(self) -> bool:
        """Check if GitHub is configured."""
        import os

        return bool(os.getenv("GITHUB_TOKEN"))

    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute GitHub operations based on intent."""
        if not self._configured:
            return FacultyResult(
                success=False,
                summary="GitHub is not configured. Please set GITHUB_TOKEN.",
                error="GITHUB_TOKEN not set",
            )

        logger.info(f"[faculty:github] Intent: {intent}")

        try:
            # Parse intent to determine action
            action, params = self._parse_intent(intent, constraints)
            logger.info(f"[faculty:github] Action: {action}, Params: {params}")

            # Execute the action
            result = await self._execute_action(action, params, user_id)
            return result

        except Exception as e:
            logger.exception(f"[faculty:github] Error: {e}")
            return FacultyResult(
                success=False,
                summary=f"GitHub operation failed: {str(e)}",
                error=str(e),
            )

    def _parse_intent(
        self, intent: str, constraints: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Parse natural language intent into action and parameters."""
        intent_lower = intent.lower()

        # Profile / user info
        if any(x in intent_lower for x in ["my profile", "who am i", "authenticated"]):
            return "get_me", {}

        # Search repos
        if "search" in intent_lower and "repo" in intent_lower:
            query = self._extract_query(intent)
            return "search_repos", {"query": query}

        # Search users
        if "search" in intent_lower and "user" in intent_lower:
            query = self._extract_query(intent)
            return "search_users", {"query": query}

        # List issues
        if any(x in intent_lower for x in ["list issue", "my issue", "open issue"]):
            owner, repo = self._extract_repo(intent)
            state = "open" if "open" in intent_lower else "all"
            return "list_issues", {"owner": owner, "repo": repo, "state": state}

        # Create issue
        if "create" in intent_lower and "issue" in intent_lower:
            owner, repo = self._extract_repo(intent)
            title = self._extract_title(intent)
            body = self._extract_body(intent)
            return "create_issue", {
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
            }

        # List PRs
        if any(x in intent_lower for x in ["list pr", "pull request", "open pr"]):
            owner, repo = self._extract_repo(intent)
            state = "open" if "open" in intent_lower else "all"
            return "list_pull_requests", {"owner": owner, "repo": repo, "state": state}

        # Create PR
        if "create" in intent_lower and (
            "pr" in intent_lower or "pull" in intent_lower
        ):
            owner, repo = self._extract_repo(intent)
            title = self._extract_title(intent)
            head = self._extract_branch(intent, "head")
            base = self._extract_branch(intent, "base") or "main"
            return "create_pull_request", {
                "owner": owner,
                "repo": repo,
                "title": title,
                "head": head,
                "base": base,
            }

        # List commits
        if "commit" in intent_lower and any(
            x in intent_lower for x in ["list", "show", "recent"]
        ):
            owner, repo = self._extract_repo(intent)
            return "list_commits", {"owner": owner, "repo": repo}

        # Get file
        if any(
            x in intent_lower
            for x in ["get file", "read file", "show file", "contents of"]
        ):
            owner, repo = self._extract_repo(intent)
            path = self._extract_path(intent)
            return "get_file", {"owner": owner, "repo": repo, "path": path}

        # List workflows
        if "workflow" in intent_lower and any(
            x in intent_lower for x in ["list", "show"]
        ):
            owner, repo = self._extract_repo(intent)
            return "list_workflows", {"owner": owner, "repo": repo}

        # Run workflow
        if "workflow" in intent_lower and any(
            x in intent_lower for x in ["run", "trigger", "start"]
        ):
            owner, repo = self._extract_repo(intent)
            workflow_id = self._extract_workflow_id(intent)
            return "run_workflow", {
                "owner": owner,
                "repo": repo,
                "workflow_id": workflow_id,
            }

        # List gists
        if "gist" in intent_lower and any(
            x in intent_lower for x in ["list", "show", "my"]
        ):
            return "list_gists", {}

        # Create gist
        if "gist" in intent_lower and "create" in intent_lower:
            description = self._extract_description(intent)
            return "create_gist", {"description": description}

        # List notifications
        if "notification" in intent_lower:
            return "list_notifications", {}

        # Starred repos
        if "star" in intent_lower:
            if "list" in intent_lower or "show" in intent_lower or "my" in intent_lower:
                return "list_starred", {}
            else:
                owner, repo = self._extract_repo(intent)
                return "star_repo", {"owner": owner, "repo": repo}

        # Default: try to extract repo and list it
        owner, repo = self._extract_repo(intent)
        if owner and repo:
            return "get_repo", {"owner": owner, "repo": repo}

        # Fallback: search
        return "search_repos", {"query": intent}

    async def _execute_action(
        self, action: str, params: dict[str, Any], user_id: str | None
    ) -> FacultyResult:
        """Execute a GitHub action via the underlying tools."""
        from tools._base import ToolContext

        # Create a context for tool execution
        ctx = ToolContext(user_id=user_id or "faculty")

        # Import the GitHub tools module
        from tools import github as gh

        try:
            if action == "get_me":
                result = await gh.get_me({}, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"GitHub profile: {result}",
                    data={"profile": result},
                )

            elif action == "search_repos":
                result = await gh.search_repositories(
                    {"query": params.get("query", ""), "per_page": 10}, ctx
                )
                return FacultyResult(
                    success=True,
                    summary=f"Found repositories:\n{result}",
                    data={"results": result},
                )

            elif action == "search_users":
                result = await gh.search_users(
                    {"query": params.get("query", ""), "per_page": 10}, ctx
                )
                return FacultyResult(
                    success=True,
                    summary=f"Found users:\n{result}",
                    data={"results": result},
                )

            elif action == "list_issues":
                result = await gh.list_issues(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "state": params.get("state", "open"),
                        "per_page": 10,
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Issues:\n{result}",
                    data={"issues": result},
                )

            elif action == "create_issue":
                result = await gh.create_issue(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "title": params.get("title", ""),
                        "body": params.get("body", ""),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Created issue: {result}",
                    data={"issue": result},
                )

            elif action == "list_pull_requests":
                result = await gh.list_pull_requests(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "state": params.get("state", "open"),
                        "per_page": 10,
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Pull requests:\n{result}",
                    data={"pull_requests": result},
                )

            elif action == "create_pull_request":
                result = await gh.create_pull_request(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "title": params.get("title", ""),
                        "head": params.get("head", ""),
                        "base": params.get("base", "main"),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Created PR: {result}",
                    data={"pull_request": result},
                )

            elif action == "list_commits":
                result = await gh.list_commits(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "per_page": 10,
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Recent commits:\n{result}",
                    data={"commits": result},
                )

            elif action == "get_file":
                result = await gh.get_file_contents(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "path": params.get("path", ""),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"File contents:\n{result[:500]}...",
                    data={"contents": result},
                )

            elif action == "list_workflows":
                result = await gh.list_workflows(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Workflows:\n{result}",
                    data={"workflows": result},
                )

            elif action == "run_workflow":
                result = await gh.run_workflow(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                        "workflow_id": params.get("workflow_id", ""),
                        "ref": params.get("ref", "main"),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Workflow triggered: {result}",
                    data={"result": result},
                )

            elif action == "list_gists":
                result = await gh.list_gists({"per_page": 10}, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Gists:\n{result}",
                    data={"gists": result},
                )

            elif action == "create_gist":
                # This would need more params in practice
                return FacultyResult(
                    success=False,
                    summary="Creating gists requires more info (files, content).",
                    error="Incomplete parameters for gist creation",
                )

            elif action == "list_notifications":
                result = await gh.list_notifications({"per_page": 10}, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Notifications:\n{result}",
                    data={"notifications": result},
                )

            elif action == "list_starred":
                result = await gh.list_starred({"per_page": 10}, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Starred repos:\n{result}",
                    data={"starred": result},
                )

            elif action == "star_repo":
                result = await gh.star_repository(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Starred: {result}",
                    data={"result": result},
                )

            elif action == "get_repo":
                result = await gh.get_repository(
                    {
                        "owner": params.get("owner", ""),
                        "repo": params.get("repo", ""),
                    },
                    ctx,
                )
                return FacultyResult(
                    success=True,
                    summary=f"Repository info:\n{result}",
                    data={"repository": result},
                )

            else:
                return FacultyResult(
                    success=False,
                    summary=f"Unknown GitHub action: {action}",
                    error=f"Action '{action}' not implemented",
                )

        except Exception as e:
            return FacultyResult(
                success=False,
                summary=f"GitHub API error: {str(e)}",
                error=str(e),
            )

    # Helper methods for intent parsing
    def _extract_query(self, intent: str) -> str:
        """Extract search query from intent."""
        # Look for quoted strings
        match = re.search(r'"([^"]+)"', intent)
        if match:
            return match.group(1)
        # Look for "for X" pattern
        match = re.search(r"for\s+(.+)", intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return intent

    def _extract_repo(self, intent: str) -> tuple[str, str]:
        """Extract owner/repo from intent."""
        # Look for owner/repo pattern
        match = re.search(r"(\w+)/(\w[\w.-]*)", intent)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    def _extract_title(self, intent: str) -> str:
        """Extract title from intent."""
        match = re.search(r'title[:\s]+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r'titled?\s+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_body(self, intent: str) -> str:
        """Extract body/description from intent."""
        match = re.search(r'body[:\s]+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_branch(self, intent: str, branch_type: str) -> str:
        """Extract branch name from intent."""
        match = re.search(rf"{branch_type}[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_path(self, intent: str) -> str:
        """Extract file path from intent."""
        # Look for path-like strings
        match = re.search(r"path[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"file[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        # Look for anything that looks like a path
        match = re.search(r"(\S+/\S+\.\w+)", intent)
        if match:
            return match.group(1)
        return ""

    def _extract_workflow_id(self, intent: str) -> str:
        """Extract workflow ID from intent."""
        match = re.search(r"workflow[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_description(self, intent: str) -> str:
        """Extract description from intent."""
        match = re.search(r'description[:\s]+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""
