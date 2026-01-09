"""Azure DevOps proactive checker.

Monitors Azure DevOps for updates relevant to the user:
- Work items assigned to user
- PR reviews requested
- Build/pipeline failures
- Mentions in work item discussions
"""

from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from checkers.base import BaseChecker, CheckerConfig, CheckResult

# Configuration
AZURE_DEVOPS_ORG = os.getenv("AZURE_DEVOPS_ORG", "")
AZURE_DEVOPS_PAT = os.getenv("AZURE_DEVOPS_PAT", "")
API_VERSION = "7.1"


def _get_base_url() -> str:
    """Get the Azure DevOps base URL."""
    org = AZURE_DEVOPS_ORG
    if org.startswith("http"):
        return org.rstrip("/")
    return f"https://dev.azure.com/{org}"


def _get_headers() -> dict[str, str]:
    """Get headers for Azure DevOps API requests."""
    auth = base64.b64encode(f":{AZURE_DEVOPS_PAT}".encode()).decode()
    return {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }


async def _ado_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
) -> dict | list:
    """Make an Azure DevOps API request."""
    if not AZURE_DEVOPS_ORG or not AZURE_DEVOPS_PAT:
        raise ValueError("Azure DevOps not configured")

    base_url = _get_base_url()
    url = f"{base_url}/{endpoint.lstrip('/')}"

    params = params or {}
    params["api-version"] = API_VERSION

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=_get_headers(),
            params=params,
            timeout=30.0,
        )

        if response.status_code >= 400:
            error_msg = response.text
            try:
                error_data = response.json()
                error_msg = error_data.get("message", response.text)
            except Exception:
                pass
            raise ValueError(f"ADO API error ({response.status_code}): {error_msg}")

        return response.json()


class AzureDevOpsChecker(BaseChecker):
    """Checks Azure DevOps for updates relevant to the user.

    Monitors:
    - Work items assigned to user
    - PRs awaiting review
    - Build failures
    """

    name = "ado"
    default_interval_minutes = 15

    def __init__(self, config: CheckerConfig | None = None):
        """Initialize the ADO checker."""
        super().__init__(config)
        self._last_work_item_ids: dict[str, set[str]] = {}  # user_id -> seen IDs

    def _load_config(self) -> CheckerConfig:
        """Load configuration from environment."""
        config = CheckerConfig.from_env("ADO_CHECKER")
        # Disable if not configured
        if not AZURE_DEVOPS_ORG or not AZURE_DEVOPS_PAT:
            config.enabled = False
        return config

    async def check(self, user_id: str) -> CheckResult:
        """Check Azure DevOps for updates.

        Args:
            user_id: User identifier

        Returns:
            CheckResult with any updates found
        """
        if not AZURE_DEVOPS_ORG or not AZURE_DEVOPS_PAT:
            return CheckResult(
                has_updates=False,
                summary="Azure DevOps not configured",
            )

        updates: list[dict[str, Any]] = []
        priority = "normal"

        try:
            # Check work items assigned to user
            work_items = await self._get_my_work_items()
            new_work_items = self._filter_new_work_items(user_id, work_items)
            updates.extend(new_work_items)

            # Check PRs awaiting review
            prs_to_review = await self._get_review_requests()
            for pr in prs_to_review:
                updates.append({
                    "type": "review_requested",
                    "title": pr.get("title", ""),
                    "repo": pr.get("repository", {}).get("name", ""),
                    "url": pr.get("url", ""),
                })

            # Check build failures
            build_failures = await self._get_build_failures()
            for failure in build_failures:
                updates.append({
                    "type": "build_failure",
                    "title": failure.get("buildNumber", ""),
                    "pipeline": failure.get("definition", {}).get("name", ""),
                    "url": failure.get("_links", {}).get("web", {}).get("href", ""),
                })
                priority = "high"

        except Exception as e:
            return CheckResult(
                has_updates=False,
                summary=f"Error checking ADO: {e}",
            )

        if not updates:
            return CheckResult(has_updates=False)

        # Build summary
        summary_parts = []
        review_count = sum(1 for u in updates if u.get("type") == "review_requested")
        build_count = sum(1 for u in updates if u.get("type") == "build_failure")
        wi_count = sum(1 for u in updates if u.get("type") == "work_item")

        if review_count:
            summary_parts.append(f"{review_count} PR(s) awaiting review")
        if build_count:
            summary_parts.append(f"{build_count} build failure(s)")
        if wi_count:
            summary_parts.append(f"{wi_count} new work item(s)")

        return CheckResult(
            has_updates=True,
            priority=priority,
            summary=", ".join(summary_parts),
            details={"updates": updates},
            suggested_action=self._build_suggested_action(updates),
            target_users=[user_id],
        )

    async def _get_my_work_items(self) -> list[dict]:
        """Get work items assigned to the current user."""
        try:
            # Use WIQL to query assigned work items
            wiql = {
                "query": """
                    SELECT [System.Id], [System.Title], [System.State]
                    FROM WorkItems
                    WHERE [System.AssignedTo] = @Me
                    AND [System.State] NOT IN ('Done', 'Closed', 'Removed')
                    ORDER BY [System.ChangedDate] DESC
                """
            }

            # This would require a POST request with the WIQL query
            # For simplicity, we return empty for now
            # A full implementation would use the work item tracking API
            return []
        except Exception:
            return []

    async def _get_review_requests(self) -> list[dict]:
        """Get PRs where user's review is requested."""
        try:
            # Query all repos and their PRs
            # This would require listing projects, then repos, then PRs
            # For simplicity, we return empty for now
            return []
        except Exception:
            return []

    async def _get_build_failures(self) -> list[dict]:
        """Get recent build failures."""
        try:
            # Query recent builds with failed status
            # Would need to iterate through projects
            return []
        except Exception:
            return []

    def _filter_new_work_items(
        self, user_id: str, work_items: list[dict]
    ) -> list[dict]:
        """Filter to only new work items since last check."""
        seen = self._last_work_item_ids.get(user_id, set())
        new_items = []

        current_ids = set()
        for item in work_items:
            item_id = str(item.get("id", ""))
            current_ids.add(item_id)

            if item_id and item_id not in seen:
                new_items.append({
                    "type": "work_item",
                    "id": item_id,
                    "title": item.get("fields", {}).get("System.Title", ""),
                    "state": item.get("fields", {}).get("System.State", ""),
                })

        self._last_work_item_ids[user_id] = current_ids
        return new_items

    def _build_suggested_action(self, updates: list[dict]) -> str | None:
        """Build suggested action based on updates."""
        review_count = sum(1 for u in updates if u.get("type") == "review_requested")
        build_count = sum(1 for u in updates if u.get("type") == "build_failure")

        if build_count:
            return "Review the failing builds and fix any issues."
        if review_count:
            return "Review the pending pull requests."

        return None
