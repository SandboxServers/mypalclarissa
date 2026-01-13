"""Rumination models for Clarissa's conscious decision-making.

The rumination process is Clarissa's way of deciding what to do:
- SPEAK: Respond directly with knowledge + memory
- COMMAND: Use a faculty to take action
- WAIT: Do nothing (no response needed)

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Decision = Literal["speak", "command", "wait"]


@dataclass
class RuminationResult:
    """Output of Clarissa's conscious thought.

    The rumination process analyzes the user's message and decides:
    1. Can I respond directly? -> decision="speak"
    2. Do I need to use a capability? -> decision="command"
    3. Is there nothing to do? -> decision="wait"

    Attributes:
        decision: The decision type (speak, command, wait)
        reasoning: Internal reasoning for debugging/logging
        response_draft: The response to send (if decision="speak")
        faculty: Which faculty to use (if decision="command")
        intent: What to accomplish with the faculty (if decision="command")
        constraints: Boundaries on the action (if decision="command")
        wait_reason: Why no response is needed (if decision="wait")
    """

    decision: Decision
    reasoning: str

    # If decision == "speak"
    response_draft: str | None = None

    # If decision == "command"
    faculty: str | None = None
    intent: str | None = None
    constraints: list[str] = field(default_factory=list)

    # If decision == "wait"
    wait_reason: str | None = None

    def __repr__(self) -> str:
        if self.decision == "speak":
            preview = (self.response_draft or "")[:30]
            return f"RuminationResult(SPEAK: {preview}...)"
        elif self.decision == "command":
            return f"RuminationResult(COMMAND: {self.faculty} - {self.intent})"
        else:
            return f"RuminationResult(WAIT: {self.wait_reason})"

    def validate(self) -> list[str]:
        """Validate the rumination result.

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        if self.decision == "speak" and not self.response_draft:
            errors.append("decision='speak' requires response_draft")

        if self.decision == "command":
            if not self.faculty:
                errors.append("decision='command' requires faculty")
            if not self.intent:
                errors.append("decision='command' requires intent")

        return errors

    @property
    def is_valid(self) -> bool:
        """Check if the rumination result is valid."""
        return len(self.validate()) == 0


@dataclass
class RuminationContext:
    """Context provided to the rumination process.

    This contains all the information Clarissa needs to make a decision.
    """

    message: str
    user_id: str
    channel_id: str | None = None

    # Memory context
    user_memories: list[str] = field(default_factory=list)
    project_memories: list[str] = field(default_factory=list)

    # Conversation history
    conversation_history: list[dict] = field(default_factory=list)

    # Previous faculty result (if continuing after a command)
    previous_faculty_result: str | None = None

    # Available faculties (names and descriptions)
    available_faculties: list[str] = field(default_factory=list)

    # Iteration count (to prevent infinite loops)
    iteration: int = 0
