"""Offline benchmark for memory dedup and write-failure fallback semantics.

The store uses the same contract as ``AsyncMemory._create_memory``: exact
hash+content lookup before insert, redundant updates skipped, unknown memory
ids ignored, and history write failures never abort the vector-store write.
"""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class DedupRecord:
    id: str
    content: str
    hash: str


class DedupMemoryStore:
    def __init__(self, history_failures: bool = False):
        self.history_failures = history_failures
        self.records: Dict[str, DedupRecord] = {}
        self.stats = {
            "inserted": 0,
            "duplicates_skipped": 0,
            "updated": 0,
            "redundant_updates_skipped": 0,
            "unknown_update_skipped": 0,
            "history_write_failures": 0,
        }

    def _hash(self, content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()

    def _find_existing(self, content: str) -> Optional[DedupRecord]:
        content_hash = self._hash(content)
        for record in self.records.values():
            if record.hash == content_hash and record.content == content:
                return record
        return None

    def _write_history(self) -> None:
        if self.history_failures:
            self.stats["history_write_failures"] += 1
            raise RuntimeError("simulated history write failure")

    def add(self, content: str):
        existing = self._find_existing(content)
        if existing is not None:
            self.stats["duplicates_skipped"] += 1
            return existing.id, "skipped"

        memory_id = uuid.uuid4().hex
        self.records[memory_id] = DedupRecord(
            id=memory_id,
            content=content,
            hash=self._hash(content),
        )
        self.stats["inserted"] += 1

        try:
            self._write_history()
        except RuntimeError:
            # Vector write has already happened; only history durability is lost.
            pass
        return memory_id, "added"

    def update(self, memory_id: str, content: str):
        record = self.records.get(memory_id)
        if record is None:
            self.stats["unknown_update_skipped"] += 1
            return None
        if record.content == content:
            self.stats["redundant_updates_skipped"] += 1
            return memory_id
        record.content = content
        record.hash = self._hash(content)
        self.stats["updated"] += 1
        return memory_id


def run_memory_duplicate_benchmark() -> Dict:
    store = DedupMemoryStore()
    facts = [f"memory_fact_{index}" for index in range(20)]

    first_ids = [store.add(fact)[0] for fact in facts]
    duplicate_ids = [store.add(fact)[0] for fact in facts]
    reverse_ids = [store.add(fact)[0] for fact in reversed(facts)]
    ids_stable = all(
        duplicate_ids[index] == first_ids[index] == reverse_ids[-(index + 1)]
        for index in range(len(facts))
    )

    for memory_id, fact in zip(first_ids, facts):
        store.update(memory_id, fact)
    store.update(first_ids[0], "memory_fact_0_updated")
    store.update("unknown_memory_id", "任何内容")

    failing_store = DedupMemoryStore(history_failures=True)
    failing_id, failing_event = failing_store.add("critical_fact")

    return {
        "framework": "memory_dedup",
        "add_attempts": store.stats["inserted"] + store.stats["duplicates_skipped"],
        "inserted": store.stats["inserted"],
        "duplicates_skipped": store.stats["duplicates_skipped"],
        "duplicate_skip_rate": round(
            store.stats["duplicates_skipped"]
            / max(1, store.stats["inserted"] + store.stats["duplicates_skipped"]),
            4,
        ),
        "exact_hash_ids_stable": ids_stable,
        "updates": {
            "real_updates": store.stats["updated"],
            "redundant_updates_skipped": store.stats["redundant_updates_skipped"],
            "unknown_update_skipped": store.stats["unknown_update_skipped"],
        },
        "history_failure_fallback": {
            "simulated_failures": failing_store.stats["history_write_failures"],
            "vector_write_survived": failing_id is not None and failing_event == "added",
            "record_count_after_failure": len(failing_store.records),
        },
    }
