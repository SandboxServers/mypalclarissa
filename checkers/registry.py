"""Checker registry for discovery and management.

Automatically discovers and registers checker implementations.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from checkers.base import BaseChecker

# Global proactive monitoring toggle
PROACTIVE_ENABLED = os.getenv("PROACTIVE_ENABLED", "true").lower() == "true"


class CheckerRegistry:
    """Registry for proactive checkers.

    Manages checker discovery, registration, and access.
    """

    _instance: CheckerRegistry | None = None

    def __init__(self):
        """Initialize the registry."""
        self._checkers: dict[str, BaseChecker] = {}
        self._initialized = False

    @classmethod
    def get_instance(cls) -> CheckerRegistry:
        """Get or create the singleton registry instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, checker: BaseChecker) -> None:
        """Register a checker.

        Args:
            checker: The checker instance to register
        """
        self._checkers[checker.name] = checker

    def unregister(self, name: str) -> None:
        """Unregister a checker by name."""
        self._checkers.pop(name, None)

    def get(self, name: str) -> BaseChecker | None:
        """Get a checker by name."""
        return self._checkers.get(name)

    def get_all(self) -> list[BaseChecker]:
        """Get all registered checkers."""
        return list(self._checkers.values())

    def get_enabled(self) -> list[BaseChecker]:
        """Get all enabled checkers."""
        if not PROACTIVE_ENABLED:
            return []
        return [c for c in self._checkers.values() if c.enabled]

    def get_names(self) -> list[str]:
        """Get names of all registered checkers."""
        return list(self._checkers.keys())

    def initialize(self) -> None:
        """Initialize and register all available checkers.

        This should be called once at application startup.
        """
        if self._initialized:
            return

        # Import checkers to trigger registration
        try:
            from checkers.github import GitHubChecker
            github_checker = GitHubChecker()
            self.register(github_checker)
        except ImportError:
            pass
        except Exception as e:
            print(f"[checkers] Failed to initialize GitHub checker: {e}")

        try:
            from checkers.ado import AzureDevOpsChecker
            ado_checker = AzureDevOpsChecker()
            self.register(ado_checker)
        except ImportError:
            pass
        except Exception as e:
            print(f"[checkers] Failed to initialize ADO checker: {e}")

        try:
            from checkers.email import EmailChecker
            email_checker = EmailChecker()
            self.register(email_checker)
        except ImportError:
            pass
        except Exception as e:
            print(f"[checkers] Failed to initialize Email checker: {e}")

        self._initialized = True

        enabled = [c.name for c in self.get_enabled()]
        print(f"[checkers] Initialized. Enabled checkers: {enabled}")


# Convenience function
def get_registry() -> CheckerRegistry:
    """Get the checker registry singleton."""
    return CheckerRegistry.get_instance()
