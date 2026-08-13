import math
from statistics import mean, median
from typing import Dict, List, Sequence


def percentile(values: Sequence[float], p: float) -> float:
    """计算给定百分位数对应的数值，例如 p50、p90、p95。"""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * p / 100.0
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[lower]
    fraction = pos - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def recall_at_k(relevant: Sequence[str], retrieved: Sequence[str], k: int = None) -> float:
    """召回率 @ K：在前 K 个结果中命中多少比例的真实相关项。"""
    if not relevant:
        return 0.0
    if k is None:
        k = len(retrieved)
    hits = len(set(relevant) & set(retrieved[:k]))
    return round(hits / len(relevant), 4)


def mrr_at_k(relevant: Sequence[str], retrieved: Sequence[str], k: int = None) -> float:
    """平均倒数排名（MRR@K）：首个命中项出现在第几位。"""
    if k is None:
        k = len(retrieved)
    relevant_set = set(relevant)
    for index, item in enumerate(retrieved[:k]):
        if item in relevant_set:
            return round(1.0 / (index + 1.0), 4)
    return 0.0


def hit_at_k(relevant: Sequence[str], retrieved: Sequence[str], k: int = None) -> float:
    """命中率 @ K：是否在前 K 个候选中至少命中一个真实结果。"""
    return 1.0 if mrr_at_k(relevant, retrieved, k) > 0 else 0.0


def latency_stats(values: Sequence[float]) -> Dict[str, float]:
    """汇总一组延迟数据的平均值和分位数统计，便于 benchmark 对比。"""
    if not values:
        return {
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p90_ms": 0.0,
            "p95_ms": 0.0,
            "max_ms": 0.0,
        }
    return {
        "mean_ms": round(mean(values), 3),
        "p50_ms": round(percentile(values, 50), 3),
        "p90_ms": round(percentile(values, 90), 3),
        "p95_ms": round(percentile(values, 95), 3),
        "max_ms": round(max(values), 3),
    }


def summarize_retrieval_cases(cases: List[Dict], top_k: int) -> Dict:
    """汇总 RAG 召回 benchmark 的案例结果，输出整体性能摘要。"""
    if not cases:
        return {
            "case_count": 0,
            "top_k": top_k,
            "mean_recall_at_k": 0.0,
            "mean_mrr": 0.0,
            "hit_rate_at_k": 0.0,
            "latency_ms": latency_stats([]),
        }

    recalls = [case["recall_at_k"] for case in cases]
    mrrs = [case["mrr_at_k"] for case in cases]
    hits = [case["hit_at_k"] for case in cases]
    latencies = [case["latency_ms"] for case in cases]

    return {
        "case_count": len(cases),
        "top_k": top_k,
        "mean_recall_at_k": round(mean(recalls), 4),
        "mean_mrr": round(mean(mrrs), 4),
        "hit_rate_at_k": round(mean(hits), 4),
        "median_recall_at_k": round(median(recalls), 4),
        "latency_ms": latency_stats(latencies),
    }
