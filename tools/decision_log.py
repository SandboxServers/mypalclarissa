"""Decision logging tools for business/team context.

This module provides tools for explicitly logging and querying:
- Technical decisions and rationale
- Project blockers and issues
- Team context (member roles, expertise)
- Meeting notes and action items

v0.8 Feature: Business-focused memory management
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tools._base import ToolContext, ToolDef

MODULE_NAME = "decision_log"
MODULE_VERSION = "0.8.0"

SYSTEM_PROMPT = """You have access to decision logging tools for tracking important project decisions, blockers, and team context.

When the user:
- Makes a technical decision, use log_decision to record it
- Mentions a blocker or issue, use log_blocker to track it
- Shares team member information, use log_team_context to remember it
- Asks about past decisions, use query_decisions to recall them

Always include rationale when logging decisions. This helps future reference."""


# Decision categories for filtering
DECISION_CATEGORIES = [
    "architecture",  # System design, component structure
    "technology",  # Tech stack, library choices
    "process",  # Development workflow, CI/CD
    "api",  # API design, endpoints
    "database",  # Schema, data modeling
    "security",  # Auth, access control
    "performance",  # Optimization choices
    "ux",  # User experience decisions
    "other",  # Anything else
]


async def log_decision(args: dict[str, Any], ctx: ToolContext) -> str:
    """Log a technical or project decision with rationale."""
    from config.mem0 import MEM0

    decision = args.get("decision", "").strip()
    rationale = args.get("rationale", "").strip()
    category = args.get("category", "other").lower()
    project_id = args.get("project_id", "default")

    if not decision:
        return "Error: decision is required"

    if category not in DECISION_CATEGORIES:
        category = "other"

    if MEM0 is None:
        return "Error: Memory system not available"

    # Format as structured memory
    timestamp = datetime.now(UTC).isoformat()
    content = f"[DECISION/{category.upper()}] {decision}"
    if rationale:
        content += f"\nRationale: {rationale}"

    # Store with metadata for filtering
    metadata = {
        "type": "decision",
        "category": category,
        "project_id": project_id,
        "logged_at": timestamp,
        "logged_by": ctx.user_id,
    }

    result = MEM0.add(
        [{"role": "user", "content": content}],
        user_id=ctx.user_id,
        metadata=metadata,
    )

    return f"Logged decision ({category}): {decision[:100]}..."


async def log_blocker(args: dict[str, Any], ctx: ToolContext) -> str:
    """Log a project blocker or issue that needs resolution."""
    from config.mem0 import MEM0

    blocker = args.get("blocker", "").strip()
    impact = args.get("impact", "").strip()
    status = args.get("status", "open").lower()
    project_id = args.get("project_id", "default")

    if not blocker:
        return "Error: blocker description is required"

    if status not in ["open", "resolved", "in_progress"]:
        status = "open"

    if MEM0 is None:
        return "Error: Memory system not available"

    timestamp = datetime.now(UTC).isoformat()
    content = f"[BLOCKER/{status.upper()}] {blocker}"
    if impact:
        content += f"\nImpact: {impact}"

    metadata = {
        "type": "blocker",
        "status": status,
        "project_id": project_id,
        "logged_at": timestamp,
        "logged_by": ctx.user_id,
    }

    result = MEM0.add(
        [{"role": "user", "content": content}],
        user_id=ctx.user_id,
        metadata=metadata,
    )

    return f"Logged blocker ({status}): {blocker[:100]}..."


async def log_team_context(args: dict[str, Any], ctx: ToolContext) -> str:
    """Log information about a team member's role or expertise."""
    from config.mem0 import MEM0

    member_name = args.get("member_name", "").strip()
    role = args.get("role", "").strip()
    expertise = args.get("expertise", [])
    notes = args.get("notes", "").strip()

    if not member_name:
        return "Error: member_name is required"

    if MEM0 is None:
        return "Error: Memory system not available"

    # Build content
    parts = [f"[TEAM] {member_name}"]
    if role:
        parts.append(f"Role: {role}")
    if expertise:
        if isinstance(expertise, list):
            parts.append(f"Expertise: {', '.join(expertise)}")
        else:
            parts.append(f"Expertise: {expertise}")
    if notes:
        parts.append(f"Notes: {notes}")

    content = "\n".join(parts)

    metadata = {
        "type": "team_context",
        "member_name": member_name,
        "role": role or "unknown",
        "logged_by": ctx.user_id,
    }

    result = MEM0.add(
        [{"role": "user", "content": content}],
        user_id=ctx.user_id,
        metadata=metadata,
    )

    return f"Logged team context for {member_name}"


