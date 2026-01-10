"""Rejection pipeline for multi-user/group chat handling.

Inspired by HuixiangDou's three-stage pipeline, this module determines
whether Clarissa should respond to a message in group contexts.

Key concepts:
- Rejection codes for different non-response scenarios
- Dynamic throttle that adjusts based on feedback
- Group-aware context analysis
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from clarissa_core.intent import IntentResult


class RejectionCode(Enum):
    """Rejection codes for message filtering."""

    # Should respond
    SUCCESS = "success"

    # Soft rejections (might still respond in some contexts)
    LOW_RELEVANCE = "low_relevance"  # Not clearly directed at bot
    AMBIENT_CHAT = "ambient_chat"    # General conversation, not a question

    # Hard rejections (should not respond)
    TOO_SHORT = "too_short"          # Message too brief to be meaningful
    NOT_A_QUESTION = "not_a_question"  # Statement, not seeking response
    UNRELATED = "unrelated"          # Outside bot's domain
    RATE_LIMITED = "rate_limited"    # Too many responses recently
    QUIET_HOURS = "quiet_hours"      # During configured quiet period
    ANOTHER_USER_RESPONDING = "another_user_responding"  # Someone else is handling it


@dataclass
class RejectionResult:
    """Result of rejection analysis."""

    should_respond: bool
    code: RejectionCode
    confidence: float  # 0.0-1.0
    reason: str

    # For debugging/analytics
    scores: dict[str, float] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"RejectionResult(respond={self.should_respond}, code={self.code.value}, conf={self.confidence:.2f})"


# Default thresholds
DEFAULT_REJECT_THROTTLE = float(os.getenv("REJECT_THROTTLE", "0.35"))
MIN_MESSAGE_LENGTH = int(os.getenv("MIN_MESSAGE_LENGTH", "3"))


class RejectionClassifier:
    """Classifies whether to respond to a message in group contexts.

    Uses multiple signals:
    1. Direct mention detection
    2. Question/statement classification
    3. Relevance scoring
    4. Rate limiting
    5. Participant context
    """

    # Patterns that indicate direct address
    DIRECT_PATTERNS = [
        r"^clarissa[,:]?\s",
        r"^clara[,:]?\s",
        r"^hey clarissa",
        r"^@clarissa",
        r"clarissa[,]?\s+(can|could|would|will|do|does|is|are|what|how|why|when|where)\b",
    ]

    # Patterns that indicate questions
    QUESTION_PATTERNS = [
        r"\?\s*$",  # Ends with question mark
        r"^(what|how|why|when|where|who|which|can|could|would|should|is|are|do|does|did)\b",
        r"^(tell me|explain|help|show|find|search|look up)\b",
    ]

    # Patterns that indicate statements/non-questions
    STATEMENT_PATTERNS = [
        r"^(i think|i believe|i feel|imo|imho|tbh)\b",
        r"^(yeah|yes|no|nope|okay|ok|sure|right|exactly|agreed)\b",
        r"^(lol|lmao|haha|nice|cool|wow|damn)\b",
    ]

    def __init__(self, throttle: float = DEFAULT_REJECT_THROTTLE):
        """Initialize the classifier.

        Args:
            throttle: Base rejection threshold (0.0-1.0)
                     Lower = more responsive, Higher = more selective
        """
        self._throttle = throttle
        self._compiled_direct = [re.compile(p, re.IGNORECASE) for p in self.DIRECT_PATTERNS]
        self._compiled_question = [re.compile(p, re.IGNORECASE) for p in self.QUESTION_PATTERNS]
        self._compiled_statement = [re.compile(p, re.IGNORECASE) for p in self.STATEMENT_PATTERNS]

    @property
    def throttle(self) -> float:
        return self._throttle

    @throttle.setter
    def throttle(self, value: float) -> None:
        self._throttle = max(0.0, min(1.0, value))

    def classify(
        self,
        message: str,
        context: dict[str, Any] | None = None,
        intent: IntentResult | None = None,
    ) -> RejectionResult:
        """Classify whether to respond to a message.

        Args:
            message: The user's message text
            context: Optional context with keys:
                - is_dm: Is this a direct message?
                - is_mentioned: Was the bot @mentioned?
                - is_reply: Is this a reply to the bot?
                - channel_throttle: Per-channel throttle override
                - recent_bot_messages: Count of recent bot messages
                - participants: List of active participants
                - bot_name: The bot's display name
            intent: Optional IntentResult from intent detection

        Returns:
            RejectionResult with should_respond and reasoning
        """
        context = context or {}
        scores: dict[str, float] = {}

        # DMs always get a response
        if context.get("is_dm", False):
            return RejectionResult(
                should_respond=True,
                code=RejectionCode.SUCCESS,
                confidence=1.0,
                reason="Direct message",
                scores={"is_dm": 1.0},
            )

        # Explicit mentions always get a response
        if context.get("is_mentioned", False) or context.get("is_reply", False):
            return RejectionResult(
                should_respond=True,
                code=RejectionCode.SUCCESS,
                confidence=1.0,
                reason="Explicitly mentioned or replied to",
                scores={"is_mentioned": 1.0},
            )

        # Too short messages
        if len(message.strip()) < MIN_MESSAGE_LENGTH:
            return RejectionResult(
                should_respond=False,
                code=RejectionCode.TOO_SHORT,
                confidence=0.9,
                reason=f"Message too short ({len(message)} chars)",
                scores={"length": 0.0},
            )

        # Calculate individual scores
        scores["direct_address"] = self._score_direct_address(message, context)
        scores["is_question"] = self._score_question(message)
        scores["is_statement"] = self._score_statement(message)
        scores["relevance"] = self._score_relevance(message, intent)

        # Intent-based adjustments
        if intent:
            scores["intent_respond"] = 1.0 if intent.should_respond else 0.0
            scores["intent_confidence"] = intent.confidence

        # Calculate aggregate score
        # Direct address and questions boost response likelihood
        # Statements reduce it
        aggregate = (
            scores["direct_address"] * 0.4 +
            scores["is_question"] * 0.3 +
            scores["relevance"] * 0.2 +
            (1.0 - scores["is_statement"]) * 0.1
        )

        # Apply throttle
        channel_throttle = context.get("channel_throttle", self._throttle)
        should_respond = aggregate >= channel_throttle

        # Determine rejection code
        if should_respond:
            code = RejectionCode.SUCCESS
            reason = "Score above threshold"
        elif scores["is_statement"] > 0.7:
            code = RejectionCode.NOT_A_QUESTION
            reason = "Appears to be a statement, not a question"
        elif scores["direct_address"] < 0.2 and scores["is_question"] < 0.3:
            code = RejectionCode.AMBIENT_CHAT
            reason = "Ambient conversation not directed at bot"
        elif scores["relevance"] < 0.3:
            code = RejectionCode.UNRELATED
            reason = "Topic appears unrelated to bot's domain"
        else:
            code = RejectionCode.LOW_RELEVANCE
            reason = f"Below threshold ({aggregate:.2f} < {channel_throttle:.2f})"

        return RejectionResult(
            should_respond=should_respond,
            code=code,
            confidence=aggregate,
            reason=reason,
            scores=scores,
        )

    def _score_direct_address(self, message: str, context: dict) -> float:
        """Score how directly the message addresses the bot."""
        message_lower = message.lower()

        # Check compiled patterns
        for pattern in self._compiled_direct:
            if pattern.search(message_lower):
                return 1.0

        # Check for bot name in message
        bot_name = context.get("bot_name", "clarissa").lower()
        if bot_name in message_lower:
            return 0.8

        # Check for "you" at start (might be addressing bot in conversation)
        if message_lower.startswith(("you ", "your ")):
            return 0.4

        return 0.0

    def _score_question(self, message: str) -> float:
        """Score likelihood this is a question seeking response."""
        score = 0.0

        for pattern in self._compiled_question:
            if pattern.search(message):
                score += 0.4

        # Question marks are strong signal
        if "?" in message:
            score += 0.5

        return min(1.0, score)

    def _score_statement(self, message: str) -> float:
        """Score likelihood this is a statement not seeking response."""
        message_lower = message.lower()

        for pattern in self._compiled_statement:
            if pattern.search(message_lower):
                return 0.8

        # Short messages without questions are likely statements
        if len(message) < 20 and "?" not in message:
            return 0.5

        return 0.0

    def _score_relevance(self, message: str, intent: IntentResult | None) -> float:
        """Score relevance to bot's domain."""
        if intent:
            # Use intent's requires_tools as proxy for actionability
            if intent.requires_tools:
                return 0.9
            # Complex topics are more likely bot-relevant
            if intent.complexity == "complex":
                return 0.7
            if intent.complexity == "moderate":
                return 0.5

        # Default moderate relevance
        return 0.4

    def adjust_throttle(self, delta: float) -> None:
        """Adjust throttle based on feedback.

        Positive delta = more selective (fewer responses)
        Negative delta = more responsive
        """
        self._throttle = max(0.1, min(0.6, self._throttle + delta))

    def report_badcase(self) -> None:
        """Report that a response was inappropriate (feedback signal).

        Increases throttle to be more selective.
        """
        self.adjust_throttle(0.02)

    def report_missed(self) -> None:
        """Report that the bot should have responded but didn't.

        Decreases throttle to be more responsive.
        """
        self.adjust_throttle(-0.02)


# Singleton instance
_classifier: RejectionClassifier | None = None


def get_rejection_classifier(throttle: float | None = None) -> RejectionClassifier:
    """Get or create the singleton RejectionClassifier."""
    global _classifier
    if _classifier is None:
        _classifier = RejectionClassifier(throttle or DEFAULT_REJECT_THROTTLE)
    return _classifier


def should_respond(
    message: str,
    context: dict[str, Any] | None = None,
    intent: IntentResult | None = None,
) -> RejectionResult:
    """Convenience function to check if bot should respond."""
    return get_rejection_classifier().classify(message, context, intent)
