"""Automatic model tier selection for KIRA-inspired pipeline.

Selects optimal model tier (high/mid/low) based on:
- Intent analysis results
- Message complexity indicators
- Conversation context
- Cost/quality optimization

Manual tier prefixes (!high, !mid, !low) always override automatic selection.
"""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from clarissa_core.intent import IntentResult

# Model tier type
ModelTier = Literal["high", "mid", "low"]

# Environment configuration
AUTO_TIER_ENABLED = os.getenv("AUTO_TIER_ENABLED", "true").lower() == "true"
AUTO_TIER_DEFAULT: ModelTier = os.getenv("AUTO_TIER_DEFAULT", "mid").lower()  # type: ignore
if AUTO_TIER_DEFAULT not in ("high", "mid", "low"):
    AUTO_TIER_DEFAULT = "mid"


class TierSelector:
    """Selects optimal model tier based on task analysis.

    Tier Assignment:
    - LOW (Haiku): Simple chat, acknowledgments, quick facts
    - MID (Sonnet): Most conversations, moderate reasoning, tool use
    - HIGH (Opus): Complex analysis, multi-step reasoning, creative tasks
    """

    # Keywords that strongly suggest HIGH tier
    HIGH_TIER_KEYWORDS = {
        # Analysis/reasoning
        "analyze",
        "analyse",
        "deep dive",
        "comprehensive",
        "thorough",
        "detailed analysis",
        "explain the implications",
        "trade-offs",
        "tradeoffs",
        # Code review
        "code review",
        "review this code",
        "security audit",
        "architecture review",
        # Creative
        "write a story",
        "write a poem",
        "creative writing",
        "brainstorm ideas",
        # Planning
        "create a plan",
        "design a system",
        "architect",
        "strategy",
        # Multi-step
        "step by step",
        "walk me through",
        "break down",
    }

    # Keywords that suggest LOW tier is fine
    LOW_TIER_KEYWORDS = {
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "yes",
        "no",
        "ok",
        "okay",
        "got it",
        "sure",
        "cool",
        "nice",
        "what time",
        "what's the date",
        "how are you",
        "goodbye",
        "bye",
    }

    def __init__(self):
        """Initialize the tier selector."""
        self._enabled = AUTO_TIER_ENABLED
        self._default_tier = AUTO_TIER_DEFAULT

    @property
    def enabled(self) -> bool:
        """Check if auto tier selection is enabled."""
        return self._enabled

    def select(
        self,
        intent: IntentResult | None = None,
        context: dict | None = None,
        manual_tier: ModelTier | None = None,
    ) -> ModelTier:
        """Select the optimal model tier.

        Args:
            intent: Result from intent detection (optional)
            context: Context dict with keys like:
                     - message: the user's message text
                     - messages: list of previous messages
                     - manual_tier: explicit tier override
            manual_tier: Explicit tier override (highest priority)

        Returns:
            Selected tier: "high", "mid", or "low"
        """
        context = context or {}

        # Manual override always wins
        if manual_tier:
            return manual_tier

        # Check context for manual tier
        if context.get("manual_tier"):
            return context["manual_tier"]

        # If auto tier is disabled, use default
        if not self._enabled:
            return self._default_tier

        # Get message for keyword analysis
        message = context.get("message", "").lower()

        # Quick check for simple greetings -> LOW tier
        words = set(message.split())
        if words.intersection(self.LOW_TIER_KEYWORDS) and len(words) <= 5:
            return "low"

        # Check for HIGH tier indicators
        if self._should_use_high_tier(intent, message, context):
            return "high"

        # Check for LOW tier indicators
        if self._should_use_low_tier(intent, message, context):
            return "low"

        # Default to MID tier for balanced performance
        return "mid"

    def _should_use_high_tier(
        self,
        intent: IntentResult | None,
        message: str,
        context: dict,
    ) -> bool:
        """Determine if HIGH tier should be used."""
        indicators = 0

        # Intent-based indicators
        if intent:
            if intent.complexity == "complex":
                indicators += 2
            if intent.intent_type == "creative":
                indicators += 2
            if intent.requires_tools and len(intent.entities) > 3:
                indicators += 1

        # Keyword-based indicators
        for keyword in self.HIGH_TIER_KEYWORDS:
            if keyword in message:
                indicators += 1

        # Context-based indicators
        messages = context.get("messages", [])
        if len(messages) > 15:
            # Long conversation suggests complexity
            indicators += 1

        # Code blocks in message
        if "```" in context.get("message", ""):
            # Code-heavy tasks benefit from higher tier
            indicators += 1

        # Multiple entities (files, URLs, etc.)
        if intent and len(intent.entities) > 5:
            indicators += 1

        # Threshold for HIGH tier
        return indicators >= 2

    def _should_use_low_tier(
        self,
        intent: IntentResult | None,
        message: str,
        context: dict,
    ) -> bool:
        """Determine if LOW tier is sufficient."""
        # Intent-based check
        if intent:
            if intent.complexity != "simple":
                return False
            if intent.requires_tools:
                return False
            if intent.intent_type not in ("chat", "query"):
                return False

        # Short, simple messages
        if len(message) < 50 and "?" not in message:
            # Check for simple keywords
            words = set(message.split())
            if words.intersection(self.LOW_TIER_KEYWORDS):
                return True

        # Very short conversation
        messages = context.get("messages", [])
        if len(messages) <= 2:
            # New conversation with simple message
            if intent and intent.complexity == "simple":
                return True

        return False

    def get_tier_reason(
        self,
        tier: ModelTier,
        intent: IntentResult | None = None,
        context: dict | None = None,
    ) -> str:
        """Get a human-readable reason for the tier selection."""
        context = context or {}

        if context.get("manual_tier"):
            return "Manual override via message prefix"

        if tier == "high":
            reasons = []
            if intent and intent.complexity == "complex":
                reasons.append("complex task")
            if intent and intent.intent_type == "creative":
                reasons.append("creative writing")
            message = context.get("message", "").lower()
            for kw in self.HIGH_TIER_KEYWORDS:
                if kw in message:
                    reasons.append(f"keyword '{kw}'")
                    break
            return f"High tier selected: {', '.join(reasons) or 'complexity indicators'}"

        if tier == "low":
            if intent and intent.complexity == "simple":
                return "Low tier selected: simple interaction"
            return "Low tier selected: basic conversation"

        return "Mid tier selected: balanced default"


# Singleton instance
_selector: TierSelector | None = None


def get_tier_selector() -> TierSelector:
    """Get or create the singleton TierSelector instance."""
    global _selector
    if _selector is None:
        _selector = TierSelector()
    return _selector


def select_tier(
    intent: IntentResult | None = None,
    context: dict | None = None,
    manual_tier: ModelTier | None = None,
) -> ModelTier:
    """Convenience function to select tier using the singleton selector."""
    return get_tier_selector().select(intent, context, manual_tier)


# Tier display info for Discord
TIER_DISPLAY = {
    "high": ("🔴", "High Tier"),
    "mid": ("🟡", "Mid Tier"),
    "low": ("🟢", "Low Tier"),
}


def get_tier_display(tier: ModelTier) -> tuple[str, str]:
    """Get emoji and display name for a tier."""
    return TIER_DISPLAY.get(tier, ("⚙️", tier))
