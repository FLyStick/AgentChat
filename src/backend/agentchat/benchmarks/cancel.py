import asyncio
from contextlib import suppress
from typing import Any, Dict, List

from agentchat.benchmarks.metrics import latency_stats
from agentchat.utils.cancellable_stream import CancellableAsyncStream


def summarize_cancel_runs(runs: List[Dict], threshold_ms: float = 500.0) -> Dict:
    """汇总多轮取消压测结果，并检查是否在阈值内完成终止。"""
    durations = [
        run.get("cancel_to_terminate_ms")
        for run in runs
        if run.get("cancel_to_terminate_ms") is not None
    ]
    if not durations:
        return {
            "runs": len(runs),
            "threshold_ms": threshold_ms,
            "passed_runs": 0,
            "pass_rate": 0.0,
            "cancel_to_terminate_ms": latency_stats([]),
        }
    # 统计在阈值内终止的运行次数
    passed = sum(1 for duration in durations if duration <= threshold_ms)
    return {
        "runs": len(runs),
        "threshold_ms": threshold_ms,
        "passed_runs": passed,
        "pass_rate": round(passed / len(runs), 4),
        "cancel_to_terminate_ms": latency_stats(durations),
    }


async def _simulated_producer(
    queue: asyncio.Queue,
    *,
    initial_delay_ms: float,
    chunk_interval_ms: float,
    chunks: int,
) -> None:
    """模拟异步流式生产者：延迟一段时间后持续放入数据块。"""
    await asyncio.sleep(initial_delay_ms / 1000.0)
    for index in range(chunks):
        queue.put_nowait(f"chunk-{index}")
        await asyncio.sleep(chunk_interval_ms / 1000.0)


async def run_cancel_stress(
    runs: int = 8,
    *,
    initial_delay_ms: float = 200.0,
    chunk_interval_ms: float = 40.0,
    chunks: int = 50,
    cancel_after_ms: float = 400.0,
    threshold_ms: float = 500.0,
) -> Dict[str, Any]:
    """模拟流式输出中途取消，衡量 cancel 到 terminate 的响应延迟。"""

    results: List[Dict[str, Any]] = []
    for run_index in range(runs):
        # 构造一个可取消的异步流，便于模拟中途断流场景
        stream = CancellableAsyncStream(
            lambda queue: _simulated_producer(
                queue,
                initial_delay_ms=initial_delay_ms,
                chunk_interval_ms=chunk_interval_ms,
                chunks=chunks,
            )
        )

        async def cancel_later():
            """在指定时间后请求取消流的消费。"""
            await asyncio.sleep(cancel_after_ms / 1000.0)
            stream.request_cancel()

        canceler = asyncio.create_task(cancel_later())
        received: List[str] = []
        try:
            async for item in stream:
                received.append(item)
        finally:
            if not canceler.done():
                canceler.cancel()
                with suppress(asyncio.CancelledError):
                    await canceler

        summary = stream.summary() or {}
        results.append(
            {
                "run": run_index + 1,
                "received_chunks": len(received),
                **summary,
            }
        )

    return {
        "framework": "cancel_stress",
        "settings": {
            "runs": runs,
            "initial_delay_ms": initial_delay_ms,
            "chunk_interval_ms": chunk_interval_ms,
            "chunks": chunks,
            "cancel_after_ms": cancel_after_ms,
        },
        "runs": results,
        "summary": summarize_cancel_runs(results, threshold_ms),
    }
