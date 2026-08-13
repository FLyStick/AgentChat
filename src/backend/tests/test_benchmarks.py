import asyncio

from agentchat.benchmarks.cancel import run_cancel_stress, summarize_cancel_runs
from agentchat.benchmarks.memory import load_memory_fixtures, run_offline_memory_benchmark
from agentchat.benchmarks.metrics import mrr_at_k, percentile, recall_at_k
from agentchat.benchmarks.rag import (
    OfflineRetriever,
    load_rag_fixtures,
    run_rag_benchmark,
)
from agentchat.utils.cancellable_stream import CancellableAsyncStream


def test_retrieval_metrics_are_deterministic():
    relevant = ["chunk_a", "chunk_b"]
    assert recall_at_k(relevant, ["chunk_b", "chunk_c"], k=2) == 0.5
    assert mrr_at_k(relevant, ["chunk_c", "chunk_a"], k=2) == 0.5
    assert percentile([1, 2, 3, 4], 50) == 2.5


def test_offline_rag_benchmark_runs_fixture_dataset():
    dataset = load_rag_fixtures()
    result = asyncio.run(
        run_rag_benchmark(
            OfflineRetriever(dataset["docs"]),
            dataset=dataset,
            top_k=5,
        )
    )

    assert result["summary"]["case_count"] == len(dataset["queries"])
    assert result["summary"]["mean_recall_at_k"] > 0

    checkin_case = next(
        case for case in result["cases"] if case["query_id"] == "rag_q_checkin_time"
    )
    assert "hotel_faq_checkin" in checkin_case["retrieved"]


def test_memory_benchmark_filters_by_case_type():
    cases = {
        "cases": [
            {
                "id": "s1",
                "type": "short_term",
                "history": [
                    {"role": "user", "content": "会议改到下午3点。"},
                    {"role": "assistant", "content": "好的，下午3点。"},
                ],
                "summary": [],
                "facts": [],
                "query": "几点开会？",
                "expected": ["下午3点"],
            },
            {
                "id": "m1",
                "type": "summary",
                "history": [],
                "summary": ["客户预算80万。"],
                "facts": [],
                "query": "预算多少？",
                "expected": ["80万"],
            },
            {
                "id": "l1",
                "type": "long_term",
                "history": [],
                "summary": [],
                "facts": ["张三邮箱zhang@example.com。"],
                "query": "邮箱是什么？",
                "expected": ["zhang@example.com"],
            },
        ]
    }

    report = run_offline_memory_benchmark(cases=cases)
    short_term = report["mode_reports"]["short_term"]
    summary = report["mode_reports"]["summary"]
    long_term = report["mode_reports"]["long_term"]

    assert short_term["case_count"] == 1
    assert summary["case_count"] == 1
    assert long_term["case_count"] == 1
    assert short_term["hit_rate"] == 1.0
    assert summary["hit_rate"] == 1.0
    assert long_term["hit_rate"] == 1.0


def test_memory_benchmark_runs_full_fixture():
    report = run_offline_memory_benchmark()
    for mode in ("short_term", "summary", "long_term"):
        mode_report = report["mode_reports"][mode]
        assert mode_report["case_count"] == 4
        assert mode_report["hit_rate"] == 1.0


def test_cancellable_stream_records_real_cancel_latency():
    async def slow_producer(queue):
        for index in range(20):
            await asyncio.sleep(0.02)
            queue.put_nowait(f"chunk-{index}")

    async def run():
        stream = CancellableAsyncStream(slow_producer)
        received = []

        async def cancel_later():
            await asyncio.sleep(0.05)
            stream.request_cancel()

        canceler = asyncio.create_task(cancel_later())
        try:
            async for item in stream:
                received.append(item)
        finally:
            if not canceler.done():
                canceler.cancel()
                try:
                    await canceler
                except asyncio.CancelledError:
                    pass

        return received, stream.summary()

    received, summary = asyncio.run(run())
    assert summary is not None
    assert summary["cancelled"] is True
    assert summary["cancel_to_terminate_ms"] is not None
    assert summary["cancel_to_terminate_ms"] <= summary["total_duration_ms"]
    assert len(received) > 0


def test_cancel_stress_reports_pass_rate():
    result = asyncio.run(
        run_cancel_stress(
            runs=3,
            initial_delay_ms=100,
            chunk_interval_ms=30,
            chunks=20,
            cancel_after_ms=150,
        )
    )
    assert result["summary"]["runs"] == 3
    assert result["summary"]["pass_rate"] == 1.0


def test_cancel_summary_ignores_runs_without_latency():
    summary = summarize_cancel_runs(
        [
            {"cancel_to_terminate_ms": 100},
            {"cancel_to_terminate_ms": None},
        ],
        threshold_ms=200,
    )
    assert summary["runs"] == 2
    assert summary["passed_runs"] == 1
    assert summary["pass_rate"] == 0.5
