"""
Planning tools for persistent task management.

Inspired by Manus AI's "Planning with Files" pattern, this module provides
tools for Clarissa to maintain persistent working memory across sessions
using markdown files.

The 3-File System:
- task_plan.md - Tracks phases, goals, decisions, and errors
- findings.md - Stores research results and technical decisions
- progress.md - Contains session logs and actions taken

Tools:
    create_task_plan   - Create a new task plan for complex tasks
    get_current_plan   - Read the current active plan
    update_plan        - Update the task plan with new information
    add_finding        - Add a research finding or discovery
    get_findings       - Read all findings for current task
    log_progress       - Log an action or result
    get_progress       - Read the progress log
    complete_task      - Mark the current task as complete
    list_plans         - List all task plans for user
"""

import os
from datetime import datetime, timezone
from typing import Any

from tools._base import ToolContext, ToolDef

# Import file manager for persistence
from storage.local_files import get_file_manager

# Configuration
PLANNING_ENABLED = os.getenv("PLANNING_FILES_ENABLED", "true").lower() == "true"

# File naming conventions
PLAN_DIR = ".planning"
ACTIVE_PLAN_FILE = "task_plan.md"
FINDINGS_FILE = "findings.md"
PROGRESS_FILE = "progress.md"

# Module metadata
MODULE_NAME = "planning"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """## Task Planning Tools

You have access to persistent planning tools that help you manage complex, multi-step tasks.
Use these when a task requires research, multiple steps, or spans multiple sessions.

**The 3-File System:**
- `task_plan.md` - Your main plan: goals, phases, decisions, blockers
- `findings.md` - Research notes: discoveries, resources, technical decisions
- `progress.md` - Action log: what you did, results, errors encountered

**When to Use Planning:**
- Complex tasks with multiple steps
- Research-heavy tasks where you need to remember findings
- Tasks that might span multiple sessions
- When you need to track errors and how you solved them

**Best Practices:**
- Create a plan at the start of complex tasks
- Update findings after every 2 research operations (2-action rule)
- Log progress after significant actions
- Mark tasks complete when done

**Available Tools:**
- `create_task_plan` - Start a new plan
- `get_current_plan` - Read your active plan
- `update_plan` - Modify the plan
- `add_finding` - Record a discovery
- `log_progress` - Log an action
- `complete_task` - Finish and archive
"""


def _get_planning_dir(user_id: str, channel_id: str | None) -> str:
    """Get the planning directory path for a user."""
    return PLAN_DIR


def _now_iso() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _generate_plan_template(task: str, goals: list[str], phases: list[str]) -> str:
    """Generate the task_plan.md template."""
    goals_md = "\n".join(f"- [ ] {g}" for g in goals) if goals else "- [ ] Complete the task"
    phases_md = "\n".join(f"### Phase {i+1}: {p}\n- [ ] Not started" for i, p in enumerate(phases)) if phases else "### Phase 1: Implementation\n- [ ] Not started"

    return f"""# Task Plan

## Overview
**Task:** {task}
**Created:** {_now_iso()}
**Status:** In Progress

## Goals
{goals_md}

## Phases
{phases_md}

## Decisions
<!-- Record important decisions made during this task -->

## Blockers & Errors
<!-- Track issues encountered and how they were resolved -->

## Notes
<!-- Additional context and reminders -->
"""


def _generate_findings_template(task: str) -> str:
    """Generate the findings.md template."""
    return f"""# Findings

**Task:** {task}
**Started:** {_now_iso()}

---

<!-- Add findings using the add_finding tool -->
<!-- Format: ## [Category] Title -->
"""


def _generate_progress_template(task: str) -> str:
    """Generate the progress.md template."""
    return f"""# Progress Log

**Task:** {task}
**Started:** {_now_iso()}

---

<!-- Actions are logged automatically -->
"""


# ============================================================================
# Tool Handlers
# ============================================================================


