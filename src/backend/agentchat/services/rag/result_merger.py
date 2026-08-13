from typing import Any, Iterable, List


def merge_documents_by_score(documents: Iterable[Any], top_k: int = 10) -> List[Any]:
    """Sort by score, deduplicate by chunk_id, and keep the highest-scored top_k."""
    sorted_documents = sorted(documents, key=lambda doc: doc.score, reverse=True)
    merged = []
    seen_chunk_ids = set()

    for doc in sorted_documents:
        if doc.chunk_id in seen_chunk_ids:
            continue
        seen_chunk_ids.add(doc.chunk_id)
        merged.append(doc)
        if len(merged) >= top_k:
            break

    return merged
