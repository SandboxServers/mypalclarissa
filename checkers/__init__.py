"""KIRA-inspired proactive monitoring framework.

Background checkers that monitor external services and proactively
notify users of important updates.

Available checkers:
- GitHubChecker: PR reviews, CI failures, mentions
- AzureDevOpsChecker: Work items, PR reviews, pipeline failures
- EmailChecker: New emails matching criteria

Usage:
    from checkers import CheckerRegistry, CheckerScheduler

    # Get enabled checkers
    registry = CheckerRegistry.get_instance()
    checkers = registry.get_enabled()

    # Start background monitoring (in Discord bot)
    scheduler = CheckerScheduler(bot)
    await scheduler.start()
"""

from checkers.base import BaseChecker, CheckResult, CheckerConfig
from checkers.registry import CheckerRegistry, get_registry
from checkers.scheduler import CheckerScheduler, get_scheduler

__all__ = [
    "BaseChecker",
    "CheckResult",
    "CheckerConfig",
    "CheckerRegistry",
    "get_registry",
    "CheckerScheduler",
    "get_scheduler",
]
