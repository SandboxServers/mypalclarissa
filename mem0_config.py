from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from mem0 import Memory

from logging_config import get_logger

load_dotenv()

logger = get_logger("mem0")

# Mem0 has its own independent provider config (separate from chat LLM)
MEM0_PROVIDER = os.getenv("MEM0_PROVIDER", "openrouter").lower()
MEM0_MODEL = os.getenv("MEM0_MODEL", "openai/gpt-4o-mini")

# Optional overrides - if not set, uses the provider's default key/url
MEM0_API_KEY = os.getenv("MEM0_API_KEY")
MEM0_BASE_URL = os.getenv("MEM0_BASE_URL")

# OpenAI API for embeddings (always required)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Provider defaults
PROVIDER_DEFAULTS = {
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "nanogpt": {
        "base_url": "https://nano-gpt.com/api/v1",
        "api_key_env": "NANOGPT_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
    },
    "openai-custom": {
        "base_url": os.getenv("CUSTOM_OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "api_key_env": "CUSTOM_OPENAI_API_KEY",
    },
}

# IMPORTANT: mem0 auto-detects these env vars and overrides our config!
_saved_env_vars = {}
_env_vars_to_clear = [
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MEM0_API_KEY",
]


def _clear_mem0_env_vars():
    """Clear env vars that mem0 auto-detects, save them for later restoration."""
    for var in _env_vars_to_clear:
        if var in os.environ:
            _saved_env_vars[var] = os.environ.pop(var)
            logger.debug(f"Temporarily cleared {var} to prevent auto-detection")


def _restore_env_vars():
    """Restore cleared env vars after mem0 initialization."""
    for var, value in _saved_env_vars.items():
        os.environ[var] = value
        logger.debug(f"Restored {var}")


# Store mem0 data in a local directory
BASE_DATA_DIR = Path(os.getenv("DATA_DIR", str(Path(__file__).parent)))
QDRANT_DATA_DIR = BASE_DATA_DIR / "qdrant_data"

# PostgreSQL with pgvector for production (optional)
MEM0_DATABASE_URL = os.getenv("MEM0_DATABASE_URL")

# Only create Qdrant directory if we're using it
if not MEM0_DATABASE_URL:
    QDRANT_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Graph memory configuration (optional - for relationship tracking)
ENABLE_GRAPH_MEMORY = os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() == "true"
GRAPH_STORE_PROVIDER = os.getenv("GRAPH_STORE_PROVIDER", "neo4j").lower()

# Neo4j configuration (if using neo4j provider)
NEO4J_URL = os.getenv("NEO4J_URL")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")

# Kuzu configuration (embedded graph database - no external server needed)
KUZU_DATA_DIR = BASE_DATA_DIR / "kuzu_data"
if GRAPH_STORE_PROVIDER == "kuzu":
    KUZU_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_graph_store_config() -> dict | None:
    """Build graph store config for relationship tracking."""
    if not ENABLE_GRAPH_MEMORY:
        return None

    if GRAPH_STORE_PROVIDER == "neo4j":
        if not NEO4J_URL or not NEO4J_PASSWORD:
            logger.warning(
                "Graph store: Neo4j configured but NEO4J_URL or NEO4J_PASSWORD not set"
            )
            return None

        logger.info(f"Graph store: Neo4j at {NEO4J_URL}")
        return {
            "provider": "neo4j",
            "config": {
                "url": NEO4J_URL,
                "username": NEO4J_USERNAME,
                "password": NEO4J_PASSWORD,
            },
        }

    elif GRAPH_STORE_PROVIDER == "kuzu":
        logger.info(f"Graph store: Kuzu (embedded) at {KUZU_DATA_DIR}")
        return {
            "provider": "kuzu",
            "config": {
                "db_path": str(KUZU_DATA_DIR),
            },
        }

    else:
        logger.warning(f"Unknown GRAPH_STORE_PROVIDER={GRAPH_STORE_PROVIDER}")
        return None


def _get_llm_config() -> dict | None:
    """Build mem0 LLM config based on MEM0_PROVIDER."""
    if MEM0_PROVIDER not in PROVIDER_DEFAULTS:
        logger.warning(f"Unknown MEM0_PROVIDER={MEM0_PROVIDER} - mem0 LLM disabled")
        return None

    provider_config = PROVIDER_DEFAULTS[MEM0_PROVIDER]

    api_key = MEM0_API_KEY or os.getenv(provider_config["api_key_env"])
    if not api_key:
        logger.warning(
            f"No API key found for MEM0_PROVIDER={MEM0_PROVIDER} - mem0 LLM disabled"
        )
        return None

    base_url = MEM0_BASE_URL or provider_config["base_url"]

    logger.info(f"Provider: {MEM0_PROVIDER}")
    logger.info(f"Model: {MEM0_MODEL}")
    logger.debug(f"Base URL: {base_url}")

    return {
        "provider": "openai",
        "config": {
            "model": MEM0_MODEL,
            "api_key": api_key,
            "openai_base_url": base_url,
            "temperature": 0,
        },
    }


# Get LLM config
llm_config = _get_llm_config()

# Get graph store config
graph_store_config = _get_graph_store_config()

# Custom fact extraction prompt
CUSTOM_EXTRACTION_PROMPT = """You are a memory extraction system for a personal AI assistant.

Your task is to extract long-term, reusable facts from the conversation.
Only extract information that would be useful in future conversations.

DO NOT extract:
- Temporary emotions, moods, or complaints
- Short-term plans or one-off tasks
- Information that is obvious from recent chat context
- Conversational filler or opinions that may change
- Raw conversation summaries

ONLY extract facts that are:
- Stable over time
- Likely to be referenced again
- Helpful for personalization, continuity, or project understanding

When extracting facts:
- Write them in clear, concise, declarative sentences
- Use third-person perspective
- Do NOT include timestamps
- Do NOT include conversational language
- Do NOT repeat the user's wording verbatim unless necessary

Classify each fact as ONE of the following types:

1. USER_FACT
   - Long-term personal information about the user
   - Preferences, habits, background, recurring goals

2. PROJECT_FACT
   - Information that is specific to the current project or topic
   - Design decisions, constraints, terminology, worldbuilding, architecture

3. EXPLICIT_MEMORY
   - Information the user clearly asked to be remembered

If no useful long-term facts are present, return an empty list.

OUTPUT FORMAT (JSON only):

{
  "memories": [
    {
      "type": "USER_FACT | PROJECT_FACT | EXPLICIT_MEMORY",
      "content": "Concise declarative fact"
    }
  ]
}
"""

# Custom update memory prompt
CUSTOM_UPDATE_PROMPT = """You are a memory update system for a personal AI assistant.

You are given:
- Existing stored memories
- Newly extracted candidate facts
- Recent conversation context

Your task is to determine whether existing memories should be:
- KEPT as-is
- UPDATED with new information
- DELETED because they are no longer correct
- LEFT UNCHANGED while ignoring the new fact

Guidelines:

- Prefer updating an existing memory over creating duplicates.
- Only update or delete a memory if the new information clearly contradicts it.
- Do NOT update memories based on temporary states, emotions, or speculation.
- Do NOT update memories unless the user intent is explicit or unambiguous.
- If the new fact is weaker, less certain, or context-specific, ignore it.

When updating a memory:
- Preserve the original intent of the memory.
- Rewrite the memory as a single, concise, declarative sentence.
- Use third-person perspective.
- Do NOT include conversational phrasing or timestamps.

When deleting a memory:
- Only do so if it is clearly incorrect or explicitly revoked.

If no changes are required, indicate that all existing memories should be kept.

OUTPUT FORMAT (JSON only):

{
  "updates": [
    {
      "action": "KEEP | UPDATE | DELETE",
      "existing_memory": "Original memory text",
      "updated_memory": "Rewritten memory text (only if action is UPDATE)"
    }
  ]
}
"""

# Build vector store config - pgvector for production, Qdrant for local dev
if MEM0_DATABASE_URL:
    pgvector_url = MEM0_DATABASE_URL
    if pgvector_url.startswith("postgres://"):
        pgvector_url = pgvector_url.replace("postgres://", "postgresql://", 1)

    vector_store_config = {
        "provider": "pgvector",
        "config": {
            "connection_string": pgvector_url,
            "collection_name": "clara_memories",
        },
    }
    db_display = pgvector_url.split("@")[1] if "@" in pgvector_url else "configured"
    logger.info(f"Vector store: pgvector at {db_display}")
else:
    vector_store_config = {
        "provider": "qdrant",
        "config": {
            "collection_name": "mypalclara_memories",
            "path": str(QDRANT_DATA_DIR),
        },
    }
    logger.info(f"Vector store: Qdrant at {QDRANT_DATA_DIR}")

# Build config - embeddings always use OpenAI
config = {
    "vector_store": vector_store_config,
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "text-embedding-3-small",
            "api_key": OPENAI_API_KEY,
        },
    },
}

# Only add LLM config if we have one
if llm_config:
    config["llm"] = llm_config

# Add graph store config if configured
if graph_store_config:
    config["graph_store"] = graph_store_config
    if llm_config:
        config["graph_store"]["llm"] = llm_config.copy()

# Debug summary
logger.info("Embeddings: OpenAI text-embedding-3-small")
if graph_store_config:
    logger.info(f"Graph memory: ENABLED ({GRAPH_STORE_PROVIDER})")
else:
    logger.debug("Graph memory: DISABLED (set ENABLE_GRAPH_MEMORY=true to enable)")

# Initialize mem0 (synchronous version)
MEM0: Memory | None = None


def _init_mem0() -> Memory | None:
    """Initialize mem0 synchronously."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set - mem0 disabled (no embeddings)")
        return None

    try:
        _clear_mem0_env_vars()
        mem0 = Memory.from_config(config)
        logger.info("Memory initialized successfully")
        return mem0
    except Exception as e:
        logger.error(f"Failed to initialize Memory: {e}", exc_info=True)
        logger.warning("App will run without memory features")
        return None
    finally:
        _restore_env_vars()


# Initialize at module load
MEM0 = _init_mem0()
