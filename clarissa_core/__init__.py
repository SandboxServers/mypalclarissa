"""Clarissa Core - Shared infrastructure for the Clarissa platform.

This package provides the common components used by all Clarissa platform services:
- API server
- Discord bot
- Email monitor
- Future platforms (Slack, Telegram, etc.)

Usage:
    from clarissa_core import init_platform, MemoryManager, ToolRegistry

    # Initialize shared infrastructure (call once at startup)
    init_platform()

    # Access singletons
    mm = MemoryManager.get_instance()
    tools = ToolRegistry.get_instance()
"""

from pathlib import Path

# Read version from VERSION file
_VERSION_FILE = Path(__file__).parent.parent / "VERSION"
__version__ = _VERSION_FILE.read_text().strip() if _VERSION_FILE.exists() else "0.0.0"


def get_version() -> str:
    """Get the current Clarissa platform version."""
    return __version__


from clarissa_core.config import get_config, init_platform

# Emotional context and sentiment tracking
from clarissa_core.sentiment import (
    analyze_sentiment,
    classify_sentiment,
    get_sentiment_summary,
)
from clarissa_core.emotional_context import (
    EmotionalSummary,
    track_message_sentiment,
    get_conversation_sentiments,
    clear_conversation_sentiments,
    compute_emotional_arc,
    finalize_conversation_emotional_context,
    has_pending_emotional_context,
)

from clarissa_core.llm import (
    ModelTier,
    make_llm,
    make_llm_streaming,
    make_llm_with_tools,
    get_model_for_tier,
    get_current_tier,
    get_tier_info,
    DEFAULT_TIER,
)
from clarissa_core.memory import MemoryManager, load_initial_profile
from clarissa_core.platform import PlatformAdapter, PlatformContext, PlatformMessage
from clarissa_core.tools import ToolRegistry

# KIRA-inspired pipeline components
from clarissa_core.intent import (
    IntentDetector,
    IntentResult,
    detect_intent,
    get_intent_detector,
)
from clarissa_core.tier_selector import (
    TierSelector,
    get_tier_selector,
    select_tier,
    get_tier_display,
    TIER_DISPLAY,
)
from clarissa_core.pipeline import (
    MessagePipeline,
    PipelineContext,
    PipelineResult,
    get_pipeline,
    configure_pipeline,
)

# Multi-user / group chat components
from clarissa_core.rejection import (
    RejectionClassifier,
    RejectionCode,
    RejectionResult,
    get_rejection_classifier,
    should_respond,
)
from clarissa_core.group_session import (
    GroupSession,
    Participant,
    get_group_session,
    cleanup_stale_sessions,
)

# MCP Plugin System (standalone, separate from native tools)
from clarissa_core.mcp import (
    MCPServerManager,
    MCPInstaller,
    MCPClient,
    MCPTool,
    MCPServerConfig,
    InstallResult,
    init_mcp,
    get_mcp_manager,
    load_server_config,
    save_server_config,
)

__all__ = [
    # Version
    "__version__",
    "get_version",
    # Initialization
    "init_platform",
    "get_config",
    # Core classes
    "MemoryManager",
    "ToolRegistry",
    # Platform abstractions
    "PlatformAdapter",
    "PlatformContext",
    "PlatformMessage",
    # Sentiment analysis
    "analyze_sentiment",
    "classify_sentiment",
    "get_sentiment_summary",
    # Emotional context tracking
    "EmotionalSummary",
    "track_message_sentiment",
    "get_conversation_sentiments",
    "clear_conversation_sentiments",
    "compute_emotional_arc",
    "finalize_conversation_emotional_context",
    "has_pending_emotional_context",
    # LLM functions
    "make_llm",
    "make_llm_streaming",
    "make_llm_with_tools",
    # Model tiers
    "ModelTier",
    "get_model_for_tier",
    "get_current_tier",
    "get_tier_info",
    "DEFAULT_TIER",
    # Profile loading
    "load_initial_profile",
    # KIRA-inspired pipeline
    "IntentDetector",
    "IntentResult",
    "detect_intent",
    "get_intent_detector",
    "TierSelector",
    "get_tier_selector",
    "select_tier",
    "get_tier_display",
    "TIER_DISPLAY",
    "MessagePipeline",
    "PipelineContext",
    "PipelineResult",
    "get_pipeline",
    "configure_pipeline",
    # Multi-user / group chat
    "RejectionClassifier",
    "RejectionCode",
    "RejectionResult",
    "get_rejection_classifier",
    "should_respond",
    "GroupSession",
    "Participant",
    "get_group_session",
    "cleanup_stale_sessions",
    # MCP Plugin System
    "MCPServerManager",
    "MCPInstaller",
    "MCPClient",
    "MCPTool",
    "MCPServerConfig",
    "InstallResult",
    "init_mcp",
    "get_mcp_manager",
    "load_server_config",
    "save_server_config",
]
