"""KIRA-inspired multi-stage message processing pipeline.

Processes messages through multiple stages:
1. Intent Detection (low tier) - Classify message type and complexity
2. Tier Selection (rule-based) - Choose optimal model tier
3. Execution (selected tier) - Generate response with tools
4. Memory Extraction (async, mid tier) - Store important facts

This enables cost-efficient processing where simple messages use cheap
models while complex tasks get the full power of high-tier models.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

from clarissa_core.intent import IntentResult, detect_intent, get_intent_detector
from clarissa_core.tier_selector import (
    ModelTier,
    get_tier_display,
    get_tier_selector,
    select_tier,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable

# Pipeline configuration
PIPELINE_ENABLED = os.getenv("PIPELINE_ENABLED", "true").lower() == "true"
PIPELINE_LOG_STAGES = os.getenv("PIPELINE_LOG_STAGES", "true").lower() == "true"


@dataclass
class PipelineContext:
    """Context passed through pipeline stages."""

    # Input
    message: str
    user_id: str
    channel_id: str | None = None
    thread_id: str | None = None

    # Optional context
    is_dm: bool = False
    has_attachments: bool = False
    messages: list[dict] = field(default_factory=list)  # Previous messages
    participants: list[dict] = field(default_factory=list)  # Multi-user context

    # Pipeline state
    manual_tier: ModelTier | None = None  # Explicit tier override
    intent: IntentResult | None = None
    selected_tier: ModelTier = "mid"

    # Timing
    started_at: datetime = field(default_factory=datetime.utcnow)
    stage_times: dict[str, float] = field(default_factory=dict)

    # Extra data (platform-specific)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Result from pipeline processing."""

    response: str
    tier_used: ModelTier
    intent: IntentResult | None
    tools_used: list[str] = field(default_factory=list)
    files_to_send: list[Any] = field(default_factory=list)
    stage_times: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    @property
    def success(self) -> bool:
        """Check if pipeline completed successfully."""
        return self.error is None and bool(self.response)


