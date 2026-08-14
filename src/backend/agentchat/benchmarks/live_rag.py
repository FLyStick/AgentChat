from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from agentchat.benchmarks.live_utils import (
    BACKEND_DIR,
    DEFAULT_STATE_PATH,
    load_state,
    utcnow_iso,
)
from agentchat.benchmarks.metrics import (
    hit_at_k,
    mrr_at_k,
    recall_at_k,
    summarize_retrieval_cases,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GROUND_TRUTH = REPO_ROOT / "docs" / "eval" / "live_rag_ground_truth.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval"


def _document_ids(documents: Sequence[Any]) -> List[str]:
    ids: List[str] = []
    for document in documents:
        if isinstance(document, dict):
            ids.append(str(document.get("chunk_id", "")))
        else:
            ids.append(str(getattr(document, "chunk_id", "")))
    return ids


def _serialize_documents(documents: Sequence[Any]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for document in documents:
        if hasattr(document, "to_dict"):
            serialized.append(document.to_dict())
            continue
        if isinstance(document, dict):
            serialized.append(dict(document))
        else:
            serialized.append(
                {
                    field: getattr(document, field, None)
                    for field in (
                        "chunk_id",
                        "content",
                        "summary",
                        "file_id",
                        "file_name",
                        "knowledge_id",
                        "update_time",
                        "score",
                    )
                }
            )
    return serialized


async def _evaluate_query(
    retriever: Any,
    query_case: Dict[str, Any],
    top_k: int,
) -> Dict[str, Any]:
    query = query_case["query"]
    started = time.perf_counter()
    documents = await retriever.search(query, top_k)
    latency_ms = round((time.perf_counter() - started) * 1000, 3)

    expected = list(query_case.get("expected_chunk_ids", []))
    retrieved_ids = _document_ids(documents)
    retrieved_documents = _serialize_documents(documents)
    facts = list(query_case.get("expected_facts", []))

    fact_results = []
    for fact in facts:
        matched_single_doc = any(fact in doc.get("content", "") for doc in retrieved_documents)
        matched_joined = fact in "\n".join(doc.get("content", "") for doc in retrieved_documents)
        fact_results.append(
            {
                "fact": fact,
                "matched_single_doc": matched_single_doc,
                "matched_joined": matched_joined,
            }
        )

    matched_expected = [item for item in retrieved_ids if item in set(expected)]
    return {
        "query_id": query_case.get("id", ""),
        "query": query,
        "difficulty": query_case.get("difficulty", "normal"),
        "expected_chunk_ids": expected,
        "retrieved_chunk_ids": retrieved_ids,
        "matched_expected_chunk_ids": matched_expected,
        "recall_at_k": recall_at_k(expected, retrieved_ids, top_k),
        "mrr_at_k": mrr_at_k(expected, retrieved_ids, top_k),
        "hit_at_k": hit_at_k(expected, retrieved_ids, top_k),
        "latency_ms": latency_ms,
        "evidence": {
            "fact_count": len(facts),
            "matched_single_doc_count": sum(item["matched_single_doc"] for item in fact_results),
            "matched_joined_count": sum(item["matched_joined"] for item in fact_results),
            "fact_results": fact_results,
        },
        "retrieved_documents": retrieved_documents,
    }


def _evidence_hit_rate(cases: List[Dict[str, Any]], key: str) -> float:
    ratios = []
    for case in cases:
        evidence = case.get("evidence", {})
        fact_count = evidence.get("fact_count", 0)
        if fact_count:
            ratios.append(evidence.get(key, 0) / fact_count)
    return round(sum(ratios) / len(ratios), 4) if ratios else 0.0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-rag",
        description="使用真实向量库和 Embedding 链路执行 RAG 召回评测。",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="评测状态 JSON")
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH, help="ground truth 文件")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="评测产物目录")
    parser.add_argument("--knowledge-id", default=None, help="覆盖状态文件中的知识库 ID")
    parser.add_argument("--top-k", type=int, default=10, help="召回数量")
    return parser


async def run_live_rag(args: argparse.Namespace) -> Dict[str, Any]:
    os.chdir(BACKEND_DIR)

    from agentchat.settings import init_app_settings

    await init_app_settings()

    state = load_state(args.state)
    knowledge_id = args.knowledge_id or state.get("knowledge_id") or ""
    if not knowledge_id:
        raise RuntimeError("无法确定 knowledge_id，请先运行 live_seed.py 或传入 --knowledge-id")

    ground_truth_path = Path(args.ground_truth).expanduser().resolve()
    if not ground_truth_path.is_file():
        raise FileNotFoundError(f"ground truth 文件不存在: {ground_truth_path}")
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    if ground_truth.get("knowledge_id") != knowledge_id:
        raise RuntimeError(
            "ground truth 的 knowledge_id 与状态文件不一致："
            f"{ground_truth.get('knowledge_id')} != {knowledge_id}"
        )

    from agentchat.benchmarks.rag import LiveRetriever

    retriever = LiveRetriever([knowledge_id])
    cases: List[Dict[str, Any]] = []
    for query_case in ground_truth["queries"]:
        cases.append(await _evaluate_query(retriever, query_case, args.top_k))

    summary = summarize_retrieval_cases(cases, args.top_k)
    summary["evidence_hit_rate_joined"] = _evidence_hit_rate(cases, "matched_joined_count")
    summary["evidence_hit_rate_single_doc"] = _evidence_hit_rate(
        cases, "matched_single_doc_count"
    )
    summary["failed_case_count"] = 0

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {
        "stage": "p5.3",
        "created_at": created_at,
        "dataset_name": ground_truth.get("dataset_name", ""),
        "knowledge_id": knowledge_id,
        "vector_store": ground_truth.get("vector_store", ""),
        "retriever": "LiveRetriever -> MixRetrival/Chroma -> merge_documents_by_score",
        "top_k": args.top_k,
        "query_count": len(cases),
        "ground_truth_file": str(ground_truth_path),
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
        },
        "cases": cases,
        "summary": summary,
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"live_rag_{timestamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = asyncio.run(run_live_rag(args))
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
