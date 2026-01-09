"""Email proactive checker.

Monitors email inbox for new messages.
Wraps the existing email_monitor.py functionality.
"""

from __future__ import annotations

import os
from typing import Any

from checkers.base import BaseChecker, CheckerConfig, CheckResult

# Import from existing email monitor
try:
    from email_monitor import EmailMonitor, EmailInfo
    EMAIL_AVAILABLE = True
except ImportError:
    EMAIL_AVAILABLE = False

# Configuration
EMAIL_ADDRESS = os.getenv("CLARISSA_EMAIL_ADDRESS", "")
EMAIL_PASSWORD = os.getenv("CLARISSA_EMAIL_PASSWORD", "")


class EmailChecker(BaseChecker):
    """Checks email inbox for new messages.

    Uses the existing EmailMonitor infrastructure.
    """

    name = "email"
    default_interval_minutes = 5

    def __init__(self, config: CheckerConfig | None = None):
        """Initialize the email checker."""
        super().__init__(config)
        self._monitor: EmailMonitor | None = None
        self._last_email_ids: dict[str, set[str]] = {}

    def _load_config(self) -> CheckerConfig:
        """Load configuration from environment."""
        config = CheckerConfig.from_env("EMAIL_CHECKER")
        # Disable if not configured
        if not EMAIL_AVAILABLE or not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            config.enabled = False
        return config

    def _get_monitor(self) -> EmailMonitor | None:
        """Get or create the email monitor."""
        if not EMAIL_AVAILABLE:
            return None
        if self._monitor is None:
            self._monitor = EmailMonitor()
        return self._monitor

    async def check(self, user_id: str) -> CheckResult:
        """Check email for new messages.

        Args:
            user_id: User identifier

        Returns:
            CheckResult with any new emails found
        """
        if not EMAIL_AVAILABLE:
            return CheckResult(
                has_updates=False,
                summary="Email monitor not available",
            )

        if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
            return CheckResult(
                has_updates=False,
                summary="Email not configured",
            )

        monitor = self._get_monitor()
        if not monitor:
            return CheckResult(has_updates=False)

        try:
            # Get unread emails
            emails = await self._fetch_unread_emails(monitor)

            # Filter to new ones
            new_emails = self._filter_new_emails(user_id, emails)

            if not new_emails:
                return CheckResult(has_updates=False)

            # Build summary
            count = len(new_emails)
            summary = f"{count} new email{'s' if count > 1 else ''}"

            # Build details
            email_details = []
            for email_info in new_emails[:5]:  # Limit to 5 in details
                email_details.append({
                    "from": email_info.from_addr,
                    "subject": email_info.subject,
                    "preview": email_info.preview[:100] if email_info.preview else "",
                })

            return CheckResult(
                has_updates=True,
                priority="normal",
                summary=summary,
                details={"emails": email_details, "total": count},
                suggested_action="Check your inbox for new messages.",
                target_users=[user_id],
            )

        except Exception as e:
            return CheckResult(
                has_updates=False,
                summary=f"Error checking email: {e}",
            )

    async def _fetch_unread_emails(self, monitor: EmailMonitor) -> list[EmailInfo]:
        """Fetch unread emails from the inbox."""
        import asyncio

        # Run the sync email check in a thread pool
        loop = asyncio.get_event_loop()
        emails = await loop.run_in_executor(
            None,
            self._sync_fetch_unread,
            monitor,
        )
        return emails

    def _sync_fetch_unread(self, monitor: EmailMonitor) -> list[EmailInfo]:
        """Synchronously fetch unread emails."""
        try:
            # Use the monitor's check_for_new_mail method if available
            # Otherwise use get_inbox
            if hasattr(monitor, "check_for_new_mail"):
                return monitor.check_for_new_mail()
            elif hasattr(monitor, "get_inbox"):
                return [e for e in monitor.get_inbox() if not e.is_read]
            else:
                return []
        except Exception:
            return []

    def _filter_new_emails(
        self, user_id: str, emails: list[EmailInfo]
    ) -> list[EmailInfo]:
        """Filter to only emails not seen before."""
        seen = self._last_email_ids.get(user_id, set())
        new_emails = []

        current_ids = set()
        for email_info in emails:
            email_id = email_info.uid
            current_ids.add(email_id)

            if email_id and email_id not in seen:
                new_emails.append(email_info)

        self._last_email_ids[user_id] = current_ids
        return new_emails
