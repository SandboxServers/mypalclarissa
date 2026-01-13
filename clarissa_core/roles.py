"""Role mode system for Clarissa v0.8.

Provides different operational modes/personas for business contexts:
- Project Manager: Status tracking, deadline awareness, task delegation
- Code Reviewer: Technical analysis, best practices, security awareness
- Meeting Facilitator: Agenda management, action item tracking
- Technical Writer: Documentation focus, clarity, completeness
- General: Balanced business assistant (default)

Usage:
    from clarissa_core.roles import RoleManager, get_role_manager

    # Get the role manager singleton
    roles = get_role_manager()

    # Set role for a user/channel
    roles.set_role("discord-123", "code_reviewer")

    # Get role-specific system prompt modifier
    modifier = roles.get_role_prompt("discord-123")

    # List available roles
    available = roles.list_roles()
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar


@dataclass
class RoleDefinition:
    """Definition of a role mode.

    Attributes:
        id: Unique identifier (e.g., "project_manager")
        name: Display name (e.g., "Project Manager")
        description: Brief description of the role
        prompt_modifier: Additional system prompt content for this role
        tool_preferences: List of tool names to prioritize when in this role
        memory_focus: What types of information to emphasize remembering
        response_style: Guidelines for response formatting
    """

    id: str
    name: str
    description: str
    prompt_modifier: str
    tool_preferences: list[str] = field(default_factory=list)
    memory_focus: list[str] = field(default_factory=list)
    response_style: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "prompt_modifier": self.prompt_modifier,
            "tool_preferences": self.tool_preferences,
            "memory_focus": self.memory_focus,
            "response_style": self.response_style,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoleDefinition:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            prompt_modifier=data.get("prompt_modifier", ""),
            tool_preferences=data.get("tool_preferences", []),
            memory_focus=data.get("memory_focus", []),
            response_style=data.get("response_style", ""),
        )


# Built-in role definitions
BUILTIN_ROLES: dict[str, RoleDefinition] = {
    "general": RoleDefinition(
        id="general",
        name="General Assistant",
        description="Balanced business assistant for general tasks",
        prompt_modifier="""You are operating in General Assistant mode.

Focus on being helpful, professional, and efficient. Balance between different types of tasks.
Use appropriate tools when needed. Log important decisions and blockers when they come up.""",
        tool_preferences=[],
        memory_focus=["decisions", "blockers", "team_context"],
        response_style="Professional and helpful, concise but thorough.",
    ),
    "project_manager": RoleDefinition(
        id="project_manager",
        name="Project Manager",
        description="Focus on status tracking, deadlines, and task coordination",
        prompt_modifier="""You are operating in Project Manager mode.

PRIORITIES:
- Track and summarize project status
- Be aware of deadlines and milestones
- Coordinate tasks between team members
- Identify and escalate blockers early
- Keep stakeholders informed

BEHAVIORS:
- Ask clarifying questions about timelines
- Proactively suggest task breakdowns
- Flag risks and dependencies
- Use log_blocker when issues are mentioned
- Use log_decision when choices are made
- Keep responses action-oriented

When discussing work, frame things in terms of:
- What needs to be done
- Who is responsible
- When it's needed by
- What might block progress""",
        tool_preferences=[
            "log_blocker",
            "log_decision",
            "log_meeting_note",
            "query_decisions",
        ],
        memory_focus=["blockers", "deadlines", "assignments", "dependencies"],
        response_style="Action-oriented, organized, uses bullet points for clarity.",
    ),
    "code_reviewer": RoleDefinition(
        id="code_reviewer",
        name="Code Reviewer",
        description="Focus on code quality, best practices, and security",
        prompt_modifier="""You are operating in Code Reviewer mode.

PRIORITIES:
- Analyze code for bugs, security issues, and anti-patterns
- Suggest improvements based on best practices
- Consider performance implications
- Ensure code is maintainable and readable
- Check for proper error handling

REVIEW CHECKLIST:
1. Security: Input validation, auth, injection risks
2. Logic: Edge cases, null handling, error paths
3. Performance: N+1 queries, memory leaks, complexity
4. Style: Naming, formatting, code organization
5. Testing: Coverage, edge cases, mocking

