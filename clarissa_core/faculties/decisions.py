"""Decisions faculty for Clarissa.

Wraps the existing tools/decision_log.py tools with intent-based orchestration.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

import logging
import re
from typing import Any

from clarissa_core.faculty import Faculty, FacultyResult

logger = logging.getLogger(__name__)


class DecisionsFaculty(Faculty):
    """Decision and team context orchestrator.

    Wraps the existing decision log tools with intent-based execution.
    Clarissa can say "log a decision about using React" and this faculty will
    figure out which tool(s) to call.
    """

    name = "decisions"
    description = "Decision logging, blockers, team context, and meeting notes"
    available_actions = [
        "log_decision",
        "log_blocker",
        "log_team_context",
        "log_meeting_note",
        "query_decisions",
    ]

    def __init__(self):
        self._configured = True  # Always available

    async def execute(
        self,
        intent: str,
        constraints: list[str] | None = None,
        user_id: str | None = None,
        channel_id: str | None = None,
    ) -> FacultyResult:
        """Execute decision operations based on intent."""
        logger.info(f"[faculty:decisions] Intent: {intent}")

        try:
            # Parse intent to determine action
            action, params = self._parse_intent(intent, constraints)
            logger.info(f"[faculty:decisions] Action: {action}, Params: {params}")

            # Execute the action
            result = await self._execute_action(action, params, user_id, channel_id)
            return result

        except Exception as e:
            logger.exception(f"[faculty:decisions] Error: {e}")
            return FacultyResult(
                success=False,
                summary=f"Decision operation failed: {str(e)}",
                error=str(e),
            )

    def _parse_intent(
        self, intent: str, constraints: list[str] | None = None
    ) -> tuple[str, dict[str, Any]]:
        """Parse natural language intent into action and parameters."""
        intent_lower = intent.lower()

        # Log decision
        if any(
            x in intent_lower
            for x in ["log decision", "record decision", "decided to", "we decided"]
        ):
            decision = self._extract_decision(intent)
            rationale = self._extract_rationale(intent)
            category = self._extract_category(intent)
            project_id = self._extract_project_id(intent, constraints)
            return "log_decision", {
                "decision": decision,
                "rationale": rationale,
                "category": category,
                "project_id": project_id,
            }

        # Log blocker
        if any(
            x in intent_lower
            for x in ["log blocker", "record blocker", "blocked by", "blocking"]
        ):
            blocker = self._extract_blocker(intent)
            impact = self._extract_impact(intent)
            status = self._extract_status(intent)
            project_id = self._extract_project_id(intent, constraints)
            return "log_blocker", {
                "blocker": blocker,
                "impact": impact,
                "status": status,
                "project_id": project_id,
            }

        # Log team context
        if any(
            x in intent_lower
            for x in ["team member", "add member", "team context", "who is"]
        ):
            member_name = self._extract_member_name(intent)
            role = self._extract_role(intent)
            expertise = self._extract_expertise(intent)
            notes = self._extract_notes(intent)
            return "log_team_context", {
                "member_name": member_name,
                "role": role,
                "expertise": expertise,
                "notes": notes,
            }

        # Log meeting note
        if any(
            x in intent_lower
            for x in ["meeting note", "record meeting", "meeting summary"]
        ):
            summary = self._extract_summary(intent)
            attendees = self._extract_attendees(intent)
            action_items = self._extract_action_items(intent)
            project_id = self._extract_project_id(intent, constraints)
            return "log_meeting_note", {
                "summary": summary,
                "attendees": attendees,
                "action_items": action_items,
                "project_id": project_id,
            }

        # Query decisions
        if any(
            x in intent_lower
            for x in [
                "query decision",
                "find decision",
                "search decision",
                "what decision",
            ]
        ):
            query = self._extract_query(intent)
            category = self._extract_category(intent)
            decision_type = self._extract_type(intent)
            return "query_decisions", {
                "query": query,
                "category": category,
                "type": decision_type,
            }

        # Default: query decisions
        return "query_decisions", {"query": intent}

    async def _execute_action(
        self,
        action: str,
        params: dict[str, Any],
        user_id: str | None,
        channel_id: str | None,
    ) -> FacultyResult:
        """Execute a decision action via the underlying tools."""
        from tools._base import ToolContext

        # Create a context for tool execution
        ctx = ToolContext(user_id=user_id or "faculty", channel_id=channel_id)

        # Import the decision log tools module
        from tools import decision_log

        try:
            if action == "log_decision":
                result = await decision_log.log_decision(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Decision logged:\n{result}",
                    data={"result": result},
                )

            elif action == "log_blocker":
                result = await decision_log.log_blocker(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Blocker logged:\n{result}",
                    data={"result": result},
                )

            elif action == "log_team_context":
                result = await decision_log.log_team_context(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Team context logged:\n{result}",
                    data={"result": result},
                )

            elif action == "log_meeting_note":
                result = await decision_log.log_meeting_note(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Meeting note logged:\n{result}",
                    data={"result": result},
                )

            elif action == "query_decisions":
                result = await decision_log.query_decisions(params, ctx)
                return FacultyResult(
                    success=True,
                    summary=f"Query results:\n{result}",
                    data={"results": result},
                )

            else:
                return FacultyResult(
                    success=False,
                    summary=f"Unknown decision action: {action}",
                    error=f"Action '{action}' not implemented",
                )

        except Exception as e:
            return FacultyResult(
                success=False,
                summary=f"Decision log error: {str(e)}",
                error=str(e),
            )

    # Helper methods for intent parsing
    def _extract_decision(self, intent: str) -> str:
        """Extract decision from intent."""
        match = re.search(
            r'(?:decided to|decision[:\s]+)["\']?(.+?)["\']?(?:because|$)',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        return intent

    def _extract_rationale(self, intent: str) -> str:
        """Extract rationale from intent."""
        match = re.search(
            r'(?:because|reason[:\s]+|rationale[:\s]+)["\']?(.+?)["\']?$',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_category(self, intent: str) -> str:
        """Extract category from intent."""
        categories = [
            "architecture",
            "technology",
            "process",
            "team",
            "security",
            "performance",
        ]
        intent_lower = intent.lower()
        for category in categories:
            if category in intent_lower:
                return category
        return "general"

    def _extract_project_id(
        self, intent: str, constraints: list[str] | None = None
    ) -> str:
        """Extract project ID from intent or constraints."""
        if constraints:
            for c in constraints:
                if "project" in c.lower():
                    match = re.search(r"project[:\s]+(\S+)", c, re.IGNORECASE)
                    if match:
                        return match.group(1)
        match = re.search(r'project[:\s]+["\']?(\S+)["\']?', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_blocker(self, intent: str) -> str:
        """Extract blocker from intent."""
        match = re.search(
            r'(?:blocked by|blocker[:\s]+)["\']?(.+?)["\']?(?:impact|$)',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        return intent

    def _extract_impact(self, intent: str) -> str:
        """Extract impact from intent."""
        match = re.search(r'(?:impact[:\s]+)["\']?(.+?)["\']?$', intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_status(self, intent: str) -> str:
        """Extract status from intent."""
        statuses = ["open", "in_progress", "resolved", "wont_fix"]
        intent_lower = intent.lower()
        for status in statuses:
            if status.replace("_", " ") in intent_lower or status in intent_lower:
                return status
        return "open"

    def _extract_member_name(self, intent: str) -> str:
        """Extract member name from intent."""
        match = re.search(
            r'(?:member|person|who)[:\s]+["\']?(\w+(?:\s+\w+)?)["\']?',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_role(self, intent: str) -> str:
        """Extract role from intent."""
        roles = [
            "developer",
            "designer",
            "pm",
            "manager",
            "lead",
            "architect",
            "qa",
            "devops",
        ]
        intent_lower = intent.lower()
        for role in roles:
            if role in intent_lower:
                return role
        match = re.search(r'role[:\s]+["\']?(\w+)["\']?', intent, re.IGNORECASE)
        if match:
            return match.group(1)
        return ""

    def _extract_expertise(self, intent: str) -> str:
        """Extract expertise from intent."""
        match = re.search(
            r'(?:expertise|expert in|knows)[:\s]+["\']?(.+?)["\']?(?:,|$)',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return ""

    def _extract_notes(self, intent: str) -> str:
        """Extract notes from intent."""
        match = re.search(r'(?:notes?[:\s]+)["\']?(.+?)["\']?$', intent, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def _extract_summary(self, intent: str) -> str:
        """Extract meeting summary from intent."""
        match = re.search(
            r'(?:summary[:\s]+|meeting about)["\']?(.+?)["\']?(?:attendees|action|$)',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return intent

    def _extract_attendees(self, intent: str) -> list[str]:
        """Extract attendees from intent."""
        match = re.search(
            r'attendees?[:\s]+["\']?(.+?)["\']?(?:action|$)', intent, re.IGNORECASE
        )
        if match:
            attendees_str = match.group(1)
            return [
                a.strip() for a in re.split(r",|\band\b", attendees_str) if a.strip()
            ]
        return []

    def _extract_action_items(self, intent: str) -> list[str]:
        """Extract action items from intent."""
        match = re.search(
            r'action items?[:\s]+["\']?(.+?)["\']?$', intent, re.IGNORECASE
        )
        if match:
            items_str = match.group(1)
            return [
                i.strip() for i in re.split(r",|\band\b|\d+\.", items_str) if i.strip()
            ]
        return []

    def _extract_query(self, intent: str) -> str:
        """Extract query from intent."""
        match = re.search(
            r'(?:query|find|search|about)[:\s]+["\']?(.+?)["\']?$',
            intent,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        match = re.search(r'["\'](.+?)["\']', intent)
        if match:
            return match.group(1)
        return intent

    def _extract_type(self, intent: str) -> str:
        """Extract decision type from intent."""
        types = ["decision", "blocker", "team", "meeting"]
        intent_lower = intent.lower()
        for t in types:
            if t in intent_lower:
                return t
        return "all"