async def log_meeting_note(args: dict[str, Any], ctx: ToolContext) -> str:
    """Log key points or action items from a meeting."""
    from config.mem0 import MEM0

    summary = args.get("summary", "").strip()
    action_items = args.get("action_items", [])
    attendees = args.get("attendees", [])
    project_id = args.get("project_id", "default")

    if not summary:
        return "Error: summary is required"

    if MEM0 is None:
        return "Error: Memory system not available"

    timestamp = datetime.now(UTC).isoformat()

    parts = [f"[MEETING] {summary}"]
    if attendees:
        if isinstance(attendees, list):
            parts.append(f"Attendees: {', '.join(attendees)}")
        else:
            parts.append(f"Attendees: {attendees}")
    if action_items:
        if isinstance(action_items, list):
            parts.append("Action items:")
            for item in action_items:
                parts.append(f"  - {item}")
        else:
            parts.append(f"Action items: {action_items}")

    content = "\n".join(parts)

    metadata = {
        "type": "meeting_note",
        "project_id": project_id,
        "logged_at": timestamp,
        "logged_by": ctx.user_id,
        "attendees": attendees
        if isinstance(attendees, list)
        else [attendees]
        if attendees
        else [],
    }

    result = MEM0.add(
        [{"role": "user", "content": content}],
        user_id=ctx.user_id,
        metadata=metadata,
    )

    return f"Logged meeting note: {summary[:100]}..."


async def query_decisions(args: dict[str, Any], ctx: ToolContext) -> str:
    """Query past decisions, blockers, or team context."""
    from config.mem0 import MEM0

    query = args.get("query", "").strip()
    memory_type = args.get(
        "type", "all"
    ).lower()  # decision, blocker, team_context, meeting_note, all
    category = args.get("category")  # For decisions only
    limit = min(args.get("limit", 10), 20)

    if not query:
        return "Error: query is required"

    if MEM0 is None:
        return "Error: Memory system not available"

    # Build filters
    filters = {}
    if memory_type != "all":
        filters["type"] = memory_type
    if category and memory_type == "decision":
        filters["category"] = category

    # Search
    results = MEM0.search(
        query,
        user_id=ctx.user_id,
        filters=filters if filters else None,
        limit=limit,
    )

    memories = results.get("results", [])

    if not memories:
        return f"No {memory_type} memories found matching: {query}"

    # Format results
    output_lines = [f"Found {len(memories)} result(s):"]
    for i, mem in enumerate(memories, 1):
        memory_text = mem.get("memory", "")
        metadata = mem.get("metadata", {})
        mem_type = metadata.get("type", "unknown")
        logged_at = metadata.get("logged_at", "")

        output_lines.append(f"\n{i}. [{mem_type.upper()}]")
        output_lines.append(f"   {memory_text}")
        if logged_at:
            output_lines.append(f"   (logged: {logged_at[:10]})")

    return "\n".join(output_lines)


# Tool definitions
TOOLS = [
    ToolDef(
        name="log_decision",
        description="Log a technical or project decision with rationale. Use for architecture choices, technology selections, process decisions, etc.",
        parameters={
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "description": "The decision that was made",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this decision was made (reasoning, tradeoffs considered)",
                },
                "category": {
                    "type": "string",
                    "enum": DECISION_CATEGORIES,
                    "description": "Category of decision",
                    "default": "other",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project identifier (default: 'default')",
                },
            },
            "required": ["decision"],
        },
        handler=log_decision,
        priority=10,
    ),
    ToolDef(
        name="log_blocker",
        description="Log a project blocker or issue that needs resolution. Track status as open, in_progress, or resolved.",
        parameters={
            "type": "object",
            "properties": {
                "blocker": {
                    "type": "string",
                    "description": "Description of the blocker or issue",
                },
                "impact": {
                    "type": "string",
                    "description": "How this blocker affects the project",
                },
                "status": {
                    "type": "string",
                    "enum": ["open", "in_progress", "resolved"],
                    "description": "Current status of the blocker",
                    "default": "open",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project identifier",
                },
            },
            "required": ["blocker"],
        },
        handler=log_blocker,
        priority=10,
    ),
    ToolDef(
        name="log_team_context",
        description="Log information about a team member's role, expertise, or relevant context.",
        parameters={
            "type": "object",
            "properties": {
                "member_name": {
                    "type": "string",
                    "description": "Name of the team member",
                },
                "role": {
                    "type": "string",
                    "description": "Their role on the team (e.g., 'Backend Developer', 'Product Manager')",
                },
                "expertise": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Areas of expertise (e.g., ['Python', 'AWS', 'GraphQL'])",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional context about this person",
                },
            },
            "required": ["member_name"],
        },
        handler=log_team_context,
        priority=10,
    ),
    ToolDef(
        name="log_meeting_note",
        description="Log key points or action items from a meeting or discussion.",
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of the meeting/discussion",
                },
                "action_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of action items or follow-ups",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Who was present",
                },
                "project_id": {
                    "type": "string",
                    "description": "Project identifier",
                },
            },
            "required": ["summary"],
        },
        handler=log_meeting_note,
        priority=10,
    ),
    ToolDef(
        name="query_decisions",
        description="Query past decisions, blockers, team context, or meeting notes. Use to recall important project history.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (what to look for)",
                },
                "type": {
                    "type": "string",
                    "enum": [
                        "decision",
                        "blocker",
                        "team_context",
                        "meeting_note",
                        "all",
                    ],
                    "description": "Type of memory to search (default: all)",
                    "default": "all",
                },
                "category": {
                    "type": "string",
                    "enum": DECISION_CATEGORIES,
                    "description": "Filter by category (only for decisions)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default: 10, max: 20)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        handler=query_decisions,
        priority=10,
    ),
]
