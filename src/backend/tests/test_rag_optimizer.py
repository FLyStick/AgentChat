import asyncio

from agentchat.benchmarks.rag import load_rag_fixtures
from agentchat.benchmarks.rag_optimizer import (
    OptimizedRetriever,
    rewrite_query,
    run_rag_optimizer_benchmark,
)


def test_rewrite_query_expands_domain_synonyms():
    query = "\u4e0b\u73ed\u540e\u7ee7\u7eed\u5e72\u6d3b\uff0c\u516c\u53f8\u5982\u4f55\u8ba1\u916c\uff1f"
    rewritten = rewrite_query(query)

    assert "\u52a0\u73ed" in rewritten
    assert "\u65f6\u85aa" in rewritten
    assert query in rewritten


def test_optimized_retriever_ranks_hard_query_first():
    documents = load_rag_fixtures()["docs"]
    query = "\u52a0\u73ed\u600e\u4e48\u8ba1\u916c"
    results = asyncio.run(
        OptimizedRetriever(documents, rerank_threshold=0.08).search(query, top_k=5)
    )

    assert results[0].chunk_id == "internal_policy_overtime"


def test_rag_optimizer_improves_hard_query_rank():
    report = asyncio.run(run_rag_optimizer_benchmark(top_k=5, rerank_threshold=0.08))
    comparison = report["comparison"]

    assert comparison["mean_recall_after"] >= comparison["mean_recall_before"]
    assert comparison["mean_mrr_after"] >= comparison["mean_mrr_before"]
    assert comparison["hard_query_before_rank"] > comparison["hard_query_after_rank"]
    assert comparison["hard_query_after_rank"] == 1
