from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session as OrmSession

from bot_config import PERSONALITY
from logging_config import get_logger
from mem0_config import MEM0
from models import Message, Session

logger = get_logger("mem0")
thread_logger = get_logger("thread")

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
    memory_files = ["profile_bio.json", "interaction_style.json", "project_seed.json"]
    return any((GENERATED_DIR / f).exists() for f in memory_files)


def _generate_memories_from_profile() -> dict | None:
    """Generate structured memories from user_profile.txt using LLM extraction."""
    if not USER_PROFILE_PATH.exists():
        logger.warning("No user_profile.txt found, cannot generate memories")
        return None

    from src.bootstrap_memory import (
        consolidate_memories,
        extract_memories_with_llm,
        validate_memories,
        write_json_files,
    )

    logger.info("Generating memories from user_profile.txt...")
    try:
        profile_text = USER_PROFILE_PATH.read_text()

        raw_memories = extract_memories_with_llm(profile_text)
        memories = validate_memories(raw_memories)
        memories = consolidate_memories(memories)

        write_json_files(memories, GENERATED_DIR)

        return memories
    except Exception as e:
        logger.error(f"Error generating memories: {e}", exc_info=True)
        return None


def load_initial_profile(user_id: str) -> None:
    """Load initial user profile into mem0 once on first run."""
    skip_profile = os.getenv("SKIP_PROFILE_LOAD", "true").lower() == "true"
    if skip_profile:
        logger.info("Profile loading disabled (SKIP_PROFILE_LOAD=true)")
        return

    if MEM0 is None:
        logger.info("Skipping profile load - mem0 not available")
        return

    if PROFILE_LOADED_FLAG.exists():
        logger.debug("Profile already loaded (flag exists), skipping")
        return

    from src.bootstrap_memory import (
        apply_to_mem0,
        load_existing_memories,
    )

    if _has_generated_memories():
        logger.info("Loading from existing generated/*.json files...")
        memories = load_existing_memories(GENERATED_DIR)
    else:
        logger.info("No generated files found, extracting from profile...")
        memories = _generate_memories_from_profile()
        if not memories:
            logger.warning("Could not generate memories, skipping profile load")
            return

    logger.debug("Creating flag file to prevent duplicate loads...")
    try:
        PROFILE_LOADED_FLAG.write_text(
            f"loading started at {datetime.now().isoformat()}"
        )
    except Exception as e:
        logger.error(f"Could not create flag file: {e}")

    try:
        apply_to_mem0(memories, user_id)
        PROFILE_LOADED_FLAG.write_text(f"completed at {datetime.now().isoformat()}")
        logger.info("Profile loaded successfully", extra={"user_id": user_id})
    except Exception as e:
        logger.error(f"Error applying memories to mem0: {e}", exc_info=True)


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
            for m in all_msgs[-30:]
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
        thread_logger.info(f"Updated summary for thread", extra={"session_id": thread.id})
        return summary

    # ---------- mem0 integration ----------

    def fetch_mem0_context(
        self,
        user_id: str,
        project_id: str,
        user_message: str,
        participants: list[dict] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Fetch relevant memories from mem0."""
        if MEM0 is None:
            return [], []

        search_query = user_message
        if len(search_query) > MAX_SEARCH_QUERY_CHARS:
            search_query = search_query[-MAX_SEARCH_QUERY_CHARS:]
            logger.debug(f"Truncated search query to {MAX_SEARCH_QUERY_CHARS} chars")

        user_res = MEM0.search(search_query, user_id=user_id)
        proj_res = MEM0.search(
            search_query,
            user_id=user_id,
            filters={"project_id": project_id},
        )

        user_mems = [r["memory"] for r in user_res.get("results", [])]
        proj_mems = [r["memory"] for r in proj_res.get("results", [])]

        if participants:
            for p in participants:
                p_id = p.get("id")
                p_name = p.get("name", p_id)
                if not p_id or p_id == user_id:
                    continue

                try:
                    p_search = MEM0.search(
                        f"{p_name} {search_query[:500]}",
                        user_id=user_id,
                    )
                    for r in p_search.get("results", []):
                        mem = r["memory"]
                        if mem not in user_mems:
                            labeled_mem = f"[About {p_name}]: {mem}"
                            if labeled_mem not in user_mems:
                                user_mems.append(labeled_mem)
                except Exception as e:
                    logger.warning(f"Error searching participant {p_id}: {e}")

        for r in user_res.get("results", []):
            metadata = r.get("metadata", {})
            if metadata.get("contact_id"):
                contact_name = metadata.get("contact_name", metadata.get("contact_id"))
                mem_text = f"[About {contact_name}]: {r['memory']}"
                if mem_text not in user_mems:
                    user_mems.append(mem_text)

        if user_mems or proj_mems:
            logger.info(
                f"Found {len(user_mems)} user memories, {len(proj_mems)} project memories",
                extra={"user_id": user_id}
            )
        return user_mems, proj_mems

    def add_to_mem0(
        self,
        user_id: str,
        project_id: str,
        recent_msgs: list[Message],
        user_message: str,
        assistant_reply: str,
        participants: list[dict] | None = None,
    ) -> None:
        """Send conversation slice to mem0 for memory extraction."""
        if MEM0 is None:
            return

        context_prefix = ""
        if participants:
            names = [p.get("name", p.get("id", "Unknown")) for p in participants]
            context_prefix = f"[Participants: {', '.join(names)}]\n"

        history_slice = [
            {"role": m.role, "content": m.content} for m in recent_msgs[-4:]
        ] + [
            {"role": "user", "content": context_prefix + user_message},
            {"role": "assistant", "content": assistant_reply},
        ]

        metadata = {"project_id": project_id}
        if participants:
            metadata["participant_ids"] = [
                p.get("id") for p in participants if p.get("id")
            ]
            metadata["participant_names"] = [
                p.get("name") for p in participants if p.get("name")
            ]

        result = MEM0.add(
            history_slice,
            user_id=user_id,
            metadata=metadata,
        )
        logger.debug(f"Added memories: {result}", extra={"user_id": user_id})

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
            {"role": "system", "content": PERSONALITY},
        ]

        if context_parts:
            messages.append({"role": "system", "content": "\n\n".join(context_parts)})

        for m in recent_msgs:
            messages.append({"role": m.role, "content": m.content})

        messages.append({"role": "user", "content": user_message})
        return messages
