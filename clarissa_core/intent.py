"""Intent detection for KIRA-inspired multi-agent pipeline.

Uses a lightweight LLM call (low tier) to quickly classify message intent
and complexity, enabling intelligent tier selection and routing.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

# Intent types
IntentType = Literal["chat", "task", "query", "command", "creative"]

# Complexity levels
ComplexityLevel = Literal["simple", "moderate", "complex"]


@dataclass
class IntentResult:
    """Result of intent detection analysis."""

    should_respond: bool = True
    intent_type: IntentType = "chat"
    complexity: ComplexityLevel = "moderate"
    requires_tools: bool = False
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.8
    raw_analysis: str = ""  # For debugging

    def __repr__(self) -> str:
        return (
            f"IntentResult(type={self.intent_type}, complexity={self.complexity}, "
            f"tools={self.requires_tools}, confidence={self.confidence:.2f})"
        )


# Keywords that indicate tool usage is likely needed
TOOL_KEYWORDS = {
    # Code execution
    "run",
    "execute",
    "calculate",
    "compute",
    "script",
    "code",
    "python",
    "install",
    # File operations
    "file",
    "save",
    "download",
    "upload",
    "read file",
    "write file",
    "create file",
    # Web/search
    "search",
    "google",
    "look up",
    "find online",
    "fetch",
    "web",
    # GitHub
    "github",
    "repo",
    "repository",
    "pull request",
    "pr",
    "issue",
    "commit",
    "branch",
    "workflow",
    "action",
    "gist",
    # Azure DevOps
    "ado",
    "azure devops",
    "work item",
    "pipeline",
    "build",
    "wiki",
    # Email
    "email",
    "send email",
    "check email",
    # Git operations
    "git",
    "clone",
    "push",
    "pull",
    "merge",
}

# Keywords indicating complex reasoning
COMPLEX_KEYWORDS = {
    # Analysis
    "analyze",
    "analyse",
    "explain why",
    "compare",
    "contrast",
    "evaluate",
    "assess",
    "review",
    "critique",
    # Planning
    "plan",
    "design",
    "architect",
    "strategy",
    "roadmap",
    # Code review
    "code review",
    "review this code",
    "review my code",
    "refactor",
    "optimize",
    "debug",
    # Creative
    "write a story",
    "write a poem",
    "creative writing",
    "brainstorm",
    # Deep reasoning
    "step by step",
    "think through",
    "reasoning",
    "implications",
    "trade-offs",
    "tradeoffs",
    "pros and cons",
}

# Keywords indicating simple interactions
SIMPLE_KEYWORDS = {
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank you",
    "bye",
    "goodbye",
    "ok",
    "okay",
    "yes",
    "no",
    "sure",
    "got it",
    "understood",
    "cool",
    "nice",
    "great",
    "awesome",
    "lol",
    "haha",
}


class IntentDetector:
    """Detects message intent and complexity for pipeline routing.

    Uses a combination of:
    1. Fast rule-based heuristics (no LLM call)
    2. Optional LLM-based analysis for ambiguous cases (low tier)
    """

    def __init__(self, llm_callable: Callable[[list[dict]], str] | None = None):
        """Initialize the intent detector.

        Args:
            llm_callable: Optional LLM function for ambiguous cases.
                         If None, only uses rule-based detection.
        """
        self._llm = llm_callable
        self._use_llm = os.getenv("INTENT_USE_LLM", "false").lower() == "true"

    def detect(self, message: str, context: dict | None = None) -> IntentResult:
        """Detect intent from a message.

        Args:
            message: The user's message text
            context: Optional context dict with keys like:
                     - messages: list of previous messages
                     - is_dm: whether this is a DM
                     - has_attachments: whether message has attachments

        Returns:
            IntentResult with classification details
        """
        context = context or {}
        message_lower = message.lower().strip()

        # Initialize result
        result = IntentResult()

        # Detect entities (URLs, code blocks, mentions)
        result.entities = self._extract_entities(message)

        # Rule-based detection
        result = self._rule_based_detect(message_lower, result, context)

        # If ambiguous and LLM is available, use it for better classification
        if self._use_llm and self._llm and result.confidence < 0.7:
            result = self._llm_detect(message, result, context)

        return result

    def _extract_entities(self, message: str) -> list[str]:
        """Extract notable entities from the message."""
        entities = []

        # URLs
        url_pattern = r"https?://[^\s<>\"{}|\\^`\[\]]+"
        urls = re.findall(url_pattern, message)
        entities.extend([f"url:{u[:50]}" for u in urls[:3]])

        # Code blocks
        if "```" in message:
            entities.append("code_block")

        # File extensions
        file_pattern = r"\b\w+\.(py|js|ts|json|yaml|yml|md|txt|html|css|sql)\b"
        files = re.findall(file_pattern, message.lower())
        entities.extend([f"file:.{ext}" for ext in set(files)])

        # GitHub references
        if re.search(r"github\.com/[\w-]+/[\w-]+", message):
            entities.append("github_repo")
        if re.search(r"#\d+", message):
            entities.append("issue_ref")

        return entities

    def _rule_based_detect(
        self, message_lower: str, result: IntentResult, context: dict
    ) -> IntentResult:
        """Apply rule-based heuristics for intent detection."""
        # Check for simple greetings/acknowledgments
        words = set(message_lower.split())
        if words.intersection(SIMPLE_KEYWORDS) and len(words) <= 5:
            result.intent_type = "chat"
            result.complexity = "simple"
            result.requires_tools = False
            result.confidence = 0.95
            return result

        # Check for tool-related keywords
        tool_matches = sum(1 for kw in TOOL_KEYWORDS if kw in message_lower)
        if tool_matches > 0:
            result.requires_tools = True
            result.intent_type = "task"
            result.confidence = min(0.6 + tool_matches * 0.1, 0.9)

        # Check for complexity indicators
        complex_matches = sum(1 for kw in COMPLEX_KEYWORDS if kw in message_lower)
        if complex_matches > 0:
            result.complexity = "complex"
            result.confidence = min(0.6 + complex_matches * 0.15, 0.9)

        # Questions are often queries
        if message_lower.endswith("?"):
            if not result.requires_tools:
                result.intent_type = "query"

        # Commands start with action words
        command_starters = ["show", "list", "get", "set", "add", "remove", "delete"]
        first_word = message_lower.split()[0] if message_lower else ""
        if first_word in command_starters:
            result.intent_type = "command"

        # Creative tasks
        creative_indicators = ["write", "create", "generate", "compose", "draft"]
        if any(ind in message_lower for ind in creative_indicators):
            if any(
                word in message_lower
                for word in ["story", "poem", "essay", "article", "blog"]
            ):
                result.intent_type = "creative"
                result.complexity = "complex"

        # Long messages are likely more complex
        if len(message_lower) > 500:
            if result.complexity == "simple":
                result.complexity = "moderate"

        # Code blocks indicate task complexity
        if "code_block" in result.entities:
            result.complexity = "moderate" if result.complexity == "simple" else result.complexity
            result.requires_tools = True

        # Attachments often mean task work
        if context.get("has_attachments"):
            result.requires_tools = True
            if result.complexity == "simple":
                result.complexity = "moderate"

        # Long conversation context suggests complexity
        messages = context.get("messages", [])
        if len(messages) > 10:
            if result.complexity == "simple":
                result.complexity = "moderate"

        return result

    def _llm_detect(
        self, message: str, result: IntentResult, context: dict
    ) -> IntentResult:
        """Use LLM for more accurate intent detection.

        Only called when rule-based confidence is low.
        """
        if not self._llm:
            return result

        prompt = f"""Analyze this user message and classify it. Respond with ONLY a JSON object.

