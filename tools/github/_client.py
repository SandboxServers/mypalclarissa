"""GitHub API client and shared utilities.

This module contains the core HTTP client, authentication, and configuration
shared by all GitHub tool modules.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_API_URL = "https://api.github.com"


def is_configured() -> bool:
    """Check if GitHub is configured."""
    return bool(GITHUB_TOKEN)


def _get_headers() -> dict[str, str]:
    """Get headers for GitHub API requests."""
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def github_request(
    method: str,
    endpoint: str,
    params: dict | None = None,
    json_data: dict | None = None,
) -> dict | list | str:
    """Make a GitHub API request.
    
    Args:
        method: HTTP method (GET, POST, PUT, PATCH, DELETE)
        endpoint: API endpoint (e.g., "/repos/{owner}/{repo}")
        params: Query parameters
        json_data: JSON body data
        
    Returns:
        Parsed JSON response or success dict for 204 responses
        
    Raises:
        ValueError: If GITHUB_TOKEN not configured or API error
    """
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN not configured")

    url = f"{GITHUB_API_URL}{endpoint}"

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            url,
            headers=_get_headers(),
            params=params,
            json=json_data,
            timeout=30.0,
        )

        if response.status_code == 204:
            return {"success": True}

        if response.status_code >= 400:
            error_msg = response.text
            try:
                error_data = response.json()
                error_msg = error_data.get("message", response.text)
            except Exception:
                pass
            raise ValueError(f"GitHub API error ({response.status_code}): {error_msg}")

        return response.json()


async def github_request_raw(
    method: str,
    endpoint: str,
    accept: str = "application/vnd.github+json",
) -> str:
    """Make a GitHub API request returning raw text (for diffs, etc).
    
    Args:
        method: HTTP method
        endpoint: API endpoint
        accept: Accept header value
        
    Returns:
        Raw response text
    """
    if not GITHUB_TOKEN:
        raise ValueError("GITHUB_TOKEN not configured")

    url = f"{GITHUB_API_URL}{endpoint}"
    headers = _get_headers()
    headers["Accept"] = accept

    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, headers=headers, timeout=30.0)
        response.raise_for_status()
        return response.text
