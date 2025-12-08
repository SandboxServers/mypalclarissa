from __future__ import annotations

import os
from typing import Callable, List, Dict, Generator, AsyncGenerator

from openai import OpenAI

# Global client for reuse
_openrouter_client: OpenAI = None


def _get_openrouter_client() -> OpenAI:
    """Get or create OpenRouter client."""
    global _openrouter_client
    if _openrouter_client is None:
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")

        site = os.getenv("OPENROUTER_SITE", "http://localhost:3000")
        title = os.getenv("OPENROUTER_TITLE", "Mara Assistant")

        _openrouter_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            default_headers={
                "HTTP-Referer": site,
                "X-Title": title,
            },
        )
    return _openrouter_client


def make_llm() -> Callable[[List[Dict[str, str]]], str]:
    """
    Return a function(messages) -> assistant_reply string.

    Select backend with env var LLM_PROVIDER:
      - "openrouter" (default)
      - "huggingface"
    """
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return _make_openrouter_llm()
    elif provider == "huggingface":
        return _make_hf_llm()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER={provider}")


def _make_openrouter_llm() -> Callable[[List[Dict[str, str]]], str]:
    """Non-streaming OpenRouter LLM."""
    client = _get_openrouter_client()
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    def llm(messages: List[Dict[str, str]]) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        return resp.choices[0].message.content

    return llm


def _make_hf_llm() -> Callable[[List[Dict[str, str]]], str]:
    """Non-streaming HuggingFace LLM."""
    from huggingface_hub import InferenceClient

    token = os.getenv("HF_API_TOKEN")
    if not token:
        raise RuntimeError("HF_API_TOKEN is not set")

    model = os.getenv("HF_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct")
    client = InferenceClient(token=token, model=model)

    def llm(messages: List[Dict[str, str]]) -> str:
        resp = client.chat_completion(messages=messages, max_tokens=512, stream=False)
        return resp.choices[0].message.content

    return llm


def make_llm_streaming() -> Callable[[List[Dict[str, str]]], Generator[str, None, None]]:
    """Return a streaming LLM function that yields chunks."""
    provider = os.getenv("LLM_PROVIDER", "openrouter").lower()

    if provider == "openrouter":
        return _make_openrouter_llm_streaming()
    else:
        raise ValueError(f"Streaming not supported for LLM_PROVIDER={provider}")


def _make_openrouter_llm_streaming() -> Callable[[List[Dict[str, str]]], Generator[str, None, None]]:
    """Streaming OpenRouter LLM."""
    client = _get_openrouter_client()
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    def llm(messages: List[Dict[str, str]]) -> Generator[str, None, None]:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    return llm
