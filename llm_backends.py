from __future__ import annotations

import os
from collections.abc import Callable, Generator

from openai import OpenAI
from openai.types.chat import ChatCompletion

# Global clients for reuse
_openrouter_client: OpenAI = None
_nanogpt_client: OpenAI = None
_custom_openai_client: OpenAI = None
_openai_tool_client: OpenAI = None  # Dedicated client for tool calling
_fast_llm_client: OpenAI = None  # Fast model for quick decisions


def _get_openrouter_client() -> OpenAI:
    """Get or create OpenRouter client."""
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
        title = os.getenv("OPENROUTER_TITLE", "MyPalClara")

        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": site,
                "X-Title": title,
            },
        )
    return _openrouter_client


def _get_nanogpt_client() -> OpenAI:
    """Get or create NanoGPT client."""
    global _nanogpt_client
    if _nanogpt_client is None:
        api_key = os.getenv("NANOGPT_API_KEY")
        if not api_key:
            raise RuntimeError("NANOGPT_API_KEY is not set")

        _nanogpt_client = OpenAI(
            base_url="https://nano-gpt.com/api/v1",
            api_key=api_key,
        )
    return _nanogpt_client


def _get_custom_openai_client() -> OpenAI:
    """Get or create custom OpenAI-compatible client."""
    global _custom_openai_client
    if _custom_openai_client is None:
        api_key = os.getenv("CUSTOM_OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("CUSTOM_OPENAI_API_KEY is not set")

        base_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")

        _custom_openai_client = OpenAI(
            base_url=base_url,
            api_key=api_key,
        )
    return _custom_openai_client


def _get_openai_tool_client() -> OpenAI:
    """Get or create dedicated client for tool calling.

    By default, uses the same endpoint as the main chat LLM (based on LLM_PROVIDER).
    Can be overridden with explicit TOOL_* environment variables.

    Configurable via environment variables:
    - TOOL_API_KEY: API key for tool calls (defaults to main LLM's key)
    - TOOL_BASE_URL: Base URL for tool calls (defaults to main LLM's URL)
    - TOOL_MODEL: Model to use (defaults to main LLM's model)
    """
    global _openai_tool_client
    if _openai_tool_client is None:
        provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

        # Determine defaults based on main LLM provider
        if provider == "openai":
            # Custom OpenAI endpoint (e.g., clewdr)
            default_key = os.getenv("CUSTOM_OPENAI_API_KEY")
            default_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        elif provider == "nanogpt":
            default_key = os.getenv("NANOGPT_API_KEY")
            default_url = "https://nano-gpt.com/api/v1"
        else:  # openrouter
            default_key = os.getenv("OPENROUTER_API_KEY")
            default_url = "https://openrouter.ai/api/v1"

        # Use explicit TOOL_* config or fall back to main LLM config
        api_key = os.getenv("TOOL_API_KEY") or default_key
        base_url = os.getenv("TOOL_BASE_URL") or default_url

        if not api_key:
            raise RuntimeError(
                "No API key found for tool calling. Set TOOL_API_KEY or configure your main LLM provider."
            )

        # Build client config
        client_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }

        # Add OpenRouter headers if using OpenRouter
        if "openrouter.ai" in base_url:
            site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
            title = os.getenv("OPENROUTER_TITLE", "MyPalClara")
            client_kwargs["default_headers"] = {
                "HTTP-Referer": site,
                "X-Title": title,
            }

        _openai_tool_client = OpenAI(**client_kwargs)
    return _openai_tool_client