async def handle_create_task_plan(args: dict[str, Any], ctx: ToolContext) -> str:
    """Create a new task plan."""
    task = args.get("task", "Untitled Task")
    goals = args.get("goals", [])
    phases = args.get("phases", [])

    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)

    # Generate templates
    plan_content = _generate_plan_template(task, goals, phases)
    findings_content = _generate_findings_template(task)
    progress_content = _generate_progress_template(task)

    # Save files
    results = []

    plan_path = f"{planning_dir}/{ACTIVE_PLAN_FILE}"
    result = fm.save_file(ctx.user_id, plan_path, plan_content, ctx.channel_id)
    results.append(f"Plan: {'Created' if result.success else result.message}")

    findings_path = f"{planning_dir}/{FINDINGS_FILE}"
    result = fm.save_file(ctx.user_id, findings_path, findings_content, ctx.channel_id)
    results.append(f"Findings: {'Created' if result.success else result.message}")

    progress_path = f"{planning_dir}/{PROGRESS_FILE}"
    result = fm.save_file(ctx.user_id, progress_path, progress_content, ctx.channel_id)
    results.append(f"Progress: {'Created' if result.success else result.message}")

    # Log initial progress
    await handle_log_progress({
        "action": "Created task plan",
        "result": f"Task: {task}",
        "category": "setup"
    }, ctx)

    return f"Task plan created for: {task}\n" + "\n".join(results)


async def handle_get_current_plan(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read the current task plan."""
    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    plan_path = f"{planning_dir}/{ACTIVE_PLAN_FILE}"

    result = fm.read_file(ctx.user_id, plan_path, ctx.channel_id)

    if not result.success:
        return "No active task plan found. Use create_task_plan to start one."

    return result.message


async def handle_update_plan(args: dict[str, Any], ctx: ToolContext) -> str:
    """Update the task plan."""
    section = args.get("section", "notes")
    content = args.get("content", "")

    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    plan_path = f"{planning_dir}/{ACTIVE_PLAN_FILE}"

    # Read current plan
    result = fm.read_file(ctx.user_id, plan_path, ctx.channel_id)
    if not result.success:
        return "No active task plan found. Use create_task_plan first."

    current = result.message

    # Map section to header
    section_headers = {
        "goals": "## Goals",
        "phases": "## Phases",
        "decisions": "## Decisions",
        "blockers": "## Blockers & Errors",
        "notes": "## Notes",
        "status": "**Status:**",
    }

    header = section_headers.get(section.lower())
    if not header:
        return f"Unknown section: {section}. Valid: {', '.join(section_headers.keys())}"

    # Find section and append content
    if header in current:
        # Find the next section header
        header_pos = current.find(header)
        after_header = current[header_pos + len(header):]

        # Find next ## header
        next_section = after_header.find("\n## ")
        if next_section == -1:
            # Last section, append at end
            updated = current.rstrip() + f"\n\n{content}\n"
        else:
            # Insert before next section
            insert_pos = header_pos + len(header) + next_section
            updated = current[:insert_pos] + f"\n{content}\n" + current[insert_pos:]
    else:
        # Section not found, append to end
        updated = current.rstrip() + f"\n\n{header}\n{content}\n"

    # Save updated plan
    save_result = fm.save_file(ctx.user_id, plan_path, updated, ctx.channel_id)

    if save_result.success:
        return f"Updated {section} section in task plan."
    return f"Failed to update plan: {save_result.message}"


async def handle_add_finding(args: dict[str, Any], ctx: ToolContext) -> str:
    """Add a finding to the findings file."""
    category = args.get("category", "General")
    title = args.get("title", "Finding")
    content = args.get("content", "")

    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    findings_path = f"{planning_dir}/{FINDINGS_FILE}"

    # Read current findings
    result = fm.read_file(ctx.user_id, findings_path, ctx.channel_id)
    if not result.success:
        # Create findings file if it doesn't exist
        current = _generate_findings_template("Unknown Task")
    else:
        current = result.message

    # Format new finding
    timestamp = _now_iso()
    new_finding = f"""
## [{category}] {title}
*{timestamp}*

{content}

---
"""

    # Append finding
    updated = current.rstrip() + "\n" + new_finding

    # Save
    save_result = fm.save_file(ctx.user_id, findings_path, updated, ctx.channel_id)

    if save_result.success:
        return f"Added finding: [{category}] {title}"
    return f"Failed to add finding: {save_result.message}"


async def handle_get_findings(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read all findings."""
    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    findings_path = f"{planning_dir}/{FINDINGS_FILE}"

    result = fm.read_file(ctx.user_id, findings_path, ctx.channel_id)

    if not result.success:
        return "No findings file found. Findings are created when you use add_finding."

    return result.message


async def handle_log_progress(args: dict[str, Any], ctx: ToolContext) -> str:
    """Log a progress entry."""
    action = args.get("action", "Action taken")
    result_text = args.get("result", "")
    category = args.get("category", "action")
    error = args.get("error", "")

    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    progress_path = f"{planning_dir}/{PROGRESS_FILE}"

    # Read current progress
    read_result = fm.read_file(ctx.user_id, progress_path, ctx.channel_id)
    if not read_result.success:
        current = _generate_progress_template("Unknown Task")
    else:
        current = read_result.message

    # Format entry
    timestamp = _now_iso()
    icon = {
        "action": "->",
        "success": "[OK]",
        "error": "[ERR]",
        "setup": "[+]",
        "research": "[?]",
        "decision": "[!]",
    }.get(category, "->")

    entry = f"\n### {timestamp}\n**{icon} {action}**\n"
    if result_text:
        entry += f"Result: {result_text}\n"
    if error:
        entry += f"Error: {error}\n"

    # Append entry
    updated = current.rstrip() + entry

    # Save
    save_result = fm.save_file(ctx.user_id, progress_path, updated, ctx.channel_id)

    if save_result.success:
        return f"Logged: {action}"
    return f"Failed to log progress: {save_result.message}"


async def handle_get_progress(args: dict[str, Any], ctx: ToolContext) -> str:
    """Read the progress log."""
    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)
    progress_path = f"{planning_dir}/{PROGRESS_FILE}"

    result = fm.read_file(ctx.user_id, progress_path, ctx.channel_id)

    if not result.success:
        return "No progress log found."

    # Optionally limit to recent entries
    limit = args.get("limit", 0)
    content = result.message

    if limit > 0:
        # Extract last N entries (sections starting with ###)
        lines = content.split("\n")
        entries = []
        current_entry = []

        for line in lines:
            if line.startswith("### "):
                if current_entry:
                    entries.append("\n".join(current_entry))
                current_entry = [line]
            else:
                current_entry.append(line)

        if current_entry:
            entries.append("\n".join(current_entry))

        # Return header + last N entries
        header_end = content.find("---")
        if header_end != -1:
            header = content[:header_end + 3]
        else:
            header = ""

        return header + "\n" + "\n".join(entries[-limit:])

    return content