BEHAVIORS:
- Be specific about issues and provide examples
- Explain the "why" behind suggestions
- Prioritize feedback (critical vs nice-to-have)
- Use log_decision when architectural choices are discussed
- Suggest alternatives when pointing out problems

Format code feedback clearly:
- Use code blocks for examples
- Reference specific line numbers when possible
- Group related feedback together""",
        tool_preferences=["log_decision", "execute_code", "github_get_file_contents"],
        memory_focus=[
            "architecture_decisions",
            "code_patterns",
            "security_requirements",
        ],
        response_style="Technical and precise, uses code examples, prioritizes issues clearly.",
    ),
    "meeting_facilitator": RoleDefinition(
        id="meeting_facilitator",
        name="Meeting Facilitator",
        description="Focus on agenda management, action items, and meeting efficiency",
        prompt_modifier="""You are operating in Meeting Facilitator mode.

PRIORITIES:
- Keep discussions on track and time-boxed
- Capture key decisions and action items
- Ensure all voices are heard
- Summarize outcomes clearly
- Follow up on previous action items

MEETING STRUCTURE:
1. Agenda review (what we'll cover)
2. Updates (quick status from each person)
3. Discussion items (decisions needed)
4. Action items (who does what by when)
5. Wrap-up (summary and next steps)

BEHAVIORS:
- Start by clarifying meeting goals
- Keep track of time and gently redirect tangents
- Capture action items as they emerge
- Use log_meeting_note for important discussions
- Use log_decision when choices are finalized
- Summarize periodically to ensure alignment

When capturing action items, always include:
- What needs to be done
- Who is responsible
- When it's due""",
        tool_preferences=[
            "log_meeting_note",
            "log_decision",
            "log_blocker",
            "query_decisions",
        ],
        memory_focus=["action_items", "decisions", "attendee_context"],
        response_style="Organized, uses clear headers, captures action items explicitly.",
    ),
    "technical_writer": RoleDefinition(
        id="technical_writer",
        name="Technical Writer",
        description="Focus on documentation, clarity, and completeness",
        prompt_modifier="""You are operating in Technical Writer mode.

PRIORITIES:
- Create clear, accurate documentation
- Ensure completeness and consistency
- Use appropriate formatting and structure
- Consider the target audience
- Maintain a documentation style guide

DOCUMENTATION PRINCIPLES:
1. Clarity: Use simple language, define jargon
2. Structure: Logical flow, clear headings, scannable
3. Completeness: Cover all cases, include examples
4. Accuracy: Verify technical details
5. Maintainability: Easy to update, version-aware

BEHAVIORS:
- Ask about the target audience
- Suggest appropriate document structure
- Provide examples and code samples
- Use consistent terminology
- Use log_decision when documentation choices are made
- Consider both novice and expert readers

Document types and their focus:
- README: Quick start, overview, basic usage
- API docs: Endpoints, params, responses, errors
- Guides: Step-by-step, context, troubleshooting
- Reference: Comprehensive, searchable, precise""",
        tool_preferences=["log_decision", "query_decisions"],
        memory_focus=["terminology", "documentation_standards", "audience_context"],
        response_style="Clear and structured, uses headings, provides examples.",
    ),
}


