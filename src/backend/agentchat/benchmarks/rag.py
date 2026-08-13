import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Sequence

from agentchat.benchmarks.metrics import hit_at_k, mrr_at_k, recall_at_k
from agentchat.benchmarks.metrics import summarize_retrieval_cases


# 基准测试数据集目录
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "rag"
# 词法分词规则：英文按单词，中文按字/词块切分，便于 RAG 召回命中统计
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


@dataclass
class BenchmarkDocument:
    """基准数据中的单个文档块。"""

    chunk_id: str
    content: str
    summary: str = ""
    file_name: str = "benchmark_fixture.md"
    tags: Sequence[str] = ()

    def to_search_result(self, score: float):
        """将文档转换为检索结果对象，保持与真实检索接口一致的字段结构。"""
        return SimpleNamespace(
            chunk_id=self.chunk_id,
            content=self.content,
            summary=self.summary,
            score=score,
            file_id="benchmark_kb",
            file_name=self.file_name,
            knowledge_id="benchmark_kb",
            update_time="fixture",
        )


def _tokens(text: str):
    """提取文本中的 token 集合，用于衡量词重叠度。"""
    return set(TOKEN_RE.findall(text.lower()))


def lexical_similarity(query: str, document: str) -> float:
    """计算查询与文档之间的词法相似度，作为离线检索基准的打分函数。"""
    query_tokens = _tokens(query)
    doc_tokens = _tokens(document)
    if not query_tokens or not doc_tokens:
        return 0.0
    overlap = len(query_tokens & doc_tokens)
    if overlap == 0:
        return 0.0
    # 这里使用两部分比例叠加：查询覆盖率 + 文档覆盖率，加权强调词重叠。
    return round(overlap / len(query_tokens) + 0.15 * overlap / len(doc_tokens), 4)


class OfflineRetriever:
    """ 离线词法检索器：不依赖外部服务，便于稳定复现 benchmark。"""

    def __init__(self, documents: Sequence[BenchmarkDocument]):
        self.documents = list(documents)

    async def search(self, query: str, top_k: int = 10):
        """在文档集合中计算相关度并返回前 top_k 个结果。"""
        scored = []
        for document in self.documents:
            score = lexical_similarity(query, document.content + " " + document.summary)
            # 如果查询词命中文档标签，也给予轻微加权，模拟标签召回增强。
            tag_tokens = _tokens(" ".join(document.tags))
            if _tokens(query) & tag_tokens:
                score += 0.2
            if score > 0:
                scored.append((score, document))
        scored.sort(key=lambda row: row[0], reverse=True)
        return [document.to_search_result(score) for score, document in scored[:top_k]]


class LiveRetriever:
    """真实生产环境 RAG 检索器适配器，用于未来接入线上检索服务。"""

    def __init__(self, knowledge_ids: Sequence[str], index_names: Optional[Sequence[str]] = None):
        self.knowledge_ids = list(knowledge_ids)
        self.index_names = list(index_names) if index_names else None

    async def search(self, query: str, top_k: int = 10):
        """通过真实的 RagHandler 进行检索，适配在线环境 benchmark。"""
        from agentchat.services.rag.handler import RagHandler

        documents = await RagHandler.mix_retrival_documents(
            [query],
            self.knowledge_ids,
            "content",
            self.index_names,
        )
        return documents[:top_k]


def load_rag_fixtures() -> Dict:
    """加载 RAG benchmark 的静态 fixture 数据，包括 doc 和 query。"""
    path = FIXTURE_DIR / "dataset.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["docs"] = [
        BenchmarkDocument(
            chunk_id=item["chunk_id"],
            content=item["content"],
            summary=item.get("summary", ""),
            file_name=item.get("file_name", "benchmark_fixture.md"),
            tags=item.get("tags", []),
        )
        for item in data["docs"]
    ]
    return data


def _document_ids(results: Sequence) -> List[str]:
    """统一提取结果中的 chunk_id，便于后续评估召回命中情况。"""
    ids = []
    for result in results:
        if isinstance(result, dict):
            ids.append(result.get("chunk_id", ""))
        else:
            ids.append(getattr(result, "chunk_id", ""))
    return ids


async def run_rag_benchmark(retriever, dataset: Optional[Dict] = None, top_k: int = 10) -> Dict:
    """执行 RAG 基准评测，并返回召回指标与延迟统计结果。"""
    dataset = dataset or load_rag_fixtures()
    cases = []

    for query_case in dataset["queries"]:
        started = time.perf_counter()
        results = await retriever.search(query_case["query"], top_k)
        latency_ms = (time.perf_counter() - started) * 1000

        relevant = query_case["ground_truth"]
        retrieved = _document_ids(results)
        cases.append(
            {
                "query_id": query_case["id"],
                "query": query_case["query"],
                "difficulty": query_case.get("difficulty", "normal"),
                "expected": relevant,
                "retrieved": retrieved,
                "recall_at_k": recall_at_k(relevant, retrieved, top_k),
                "mrr_at_k": mrr_at_k(relevant, retrieved, top_k),
                "hit_at_k": hit_at_k(relevant, retrieved, top_k),
                "latency_ms": round(latency_ms, 3),
            }
        )

    return {
        "framework": "rag",
        "top_k": top_k,
        "retriever": retriever.__class__.__name__,
        "dataset": "src/backend/agentchat/benchmarks/fixtures/rag/dataset.json",
        "cases": cases,
        "summary": summarize_retrieval_cases(cases, top_k),
    }
