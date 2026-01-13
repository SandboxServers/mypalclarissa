"""Azure DevOps faculty for Clarissa.

Wraps the existing tools/azure_devops.py tools with intent-based orchestration.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clarissa_core.faculty import Faculty, FacultyResult

logger = logging.getLogger(__name__)


class ADOFaculty(Faculty):
    """Azure DevOps operations orchestrator.

    Wraps the existing Azure DevOps tools with intent-based execution.
    Clarissa can say "show me my work items" and this faculty will
    figure out which tool(s) to call.
    """

    name = "ado"
    description = "Azure DevOps operations (work items, repos, PRs, pipelines, wikis)"
    available_actions = [
        "list_projects",
        "list_repos",
        "list_branches",
        "list_commits",
        "get_file",
        "list_pull_requests",
        "create_pull_request",
        "list_pr_threads",
        "list_pipelines",
        "run_pipeline",
        "list_builds",
        "get_work_item",
        "list_work_items",
        "create_work_item",
        "update_work_item",
        "my_work_items",
        "search_work_items",
        "list_wikis",
        "get_wiki_page",
        "search_code",
        "list_iterations",
    ]

    def __init__(self):
        self._configured = self._check_configuration()

    def _check_configuration(self) -> bool:
        """Check if Azure DevOps is configured."""
        import os

        return bool(os.getenv("AZURE_DEVOPS_ORG") and os.getenv("AZURE_DEVOPS_PAT"))

    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute Azure DevOps operations based on intent."""
        if not self._configured:
            return FacultyResult(
                success=False,
                summary="Azure DevOps is not configured. "
                "Please set AZURE_DEVOPS_ORG and AZURE_DEVOPS_PAT.",
                error="Azure DevOps not configured",
            )

        logger.info(f"[faculty:ado] Intent: {intent}")

        try:
            # Parse intent to determine action
            action, params = self._parse_intent(intent, constraints)
            logger.info(f"[faculty:ado] Action: {action}, Params: {params}")

            # Execute the action
            result = await self._execute_action(action, params, user_id)
            return result

        except Exception as e:
            logger.exception(f"[faculty:ado] Error: {e}")
            return FacultyResult(
                success=False,
                summary=f"Azure DevOps operation failed: {str(e)}",
                error=str(e),
            )

    def _parse_intent(
        self, intent: str, constraints: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Parse natural language intent into action and parameters."""
        intent_lower = intent.lower()

        # My work items / assigned to me
        if any(
            x in intent_lower
            for x in ["my work item", "assigned to me", "my task", "my bug"]
        ):
            project = self._extract_project(intent)
            return "my_work_items", {"project": project}

        # Search work items
        if "search" in intent_lower and any(
            x in intent_lower for x in ["work item", "task", "bug", "story"]
        ):
            project = self._extract_project(intent)
            query = self._extract_query(intent)
            return "search_work_items", {"project": project, "searchText": query}

        # Get specific work item
        if any(
            x in intent_lower for x in ["work item #", "task #", "bug #", "story #"]
        ):
            work_item_id = self._extract_work_item_id(intent)
            project = self._extract_project(intent)
            return "get_work_item", {"id": work_item_id, "project": project}

        # List work items
        if any(x in intent_lower for x in ["list work item", "show work item"]):
            project = self._extract_project(intent)
            return "list_work_items", {"project": project}

        # Create work item
        if "create" in intent_lower and any(
            x in intent_lower for x in ["work item", "task", "bug", "story"]
        ):
            project = self._extract_project(intent)
            title = self._extract_title(intent)
            work_item_type = self._extract_work_item_type(intent)
            return "create_work_item", {
                "project": project,
                "workItemType": work_item_type,
                "title": title,
            }

        # Update work item
        if "update" in intent_lower and any(
            x in intent_lower for x in ["work item", "task", "bug"]
        ):
            work_item_id = self._extract_work_item_id(intent)
            state = self._extract_state(intent)
            return "update_work_item", {"id": work_item_id, "state": state}

        # List PRs
        if any(x in intent_lower for x in ["list pr", "pull request", "open pr"]):
            project = self._extract_project(intent)
            repo = self._extract_repo(intent)
            status = (
                "active"
                if "open" in intent_lower or "active" in intent_lower
                else "all"
            )
            return "list_pull_requests", {
                "project": project,
                "repository": repo,
                "status": status,
            }

        # Create PR
        if "create" in intent_lower and (
            "pr" in intent_lower or "pull" in intent_lower
        ):
            project = self._extract_project(intent)
            repo = self._extract_repo(intent)
            title = self._extract_title(intent)
            source_branch = self._extract_branch(intent, "source")
            target_branch = self._extract_branch(intent, "target") or "main"
            return "create_pull_request", {
                "project": project,
                "repository": repo,
                "title": title,
                "sourceRefName": f"refs/heads/{source_branch}",
                "targetRefName": f"refs/heads/{target_branch}",
            }

        # List repos
        if any(x in intent_lower for x in ["list repo", "show repo"]):
            project = self._extract_project(intent)
            return "list_repos", {"project": project}

        # List branches
        if "branch" in intent_lower and any(
            x in intent_lower for x in ["list", "show"]
        ):
            project = self._extract_project(intent)
            repo = self._extract_repo(intent)
            return "list_branches", {"project": project, "repository": repo}

        # List commits
        if "commit" in intent_lower and any(
            x in intent_lower for x in ["list", "show", "recent"]
        ):
            project = self._extract_project(intent)
            repo = self._extract_repo(intent)
            return "list_commits", {"project": project, "repository": repo}

        # Get file
        if any(
            x in intent_lower
            for x in ["get file", "read file", "show file", "contents of"]
        ):
            project = self._extract_project(intent)
            repo = self._extract_repo(intent)
            path = self._extract_path(intent)
            return "get_file", {"project": project, "repository": repo, "path": path}

        # List pipelines
        if "pipeline" in intent_lower and any(
            x in intent_lower for x in ["list", "show"]
        ):
            project = self._extract_project(intent)
            return "list_pipelines", {"project": project}

        # Run pipeline
        if "pipeline" in intent_lower and any(
            x in intent_lower for x in ["run", "trigger", "start"]
        ):
            project = self._extract_project(intent)
            pipeline_id = self._extract_pipeline_id(intent)
            return "run_pipeline", {"project": project, "pipelineId": pipeline_id}

        # List builds
        if "build" in intent_lower and any(
            x in intent_lower for x in ["list", "show", "recent"]
        ):
            project = self._extract_project(intent)
            return "list_builds", {"project": project}

        # List wikis
        if "wiki" in intent_lower and any(x in intent_lower for x in ["list", "show"]):
            project = self._extract_project(intent)
            return "list_wikis", {"project": project}

        # Get wiki page
        if "wiki" in intent_lower and any(
            x in intent_lower for x in ["get", "read", "page"]
        ):
            project = self._extract_project(intent)
            wiki_id = self._extract_wiki_id(intent)
            path = self._extract_path(intent)
            return "get_wiki_page", {
                "project": project,
                "wikiIdentifier": wiki_id,
                "path": path,
            }

        # Search code
        if "search" in intent_lower and "code" in intent_lower:
            project = self._extract_project(intent)
            query = self._extract_query(intent)
            return "search_code", {"project": project, "searchText": query}

        # List iterations/sprints
        if any(x in intent_lower for x in ["iteration", "sprint"]):
            project = self._extract_project(intent)
            return "list_iterations", {"project": project}

        # List projects (default)
        if "project" in intent_lower and any(
            x in intent_lower for x in ["list", "show"]
        ):
            return "list_projects", {}

        # Fallback: list projects
        return "list_projects", {}

    async def _execute_action(
        self, action: str, params: dict[str, Any], user_id: str | None
    ) -> FacultyResult:
        """Execute an Azure DevOps action via the underlying tools."""
        from tools._base import ToolContext

        # Create a context for tool execution
        ctx = ToolContext(user_id=user_id or "faculty")

        # Import the Azure DevOps tools module
        from tools import azure_devops as ado

        try:
            if action == "list_projects":
                result = await ado.list_projects({}, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Projects:\n{result}",
                    data={"projects": result},
                )

            elif action == "list_repos":
                result = await ado.list_repos(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Repositories:\n{result}",
                    data={"repos": result},
                )

            elif action == "list_branches":
                result = await ado.list_branches(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Branches:\n{result}",
                    data={"branches": result},
                )

            elif action == "list_commits":
                result = await ado.list_commits(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Recent commits:\n{result}",
                    data={"commits": result},
                )

            elif action == "get_file":
                result = await ado.get_file_contents(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"File contents:\n{result[:500]}...",
                    data={"contents": result},
                )

            elif action == "list_pull_requests":
                result = await ado.list_pull_requests(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Pull requests:\n{result}",
                    data={"pull_requests": result},
                )

            elif action == "create_pull_request":
                result = await ado.create_pull_request(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Created PR: {result}",
                    data={"pull_request": result},
                )

            elif action == "list_pr_threads":
                result = await ado.list_pr_threads(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"PR threads:\n{result}",
                    data={"threads": result},
                )

            elif action == "list_pipelines":
                result = await ado.list_pipelines(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Pipelines:\n{result}",
                    data={"pipelines": result},
                )

            elif action == "run_pipeline":
                result = await ado.run_pipeline(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Pipeline triggered: {result}",
                    data={"result": result},
                )

            elif action == "list_builds":
                result = await ado.list_builds(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Builds:\n{result}",
                    data={"builds": result},
                )

            elif action == "get_work_item":
                result = await ado.get_work_item(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Work item:\n{result}",
                    data={"work_item": result},
                )

            elif action == "list_work_items":
                result = await ado.list_work_items(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Work items:\n{result}",
                    data={"work_items": result},
                )

            elif action == "create_work_item":
                result = await ado.create_work_item(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Created work item: {result}",
                    data={"work_item": result},
                )

            elif action == "update_work_item":
                result = await ado.update_work_item(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Updated work item: {result}",
                    data={"work_item": result},
                )

            elif action == "my_work_items":
                result = await ado.my_work_items(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Your work items:\n{result}",
                    data={"work_items": result},
                )

            elif action == "search_work_items":
                result = await ado.search_work_items(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Search results:\n{result}",
                    data={"work_items": result},
                )

            elif action == "list_wikis":
                result = await ado.list_wikis(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Wikis:\n{result}",
                    data={"wikis": result},
                )

            elif action == "get_wiki_page":
                result = await ado.get_wiki_page(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Wiki page:\n{result[:500]}...",
                    data={"page": result},
                )

            elif action == "search_code":
                result = await ado.search_code(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Code search results:\n{result}",
                    data={"results": result},
                )

            elif action == "list_iterations":
                result = await ado.list_iterations(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Iterations:\n{result}",
                    data={"iterations": result},
                )

            else:
                return FacultyResult(
                    success=False,
                    summary=f"Unknown Azure DevOps action: {action}",
                    error=f"Action '{action}' not implemented",
                )

        except Exception as e:
            return FacultyResult(
                success=False,
                summary=f"Azure DevOps API error: {str(e)}",
                error=str(e),
            )

    # Helper methods for intent parsing
    def _extract_query(self, intent: str) -> str:
        """Extract search query from intent."""
        match = re.search(r'"([^"]+)"', intent)
        if match:
            return match.group(1)
        match = re.search(r"for\s+(.+)", intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return intent

    def _extract_project(self, intent: str) -> str:
        """Extract project name from intent."""
        match = re.search(r'project[:\s]+"?([^"\s,]+)"?', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r'in\s+"?([^"\s,]+)"?\s+project', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_repo(self, intent: str) -> str:
        """Extract repository name from intent."""
        match = re.search(r'repo(?:sitory)?[:\s]+"?([^"\s,]+)"?', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_work_item_id(self, intent: str) -> int:
        """Extract work item ID from intent."""
        match = re.search(r"#(\d+)", intent)
        if match:
            return int(match.group(1))
        match = re.search(r"work item\s+(\d+)", intent, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return 0

    def _extract_work_item_type(self, intent: str) -> str:
        """Extract work item type from intent."""
        intent_lower = intent.lower()
        if "bug" in intent_lower:
            return "Bug"
        elif "story" in intent_lower or "user story" in intent_lower:
            return "User Story"
        elif "feature" in intent_lower:
            return "Feature"
        elif "epic" in intent_lower:
            return "Epic"
        return "Task"

    def _extract_title(self, intent: str) -> str:
        """Extract title from intent."""
        match = re.search(r'title[:\s]+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r'titled?\s+"([^"]+)"', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_state(self, intent: str) -> str:
        """Extract work item state from intent."""
        intent_lower = intent.lower()
        if "done" in intent_lower or "completed" in intent_lower:
            return "Done"
        elif "active" in intent_lower or "in progress" in intent_lower:
            return "Active"
        elif "resolved" in intent_lower:
            return "Resolved"
        elif "closed" in intent_lower:
            return "Closed"
        return ""

    def _extract_branch(self, intent: str, branch_type: str) -> str:
        """Extract branch name from intent."""
        match = re.search(rf"{branch_type}[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_path(self, intent: str) -> str:
        """Extract file path from intent."""
        match = re.search(r"path[:\s]+(\S+)", intent, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"(\S+/\S+\.\w+)", intent)
        if match:
            return match.group(1)
        return ""

    def _extract_pipeline_id(self, intent: str) -> int:
        """Extract pipeline ID from intent."""
        match = re.search(r"pipeline\s+(\d+)", intent, re.IGNORECASE)
        if match:
            return int(match.group(1))
        match = re.search(r"#(\d+)", intent)
        if match:
            return int(match.group(1))
        return 0

    def _extract_wiki_id(self, intent: str) -> str:
        """Extract wiki identifier from intent."""
        match = re.search(r'wiki[:\s]+"?([^"\s,]+)"?', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""