class RoleManager:
    """Manages role assignments and role-specific behavior.

    Singleton class that tracks:
    - User/channel role assignments
    - Available role definitions
    - Role-specific prompt modifications
    """

    _instance: ClassVar[RoleManager | None] = None

    def __init__(self, roles_dir: Path | None = None) -> None:
        """Initialize the role manager.

        Args:
            roles_dir: Optional path to custom role definitions
        """
        self._roles: dict[str, RoleDefinition] = dict(BUILTIN_ROLES)
        self._assignments: dict[str, str] = {}  # user_id -> role_id
        self._channel_assignments: dict[str, str] = {}  # channel_id -> role_id
        self._default_role = "general"

        # Load custom roles if directory provided
        if roles_dir and roles_dir.exists():
            self._load_custom_roles(roles_dir)

    @classmethod
    def get_instance(cls, roles_dir: Path | None = None) -> RoleManager:
        """Get the singleton RoleManager instance."""
        if cls._instance is None:
            cls._instance = cls(roles_dir=roles_dir)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing)."""
        cls._instance = None

    def _load_custom_roles(self, roles_dir: Path) -> None:
        """Load custom role definitions from JSON files."""
        for json_file in roles_dir.glob("*.json"):
            try:
                data = json.loads(json_file.read_text())
                role = RoleDefinition.from_dict(data)
                self._roles[role.id] = role
                print(f"[roles] Loaded custom role: {role.id}")
            except Exception as e:
                print(f"[roles] Error loading {json_file}: {e}")

    # -------------------- Role Assignment --------------------

    def set_role(self, user_id: str, role_id: str) -> bool:
        """Set the active role for a user.

        Args:
            user_id: User identifier
            role_id: Role to activate

        Returns:
            True if role was set successfully
        """
        if role_id not in self._roles:
            return False
        self._assignments[user_id] = role_id
        return True

    def set_channel_role(self, channel_id: str, role_id: str) -> bool:
        """Set the default role for a channel.

        Args:
            channel_id: Channel identifier
            role_id: Role to set as default

        Returns:
            True if role was set successfully
        """
        if role_id not in self._roles:
            return False
        self._channel_assignments[channel_id] = role_id
        return True

    def get_role(self, user_id: str, channel_id: str | None = None) -> str:
        """Get the active role for a user.

        Priority:
        1. User-specific assignment
        2. Channel default
        3. Global default

        Args:
            user_id: User identifier
            channel_id: Optional channel for fallback

        Returns:
            Role ID
        """
        # Check user-specific assignment
        if user_id in self._assignments:
            return self._assignments[user_id]

        # Check channel default
        if channel_id and channel_id in self._channel_assignments:
            return self._channel_assignments[channel_id]

        return self._default_role

    def clear_role(self, user_id: str) -> None:
        """Clear user's role assignment (revert to default)."""
        if user_id in self._assignments:
            del self._assignments[user_id]

    def clear_channel_role(self, channel_id: str) -> None:
        """Clear channel's role assignment."""
        if channel_id in self._channel_assignments:
            del self._channel_assignments[channel_id]

    # -------------------- Role Information --------------------

    def get_role_definition(self, role_id: str) -> RoleDefinition | None:
        """Get a role definition by ID."""
        return self._roles.get(role_id)

    def get_role_prompt(self, user_id: str, channel_id: str | None = None) -> str:
        """Get the prompt modifier for a user's active role.

        Args:
            user_id: User identifier
            channel_id: Optional channel for fallback

        Returns:
            Role-specific prompt modifier text
        """
        role_id = self.get_role(user_id, channel_id)
        role = self._roles.get(role_id)
        if role:
            return role.prompt_modifier
        return ""

    def get_tool_preferences(
        self, user_id: str, channel_id: str | None = None
    ) -> list[str]:
        """Get tool preferences for a user's active role."""
        role_id = self.get_role(user_id, channel_id)
        role = self._roles.get(role_id)
        if role:
            return role.tool_preferences
        return []

    def list_roles(self) -> list[dict[str, str]]:
        """List all available roles.

        Returns:
            List of {id, name, description} dicts
        """
        return [
            {
                "id": role.id,
                "name": role.name,
                "description": role.description,
            }
            for role in self._roles.values()
        ]

    def get_role_info(self, role_id: str) -> dict[str, Any] | None:
        """Get full information about a role."""
        role = self._roles.get(role_id)
        if role:
            return role.to_dict()
        return None

    # -------------------- Custom Roles --------------------

    def add_role(self, role: RoleDefinition) -> None:
        """Add or update a role definition."""
        self._roles[role.id] = role

    def remove_role(self, role_id: str) -> bool:
        """Remove a custom role (cannot remove builtin roles).

        Returns:
            True if removed, False if not found or is builtin
        """
        if role_id in BUILTIN_ROLES:
            return False
        if role_id in self._roles:
            del self._roles[role_id]
            return True
        return False


# Module-level convenience function
def get_role_manager() -> RoleManager:
    """Get the role manager singleton."""
    from clarissa_core.config import get_config

    config = get_config()
    roles_dir = Path(config.role_config_dir)
    return RoleManager.get_instance(roles_dir=roles_dir if roles_dir.exists() else None)
