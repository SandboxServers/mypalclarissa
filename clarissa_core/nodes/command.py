"""Command node for executing faculty actions.

The command node takes a RuminationResult with decision="command"
and executes the specified faculty with the given intent.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging

from clarissa_core.faculties import get_faculty
from clarissa_core.faculty import FacultyResult
from clarissa_core.rumination import RuminationResult

logger = logging.getLogger(__name__)


async def command_node(
    rumination: RuminationResult,
    user_id: str | None = None,
    channel_id: str | None = None,
) -> FacultyResult:
    """Execute Clarissa's decision through a faculty.

    Args:
        rumination: The rumination result with decision="command"
        user_id: User context for per-user state
        channel_id: Channel context (Discord)

    Returns:
        FacultyResult with success status, summary, and optional data
    """
    if rumination.decision != "command":
        logger.warning(
            f"[command] Called with non-command decision: {rumination.decision}"
        )
        return FacultyResult(
            success=False,
            summary="Command node called without command decision",
            error="Invalid decision type",
        )

    faculty_name = rumination.faculty
    intent = rumination.intent
    constraints = rumination.constraints

    if not faculty_name:
        logger.error("[command] No faculty specified in rumination result")
        return FacultyResult(
            success=False,
            summary="No capability specified for the command",
            error="Missing faculty name",
        )

    if not intent:
        logger.error("[command] No intent specified in rumination result")
        return FacultyResult(
            success=False,
            summary="No intent specified for the command",
            error="Missing intent",
        )

    logger.info(f"[command] Faculty: {faculty_name}, Intent: {intent}")
    logger.info(f"[command] Constraints: {constraints}")

    # Get faculty from registry
    faculty = get_faculty(faculty_name)
    if not faculty:
        logger.error(f"[command] Unknown faculty: {faculty_name}")
        return FacultyResult(
            success=False,
            summary=f"Unknown capability: {faculty_name}. "
            "Available: github, ado, code, planning, decisions",
            error=f"Faculty '{faculty_name}' not found",
        )

    # Execute with intent
    try:
        result = await faculty.execute(
            intent=intent,
            constraints=constraints,
            user_id=user_id,
            channel_id=channel_id,
        )
        logger.info(f"[command] Result: {result.success} - {result.summary[:100]}...")
        return result

    except Exception as e:
        logger.exception(f"[command] Error executing {faculty_name}: {e}")
        return FacultyResult(
            success=False,
            summary=f"Error executing {faculty_name}: {str(e)}",
            error=str(e),
        )