def _get_fast_llm_client() -> OpenAI:
    """Get or create fast LLM client for quick decisions.

    Uses a fast, cheap model for rapid evaluations (e.g., organic response decisions).
    Configurable via environment variables:
    - FAST_LLM_PROVIDER: "openrouter" (default), "nanogpt", or "openai"
    - FAST_LLM_API_KEY: API key (defaults to provider's key)
    - FAST_LLM_BASE_URL: Base URL (defaults to provider's URL)
    - FAST_LLM_MODEL: Model to use (default: openai/gpt-4o-mini for openrouter)
    """
    global _fast_llm_client
    if _fast_llm_client is None:
        provider = os.getenv("FAST_LLM_PROVIDER", "openrouter").lower()

        # Determine defaults based on provider
        if provider == "openai":
            default_key = os.getenv("CUSTOM_OPENAI_API_KEY")
            default_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
        elif provider == "nanogpt":
            default_key = os.getenv("NANOGPT_API_KEY")
            default_url = "https://nano-gpt.com/api/v1"
        else:  # openrouter
            default_key = os.getenv("OPENROUTER_API_KEY")
            default_url = "https://openrouter.ai/api/v1"

        api_key = os.getenv("FAST_LLM_API_KEY") or default_key
        base_url = os.getenv("FAST_LLM_BASE_URL") or default_url

        if not api_key:
            raise RuntimeError(
                "No API key found for fast LLM. Set FAST_LLM_API_KEY or configure your provider."
            )

        client_kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }

        # Add OpenRouter headers if using OpenRouter
        if "openrouter.ai" in base_url:
            site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
            title = os.getenv("OPENROUTER_TITLE", "MyPalClara")
            client_kwargs["default_headers"] = {
                "HTTP-Referer": site,
                "X-Title": title,
            }

        _fast_llm_client = OpenAI(**client_kwargs)
    return _fast_llm_client


def _get_fast_llm_model() -> str:
    """Get the model to use for fast LLM calls."""
    if model := os.getenv("FAST_LLM_MODEL"):
        return model

    # Default to a fast, cheap model based on provider
    provider = os.getenv("FAST_LLM_PROVIDER", "openrouter").lower()
    if provider == "openai":
        return "gpt-4o-mini"
    elif provider == "nanogpt":
        return "openai/gpt-4o-mini"
    else:  # openrouter
        return "openai/gpt-4o-mini"


def make_fast_llm() -> Callable[[list[dict[str, str]]], str]:
    """Return a fast LLM function for quick decisions.

    Uses a lightweight model (default: gpt-4o-mini) for rapid evaluations
    like organic response decisions. Typically 100-300ms response time.
    """
    client = _get_fast_llm_client()
    model = _get_fast_llm_model()
    provider = os.getenv("FAST_LLM_PROVIDER", "openrouter").lower()
    print(f"[llm] Fast LLM: {model} via {provider}")

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,  # Deterministic for consistent decisions
            max_tokens=200,  # Short responses for decisions
        )
        return resp.choices[0].message.content

    return llm


def make_llm() -> Callable[[list[dict[str, str]]], str]:
    """
    Return a function(messages) -> assistant_reply string.

    Select backend with env var LLM_PROVIDER:
      - "openrouter" (default)
      - "nanogpt"
      - "openai" (custom OpenAI-compatible endpoint)
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return _make_openrouter_llm()
    elif provider == "nanogpt":
        return _make_nanogpt_llm()
    elif provider == "openai":
        return _make_custom_openai_llm()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={provider}")


def _make_openrouter_llm() -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming OpenRouter LLM."""
    client = _get_openrouter_client()
    model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")
    print(f"[llm] Main LLM: {model} via openrouter")

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content

    return llm


