"""Sandbox management for Clarissa's code execution.

Provides a singleton sandbox instance that can use either:
- Bubblewrap (lightweight Linux sandboxing)
- Docker (traditional containerization)

v0.8 Feature: Bubblewrap sandbox for lighter, faster code execution
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarissa_core.sandbox.bubblewrap import BubblewrapSandbox

_sandbox: BubblewrapSandbox | None = None


def get_sandbox() -> BubblewrapSandbox:
    """Get the singleton sandbox instance.

    Returns the bubblewrap sandbox instance, creating it if needed.
    Falls back to Docker sandbox if bubblewrap is not available.
    """
    global _sandbox

    if _sandbox is None:
        from clarissa_core.config import get_config
        from clarissa_core.sandbox.bubblewrap import BubblewrapSandbox

        config = get_config()
        _sandbox = BubblewrapSandbox(
            work_dir=config.sandbox_work_dir,
            timeout=config.sandbox_timeout,
            allow_network=config.sandbox_network,
        )

    return _sandbox


def reset_sandbox() -> None:
    """Reset the sandbox singleton (for testing)."""
    global _sandbox
    _sandbox = None


__all__ = ["get_sandbox", "reset_sandbox"]
