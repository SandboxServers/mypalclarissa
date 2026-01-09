"""Base classes for KIRA-inspired proactive checkers.

Checkers run in the background and:
1. Poll external services (GitHub, ADO, Email)
2. Determine if updates warrant notification
3. Return structured results for Clarissa to act on
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

# Priority levels for notifications
Priority = Literal["low", "normal", "high", "critical"]


@dataclass
class CheckResult:
    """Result from a checker execution."""

    has_updates: bool
    priority: Priority = "normal"
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    suggested_action: str | None = None
    target_users: list[str] = field(default_factory=list)
    # Timestamp for deduplication
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __repr__(self) -> str:
        return f"CheckResult(has_updates={self.has_updates}, priority={self.priority}, summary={self.summary!r})"

    def to_notification_text(self) -> str:
        """Convert result to user-friendly notification text."""
        if not self.has_updates:
            return ""

        priority_emoji = {
            "low": "📋",
            "normal": "📌",
            "high": "⚠️",
            "critical": "🚨",
        }
        emoji = priority_emoji.get(self.priority, "📌")

        text = f"{emoji} **{self.summary}**"

        if self.suggested_action:
            text += f"\n\n💡 {self.suggested_action}"

        return text


@dataclass
class CheckerConfig:
    """Configuration for a checker."""

    enabled: bool = True
    interval_minutes: int = 15
    quiet_hours_start: int = 22  # 10 PM
    quiet_hours_end: int = 8  # 8 AM
    min_priority_for_interrupt: Priority = "high"
    # Custom settings per checker
    custom: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str) -> CheckerConfig:
        """Load configuration from environment variables.

        Args:
            prefix: Environment variable prefix (e.g., "GITHUB_CHECKER")
        """
        return cls(
            enabled=os.getenv(f"{prefix}_ENABLED", "true").lower() == "true",
            interval_minutes=int(os.getenv(f"{prefix}_INTERVAL", "15")),
            quiet_hours_start=int(os.getenv("PROACTIVE_QUIET_START", "22")),
            quiet_hours_end=int(os.getenv("PROACTIVE_QUIET_END", "8")),
        )


class BaseChecker(ABC):
    """Base class for proactive checkers.

    Subclasses must implement:
    - name: Checker identifier
    - default_interval_minutes: Default check interval
    - check(): Execute the check for a user
    """

    def __init__(self, config: CheckerConfig | None = None):
        """Initialize the checker.

        Args:
            config: Optional configuration. If None, loads from environment.
        """
        self._config = config or self._load_config()
        self._last_check: dict[str, datetime] = {}  # user_id -> last check time
        self._last_results: dict[str, CheckResult] = {}  # user_id -> last result

    @property
    @abstractmethod
    def name(self) -> str:
        """Checker name for logging and config."""
        ...

    @property
    @abstractmethod
    def default_interval_minutes(self) -> int:
        """Default check interval in minutes."""
        ...

    @property
    def env_prefix(self) -> str:
        """Environment variable prefix for this checker."""
        return f"{self.name.upper()}_CHECKER"

    @property
    def config(self) -> CheckerConfig:
        """Get checker configuration."""
        return self._config

    @property
    def enabled(self) -> bool:
        """Check if this checker is enabled."""
        return self._config.enabled

    @property
    def interval(self) -> timedelta:
        """Get check interval as timedelta."""
        return timedelta(minutes=self._config.interval_minutes or self.default_interval_minutes)

    def _load_config(self) -> CheckerConfig:
        """Load configuration from environment."""
        return CheckerConfig.from_env(self.env_prefix)

    @abstractmethod
    async def check(self, user_id: str) -> CheckResult:
        """Execute the check for a user.

        Args:
            user_id: User identifier to check for

        Returns:
            CheckResult with any updates found
        """
        ...

    def should_notify(
        self,
        result: CheckResult,
        is_quiet_hours: bool = False,
    ) -> bool:
        """Determine if user should be notified based on result.

        Args:
            result: The check result
            is_quiet_hours: Whether we're currently in quiet hours

        Returns:
            True if notification should be sent
        """
        if not result.has_updates:
            return False

        # During quiet hours, only critical notifications
        if is_quiet_hours:
            return result.priority == "critical"

        # Check minimum priority for interruption
        priority_order = ["low", "normal", "high", "critical"]
        min_priority = self._config.min_priority_for_interrupt
        result_priority_idx = priority_order.index(result.priority)
        min_priority_idx = priority_order.index(min_priority)

        return result_priority_idx >= min_priority_idx

    def is_quiet_hours(self) -> bool:
        """Check if we're currently in quiet hours."""
        hour = datetime.now().hour
        start = self._config.quiet_hours_start
        end = self._config.quiet_hours_end

        # Handle overnight quiet hours (e.g., 22:00 - 08:00)
        if start > end:
            return hour >= start or hour < end
        else:
            return start <= hour < end

    def should_check(self, user_id: str) -> bool:
        """Check if enough time has passed since last check for user."""
        last = self._last_check.get(user_id)
        if not last:
            return True
        return datetime.utcnow() - last >= self.interval

    def record_check(self, user_id: str, result: CheckResult) -> None:
        """Record that a check was performed."""
        self._last_check[user_id] = datetime.utcnow()
        self._last_results[user_id] = result

    def get_last_result(self, user_id: str) -> CheckResult | None:
        """Get the last check result for a user."""
        return self._last_results.get(user_id)

    async def run_check(self, user_id: str) -> CheckResult | None:
        """Run the check if it's time, return result if notification needed.

        This is the main entry point for the scheduler.
        """
        if not self.enabled:
            return None

        if not self.should_check(user_id):
            return None

        result = await self.check(user_id)
        self.record_check(user_id, result)

        if self.should_notify(result, self.is_quiet_hours()):
            return result

        return None
