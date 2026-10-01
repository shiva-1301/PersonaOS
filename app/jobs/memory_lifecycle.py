"""Nightly memory lifecycle job (decay, state transitions, drift repair).

Safe to run any number of times: strength is recomputed from last access and access
count, so the same `now` always gives the same result. Triggered by
POST /internal/jobs/memory-lifecycle (cron-friendly, protected by CRON_SECRET).
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MemoryMeta, User
from app.services.memory_lifecycle import decayed_strength, next_state

logger = logging.getLogger(__name__)

BATCH = 500


def run_memory_lifecycle(
    db: Session,
    memory,
    *,
    now: datetime | None = None,
    only_user: uuid.UUID | None = None,
) -> dict:
    """`memory` is the MemoryService. Returns counts of what changed.

    only_user limits the run to one user (verification scripts simulate "60 days later"
    for a test user without ageing anyone else's memories)."""
    now = now or datetime.now(UTC)
    rules = memory.rules
    stats = {
        "checked": 0,
        "became_stale": 0,
        "became_archived": 0,
        "became_active": 0,
        "orphan_meta_removed": 0,
        "missing_meta_backfilled": 0,
    }

    # 1. Decay every live memory.
    scope = [MemoryMeta.user_id == only_user] if only_user else []
    rows = list(
        db.scalars(select(MemoryMeta).where(MemoryMeta.state.in_(("active", "stale")), *scope))
    )
    for row in rows:
        stats["checked"] += 1
        strength = decayed_strength(row.last_accessed_at, row.access_count, now, rules.base_days)
        state = next_state(row.state, strength, rules)
        if state != row.state:
            stats[f"became_{state}"] += 1
        row.strength, row.state = round(strength, 6), state

    # 2. Drift: meta rows whose Mem0 vector no longer exists.
    store = memory.vector_store
    all_ids = list(db.scalars(select(MemoryMeta.mem0_id).where(*scope)))
    for i in range(0, len(all_ids), BATCH):
        batch = all_ids[i : i + BATCH]
        present = set(store.collection.get(ids=batch, include=[])["ids"])
        for mem_id in set(batch) - present:
            db.query(MemoryMeta).filter(MemoryMeta.mem0_id == mem_id).delete()
            stats["orphan_meta_removed"] += 1

    # 3. Drift: Mem0 vectors without a meta row (e.g. written before a crash).
    known = set(db.scalars(select(MemoryMeta.mem0_id)))
    users = {str(u) for u in db.scalars(select(User.id))}
    where = {"user_id": str(only_user)} if only_user else None
    vectors = store.collection.get(where=where, include=["metadatas"])
    for mem_id, meta in zip(vectors["ids"], vectors["metadatas"], strict=True):
        owner = (meta or {}).get("user_id")
        if mem_id not in known and owner in users:
            memory.backfill_meta(db, mem_id, uuid.UUID(owner), now)
            stats["missing_meta_backfilled"] += 1

    db.commit()
    logger.info("Memory lifecycle run", extra=stats)
    return stats