async def handle_complete_task(args: dict[str, Any], ctx: ToolContext) -> str:
    """Mark the current task as complete and archive."""
    summary = args.get("summary", "Task completed")

    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)

    # Update plan status
    plan_path = f"{planning_dir}/{ACTIVE_PLAN_FILE}"
    result = fm.read_file(ctx.user_id, plan_path, ctx.channel_id)

    if not result.success:
        return "No active task plan to complete."

    current = result.message

    # Update status
    updated = current.replace("**Status:** In Progress", f"**Status:** Completed - {_now_iso()}")

    # Add completion note
    updated += f"\n\n## Completion Summary\n{summary}\n"

    # Save updated plan
    fm.save_file(ctx.user_id, plan_path, updated, ctx.channel_id)

    # Log completion
    await handle_log_progress({
        "action": "Task completed",
        "result": summary,
        "category": "success"
    }, ctx)

    # Archive by renaming (add timestamp)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    archive_dir = f"{planning_dir}/archive"

    # Read and save to archive
    for filename in [ACTIVE_PLAN_FILE, FINDINGS_FILE, PROGRESS_FILE]:
        src_path = f"{planning_dir}/{filename}"
        read_result = fm.read_file(ctx.user_id, src_path, ctx.channel_id)
        if read_result.success:
            archive_path = f"{archive_dir}/{timestamp}_{filename}"
            fm.save_file(ctx.user_id, archive_path, read_result.message, ctx.channel_id)
            # Delete original
            fm.delete_file(ctx.user_id, src_path, ctx.channel_id)

    return f"Task completed and archived: {summary}"


