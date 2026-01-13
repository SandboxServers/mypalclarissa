"""Planning faculty for Clarissa.

Wraps the existing tools/planning tools with intent-based orchestration.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from clarissa_core.faculty import Faculty, FacultyResult

logger = logging.getLogger(__name__)


class PlanningFaculty(Faculty):
    """Task planning orchestrator.

    Wraps the existing planning tools with intent-based execution.
    Clarissa can say "create a plan for this task" and this faculty will
    figure out which tool(s) to call.
    """

    name = "planning"
    description = "Task planning and progress tracking (plans, findings, progress logs)"
    available_actions = [
        "create_plan",
        "get_plan",
        "update_plan",
        "add_finding",
        "get_findings",
        "log_progress",
        "get_progress",
        "complete_task",
        "list_plans",
    ]

    def __init__(self):
        self._configured = self._check_configuration()

    def _check_configuration(self) -> bool:
        """Check if planning is enabled."""
        return os.getenv("PLANNING_FILES_ENABLED", "true").lower() == "true"

    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute planning operations based on intent."""
        if not self._configured:
            return FacultyResult(
                success=False,
                summary="Planning disabled. Set PLANNING_FILES_ENABLED=true.",
                error="Planning not enabled",
            )

        logger.info(f"[faculty:planning] Intent: {intent}")

        try:
            # Parse intent to determine action
            action, params = self._parse_intent(intent, constraints)
            logger.info(f"[faculty:planning] Action: {action}, Params: {params}")

            # Execute the action
            result = await self._execute_action(action, params, user_id, channel_id)
            return result

        except Exception as e:
            logger.exception(f"[faculty:planning] Error: {e}")
            return FacultyResult(
                success=False,
                summary=f"Planning operation failed: {str(e)}",
                error=str(e),
            )

    def _parse_intent(
        self, intent: str, constraints: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Parse natural language intent into action and parameters."""
        intent_lower = intent.lower()

        # Create plan
        if any(
            x in intent_lower
            for x in ["create plan", "new plan", "start plan", "make plan"]
        ):
            task = self._extract_task(intent)
            goals = self._extract_goals(intent)
            phases = self._extract_phases(intent)
            return "create_plan", {"task": task, "goals": goals, "phases": phases}

        # Get current plan
        if any(
            x in intent_lower
            for x in [
                "get plan",
                "show plan",
                "current plan",
                "read plan",
                "what's the plan",
            ]
        ):
            return "get_plan", {}

        # Update plan
        if any(
            x in intent_lower for x in ["update plan", "modify plan", "change plan"]
        ):
            section = self._extract_section(intent)
            content = self._extract_content(intent)
            return "update_plan", {"section": section, "content": content}

        # Add finding
        if any(
            x in intent_lower
            for x in ["add finding", "record finding", "found that", "discovered"]
        ):
            content = self._extract_finding(intent)
            category = self._extract_category(intent)
            return "add_finding", {"content": content, "category": category}

        # Get findings
        if any(
            x in intent_lower
            for x in ["get finding", "show finding", "read finding", "what did we find"]
        ):
            return "get_findings", {}

        # Log progress
        if any(
            x in intent_lower
            for x in ["log progress", "record progress", "completed", "did", "finished"]
        ):
            content = self._extract_progress(intent)
            return "log_progress", {"content": content}

        # Get progress
        if any(
            x in intent_lower
            for x in ["get progress", "show progress", "read progress", "what's done"]
        ):
            return "get_progress", {}

        # Complete task
        if any(
            x in intent_lower
            for x in ["complete task", "finish task", "task done", "mark complete"]
        ):
            return "complete_task", {}

        # List plans
        if any(x in intent_lower for x in ["list plan", "show plan", "all plan"]):
            return "list_plans", {}

        # Default: get current plan
        return "get_plan", {}

    async def _execute_action(
        self,
        action: str,
        params: dict[str, Any],
        user_id: str | None,
        channel_id: str | None,
    ) -> FacultyResult:
        """Execute a planning action via the underlying tools."""
        from tools._base import ToolContext

        # Create a context for tool execution
        ctx = ToolContext(user_id=user_id or "faculty", channel_id=channel_id)

        # Import the planning tools module
        from tools import planning

        try:
            if action == "create_plan":
                result = await planning.handle_create_task_plan(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Plan created:\n{result}",
                    data={"plan": result},
                )

            elif action == "get_plan":
                result = await planning.handle_get_current_plan(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Current plan:\n{result}",
                    data={"plan": result},
                )

            elif action == "update_plan":
                result = await planning.handle_update_plan(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Plan updated:\n{result}",
                    data={"result": result},
                )

            elif action == "add_finding":
                result = await planning.handle_add_finding(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Finding added:\n{result}",
                    data={"result": result},
                )

            elif action == "get_findings":
                result = await planning.handle_get_findings(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Findings:\n{result}",
                    data={"findings": result},
                )

            elif action == "log_progress":
                result = await planning.handle_log_progress(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Progress logged:\n{result}",
                    data={"result": result},
                )

            elif action == "get_progress":
                result = await planning.handle_get_progress(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Progress:\n{result}",
                    data={"progress": result},
                )

            elif action == "complete_task":
                result = await planning.handle_complete_task(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Task completed:\n{result}",
                    data={"result": result},
                )

            elif action == "list_plans":
                result = await planning.handle_list_plans(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Plans:\n{result}",
                    data={"plans": result},
                )

            else:
                return FacultyResult(
                    success=False,
                    summary=f"Unknown planning action: {action}",
                    error=f"Action '{action}' not implemented",
                )

        except Exception as e:
            return FacultyResult(
                success=False,
                summary=f"Planning error: {str(e)}",
                error=str(e),
            )

    # Helper methods for intent parsing
    def _extract_task(self, intent: str) -> str:
        """Extract task description from intent."""
        # Look for task after keywords
        match = re.search(
            r'(?:plan for|task[:\s]+)["\']?(.+?)["\']?$', intent, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        # Remove plan keywords and use the rest
        task = re.sub(
            r"(?:create|new|start|make)\s+(?:a\s+)?plan\s+(?:for\s+)?",
            "",
            intent,
            flags=re.IGNORECASE,
        )
        return task.strip() or "Task"

    def _extract_goals(self, intent: str) -> list[str]:
        """Extract goals from intent."""
        goals = []
        # Look for goals keyword
        match = re.search(r"goals?[:\s]+(.+?)(?:phases?|$)", intent, re.IGNORECASE)
        if match:
            goals_str = match.group(1)
            # Split by commas or "and"
            goals = [g.strip() for g in re.split(r",|\band\b", goals_str) if g.strip()]
        return goals

    def _extract_phases(self, intent: str) -> list[str]:
        """Extract phases from intent."""
        phases = []
        # Look for phases keyword
        match = re.search(r"phases?[:\s]+(.+?)$", intent, re.IGNORECASE)
        if match:
            phases_str = match.group(1)
            # Split by commas or "then" or numbers
            phases = [
                p.strip()
                for p in re.split(r",|\bthen\b|\d+\.", phases_str)
                if p.strip()
            ]
        return phases

    def _extract_section(self, intent: str) -> str:
        """Extract section name from intent."""
        sections = ["goals", "phases", "decisions", "blockers", "notes"]
        intent_lower = intent.lower()
        for section in sections:
            if section in intent_lower:
                return section
        return "notes"

    def _extract_content(self, intent: str) -> str:
        """Extract content from intent."""
        # Look for content in quotes
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        # Look for content after section keywords
        match = re.search(r"(?:with|to|:)\s+(.+?)$", intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_finding(self, intent: str) -> str:
        """Extract finding content from intent."""
        # Look for finding after keywords
        match = re.search(
            r"(?:finding|found|discovered)[:\s]+(.+?)$", intent, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        # Look for quoted content
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        return intent

    def _extract_category(self, intent: str) -> str:
        """Extract finding category from intent."""
        categories = ["technical", "resource", "decision", "blocker", "note"]
        intent_lower = intent.lower()
        for category in categories:
            if category in intent_lower:
                return category
        return "note"

    def _extract_progress(self, intent: str) -> str:
        """Extract progress content from intent."""
        # Look for progress after keywords
        match = re.search(
            r"(?:progress|completed|did|finished)[:\s]+(.+?)$", intent, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()
        # Look for quoted content
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        return intent
