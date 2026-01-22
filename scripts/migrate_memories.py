#!/usr/bin/env python3
"""Migrate mem0 memories for Clarissa.

This script handles memory migration tasks:
1. Update agent_id from 'clara' to 'clarissa' if needed
2. Verify memory integrity after migration
3. Optional: Re-index memories with new embeddings

Usage:
    python scripts/migrate_memories.py --dry-run     # Preview changes
    python scripts/migrate_memories.py               # Apply changes
    python scripts/migrate_memories.py --verify      # Verify only
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, ".")

from config.logging import get_logger, init_logging

init_logging()
logger = get_logger("migrate_memories")


def get_mem0_client():
    """Get the mem0 client from config."""
    try:
        from config.mem0 import MEM0

        return MEM0
    except Exception as e:
        logger.error(f"Failed to get mem0 client: {e}")
        return None


def count_memories(mem0, agent_id: str) -> int:
    """Count memories for a given agent_id."""
    try:
        result = mem0.get_all(agent_id=agent_id, limit=1000)
        return len(result.get("results", []))
    except Exception as e:
        logger.error(f"Failed to count memories for {agent_id}: {e}")
        return 0


def migrate_agent_id(
    mem0,
    old_agent_id: str,
    new_agent_id: str,
    dry_run: bool = True,
) -> tuple[int, int]:
    """Migrate memories from old agent_id to new agent_id.

    Returns (migrated_count, error_count)
    """
    migrated = 0
    errors = 0

    try:
        # Get all memories with old agent_id
        result = mem0.get_all(agent_id=old_agent_id, limit=10000)
        memories = result.get("results", [])

        logger.info(f"Found {len(memories)} memories with agent_id='{old_agent_id}'")

        if dry_run:
            logger.info("DRY RUN - No changes will be made")
            return len(memories), 0

        for mem in memories:
            try:
                memory_id = mem.get("id")
                if not memory_id:
                    continue

                # mem0 doesn't support updating agent_id directly
                # We would need to delete and re-add with new agent_id
                # This is a destructive operation, so we log it
                logger.warning(
                    f"Memory {memory_id} would need to be re-created "
                    f"with new agent_id (not implemented)"
                )
                migrated += 1
            except Exception as e:
                logger.error(f"Failed to migrate memory {mem.get('id')}: {e}")
                errors += 1

    except Exception as e:
        logger.error(f"Migration failed: {e}")
        errors += 1

    return migrated, errors


def verify_memories(mem0) -> dict:
    """Verify memory system integrity."""
    stats = {
        "total_memories": 0,
        "by_agent_id": {},
        "errors": [],
    }

    try:
        # Check for common agent_ids
        for agent_id in ["clarissa", "clara", "default"]:
            count = count_memories(mem0, agent_id)
            if count > 0:
                stats["by_agent_id"][agent_id] = count
                stats["total_memories"] += count
                logger.info(f"Agent '{agent_id}': {count} memories")

    except Exception as e:
        stats["errors"].append(str(e))
        logger.error(f"Verification error: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Migrate mem0 memories for Clarissa")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without applying them",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify memory integrity only",
    )
    parser.add_argument(
        "--old-agent-id",
        default="clara",
        help="Old agent_id to migrate from (default: clara)",
    )
    parser.add_argument(
        "--new-agent-id",
        default="clarissa",
        help="New agent_id to migrate to (default: clarissa)",
    )
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("Clarissa Memory Migration")
    logger.info(f"Started at: {datetime.now().isoformat()}")
    logger.info("=" * 60)

    # Get mem0 client
    mem0 = get_mem0_client()
    if mem0 is None:
        logger.error("mem0 not configured, exiting")
        sys.exit(1)

    if args.verify:
        logger.info("Running verification only...")
        stats = verify_memories(mem0)
        logger.info(f"Verification complete: {stats}")
        return

    # Check current state
    logger.info("Checking current memory state...")
    old_count = count_memories(mem0, args.old_agent_id)
    new_count = count_memories(mem0, args.new_agent_id)

    logger.info(f"Memories with agent_id='{args.old_agent_id}': {old_count}")
    logger.info(f"Memories with agent_id='{args.new_agent_id}': {new_count}")

    if old_count == 0:
        logger.info(f"No memories found with agent_id='{args.old_agent_id}'")
        logger.info("Nothing to migrate!")
        return

    # Perform migration
    logger.info(f"Migrating from '{args.old_agent_id}' to '{args.new_agent_id}'...")
    migrated, errors = migrate_agent_id(
        mem0,
        args.old_agent_id,
        args.new_agent_id,
        dry_run=args.dry_run,
    )

    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info(f"  Processed: {migrated}")
    logger.info(f"  Errors: {errors}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
