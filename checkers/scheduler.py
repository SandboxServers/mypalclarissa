"""Background scheduler for proactive checkers.

Runs checkers on their configured intervals and delivers notifications.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from checkers.base import BaseChecker, CheckResult

# Scheduler configuration
SCHEDULER_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").lower() == "true"
SCHEDULER_LOG_LEVEL = os.getenv("PROACTIVE_LOG_LEVEL", "info").lower()


class CheckerScheduler:
    """Background scheduler for running checkers.

    Runs as asyncio tasks alongside the Discord bot.
    """

    _instance: CheckerScheduler | None = None

    def __init__(self, bot: Any = None, db_session_factory: Any = None):
        """Initialize the scheduler.

        Args:
            bot: Discord bot instance for sending notifications
            db_session_factory: Database session factory for subscriptions
        """
        self._bot = bot
        self._db_factory = db_session_factory
        self._tasks: dict[str, asyncio.Task] = {}
        self._running = False
        self._subscriptions: dict[str, dict[str, str]] = {}  # user_id -> {checker_name -> channel_id}

    @classmethod
    def get_instance(cls, bot: Any = None, db_session_factory: Any = None) -> CheckerScheduler:
        """Get or create the singleton scheduler instance."""
        if cls._instance is None:
            cls._instance = cls(bot, db_session_factory)
        return cls._instance

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    def set_bot(self, bot: Any) -> None:
        """Set the Discord bot instance."""
        self._bot = bot

    def set_db_factory(self, factory: Any) -> None:
        """Set the database session factory."""
        self._db_factory = factory

    async def start(self) -> None:
        """Start all enabled checkers.

        Called from discord_bot.py on_ready().
        """
        if not SCHEDULER_ENABLED:
            self._log("Proactive monitoring is disabled")
            return

        if self._running:
            self._log("Scheduler already running")
            return

        from checkers.registry import get_registry
        registry = get_registry()
        registry.initialize()

        enabled_checkers = registry.get_enabled()
        if not enabled_checkers:
            self._log("No enabled checkers found")
            return

        self._running = True
        self._log(f"Starting scheduler with {len(enabled_checkers)} checker(s)")

        for checker in enabled_checkers:
            task = asyncio.create_task(
                self._run_checker_loop(checker),
                name=f"checker_{checker.name}",
            )
            self._tasks[checker.name] = task
            self._log(f"Started {checker.name} checker (interval: {checker.interval})")

    async def stop(self) -> None:
        """Stop all running checker tasks."""
        self._running = False
        for name, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._log(f"Stopped {name} checker")
        self._tasks.clear()

    async def _run_checker_loop(self, checker: BaseChecker) -> None:
        """Run a checker on its interval.

        Args:
            checker: The checker to run
        """
        while self._running:
            try:
                # Get users subscribed to this checker
                subscribed_users = await self._get_subscribed_users(checker.name)

                for user_id, channel_id in subscribed_users.items():
                    try:
                        result = await checker.run_check(user_id)
                        if result:
                            await self._deliver_notification(
                                checker, user_id, channel_id, result
                            )
                    except Exception as e:
                        self._log(f"Error checking {checker.name} for {user_id}: {e}", level="error")

            except Exception as e:
                self._log(f"Error in {checker.name} loop: {e}", level="error")

            # Sleep until next check
            await asyncio.sleep(checker.interval.total_seconds())

    async def _get_subscribed_users(self, checker_name: str) -> dict[str, str]:
        """Get users subscribed to a checker.

        Args:
            checker_name: Name of the checker

        Returns:
            Dict of user_id -> channel_id for notifications
        """
        # First check in-memory subscriptions
        result = {}
        for user_id, subs in self._subscriptions.items():
            if checker_name in subs:
                result[user_id] = subs[checker_name]

        # If we have a database, check there too
        if self._db_factory:
            try:
                db_subs = await self._load_subscriptions_from_db(checker_name)
                result.update(db_subs)
            except Exception as e:
                self._log(f"Failed to load subscriptions from DB: {e}", level="error")

        return result

    async def _load_subscriptions_from_db(self, checker_name: str) -> dict[str, str]:
        """Load subscriptions from database.

        Args:
            checker_name: Name of the checker

        Returns:
            Dict of user_id -> channel_id
        """
        # This will be implemented when we add the database models
        # For now, return empty dict
        return {}

    async def _deliver_notification(
        self,
        checker: BaseChecker,
        user_id: str,
        channel_id: str,
        result: CheckResult,
    ) -> None:
        """Deliver a notification to the user.

        Args:
            checker: The checker that generated the result
            user_id: The user to notify
            channel_id: The channel to send notification to
            result: The check result
        """
        if not self._bot:
            self._log(f"No bot available for notification to {user_id}")
            return

        try:
            channel = self._bot.get_channel(int(channel_id))
            if not channel:
                self._log(f"Channel {channel_id} not found for {user_id}")
                return

            notification_text = result.to_notification_text()
            if notification_text:
                # Send proactive notification with subtle formatting
                message = f"**🔔 {checker.name.title()} Update**\n\n{notification_text}"
                await channel.send(message, silent=True)
                self._log(f"Sent {checker.name} notification to {user_id} in {channel_id}")

        except Exception as e:
            self._log(f"Failed to deliver notification to {user_id}: {e}", level="error")

    def subscribe(self, user_id: str, checker_name: str, channel_id: str) -> None:
        """Subscribe a user to a checker.

        Args:
            user_id: User identifier
            checker_name: Name of checker to subscribe to
            channel_id: Channel to receive notifications
        """
        if user_id not in self._subscriptions:
            self._subscriptions[user_id] = {}
        self._subscriptions[user_id][checker_name] = channel_id
        self._log(f"Subscribed {user_id} to {checker_name} in {channel_id}")

    def unsubscribe(self, user_id: str, checker_name: str) -> None:
        """Unsubscribe a user from a checker.

        Args:
            user_id: User identifier
            checker_name: Name of checker to unsubscribe from
        """
        if user_id in self._subscriptions:
            self._subscriptions[user_id].pop(checker_name, None)
            self._log(f"Unsubscribed {user_id} from {checker_name}")

    def get_user_subscriptions(self, user_id: str) -> dict[str, str]:
        """Get all subscriptions for a user.

        Returns:
            Dict of checker_name -> channel_id
        """
        return self._subscriptions.get(user_id, {}).copy()

    def get_status(self) -> dict:
        """Get scheduler status for monitoring."""
        from checkers.registry import get_registry
        registry = get_registry()

        return {
            "running": self._running,
            "enabled": SCHEDULER_ENABLED,
            "checkers": [
                {
                    "name": c.name,
                    "enabled": c.enabled,
                    "interval_minutes": c.config.interval_minutes,
                }
                for c in registry.get_all()
            ],
            "active_tasks": list(self._tasks.keys()),
            "total_subscriptions": sum(
                len(subs) for subs in self._subscriptions.values()
            ),
        }

    def _log(self, message: str, level: str = "info") -> None:
        """Log a scheduler message."""
        try:
            from config.logging import get_logger
            logger = get_logger("checkers")
            getattr(logger, level)(f"[scheduler] {message}")
        except ImportError:
            if level == "error" or SCHEDULER_LOG_LEVEL == "debug":
                print(f"[checkers:{level}] {message}")


# Convenience function
def get_scheduler(bot: Any = None, db_session_factory: Any = None) -> CheckerScheduler:
    """Get the scheduler singleton."""
    return CheckerScheduler.get_instance(bot, db_session_factory)
