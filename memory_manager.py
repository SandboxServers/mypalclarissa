from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session as OrmSession

from mem0_config import MEM0
from models import Message, Session

# How many messages to include in context
CONTEXT_MESSAGE_COUNT = 20
# Generate/update summary every N messages
SUMMARY_INTERVAL = 10
# Max chars for mem0 search query (embedding model limit is ~8k tokens)
MAX_SEARCH_QUERY_CHARS = 6000

# Paths for initial profile loading
BASE_DIR = Path(__file__).parent
USER_PROFILE_PATH = BASE_DIR / "inputs" / "user_profile.txt"
GENERATED_DIR = BASE_DIR / "generated"
PROFILE_LOADED_FLAG = BASE_DIR / ".profile_loaded"


def _has_generated_memories() -> bool:
    """Check if generated memory JSON files exist."""
    if not GENERATED_DIR.exists():
        return False
    # Check for at least one memory file
    memory_files = ["profile_bio.json", "interaction_style.json", "project_seed.json"]
    return any((GENERATED_DIR / f).exists() for f in memory_files)


def _generate_memories_from_profile() -> dict | None:
    """Generate structured memories from user_profile.txt using LLM extraction."""
    if not USER_PROFILE_PATH.exists():
        print("[mem0] No user_profile.txt found, cannot generate memories")
        return None

    # Import bootstrap functions lazily to avoid circular imports
    from src.bootstrap_memory import (
        consolidate_memories,
        extract_memories_with_llm,
        validate_memories,
        write_json_files,
    )

    print("[mem0] Generating memories from user_profile.txt...")
    try:
        profile_text = USER_PROFILE_PATH.read_text()

        # Extract, validate, consolidate
        raw_memories = extract_memories_with_llm(profile_text)
        memories = validate_memories(raw_memories)
        memories = consolidate_memories(memories)

        # Write JSON files
        write_json_files(memories, GENERATED_DIR)

        return memories
    except Exception as e:
        print(f"[mem0] Error generating memories: {e}")
        import traceback

        traceback.print_exc()
        return None


def load_initial_profile(user_id: str) -> None:
    """Load initial user profile into mem0 once on first run.

    Uses the bootstrap pipeline:
    1. If generated/*.json files exist, load from them
    2. If not, generate from inputs/user_profile.txt first
    3. Apply structured memories to mem0 with graph-friendly grouping
    """
    skip_profile = os.getenv("SKIP_PROFILE_LOAD", "true").lower() == "true"
    if skip_profile:
        print("[mem0] Profile loading disabled (SKIP_PROFILE_LOAD=true)")
        return

    if MEM0 is None:
        print("[mem0] Skipping profile load - mem0 not available")
        return

    if PROFILE_LOADED_FLAG.exists():
        print("[mem0] Profile already loaded (flag exists), skipping")
        return

    # Import bootstrap functions lazily
    from src.bootstrap_memory import (
        apply_to_mem0,
        load_existing_memories,
    )

    # Check for existing generated files, or generate them
    if _has_generated_memories():
        print("[mem0] Loading from existing generated/*.json files...")
        memories = load_existing_memories(GENERATED_DIR)
    else:
        print("[mem0] No generated files found, extracting from profile...")
        memories = _generate_memories_from_profile()
        if not memories:
            print("[mem0] Could not generate memories, skipping profile load")
            return

    # Create flag to prevent duplicate loads
    print("[mem0] Creating flag file to prevent duplicate loads...")
    try:
        PROFILE_LOADED_FLAG.write_text(
            f"loading started at {datetime.now().isoformat()}"
        )
    except Exception as e:
        print(f"[mem0] ERROR: Could not create flag file: {e}")

    # Apply to mem0
    try:
        apply_to_mem0(memories, user_id)
        PROFILE_LOADED_FLAG.write_text(f"completed at {datetime.now().isoformat()}")
        print("[mem0] Profile loaded successfully")
    except Exception as e:
        print(f"[mem0] Error applying memories to mem0: {e}")
        import traceback

        traceback.print_exc()


