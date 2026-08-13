from types import SimpleNamespace

from agentchat.services.rag.result_merger import merge_documents_by_score


def make_doc(score, chunk_id):
    return SimpleNamespace(score=score, chunk_id=chunk_id)


def test_sorts_by_score_descending():
    docs = [make_doc(0.5, "b"), make_doc(0.9, "a")]
    result = merge_documents_by_score(docs)
    assert [doc.chunk_id for doc in result] == ["a", "b"]


def test_deduplicates_by_chunk_id_keeping_highest_score():
    docs = [make_doc(0.4, "x"), make_doc(0.8, "x"), make_doc(0.7, "y")]
    result = merge_documents_by_score(docs)
    assert [doc.chunk_id for doc in result] == ["x", "y"]
    assert result[0].score == 0.8


def test_honors_top_k():
    docs = [make_doc(0.5, str(i)) for i in range(20)]
    assert len(merge_documents_by_score(docs, top_k=3)) == 3
