from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from agentchat.benchmarks.live_utils import (
    BACKEND_DIR,
    load_state,
    utcnow_iso,
)
from agentchat.benchmarks.metrics import (
    hit_at_k,
    latency_stats,
    mrr_at_k,
    recall_at_k,
    summarize_retrieval_cases,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_STATE_PATH = REPO_ROOT / "docs" / "eval" / "live" / "live_rag_ab_state.json"
DEFAULT_GROUND_TRUTH = REPO_ROOT / "docs" / "eval" / "live" / "live_rag_ab_ground_truth.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval" / "live"


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
            continue
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


def _rebuild_reranked(reranked_docs: Sequence[Any], candidates: Sequence[Any]) -> List[Any]:
    rebuilt: List[Any] = []
    candidates = list(candidates)
    for result in reranked_docs:
        index = int(getattr(result, "index", -1))
        if index < 0 or index >= len(candidates):
            continue
        original = candidates[index]
        rebuilt.append(
            SimpleNamespace(
                chunk_id=original.chunk_id,
                content=result.content,
                summary=original.summary,
                file_id=original.file_id,
                file_name=original.file_name,
                knowledge_id=original.knowledge_id,
                update_time=original.update_time,
                score=result.score,
            )
        )
    return rebuilt


def _arm_evaluation(
    query_case: Dict[str, Any],
    documents: Sequence[Any],
    top_k: int,
) -> Dict[str, Any]:
    expected = list(query_case.get("expected_chunk_ids", []))
    retrieved_ids = _document_ids(documents)
    serialized = _serialize_documents(documents)
    facts = list(query_case.get("expected_facts", []))

    fact_results = []
    for fact in facts:
        matched_single_doc = any(fact in doc.get("content", "") for doc in serialized)
        matched_joined = fact in "\n".join(doc.get("content", "") for doc in serialized)
        fact_results.append(
            {
                "fact": fact,
                "matched_single_doc": matched_single_doc,
                "matched_joined": matched_joined,
            }
        )

    matched_expected = [item for item in retrieved_ids if item in set(expected)]
    return {
        "expected_chunk_ids": expected,
        "retrieved_chunk_ids": retrieved_ids,
        "matched_expected_chunk_ids": matched_expected,
        "recall_at_k": recall_at_k(expected, retrieved_ids, top_k),
        "mrr_at_k": mrr_at_k(expected, retrieved_ids, top_k),
        "hit_at_k": hit_at_k(expected, retrieved_ids, top_k),
        "evidence": {
            "fact_count": len(facts),
            "matched_single_doc_count": sum(
                item["matched_single_doc"] for item in fact_results
            ),
            "matched_joined_count": sum(item["matched_joined"] for item in fact_results),
            "fact_results": fact_results,
        },
        "retrieved_documents": serialized,
    }


class LiveRagAb:
    """在同一真实知识库上执行两条检索路径的 A/B。"""

    def __init__(
        self,
        knowledge_ids: Sequence[str],
        top_k: int = 5,
        min_score: Optional[float] = None,
        rerank_threshold: Optional[float] = None,
        mode: str = "all",
    ) -> None:
        self.knowledge_ids = list(knowledge_ids)
        self.top_k = top_k
        self.min_score = min_score
        self.rerank_threshold = rerank_threshold
        self.mode = mode

    async def _search_baseline(self, query: str) -> List[Any]:
        from agentchat.services.rag.handler import RagHandler

        documents = await RagHandler.mix_retrival_documents(
            [query],
            self.knowledge_ids,
            "content",
        )
        return documents[: self.top_k]

    async def _search_optimized(self, query: str) -> Dict[str, Any]:
        from agentchat.services.rag.handler import RagHandler
        from agentchat.services.rag.rerank import Reranker
        from agentchat.services.rag.result_merger import merge_documents_by_score
        from agentchat.settings import app_settings

        timings: Dict[str, float] = {}

        rewrite_started = time.perf_counter()
        rewritten_available = True
        try:
            rewritten_queries = await RagHandler.query_rewrite(query)
            rewritten_queries = RagHandler._normalize_rewritten_queries(
                query, rewritten_queries
            )
            if not rewritten_queries:
                rewritten_queries = [query]
                rewritten_available = False
        except Exception:
            rewritten_queries = [query]
            rewritten_available = False
        timings["query_rewrite_ms"] = round(
            (time.perf_counter() - rewrite_started) * 1000, 3
        )

        retrieve_started = time.perf_counter()
        documents = await RagHandler.mix_retrival_documents(
            rewritten_queries,
            self.knowledge_ids,
            "content",
        )
        summary_available = bool(app_settings.rag.enable_summary)
        if summary_available:
            summary_documents = await RagHandler.mix_retrival_documents(
                rewritten_queries,
                self.knowledge_ids,
                "summary",
            )
            documents = merge_documents_by_score(
                [*documents, *summary_documents],
                top_k=10,
            )
        timings["retrieval_ms"] = round(
            (time.perf_counter() - retrieve_started) * 1000, 3
        )

        materials = [doc.content for doc in documents]
        rerank_started = time.perf_counter()
        rerank_available = True
        reranked_docs = await Reranker.rerank_documents(query, materials)
        if reranked_docs is None:
            rerank_available = False
            filtered = RagHandler._filter_reranked_documents(
                documents,
                self.min_score,
                None,
                self.top_k,
            )
        else:
            reranked_with_meta = _rebuild_reranked(reranked_docs, documents)
            filtered = RagHandler._filter_reranked_documents(
                reranked_with_meta,
                self.min_score,
                self.rerank_threshold,
                self.top_k,
            )
        timings["rerank_ms"] = round(
            (time.perf_counter() - rerank_started) * 1000, 3
        )
        timings["total_ms"] = round(sum(timings.values()), 3)

        return {
            "documents": filtered,
            "timings": timings,
            "availability": {
                "query_rewrite": rewritten_available,
                "summary": summary_available,
                "rerank": rerank_available,
                "candidate_count": len(documents),
            },
        }

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        top_k = top_k or self.top_k
        baseline: Optional[Dict[str, Any]] = None
        optimized: Optional[Dict[str, Any]] = None

        if self.mode in ("baseline", "all"):
            started = time.perf_counter()
            documents = await self._search_baseline(query)
            baseline = {
                "documents": documents,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "availability": {
                    "query_rewrite": False,
                    "summary": False,
                    "rerank": False,
                    "candidate_count": len(documents),
                },
            }

        if self.mode in ("optimized", "all"):
            result = await self._search_optimized(query)
            optimized = {
                "documents": result["documents"],
                "latency_ms": result["timings"]["total_ms"],
                "timings": result["timings"],
                "availability": result["availability"],
            }

        return {"baseline": baseline, "optimized": optimized}


def _case_eval(
    query_case: Dict[str, Any],
    baselines: Dict[str, Any],
    top_k: int,
) -> Dict[str, Any]:
    arm_results: Dict[str, Any] = {}
    for arm in ("baseline", "optimized"):
        entry = baselines.get(arm)
        if entry is None:
            continue
        evaluation = _arm_evaluation(query_case, entry["documents"], top_k)
        evaluation["latency_ms"] = entry["latency_ms"]
        evaluation["timings"] = entry.get("timings", {"total_ms": entry["latency_ms"]})
        evaluation["availability"] = entry["availability"]
        arm_results[arm] = evaluation

    delta = {}
    if "baseline" in arm_results and "optimized" in arm_results:
        baseline = arm_results["baseline"]
        optimized = arm_results["optimized"]
        delta = {
            "recall_at_k": round(
                optimized["recall_at_k"] - baseline["recall_at_k"], 4
            ),
            "mrr_at_k": round(optimized["mrr_at_k"] - baseline["mrr_at_k"], 4),
            "hit_at_k": round(optimized["hit_at_k"] - baseline["hit_at_k"], 4),
            "latency_ms": round(
                optimized["latency_ms"] - baseline["latency_ms"], 3
            ),
        }

    return {
        "query_id": query_case.get("id", ""),
        "query": query_case.get("query", ""),
        "difficulty": query_case.get("difficulty", "normal"),
        "baseline": arm_results.get("baseline"),
        "optimized": arm_results.get("optimized"),
        "delta": delta,
    }


def _evidence_hit_rate(cases: List[Dict[str, Any]], arm: str, key: str) -> float:
    ratios = []
    for case in cases:
        evaluation = case.get(arm) or {}
        evidence = evaluation.get("evidence", {})
        fact_count = evidence.get("fact_count", 0)
        if fact_count:
            ratios.append(evidence.get(key, 0) / fact_count)
    return round(sum(ratios) / len(ratios), 4) if ratios else 0.0


def _arm_summary(
    cases: List[Dict[str, Any]],
    top_k: int,
    arm: str,
) -> Dict[str, Any]:
    evaluations = [case[arm] for case in cases if case.get(arm)]
    summary = summarize_retrieval_cases(evaluations, top_k)
    summary["evidence_hit_rate_single_doc"] = _evidence_hit_rate(
        cases, arm, "matched_single_doc_count"
    )
    summary["evidence_hit_rate_joined"] = _evidence_hit_rate(
        cases, arm, "matched_joined_count"
    )
    availability_counts = {key: 0 for key in ("query_rewrite", "summary", "rerank")}
    for case in cases:
        availability = (case.get(arm) or {}).get("availability", {})
        for key in availability_counts:
            if availability.get(key):
                availability_counts[key] += 1
    summary["availability_counts"] = availability_counts
    return summary


def _compare_summaries(
    baseline_summary: Dict[str, Any],
    optimized_summary: Dict[str, Any],
) -> Dict[str, Any]:
    baseline_latency = baseline_summary.get("latency_ms", {})
    optimized_latency = optimized_summary.get("latency_ms", {})
    return {
        "recall_at_k": round(
            optimized_summary.get("mean_recall_at_k", 0.0)
            - baseline_summary.get("mean_recall_at_k", 0.0),
            4,
        ),
        "mrr_at_k": round(
            optimized_summary.get("mean_mrr", 0.0)
            - baseline_summary.get("mean_mrr", 0.0),
            4,
        ),
        "hit_at_k": round(
            optimized_summary.get("hit_rate_at_k", 0.0)
            - baseline_summary.get("hit_rate_at_k", 0.0),
            4,
        ),
        "latency_ms": {
            "mean_ms": round(
                optimized_latency.get("mean_ms", 0.0)
                - baseline_latency.get("mean_ms", 0.0),
                3,
            ),
            "p50_ms": round(
                optimized_latency.get("p50_ms", 0.0)
                - baseline_latency.get("p50_ms", 0.0),
                3,
            ),
            "p95_ms": round(
                optimized_latency.get("p95_ms", 0.0)
                - baseline_latency.get("p95_ms", 0.0),
                3,
            ),
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-rag-ab",
        description=(
            "真实 RAG A/B：同一知识库、同一 ground truth，"
            "对比原始单路检索与生产完整链路（rewrite/content+summary/rerank/filter）。"
        ),
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="评测状态 JSON")
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH, help="ground truth 文件")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="评测产物目录")
    parser.add_argument("--knowledge-id", default=None, help="覆盖状态文件中的知识库 ID")
    parser.add_argument("--top-k", type=int, default=5, help="评测召回数量")
    parser.add_argument("--mode", choices=("baseline", "optimized", "all"), default="all")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条 query，0 表示全量")
    return parser


