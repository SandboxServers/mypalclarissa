"""Faculty registry for Clarissa's action capabilities.

Faculties are registered here and can be retrieved by name.
The Command node uses get_faculty() to dispatch to the appropriate faculty.

v0.8 Feature: Faculty/Rumination architecture from Clara
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarissa_core.faculty import Faculty

# Singleton registry of all faculties
FACULTIES: dict[str, Faculty] = {}


def register_faculty(faculty: Faculty) -> None:
    """Register a faculty for use by Clarissa.

    Args:
        faculty: The faculty instance to register

    Raises:
        ValueError: If a faculty with the same name is already registered
    """
    if faculty.name in FACULTIES:
        raise ValueError(f"Faculty '{faculty.name}' is already registered")
    FACULTIES[faculty.name] = faculty
    print(f"[faculty] Registered: {faculty.name}")


def get_faculty(name: str) -> Faculty | None:
    """Get a faculty by name.

    Args:
        name: The faculty name (e.g., "github", "code", "ado")

    Returns:
        The faculty instance, or None if not found
    """
    return FACULTIES.get(name)


def list_faculties() -> list[str]:
    """List available faculty names.

    Returns:
        List of registered faculty names
    """
    return list(FACULTIES.keys())


def get_faculty_descriptions() -> list[str]:
    """Get formatted descriptions of all faculties for the rumination prompt.

    Returns:
        List of formatted faculty descriptions
    """
    return [f.get_description() for f in FACULTIES.values()]


def initialize_faculties() -> None:
    """Initialize and register all built-in faculties.

    Called during platform initialization when ENABLE_RUMINATION=true.
    """
    # Import and register faculties
    # These imports trigger registration via register_faculty()
    from clarissa_core.faculties.ado import ADOFaculty
    from clarissa_core.faculties.code import CodeFaculty
    from clarissa_core.faculties.decisions import DecisionsFaculty
    from clarissa_core.faculties.github import GitHubFaculty
    from clarissa_core.faculties.planning import PlanningFaculty

    # Register faculty instances
    register_faculty(GitHubFaculty())
    register_faculty(ADOFaculty())
    register_faculty(CodeFaculty())
    register_faculty(PlanningFaculty())
    register_faculty(DecisionsFaculty())

    print(f"[faculty] Initialized {len(FACULTIES)} faculties: {list_faculties()}")


def reset_faculties() -> None:
    """Reset the faculty registry (for testing)."""
    FACULTIES.clear()


# Export key functions
__all__ = [
    "register_faculty",
    "get_faculty",
    "list_faculties",
    "get_faculty_descriptions",
    "initialize_faculties",
    "reset_faculties",
    "FACULTIES",
]
