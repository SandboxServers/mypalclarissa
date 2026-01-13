"""Base faculty classes for Clarissa's action capabilities.

Faculties are skilled but not autonomous - they execute Clarissa's intent
without having their own goals. The decision to act comes from the Ruminate node.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


@dataclass
class FacultyResult:
    """Output of a faculty execution.

    Attributes:
        success: Whether the execution succeeded
        summary: Human-readable summary for Clarissa (shown to user/used in reasoning)
        data: Structured result data for programmatic use
        error: Error message if failed
        needs_followup: Signal if another action is needed
    """

    success: bool
    summary: str
    data: dict[str, Any] | None = None
    error: str | None = None
    needs_followup: bool = False

    def __repr__(self) -> str:
        status = "OK" if self.success else "FAILED"
        return f"FacultyResult({status}: {self.summary[:50]}...)"


class Faculty(ABC):
    """Base class for Clarissa's action capabilities.

    A faculty is skilled but not autonomous. It executes Clarissa's intent
    without having its own goals - the decision to act comes from Ruminate.

    Key design principles:
    - Faculties receive natural language intent, not structured commands
    - Each faculty interprets intent in its domain
    - Faculties can call multiple underlying tools to fulfill an intent
    - Results include human-readable summaries for Clarissa's reasoning

    Subclasses must define:
    - name: Unique identifier (e.g., "github", "code", "ado")
    - description: Human-readable description of capabilities
    - available_actions: List of actions this faculty can perform
    - execute(): The main execution method

    Example:
        class GitHubFaculty(Faculty):
            name = "github"
            description = "GitHub API operations (repos, issues, PRs)"
            available_actions = ["list_repos", "create_issue", "list_prs"]

            async def execute(self, intent, constraints=None, ...):
                # Parse intent and dispatch to appropriate tools
                ...
    """

    name: str
    description: str
    available_actions: list[str]

    @abstractmethod
    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute Clarissa's intent using this faculty's skills.

        Args:
            intent: Natural language description of what to accomplish
            constraints: Optional boundaries on the action (e.g., "only my repos")
            user_id: User context for per-user state
            channel_id: Channel context (Discord)

        Returns:
            FacultyResult with success status, summary, and optional data
        """
        pass

    def get_description(self) -> str:
        """Get a formatted description for the rumination prompt."""
        actions = ", ".join(self.available_actions[:5])
        if len(self.available_actions) > 5:
            actions += f", ... ({len(self.available_actions)} total)"
        return f"**{self.name}**: {self.description}\n  Actions: {actions}"

    def __repr__(self) -> str:
        return f"Faculty({self.name})"
