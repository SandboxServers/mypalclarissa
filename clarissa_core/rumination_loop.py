"""Rumination loop for Clarissa's message processing.

This module provides the main entry point for processing messages
with the rumination architecture when ENABLE_RUMINATION=true.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging

from clarissa_core.config import get_config
from clarissa_core.faculty import FacultyResult
from clarissa_core.nodes.command import command_node
from clarissa_core.nodes.ruminate import ruminate
from clarissa_core.rumination import RuminationContext

logger = logging.getLogger(__name__)


async def process_with_rumination(
    message: str,
    user_id: str,
    channel_id: str | None = None,
    user_memories: list[str] | None = None,
    project_memories: list[str] | None = None,
    conversation_history: list[dict] | None = None,
) -> str | None:
    """Process a message using the rumination loop.

    The rumination loop is Clarissa's conscious decision-making process:
    1. Ruminate: Decide what to do (speak, command, or wait)
    2. If command: Execute the faculty and loop back with the result
    3. If speak: Return the response
    4. If wait: Return None

    Args:
        message: The user's message
        user_id: User identifier for context
        channel_id: Optional channel identifier (Discord)
        user_memories: Optional list of user memories
        project_memories: Optional list of project memories
        conversation_history: Optional conversation history

    Returns:
        The response string, or None if no response needed
    """
    config = get_config()
    max_iterations = config.rumination_max_iterations

    logger.info(f"[rumination_loop] Processing message: {message[:50]}...")

    # Build initial context
    ctx = RuminationContext(
        message=message,
        user_id=user_id,
        channel_id=channel_id,
        user_memories=user_memories or [],
        project_memories=project_memories or [],
        conversation_history=conversation_history or [],
        iteration=0,
    )

    faculty_result: FacultyResult | None = None

    for iteration in range(max_iterations):
        ctx.iteration = iteration
        logger.info(f"[rumination_loop] Iteration {iteration + 1}/{max_iterations}")

        # Ruminate: decide what to do
        rumination_result = await ruminate(ctx, faculty_result)

        if rumination_result.decision == "speak":
            logger.info("[rumination_loop] Decision: SPEAK")
            return rumination_result.response_draft

        elif rumination_result.decision == "command":
            logger.info(
                f"[rumination_loop] Decision: COMMAND ({rumination_result.faculty})"
            )
            # Execute faculty and loop back
            faculty_result = await command_node(
                rumination=rumination_result,
                user_id=user_id,
                channel_id=channel_id,
            )

            # Add faculty result to context for next iteration
            ctx.previous_faculty_result = faculty_result.summary

            # If the faculty result is a failure, we might want to speak about it
            if not faculty_result.success:
                logger.warning(
                    f"[rumination_loop] Faculty failed: {faculty_result.error}"
                )
                # Continue loop - rumination will decide how to respond to failure

        elif rumination_result.decision == "wait":
            logger.info(
                f"[rumination_loop] Decision: WAIT ({rumination_result.wait_reason})"
            )
            return None

        else:
            logger.warning(
                f"[rumination_loop] Unknown decision: {rumination_result.decision}"
            )
            return "I'm having trouble processing that. Could you try rephrasing?"

    # Safety: max iterations reached
    logger.warning(f"[rumination_loop] Max iterations ({max_iterations}) reached")
    return (
        "I've been thinking about this for a while but need to pause. "
        "Could you clarify what you'd like me to do?"
    )


def is_rumination_enabled() -> bool:
    """Check if rumination mode is enabled."""
    return get_config().enable_rumination