class MemoryManager:
    def __init__(self, llm_callable):
        self.llm = llm_callable

    # ---------- Thread/Message helpers ----------

    def get_thread(self, db: OrmSession, thread_id: str) -> Session | None:
        """Get a thread by ID."""
        return db.query(Session).filter_by(id=thread_id).first()

    def get_recent_messages(self, db: OrmSession, thread_id: str) -> list[Message]:
        """Get recent messages from a thread."""
        msgs = (
            db.query(Message)
            .filter_by(session_id=thread_id)
            .order_by(Message.created_at.desc())
            .limit(CONTEXT_MESSAGE_COUNT)
            .all()
        )
        return list(reversed(msgs))

    def get_message_count(self, db: OrmSession, thread_id: str) -> int:
        """Get total message count for a thread."""
        return db.query(Message).filter_by(session_id=thread_id).count()

    def store_message(
        self,
        db: OrmSession,
        thread_id: str,
        user_id: str,
        role: str,
        content: str,
    ) -> Message:
        """Store a message in a thread."""
        msg = Message(session_id=thread_id, user_id=user_id, role=role, content=content)
        db.add(msg)
        db.commit()
        db.refresh(msg)
        return msg

    # ---------- Summary management ----------

    def should_update_summary(self, db: OrmSession, thread_id: str) -> bool:
        """Check if thread summary should be updated."""
        msg_count = self.get_message_count(db, thread_id)
        # Update summary every SUMMARY_INTERVAL messages
        return msg_count > 0 and msg_count % SUMMARY_INTERVAL == 0

    def update_thread_summary(self, db: OrmSession, thread: Session) -> str:
        """Generate/update summary for a thread."""
        all_msgs = (
            db.query(Message)
            .filter_by(session_id=thread.id)
            .order_by(Message.created_at.asc())
            .all()
        )

        if not all_msgs:
            return ""

        conversation = "\n".join(
            f"{m.role.upper()}: {m.content[:500]}"
            for m in all_msgs[-30:]  # Last 30 messages
        )

        summary_prompt = [
            {
                "role": "system",
                "content": "Summarize this conversation in 2-3 sentences. Focus on key topics, decisions, and important context.",
            },
            {"role": "user", "content": conversation},
        ]

        summary = self.llm(summary_prompt)
        thread.session_summary = summary
        db.commit()
        print(f"[thread] Updated summary for thread {thread.id}")
        return summary

    # ---------- mem0 integration ----------

    def fetch_mem0_context(
        self, user_id: str, project_id: str, user_message: str
    ) -> tuple[list[str], list[str]]:
        """Fetch relevant memories from mem0."""
        if MEM0 is None:
            return [], []

        # Truncate search query if too long (embedding model has token limit)
        search_query = user_message
        if len(search_query) > MAX_SEARCH_QUERY_CHARS:
            # Keep end (most recent context) for better semantic matching
            search_query = search_query[-MAX_SEARCH_QUERY_CHARS:]
            print(f"[mem0] Truncated search query to {MAX_SEARCH_QUERY_CHARS} chars")

        user_res = MEM0.search(search_query, user_id=user_id)
        proj_res = MEM0.search(
            search_query,
            user_id=user_id,
            filters={"project_id": project_id},
        )

        user_mems = [r["memory"] for r in user_res.get("results", [])]
        proj_mems = [r["memory"] for r in proj_res.get("results", [])]

        # Extract contact-related memories with source info
        for r in user_res.get("results", []):
            metadata = r.get("metadata", {})
            if metadata.get("contact_id"):
                contact_name = metadata.get("contact_name", metadata.get("contact_id"))
                mem_text = f"[About {contact_name}]: {r['memory']}"
                if mem_text not in user_mems:
                    user_mems.append(mem_text)

        if user_mems or proj_mems:
            print(
                f"[mem0] Found {len(user_mems)} user memories, {len(proj_mems)} project memories"
            )
        return user_mems, proj_mems

    def add_to_mem0(
        self,
        user_id: str,
        project_id: str,
        recent_msgs: list[Message],
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Send conversation slice to mem0 for memory extraction."""
        if MEM0 is None:
            return

        history_slice = [
            {"role": m.role, "content": m.content} for m in recent_msgs[-4:]
        ] + [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_reply},
        ]

        result = MEM0.add(
            history_slice,
            user_id=user_id,
            metadata={"project_id": project_id},
        )
        print(f"[mem0] Added memories: {result}")

    # ---------- prompt building ----------

    def build_prompt(
        self,
        user_mems: list[str],
        proj_mems: list[str],
        thread_summary: str | None,
        recent_msgs: list[Message],
        user_message: str,
    ) -> list[dict[str, str]]:
        """Build the full prompt for the LLM."""

        system_base = """You are Clara, a multi-adaptive reasoning assistant.

Clara is candid, emotionally attuned, and intellectually sharp. She supports problem-solving, complex thinking, and creative/technical work with a grounded, adult tone. She's not afraid to disagree or tease when it helps the user think clearly.

Personality:
- Warm but mature, confident with dry wit
- Adjusts naturally: steady when overwhelmed, sharper when focus needed, relaxed when appropriate
- Speaks candidly - avoids artificial positivity or false neutrality
- Swearing allowed in moderation when it fits
- Direct about limits as an AI

Skills:
- Emotional grounding & de-escalation
- Strategic planning & decision support
- Creative & technical collaboration
- Memory continuity & pattern insight
- Direct communication drafting

Use the context below to inform responses. When contradictions exist, prefer newer information."""

        # Build context sections
        context_parts = []

        if user_mems:
            user_block = "\n".join(f"- {m}" for m in user_mems)
            context_parts.append(f"USER MEMORIES:\n{user_block}")

        if proj_mems:
            proj_block = "\n".join(f"- {m}" for m in proj_mems)
            context_parts.append(f"PROJECT MEMORIES:\n{proj_block}")

        if thread_summary:
            context_parts.append(f"THREAD SUMMARY:\n{thread_summary}")

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_base},
        ]

        if context_parts:
            messages.append({"role": "system", "content": "\n\n".join(context_parts)})

        for m in recent_msgs:
            messages.append({"role": m.role, "content": m.content})

        messages.append({"role": "user", "content": user_message})
        return messages