class MessagePipeline:
    """KIRA-inspired multi-stage message processing pipeline.

    Usage:
        pipeline = MessagePipeline()

        # Process with automatic tier selection
        result = await pipeline.process(
            message="Help me review this code",
            user_id="discord-123",
            context={"messages": [...], "has_attachments": True}
        )

        # Access results
        print(f"Tier used: {result.tier_used}")
        print(f"Response: {result.response}")
    """

    def __init__(
        self,
        response_generator: Callable[[PipelineContext], Awaitable[tuple[str, list]]] | None = None,
        memory_extractor: Callable[[PipelineContext, str], Awaitable[None]] | None = None,
    ):
        """Initialize the pipeline.

        Args:
            response_generator: Async function to generate response.
                               Signature: (context) -> (response_text, files)
            memory_extractor: Async function to extract memories.
                             Signature: (context, response) -> None
        """
        self._response_generator = response_generator
        self._memory_extractor = memory_extractor
        self._enabled = PIPELINE_ENABLED
        self._log_stages = PIPELINE_LOG_STAGES

    @property
    def enabled(self) -> bool:
        """Check if pipeline is enabled."""
        return self._enabled

    async def process(
        self,
        message: str,
        user_id: str,
        context: dict | None = None,
        manual_tier: ModelTier | None = None,
    ) -> PipelineResult:
        """Process a message through the pipeline.

        Args:
            message: The user's message text
            user_id: User identifier for memory/context
            context: Optional context dict with:
                     - channel_id, thread_id
                     - is_dm, has_attachments
                     - messages (previous messages)
                     - participants (multi-user context)
                     - extra (platform-specific data)
            manual_tier: Explicit tier override

        Returns:
            PipelineResult with response and metadata
        """
        context = context or {}

        # Build pipeline context
        ctx = PipelineContext(
            message=message,
            user_id=user_id,
            channel_id=context.get("channel_id"),
            thread_id=context.get("thread_id"),
            is_dm=context.get("is_dm", False),
            has_attachments=context.get("has_attachments", False),
            messages=context.get("messages", []),
            participants=context.get("participants", []),
            manual_tier=manual_tier,
            extra=context.get("extra", {}),
        )

        try:
            # Stage 1: Intent Detection
            ctx = await self._stage_detect_intent(ctx)

            # Stage 2: Tier Selection
            ctx = await self._stage_select_tier(ctx)

            # Stage 3: Generate Response
            response, files = await self._stage_generate_response(ctx)

            # Stage 4: Memory Extraction (async, non-blocking)
            if self._memory_extractor:
                asyncio.create_task(
                    self._stage_extract_memories(ctx, response)
                )

            return PipelineResult(
                response=response,
                tier_used=ctx.selected_tier,
                intent=ctx.intent,
                files_to_send=files,
                stage_times=ctx.stage_times,
            )

        except Exception as e:
            return PipelineResult(
                response="",
                tier_used=ctx.selected_tier,
                intent=ctx.intent,
                stage_times=ctx.stage_times,
                error=str(e),
            )

    async def _stage_detect_intent(self, ctx: PipelineContext) -> PipelineContext:
        """Stage 1: Detect message intent and complexity."""
        start = asyncio.get_event_loop().time()

        # Build context for intent detection
        intent_context = {
            "messages": ctx.messages,
            "is_dm": ctx.is_dm,
            "has_attachments": ctx.has_attachments,
        }

        # Detect intent (fast, rule-based primarily)
        ctx.intent = detect_intent(ctx.message, intent_context)

        ctx.stage_times["intent_detection"] = asyncio.get_event_loop().time() - start

        if self._log_stages:
            self._log(f"Intent: {ctx.intent}")

        return ctx

    async def _stage_select_tier(self, ctx: PipelineContext) -> PipelineContext:
        """Stage 2: Select optimal model tier."""
        start = asyncio.get_event_loop().time()

        # Build context for tier selection
        tier_context = {
            "message": ctx.message,
            "messages": ctx.messages,
            "manual_tier": ctx.manual_tier,
        }

        # Select tier
        ctx.selected_tier = select_tier(
            intent=ctx.intent,
            context=tier_context,
            manual_tier=ctx.manual_tier,
        )

        ctx.stage_times["tier_selection"] = asyncio.get_event_loop().time() - start

        if self._log_stages:
            emoji, display = get_tier_display(ctx.selected_tier)
            reason = get_tier_selector().get_tier_reason(
                ctx.selected_tier, ctx.intent, tier_context
            )
            self._log(f"Tier: {emoji} {display} - {reason}")

        return ctx

    async def _stage_generate_response(
        self, ctx: PipelineContext
    ) -> tuple[str, list]:
        """Stage 3: Generate response using selected tier."""
        start = asyncio.get_event_loop().time()

        if not self._response_generator:
            raise ValueError("No response generator configured")

        response, files = await self._response_generator(ctx)

        ctx.stage_times["response_generation"] = asyncio.get_event_loop().time() - start

        if self._log_stages:
            self._log(f"Response: {len(response)} chars, {len(files)} files")

        return response, files

    async def _stage_extract_memories(
        self, ctx: PipelineContext, response: str
    ) -> None:
        """Stage 4: Extract and store memories (async, non-blocking)."""
        if not self._memory_extractor:
            return

        start = asyncio.get_event_loop().time()

        try:
            await self._memory_extractor(ctx, response)
        except Exception as e:
            self._log(f"Memory extraction failed: {e}")

        ctx.stage_times["memory_extraction"] = asyncio.get_event_loop().time() - start

        if self._log_stages:
            self._log(f"Memory extraction completed")

    def _log(self, message: str) -> None:
        """Log pipeline stage info."""
        # Import here to avoid circular imports
        try:
            from config.logging import get_logger
            logger = get_logger("pipeline")
            logger.debug(f"[Pipeline] {message}")
        except ImportError:
            print(f"[Pipeline] {message}")


# Singleton instance
_pipeline: MessagePipeline | None = None


def get_pipeline(
    response_generator: Callable[[PipelineContext], Awaitable[tuple[str, list]]] | None = None,
    memory_extractor: Callable[[PipelineContext, str], Awaitable[None]] | None = None,
) -> MessagePipeline:
    """Get or create the singleton MessagePipeline instance.

    Args:
        response_generator: Required on first call to configure the pipeline
        memory_extractor: Optional memory extraction callback

    Returns:
        The configured MessagePipeline instance
    """
    global _pipeline
    if _pipeline is None:
        _pipeline = MessagePipeline(response_generator, memory_extractor)
    return _pipeline


def configure_pipeline(
    response_generator: Callable[[PipelineContext], Awaitable[tuple[str, list]]],
    memory_extractor: Callable[[PipelineContext, str], Awaitable[None]] | None = None,
) -> MessagePipeline:
    """Configure and return the pipeline singleton.

    This should be called once at application startup.
    """
    global _pipeline
    _pipeline = MessagePipeline(response_generator, memory_extractor)
    return _pipeline
