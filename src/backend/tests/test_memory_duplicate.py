from agentchat.benchmarks.memory_duplicate import (
    DedupMemoryStore,
    run_memory_duplicate_benchmark,
)


def test_exact_duplicate_reuses_existing_id():
    store = DedupMemoryStore()

    first_id, first_event = store.add("memory_fact")
    second_id, second_event = store.add("memory_fact")

    assert first_event == "added"
    assert second_event == "skipped"
    assert first_id == second_id
    assert store.stats["inserted"] == 1
    assert store.stats["duplicates_skipped"] == 1


def test_redundant_update_and_unknown_id_are_safe():
    store = DedupMemoryStore()
    memory_id, _ = store.add("memory_fact")

    assert store.update(memory_id, "memory_fact") == memory_id
    assert store.stats["redundant_updates_skipped"] == 1
    assert store.update("unknown_memory_id", "anything") is None
    assert store.stats["unknown_update_skipped"] == 1


def test_history_failure_does_not_abort_vector_write():
    store = DedupMemoryStore(history_failures=True)

    memory_id, event = store.add("critical_fact")

    assert event == "added"
    assert memory_id in store.records
    assert store.stats["history_write_failures"] == 1


def test_benchmark_reports_stable_dedup_ids():
    report = run_memory_duplicate_benchmark()

    assert report["inserted"] == 20
    assert report["duplicates_skipped"] == 40
    assert report["duplicate_skip_rate"] == 0.6667
    assert report["exact_hash_ids_stable"] is True
    assert report["history_failure_fallback"]["vector_write_survived"] is True