async def run_live_rag_ab(args: argparse.Namespace) -> Dict[str, Any]:
    os.chdir(BACKEND_DIR)

    from agentchat.settings import app_settings, init_app_settings

    await init_app_settings()

    state = load_state(args.state)
    knowledge_id = args.knowledge_id or state.get("knowledge_id") or ""
    if not knowledge_id:
        raise RuntimeError("无法确定 knowledge_id，请先运行 live_seed.py（P5.9 模式）")

    ground_truth_path = Path(args.ground_truth).expanduser().resolve()
    if not ground_truth_path.is_file():
        raise FileNotFoundError(f"ground truth 文件不存在: {ground_truth_path}")
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    if ground_truth.get("knowledge_id") != knowledge_id:
        raise RuntimeError(
            "ground truth 的 knowledge_id 与状态文件不一致："
            f"{ground_truth.get('knowledge_id')} != {knowledge_id}"
        )

    queries = list(ground_truth.get("queries", []))
    if args.limit:
        queries = queries[: args.limit]
    if not queries:
        raise RuntimeError("ground truth 中没有可执行的 queries")

    rag_ab = LiveRagAb(
        [knowledge_id],
        top_k=args.top_k,
        min_score=app_settings.rag.retrival.get("min_score"),
        rerank_threshold=app_settings.rag.retrival.get("rerank_threshold"),
        mode=args.mode,
    )

    cases: List[Dict[str, Any]] = []
    for query_case in queries:
        result = await rag_ab.search(query_case["query"], args.top_k)
        cases.append(_case_eval(query_case, result, args.top_k))

    all_cases = [
        case for case in cases
        if case.get("optimized") is not None and case.get("baseline") is not None
    ]
    hard_cases = [case for case in all_cases if case.get("difficulty") == "hard"]
    easy_cases = [case for case in all_cases if case.get("difficulty") in ("easy", "normal")]

    baseline_summary = _arm_summary(all_cases, args.top_k, "baseline")
    optimized_summary = _arm_summary(all_cases, args.top_k, "optimized")
    hard_baseline_summary = _arm_summary(hard_cases, args.top_k, "baseline")
    hard_optimized_summary = _arm_summary(hard_cases, args.top_k, "optimized")
    easy_baseline_summary = _arm_summary(easy_cases, args.top_k, "baseline")
    easy_optimized_summary = _arm_summary(easy_cases, args.top_k, "optimized")

    created_at = utcnow_iso()
    result = {
        "stage": "p5.9",
        "created_at": created_at,
        "dataset_name": ground_truth.get("dataset_name", ""),
        "knowledge_id": knowledge_id,
        "vector_store": ground_truth.get("vector_store", ""),
        "ground_truth_file": str(ground_truth_path),
        "top_k": args.top_k,
        "mode": args.mode,
        "query_count": len(cases),
        "paired_query_count": len(all_cases),
        "hard_query_count": len(hard_cases),
        "config": {
            "min_score": app_settings.rag.retrival.get("min_score"),
            "rerank_threshold": app_settings.rag.retrival.get("rerank_threshold"),
            "enable_summary": bool(app_settings.rag.enable_summary),
            "enable_elasticsearch": bool(app_settings.rag.enable_elasticsearch),
            "split": dict(app_settings.rag.split or {}),
            "rerank_model": app_settings.multi_models.rerank.model_name,
            "rewrite_model": app_settings.multi_models.reasoning_model.model_name,
        },
        "arms": {
            "baseline": "raw_query -> MixRetrival(content) -> merge_documents_by_score -> top_k",
            "optimized": "query_rewrite -> content+summary -> merge -> Rerank -> min_score/rerank_threshold -> top_k",
        },
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
        },
        "summary": {
            "overall": {
                "baseline": baseline_summary,
                "optimized": optimized_summary,
                "delta": _compare_summaries(baseline_summary, optimized_summary),
            },
            "hard": {
                "query_count": len(hard_cases),
                "baseline": hard_baseline_summary,
                "optimized": hard_optimized_summary,
                "delta": _compare_summaries(hard_baseline_summary, hard_optimized_summary),
            },
            "easy_normal": {
                "query_count": len(easy_cases),
                "baseline": easy_baseline_summary,
                "optimized": easy_optimized_summary,
                "delta": _compare_summaries(easy_baseline_summary, easy_optimized_summary),
            },
        },
        "cases": cases,
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"live_rag_comparison_{timestamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = asyncio.run(run_live_rag_ab(args))
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