async def handle_list_plans(args: dict[str, Any], ctx: ToolContext) -> str:
    """List all task plans (active and archived)."""
    fm = get_file_manager()
    planning_dir = _get_planning_dir(ctx.user_id, ctx.channel_id)

    files = fm.list_files(ctx.user_id, ctx.channel_id)

    # Filter for planning files
    active = []
    archived = []

    for f in files:
        name = str(f.name)
        if PLAN_DIR in name or name.startswith(".planning"):
            if "archive" in name:
                archived.append(f)
            elif "task_plan.md" in name:
                active.append(f)

    result_lines = ["## Task Plans\n"]

    if active:
        result_lines.append("### Active")
        for f in active:
            result_lines.append(f"- {f.name} (modified {f.created_at.strftime('%Y-%m-%d')})")
    else:
        result_lines.append("### Active\nNo active task plans.\n")

    if archived:
        result_lines.append("\n### Archived")
        for f in archived[:10]:  # Limit to 10 most recent
            result_lines.append(f"- {f.name}")

    return "\n".join(result_lines)


# ============================================================================
# Tool Definitions
# ============================================================================

TOOLS = [
    ToolDef(
        name="create_task_plan",
        description=(
            "Create a new task plan for a complex, multi-step task. "
            "This sets up the 3-file planning system: task_plan.md, findings.md, progress.md. "
            "Use this at the start of complex tasks to maintain persistent working memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Brief description of the task (e.g., 'Implement user authentication')",
                },
                "goals": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of goals/success criteria for the task",
                },
                "phases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "High-level phases of the implementation",
                },
            },
            "required": ["task"],
        },
        handler=handle_create_task_plan,
    ),
    ToolDef(
        name="get_current_plan",
        description=(
            "Read the current active task plan. "
            "Returns the full task_plan.md content including goals, phases, decisions, and blockers."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=handle_get_current_plan,
    ),
    ToolDef(
        name="update_plan",
        description=(
            "Update a section of the task plan. "
            "Use this to add decisions, note blockers, or update status."
        ),
        parameters={
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": ["goals", "phases", "decisions", "blockers", "notes", "status"],
                    "description": "Which section to update",
                },
                "content": {
                    "type": "string",
                    "description": "Content to add to the section",
                },
            },
            "required": ["section", "content"],
        },
        handler=handle_update_plan,
    ),
    ToolDef(
        name="add_finding",
        description=(
            "Add a research finding or discovery. "
            "Use this to record important information discovered during research. "
            "Follow the 2-action rule: update findings after every 2 research operations."
        ),
        parameters={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Category of finding (e.g., 'API', 'Architecture', 'Bug', 'Resource')",
                },
                "title": {
                    "type": "string",
                    "description": "Brief title for the finding",
                },
                "content": {
                    "type": "string",
                    "description": "Detailed finding content (can include code, links, etc.)",
                },
            },
            "required": ["title", "content"],
        },
        handler=handle_add_finding,
    ),
    ToolDef(
        name="get_findings",
        description="Read all findings recorded for the current task.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=handle_get_findings,
    ),
    ToolDef(
        name="log_progress",
        description=(
            "Log an action or result to the progress file. "
            "Use this after significant actions to maintain a trail of what was done."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "What action was taken",
                },
                "result": {
                    "type": "string",
                    "description": "The result or outcome of the action",
                },
                "category": {
                    "type": "string",
                    "enum": ["action", "success", "error", "setup", "research", "decision"],
                    "description": "Category of the progress entry",
                },
                "error": {
                    "type": "string",
                    "description": "Error message if something went wrong",
                },
            },
            "required": ["action"],
        },
        handler=handle_log_progress,
    ),
    ToolDef(
        name="get_progress",
        description="Read the progress log. Optionally limit to recent entries.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent entries to return (0 = all)",
                },
            },
            "required": [],
        },
        handler=handle_get_progress,
    ),
    ToolDef(
        name="complete_task",
        description=(
            "Mark the current task as complete and archive the planning files. "
            "Provide a summary of what was accomplished."
        ),
        parameters={
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Summary of what was accomplished",
                },
            },
            "required": ["summary"],
        },
        handler=handle_complete_task,
    ),
    ToolDef(
        name="list_plans",
        description="List all task plans (active and archived) for the user.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=handle_list_plans,
    ),
]

__all__ = [
    "MODULE_NAME",
    "MODULE_VERSION",
    "SYSTEM_PROMPT",
    "TOOLS",
    "PLANNING_ENABLED",
    # Handlers
    "handle_create_task_plan",
    "handle_get_current_plan",
    "handle_update_plan",
    "handle_add_finding",
    "handle_get_findings",
    "handle_log_progress",
    "handle_get_progress",
    "handle_complete_task",
    "handle_list_plans",
]
