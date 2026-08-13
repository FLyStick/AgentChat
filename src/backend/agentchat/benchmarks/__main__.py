import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional


def _build_parser() -> argparse.ArgumentParser:
    """构建 benchmark 的命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="agentchat-benchmarks",
        description="可复现的 RAG、记忆与断流评测入口。",
    )
    # 通过 subparsers 支持多类 benchmark 命令
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- RAG 召回 benchmark ---
    rag = subparsers.add_parser("rag", help="运行 RAG 召回基准")
    rag.add_argument("--top-k", type=int, default=5, help="返回前 K 个召回结果参与评测")
    rag.add_argument("--output", type=Path, help="将结果写入指定 JSON 文件；不填则输出到 stdout")
    rag.set_defaults(func=_run_rag)

    # --- 记忆 benchmark ---
    memory = subparsers.add_parser("memory", help="运行记忆基准")
    memory.add_argument(
        "--mode",
        choices=["short_term", "summary", "long_term"],
        action="append",
        default=[],
        help="选择要评估的记忆模式，可多次传入",
    )
    memory.add_argument("--top-k", type=int, default=3, help="记忆检索的前 K 个结果参与评估")
    memory.add_argument("--output", type=Path, help="将结果写入指定 JSON 文件")
    memory.set_defaults(func=_run_memory)

    # --- 断流/取消压测 ---
    cancel = subparsers.add_parser("cancel", help="运行断流取消压测")
    cancel.add_argument("--runs", type=int, default=8, help="压测总轮次")
    cancel.add_argument("--initial-delay-ms", type=float, default=300.0, help="首次流式块延迟（ms）")
    cancel.add_argument("--chunk-interval-ms", type=float, default=40.0, help="块间隔时间（ms）")
    cancel.add_argument("--chunks", type=int, default=80, help="每轮模拟的输出块数量")
    cancel.add_argument("--cancel-after-ms", type=float, default=500.0, help="多久后触发取消（ms）")
    cancel.add_argument("--threshold-ms", type=float, default=500.0, help="取消时延阈值（ms）")
    cancel.add_argument("--output", type=Path, help="将结果写入指定 JSON 文件")
    cancel.set_defaults(func=_run_cancel)

    return parser


async def _run_rag(args) -> dict:
    """执行 RAG 召回评测：加载 fixture，构造离线检索器并返回指标结果。"""
    from agentchat.benchmarks.rag import (
        OfflineRetriever,
        load_rag_fixtures,
        run_rag_benchmark,
    )

    dataset = load_rag_fixtures()
    retriever = OfflineRetriever(dataset["docs"])
    return await run_rag_benchmark(
        retriever,
        dataset=dataset,
        top_k=args.top_k,
    )


async def _run_memory(args) -> dict:
    """执行记忆 benchmark，支持多个模式的组合评测。"""
    from agentchat.benchmarks.memory import MODES, run_offline_memory_benchmark

    modes = args.mode or list(MODES)
    return run_offline_memory_benchmark(modes=modes, top_k=args.top_k)


async def _run_cancel(args) -> dict:
    """执行取消中断场景的压测，观察任务是否在指定阈值内稳定退出。"""
    from agentchat.benchmarks.cancel import run_cancel_stress

    return await run_cancel_stress(
        runs=args.runs,
        initial_delay_ms=args.initial_delay_ms,
        chunk_interval_ms=args.chunk_interval_ms,
        chunks=args.chunks,
        cancel_after_ms=args.cancel_after_ms,
        threshold_ms=args.threshold_ms,
    )


def _emit(result: dict, output: Optional[Path]) -> None:
    """将 benchmark 结果以 JSON 形式输出到文件或标准输出。"""
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if output:
        output.write_text(text, encoding="utf-8")
        return
    sys.stdout.buffer.write(text.encode("utf-8"))


def main(argv=None) -> None:
    """入口函数：解析命令行参数、执行对应 benchmark，并输出结果。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    # 根据命令绑定的 func 执行对应 benchmark
    result = asyncio.run(args.func(args))
    _emit(result, args.output)


if __name__ == "__main__":
    main()
