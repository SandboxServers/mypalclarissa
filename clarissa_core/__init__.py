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
from clarissa_core.group_session import (
    GroupSession,
    Participant,
    cleanup_stale_sessions,
    get_group_session,
)

# KIRA-inspired pipeline components
from clarissa_core.intent import (
    IntentDetector,
    IntentResult,
    detect_intent,
    get_intent_detector,
)
from clarissa_core.llm import (
    DEFAULT_TIER,
    ModelTier,
    get_current_tier,
    get_model_for_tier,
    get_tier_info,
    make_llm,
    make_llm_streaming,
    make_llm_with_tools,
)
from clarissa_core.memory import MemoryManager, load_initial_profile
from clarissa_core.pipeline import (
    MessagePipeline,
    PipelineContext,
    PipelineResult,
    configure_pipeline,
    get_pipeline,
)
from clarissa_core.platform import PlatformAdapter, PlatformContext, PlatformMessage

# Multi-user / group chat components
from clarissa_core.rejection import (
    RejectionClassifier,
    RejectionCode,
    RejectionResult,
    get_rejection_classifier,
    should_respond,
)

# Role system (v0.8)
from clarissa_core.roles import (
    RoleDefinition,
    RoleManager,
    get_role_manager,
)
from clarissa_core.tier_selector import (
    TIER_DISPLAY,
    TierSelector,
    get_tier_display,
    get_tier_selector,
    select_tier,
)
from clarissa_core.tools import ToolRegistry

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
    # Role system (v0.8)
    "RoleDefinition",
    "RoleManager",
    "get_role_manager",
]