def _make_nanogpt_llm() -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming NanoGPT LLM."""
    client = _get_nanogpt_client()
    model = os.getenv("NANOGPT_MODEL", "moonshotai/Kimi-K2-Instruct-0905")
    print(f"[llm] Main LLM: {model} via nanogpt")

    def llm(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content

    return llm


def _make_custom_openai_llm() -> Callable[[list[dict[str, str]]], str]:
    """Non-streaming custom OpenAI-compatible LLM."""
    client = _get_custom_openai_client()
    model = os.getenv("CUSTOM_OPENAI_MODEL", "gpt-4o")
    base_url = os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1")
    use_claude_format = os.getenv("TOOL_FORMAT", "openai").lower() == "claude"
    print(f"[llm] Main LLM: {model} @ {base_url} (claude_format={use_claude_format})")

    def llm(messages: list[dict[str, str]]) -> str:
        # Convert to Claude format if using a Claude proxy (e.g., clewdr)
        if use_claude_format:
            converted = _convert_messages_to_claude_format(messages)
        else:
            converted = messages

        resp = client.chat.completions.create(
            model=model,
            messages=converted,
        )
        return resp.choices[0].message.content

    return llm


def make_llm_streaming() -> (
    Callable[[list[dict[str, str]]], Generator[str, None, None]]
):
    """Return a streaming LLM function that yields chunks."""
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return _make_openrouter_llm_streaming()
    elif provider == "nanogpt":
        return _make_nanogpt_llm_streaming()
    elif provider == "openai":
        return _make_custom_openai_llm_streaming()
    else:
        raise ValueError(f"Streaming not supported for LLM_PROVIDER={provider}")


def _make_openrouter_llm_streaming() -> (
    Callable[[list[dict[str, str]]], Generator[str, None, None]]
):
    """Streaming OpenRouter LLM."""
    client = _get_openrouter_client()
    model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


def _make_nanogpt_llm_streaming() -> (
    Callable[[list[dict[str, str]]], Generator[str, None, None]]
):
    """Streaming NanoGPT LLM."""
    client = _get_nanogpt_client()
    model = os.getenv("NANOGPT_MODEL", "moonshotai/Kimi-K2-Instruct-0905")

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


def _make_custom_openai_llm_streaming() -> (
    Callable[[list[dict[str, str]]], Generator[str, None, None]]
):
    """Streaming custom OpenAI-compatible LLM."""
    client = _get_custom_openai_client()
    model = os.getenv("CUSTOM_OPENAI_MODEL", "gpt-4o")
    use_claude_format = os.getenv("TOOL_FORMAT", "openai").lower() == "claude"

    def llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
        # Convert to Claude format if using a Claude proxy (e.g., clewdr)
        if use_claude_format:
            converted = _convert_messages_to_claude_format(messages)
        else:
            converted = messages

        stream = client.chat.completions.create(
            model=model,
            messages=converted,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm


# ============== Tool Calling Support ==============


def _convert_tools_to_claude_format(tools: list[dict]) -> list[dict]:
    """Convert OpenAI-format tools to Claude format.

    OpenAI: {"type": "function", "function": {"name": ..., "parameters": ...}}
    Claude: {"name": ..., "input_schema": ...}
    """
    claude_tools = []
    for tool in tools:
        if tool.get("type") == "function" and "function" in tool:
            func = tool["function"]
            claude_tools.append({
                "name": func.get("name"),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            })
        else:
            # Already in a different format, pass through
            claude_tools.append(tool)
    return claude_tools


def _convert_content_to_claude_format(content) -> list[dict] | str:
    """Convert OpenAI vision content format to Claude format.

    OpenAI: [{"type": "text", "text": "..."}, {"type": "image_url", "image_url": {"url": "..."}}]
    Claude: [{"type": "text", "text": "..."}, {"type": "image", "source": {"type": "url", "url": "..."}}]
    """
    if not isinstance(content, list):
        return content  # Plain string, no conversion needed

    claude_content = []
    for block in content:
        if block.get("type") == "image_url":
            # Convert OpenAI image_url to Claude image format
            url = block.get("image_url", {}).get("url", "")
            claude_content.append({
                "type": "image",
                "source": {
                    "type": "url",
                    "url": url,
                },
            })
        elif block.get("type") == "text":
            claude_content.append(block)  # Text blocks are the same
        else:
            claude_content.append(block)  # Pass through unknown types

    return claude_content


def _convert_messages_to_claude_format(messages: list[dict]) -> list[dict]:
    """Convert OpenAI-format messages with tool calls/results to Claude format.

    Handles:
    - Assistant messages with tool_calls -> assistant with tool_use content blocks
    - Tool role messages -> user messages with tool_result content blocks
    - Vision messages with image_url -> Claude image format
    """
    claude_messages = []
    pending_tool_results = []

    for msg in messages:
        role = msg.get("role")

        if role == "tool":
            # Collect tool results to batch into a user message
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id"),
                "content": msg.get("content", ""),
            })
            continue

        # If we have pending tool results, add them as a user message first
        if pending_tool_results:
            claude_messages.append({
                "role": "user",
                "content": pending_tool_results,
            })
            pending_tool_results = []

        if role == "assistant" and msg.get("tool_calls"):
            # Convert assistant message with tool_calls to Claude format
            content_blocks = []

            # Add text content if present
            if msg.get("content"):
                content_blocks.append({
                    "type": "text",
                    "text": msg["content"],
                })

            # Add tool_use blocks
            for tc in msg["tool_calls"]:
                import json
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": tc.get("function", {}).get("name"),
                    "input": json.loads(tc.get("function", {}).get("arguments", "{}")),
                })

            claude_messages.append({
                "role": "assistant",
                "content": content_blocks,
            })
        else:
            # Convert content (handles vision format) and pass through
            converted_content = _convert_content_to_claude_format(msg.get("content", ""))
            claude_messages.append({
                **msg,
                "content": converted_content,
            })

    # Handle any remaining tool results
    if pending_tool_results:
        claude_messages.append({
            "role": "user",
            "content": pending_tool_results,
        })

    return claude_messages


def _get_tool_model() -> str:
    """Get the model to use for tool calling.

    Defaults to the main LLM's model if TOOL_MODEL is not set.
    """
    if tool_model := os.getenv("TOOL_MODEL"):
        return tool_model

    # Fall back to main LLM's model based on provider
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
    if provider == "openai":
        return os.getenv("CUSTOM_OPENAI_MODEL", "gpt-4o")
    elif provider == "nanogpt":
        return os.getenv("NANOGPT_MODEL", "moonshotai/Kimi-K2-Instruct-0905")
    else:  # openrouter
        return os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")


# Model to use for tool calling (resolved at runtime)
TOOL_MODEL = _get_tool_model()

# Tool format: "openai" (default) or "claude" (for Claude proxies like clewdr)
TOOL_FORMAT = os.getenv("TOOL_FORMAT", "openai").lower()


def make_llm_with_tools(
    tools: list[dict] | None = None,
) -> Callable[[list[dict]], ChatCompletion]:
    """
    Return a function(messages) -> ChatCompletion that supports tool calling.

    Uses the same endpoint as your main chat LLM by default.
    Set TOOL_FORMAT=claude if using a Claude proxy (like clewdr).

    The returned function takes messages and returns the full ChatCompletion
    object so the caller can handle tool_calls if present.

    Args:
        tools: List of tool definitions in OpenAI format. If None, no tools.

    Returns:
        Function that calls the LLM with tool support.
    """
    client = _get_openai_tool_client()

    def llm(messages: list[dict]) -> ChatCompletion:
        if TOOL_FORMAT == "claude":
            # Convert messages and tools to Claude format for proxies like clewdr
            converted_messages = _convert_messages_to_claude_format(messages)
            kwargs = {"model": TOOL_MODEL, "messages": converted_messages}
            if tools:
                kwargs["tools"] = _convert_tools_to_claude_format(tools)
        else:
            kwargs = {"model": TOOL_MODEL, "messages": messages}
            if tools:
                kwargs["tools"] = tools
        # Note: not setting tool_choice - "auto" is default and some proxies
        # (like clewdr) don't accept the OpenAI format for this parameter
        return client.chat.completions.create(**kwargs)

    return llm
