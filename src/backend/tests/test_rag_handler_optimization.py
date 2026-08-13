import asyncio
from types import SimpleNamespace

from agentchat.services.rag.handler import RagHandler


def make_doc(score, content, chunk_id=None):
    return SimpleNamespace(
        score=score,
        content=content,
        chunk_id=chunk_id or content,
    )


def test_mix_retrieval_single_field_preserves_default_behavior(monkeypatch):
    captured = []

    async def fake_retrieve(query_list, knowledges_id, search_field, index_names=None):
        captured.append(search_field)
        return [make_doc(0.9, "content doc")]

    monkeypatch.setattr(RagHandler, "_retrieve_field_results", fake_retrieve)

    result = asyncio.run(
        RagHandler.mix_retrival_documents(["query"], ["kb_1"], "content")
    )

    assert captured == ["content"]
    assert [doc.content for doc in result] == ["content doc"]


def test_mix_retrieval_content_plus_summary_merges_both_fields(monkeypatch):
    captured = []

    async def fake_retrieve(query_list, knowledges_id, search_field, index_names=None):
        captured.append(search_field)
        if search_field == "content":
            return [make_doc(0.9, "content doc", "chunk_1")]
        return [make_doc(0.8, "summary doc", "chunk_2")]

    monkeypatch.setattr(RagHandler, "_retrieve_field_results", fake_retrieve)

    result = asyncio.run(
        RagHandler.mix_retrival_documents(
            ["query"], ["kb_1"], "content+summary"
        )
    )

    assert captured == ["content", "summary"]
    assert [doc.content for doc in result] == ["content doc", "summary doc"]


def test_rerank_threshold_filters_below_threshold():
    docs = [
        make_doc(0.9, "high"),
        make_doc(0.5, "medium"),
        make_doc(0.05, "low"),
    ]

    filtered = RagHandler._filter_reranked_documents(
        docs,
        min_score=0.2,
        rerank_threshold=0.4,
        top_k=5,
    )

    assert [doc.content for doc in filtered] == ["high", "medium"]


def test_retrieve_ranked_documents_top_k_none_keeps_all_results(monkeypatch):
    async def fake_retrieve(query_list, collection_names, search_field, index_names=None):
        return [make_doc(0.9, "first"), make_doc(0.8, "second")]

    async def fake_rerank(query, documents):
        return [make_doc(0.9, "first"), make_doc(0.8, "second")]

    monkeypatch.setattr(RagHandler, "mix_retrival_documents", fake_retrieve)
    monkeypatch.setattr(RagHandler, "query_rewrite", lambda query: [query])
    monkeypatch.setattr("agentchat.services.rag.handler.Reranker.rerank_documents", fake_rerank)

    result = asyncio.run(
        RagHandler.retrieve_ranked_documents(
            "query",
            ["kb_1"],
            min_score=None,
            top_k=None,
            needs_query_rewrite=False,
        )
    )

    assert result == "first\nsecond"
