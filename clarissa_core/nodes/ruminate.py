"""Ruminate node for Clarissa's conscious decision-making.

The rumination process is Clarissa's way of deciding what to do:
- SPEAK: Respond directly with knowledge + memory
- COMMAND: Use a faculty to take action
- WAIT: Do nothing (no response needed)

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import json
import logging
import re

from clarissa_core.faculties import get_faculty_descriptions
from clarissa_core.faculty import FacultyResult
from clarissa_core.rumination import RuminationContext, RuminationResult

logger = logging.getLogger(__name__)

# Rumination prompt template
RUMINATION_PROMPT = """You are Clarissa, a business-focused AI assistant.

## Your Available Capabilities (Faculties)
{faculty_descriptions}

## Memory Context
### User Memories
{user_memories}

### Project Memories
{project_memories}

## Conversation History
{conversation_history}

## Previous Action Result
{previous_result}

## Current Message
{message}

## Your Decision

You must decide one of:
1. **SPEAK**: You can respond directly using your knowledge and memory
   - Use when you can answer without needing to call external systems
   - Use when providing explanations, advice, or discussion

2. **COMMAND**: You need to use a faculty/capability to take action
   - Use when you need to interact with GitHub, Azure DevOps, execute code, etc.
   - Specify which faculty and what intent to accomplish

3. **WAIT**: Nothing to do right now
   - Use when the message doesn't require a response from you
   - Use for acknowledgments, reactions, or messages meant for others

Respond in this exact JSON format:
```json
{{
  "decision": "speak|command|wait",
  "reasoning": "Brief explanation of your decision",
  "response": "Your response if speaking (null otherwise)",
  "faculty": "faculty_name if commanding (null otherwise)",
  "intent": "What to accomplish if commanding (null otherwise)",
  "constraints": ["Optional list of constraints for the command"],
  "wait_reason": "Why no response if waiting (null otherwise)"
}}
```
"""


def _format_memories(memories: list[str], limit: int = 10) -> str:
    """Format memories for the prompt."""
    if not memories:
        return "No memories available."
    limited = memories[:limit]
    return "\n".join(f"- {m}" for m in limited)


def _format_history(history: list[dict], limit: int = 10) -> str:
    """Format conversation history for the prompt."""
    if not history:
        return "No conversation history."
    limited = history[-limit:]
    lines = []
    for msg in limited:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")[:200]  # Truncate long messages
        lines.append(f"**{role}**: {content}")
    return "\n".join(lines)


def _format_faculty_result(result: FacultyResult | None) -> str:
    """Format previous faculty result for the prompt."""
    if not result:
        return "None"
    status = "SUCCESS" if result.success else "FAILED"
    return f"[{status}] {result.summary}"


def _build_rumination_prompt(
    ctx: RuminationContext, faculty_result: FacultyResult | None = None
) -> str:
    """Build the rumination prompt from context."""
    faculty_descs = get_faculty_descriptions()
    faculty_list = (
        "\n".join(faculty_descs) if faculty_descs else "No faculties available."
    )

    return RUMINATION_PROMPT.format(
        faculty_descriptions=faculty_list,
        user_memories=_format_memories(ctx.user_memories),
        project_memories=_format_memories(ctx.project_memories),
        conversation_history=_format_history(ctx.conversation_history),
        previous_result=_format_faculty_result(faculty_result),
        message=ctx.message,
    )


def _parse_rumination_response(response: str) -> RuminationResult:
    """Parse LLM response into RuminationResult."""
    # Try to extract JSON from the response
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
    else:
        # Try to find raw JSON
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
        else:
            # Fallback: treat as speak response
            logger.warning("[ruminate] Could not parse JSON, treating as speak")
            return RuminationResult(
                decision="speak",
                reasoning="Could not parse structured response",
                response_draft=response,
            )

    try:
        data = json.loads(json_str)
        decision = data.get("decision", "speak")

        if decision not in ("speak", "command", "wait"):
            decision = "speak"

        return RuminationResult(
            decision=decision,
            reasoning=data.get("reasoning", ""),
            response_draft=data.get("response"),
            faculty=data.get("faculty"),
            intent=data.get("intent"),
            constraints=data.get("constraints", []),
            wait_reason=data.get("wait_reason"),
        )
    except json.JSONDecodeError as e:
        logger.warning(f"[ruminate] JSON parse error: {e}")
        return RuminationResult(
            decision="speak",
            reasoning=f"JSON parse error: {e}",
            response_draft=response,
        )


async def ruminate(
    ctx: RuminationContext,
    faculty_result: FacultyResult | None = None,
) -> RuminationResult:
    """Clarissa's conscious decision-making.

    Decides: respond directly, use a faculty, or wait.

    Args:
        ctx: The rumination context with message, memories, and history
        faculty_result: Optional result from a previous faculty command

    Returns:
        RuminationResult with the decision and associated data
    """
    logger.info(f"[ruminate] Processing message: {ctx.message[:50]}...")
    logger.info(f"[ruminate] Iteration: {ctx.iteration}")

    # Build the prompt
    prompt = _build_rumination_prompt(ctx, faculty_result)

    # Call LLM
    try:
        from clarissa_core.llm import get_llm_callable

        llm = get_llm_callable()
        if llm is None:
            # Fallback: just speak the message back
            logger.warning("[ruminate] No LLM available, using fallback")
            return RuminationResult(
                decision="speak",
                reasoning="No LLM available",
                response_draft="I'm having trouble processing that. Please try again.",
            )

        # Build messages for the LLM
        messages = [
            {
                "role": "system",
                "content": "You are a decision-making component. Use valid JSON.",
            },
            {"role": "user", "content": prompt},
        ]

        response = await llm(messages)
        logger.debug(f"[ruminate] LLM response: {response[:200]}...")

        # Parse the response
        result = _parse_rumination_response(response)

        # Validate the result
        errors = result.validate()
        if errors:
            logger.warning(f"[ruminate] Validation errors: {errors}")
            # Try to recover
            if result.decision == "command" and not result.faculty:
                # No faculty specified, fall back to speak
                result.decision = "speak"
                result.response_draft = (
                    "I'm not sure which capability to use. Could you clarify?"
                )

        logger.info(f"[ruminate] Decision: {result.decision}")
        return result

    except Exception as e:
        logger.exception(f"[ruminate] Error: {e}")
        return RuminationResult(
            decision="speak",
            reasoning=f"Error during rumination: {e}",
            response_draft="I encountered an error while thinking. Let me try again.",
        )
