"""GitHub proactive checker.

Monitors GitHub for updates relevant to the user:
- PR reviews requested
- PR comments/reviews on user's PRs
- Issue assignments
- CI/CD failures on watched repos
- Mentions in issues/PRs
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from checkers.base import BaseChecker, CheckerConfig, CheckResult

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_URL = "https://api.github.com"


def _get_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
) -> dict | list:
    """Make a GitHub API request."""
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN not configured")

    url = f"{GITHUB_API_URL}{endpoint}"

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
            raise ValueError(f"GitHub API error ({response.status_code}): {error_msg}")

        return response.json()


class GitHubChecker(BaseChecker):
    """Checks GitHub for updates relevant to the user.

    Monitors:
    - Notifications (unread)
    - PRs awaiting review
    - CI failures on PRs
    """

    name = "github"
    default_interval_minutes = 15

    def __init__(self, config: CheckerConfig | None = None):
        """Initialize the GitHub checker."""
        super().__init__(config)
        self._last_notification_ids: dict[str, set[str]] = {}  # user_id -> seen notification ids

    def _load_config(self) -> CheckerConfig:
        """Load configuration from environment."""
        config = CheckerConfig.from_env("GITHUB_CHECKER")
        # Disable if no token
        if not GITHUB_TOKEN:
            config.enabled = False
        return config

    async def check(self, user_id: str) -> CheckResult:
        """Check GitHub for updates.

        Args:
            user_id: User identifier (not used for GitHub as it's per-token)

        Returns:
            CheckResult with any updates found
        """
        if not GITHUB_TOKEN:
            return CheckResult(
                has_updates=False,
                summary="GitHub token not configured",
            )

        updates: list[dict[str, Any]] = []
        priority = "normal"

        try:
            # Check notifications
            notifications = await self._get_notifications()
            new_notifications = self._filter_new_notifications(user_id, notifications)
            updates.extend(new_notifications)

            # Check PRs awaiting review
            prs_to_review = await self._get_review_requests()
            for pr in prs_to_review:
                updates.append({
                    "type": "review_requested",
                    "title": pr.get("title", ""),
                    "repo": pr.get("repository", {}).get("full_name", ""),
                    "url": pr.get("html_url", ""),
                })

            # Check CI failures on user's PRs
            ci_failures = await self._get_ci_failures()
            for failure in ci_failures:
                updates.append({
                    "type": "ci_failure",
                    "title": failure.get("title", ""),
                    "repo": failure.get("repo", ""),
                    "url": failure.get("url", ""),
                })
                priority = "high"  # CI failures are high priority

        except Exception as e:
            return CheckResult(
                has_updates=False,
                summary=f"Error checking GitHub: {e}",
            )

        if not updates:
            return CheckResult(has_updates=False)

        # Build summary
        summary_parts = []
        review_count = sum(1 for u in updates if u.get("type") == "review_requested")
        ci_count = sum(1 for u in updates if u.get("type") == "ci_failure")
        notif_count = sum(1 for u in updates if u.get("type") == "notification")

        if review_count:
            summary_parts.append(f"{review_count} PR(s) awaiting your review")
        if ci_count:
            summary_parts.append(f"{ci_count} CI failure(s)")
        if notif_count:
            summary_parts.append(f"{notif_count} new notification(s)")

        return CheckResult(
            has_updates=True,
            priority=priority,
            summary=", ".join(summary_parts),
            details={"updates": updates},
            suggested_action=self._build_suggested_action(updates),
            target_users=[user_id],
        )

    async def _get_notifications(self) -> list[dict]:
        """Get unread notifications."""
        try:
            return await _github_request(
                "GET",
                "/notifications",
                params={"all": "false", "per_page": 20},
            )
        except Exception:
            return []

    async def _get_review_requests(self) -> list[dict]:
        """Get PRs where user's review is requested."""
        try:
            # Get authenticated user
            user = await _github_request("GET", "/user")
            username = user.get("login", "")

            if not username:
                return []

            # Search for PRs requesting review from this user
            result = await _github_request(
                "GET",
                "/search/issues",
                params={
                    "q": f"is:open is:pr review-requested:{username}",
                    "per_page": 10,
                },
            )
            return result.get("items", [])
        except Exception:
            return []

    async def _get_ci_failures(self) -> list[dict]:
        """Get CI failures on user's PRs."""
        try:
            # Get authenticated user
            user = await _github_request("GET", "/user")
            username = user.get("login", "")

            if not username:
                return []

            # Search for user's open PRs
            result = await _github_request(
                "GET",
                "/search/issues",
                params={
                    "q": f"is:open is:pr author:{username}",
                    "per_page": 10,
                },
            )

            failures = []
            for pr in result.get("items", []):
                # Check if PR has failing checks
                # The search API doesn't include check status, so we'd need
                # to make additional API calls per PR
                # For now, we skip this to avoid rate limiting
                pass

            return failures
        except Exception:
            return []

    def _filter_new_notifications(
        self, user_id: str, notifications: list[dict]
    ) -> list[dict]:
        """Filter to only new notifications since last check."""
        seen = self._last_notification_ids.get(user_id, set())
        new_notifications = []

        current_ids = set()
        for notif in notifications:
            notif_id = notif.get("id", "")
            current_ids.add(notif_id)

            if notif_id and notif_id not in seen:
                new_notifications.append({
                    "type": "notification",
                    "reason": notif.get("reason", "unknown"),
                    "title": notif.get("subject", {}).get("title", ""),
                    "repo": notif.get("repository", {}).get("full_name", ""),
                    "url": notif.get("subject", {}).get("url", ""),
                })

        # Update seen set
        self._last_notification_ids[user_id] = current_ids
        return new_notifications

    def _build_suggested_action(self, updates: list[dict]) -> str | None:
        """Build suggested action based on updates."""
        review_count = sum(1 for u in updates if u.get("type") == "review_requested")
        ci_count = sum(1 for u in updates if u.get("type") == "ci_failure")

        if ci_count:
            return "Review the failing CI checks and fix any issues."
        if review_count:
            return "Review the pending pull requests."

        return None