Message: "{message[:500]}"

Classify as:
- intent_type: "chat" (casual), "task" (do something), "query" (question), "command" (direct order), "creative" (writing/art)
- complexity: "simple" (quick response), "moderate" (some thought), "complex" (deep analysis/multi-step)
- requires_tools: true/false (needs code execution, file ops, web search, GitHub, etc.)

JSON response:"""

        try:
            response = self._llm([{"role": "user", "content": prompt}])
            # Parse JSON from response
            import json

            # Try to extract JSON from response
            json_match = re.search(r"\{[^}]+\}", response)
            if json_match:
                data = json.loads(json_match.group())
                result.intent_type = data.get("intent_type", result.intent_type)
                result.complexity = data.get("complexity", result.complexity)
                result.requires_tools = data.get("requires_tools", result.requires_tools)
                result.confidence = 0.85
                result.raw_analysis = response
        except Exception:
            # Fallback to rule-based result
            pass

        return result


# Singleton instance
_detector: IntentDetector | None = None


def get_intent_detector(llm_callable: Callable[[list[dict]], str] | None = None) -> IntentDetector:
    """Get or create the singleton IntentDetector instance."""
    global _detector
    if _detector is None:
        _detector = IntentDetector(llm_callable)
    return _detector


def detect_intent(message: str, context: dict | None = None) -> IntentResult:
    """Convenience function to detect intent using the singleton detector."""
    return get_intent_detector().detect(message, context)
