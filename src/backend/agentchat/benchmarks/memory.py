import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from agentchat.benchmarks.metrics import hit_at_k, latency_stats, mrr_at_k, recall_at_k
from agentchat.benchmarks.rag import lexical_similarity


# 记忆 benchmark 的测试数据目录
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "memory"
# 支持的记忆模式：短期记忆、摘要记忆、长期事实记忆
MODES = ("short_term", "summary", "long_term")


def load_memory_fixtures() -> Dict:
    """加载记忆 benchmark 的 fixture JSON 文件。"""
    path = FIXTURE_DIR / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def build_mode_context(case: Dict, mode: str) -> List[str]:
    """根据不同模式，构造可用于检索的上下文文本列表。"""
    history = case.get("history", [])
    if mode == "short_term":
        # 短期记忆通常只看最近几轮对话
        return [
            f"{message.get('role', 'user')}: {message.get('content', '')}"
            for message in history[-3:]
        ]
    if mode == "summary":
        # 摘要记忆来自历史总结，适合长期压缩信息
        summaries = case.get("summary", [])
        if isinstance(summaries, str):
            summaries = [summaries]
        return list(summaries)
    if mode == "long_term":
        # 长期记忆来自静态事实列表，适合稳定知识查询
        facts = case.get("facts", [])
        if isinstance(facts, str):
            facts = [facts]
        return list(facts)
    return []


def search_items(query: str, items: Sequence[str], top_k: int = 3) -> List[str]:
    """按词法相似度对上下文条目进行过滤和排序，返回前 top_k 个最相关结果。"""
    scored = []
    for item in items:
        score = lexical_similarity(query, item)
        if score > 0:
            scored.append((score, item))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in scored[:top_k]]


def _matches_expected(retrieved: Sequence[str], expected: Sequence[str]) -> bool:
    """判断召回结果中是否包含与预期内容相符的条目。"""
    for item in retrieved:
        lowered = item.lower()
        if any(expectation.lower() in lowered for expectation in expected):
            return True
    return False


def run_offline_memory_benchmark(
    cases: Optional[Dict] = None,
    modes: Sequence[str] = MODES,
    top_k: int = 3,
) -> Dict:
    """批量评测各类记忆模式的召回效果和延迟指标。"""
    cases = cases or load_memory_fixtures()
    mode_reports = {}

    for mode in modes:
        mode_cases = []
        for case in cases["cases"]:
            if case.get("type", mode) != mode:
                continue
            items = build_mode_context(case, mode)
            started = time.perf_counter()
            retrieved = search_items(case["query"], items, top_k)
            latency_ms = (time.perf_counter() - started) * 1000

            relevant = case.get("expected", [])
            # 命中判定：召回结果中是否包含期望事实中的任意一项
            hit = 1.0 if _matches_expected(retrieved, relevant) else 0.0
            expected_ids = [case["id"]] if hit else []
            retrieved_ids = [case["id"]] if hit else []
            mode_cases.append(
                {
                    "case_id": case["id"],
                    "case_type": case.get("type", mode),
                    "query": case["query"],
                    "expected": relevant,
                    "retrieved": retrieved,
                    "hit": hit,
                    "recall_at_k": recall_at_k(expected_ids, retrieved_ids, top_k),
                    "mrr_at_k": mrr_at_k(expected_ids, retrieved_ids, top_k),
                    "hit_at_k": hit_at_k(expected_ids, retrieved_ids, top_k),
                    "context_characters": sum(len(item) for item in items),
                    "latency_ms": round(latency_ms, 3),
                }
            )

        hits = [case["hit"] for case in mode_cases]
        mode_reports[mode] = {
            "case_count": len(mode_cases),
            "hit_rate": round(sum(hits) / len(hits), 4) if hits else 0.0,
            "mean_recall_at_k": round(
                sum(case["recall_at_k"] for case in mode_cases) / len(mode_cases), 4
            )
            if mode_cases
            else 0.0,
            "mean_mrr": round(
                sum(case["mrr_at_k"] for case in mode_cases) / len(mode_cases), 4
            )
            if mode_cases
            else 0.0,
            "mean_context_characters": round(
                sum(case["context_characters"] for case in mode_cases) / len(mode_cases), 1
            )
            if mode_cases
            else 0.0,
            "latency_ms": latency_stats([case["latency_ms"] for case in mode_cases]),
            "cases": mode_cases,
        }

    return {
        "framework": "memory",
        "top_k": top_k,
        "modes": list(modes),
        "mode_reports": mode_reports,
    }


class LiveMemoryAdapter:
    """可选的在线记忆适配器，用于未来接入真实 memory client。"""

    def __init__(self, run_id: str, user_id: str = "benchmark"):
        self.run_id = run_id
        self.user_id = user_id

    async def search(self, query: str, top_k: int = 3):
        """调用真实的 memory_client 进行在线记忆检索。"""
        from agentchat.services.memory.client import memory_client

        result = await memory_client.search(
            query=query,
            run_id=self.run_id,
            limit=top_k,
        )
        return [memory.get("memory", "") for memory in result.get("results", [])]
