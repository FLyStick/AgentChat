from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

from agentchat.benchmarks.live_utils import (
    DEFAULT_STATE_PATH,
    LiveApi,
    load_state,
    save_state,
)
from agentchat.benchmarks.memory import LiveMemoryAdapter
from agentchat.benchmarks.metrics import latency_stats
from agentchat.settings import init_app_settings


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval"
DEFAULT_AGENT_NAME = "LiveBench-MemoryAgent"
DEFAULT_DIALOG_PREFIX = "LiveBench-MemoryDialog"

MEMORY_CASES = [
    {
        "id": "mem_floor",
        "fact": "用户偏好高层酒店房间，并希望房间朝南。",
        "query": "住酒店时我喜欢住在高层还是低层？",
        "expected": "高层",
    },
    {
        "id": "mem_allergy",
        "fact": "用户对鸡蛋过敏，早餐需要特别备注。",
        "query": "我早餐有什么饮食禁忌？",
        "expected": "鸡蛋",
    },
    {
        "id": "mem_expense",
        "fact": "用户的差旅报销通常走公司统一账户。",
        "query": "我的报销款一般打到哪个账户？",
        "expected": "统一账户",
    },
    {
        "id": "mem_device",
        "fact": "用户常用 WiFi 设备是 MacBook Pro。",
        "query": "我平时用什么设备连 WiFi？",
        "expected": "MacBook Pro",
    },
    {
        "id": "mem_meeting",
        "fact": "用户喜欢在工作日早晨安排会议。",
        "query": "我的会议偏好安排在什么时间段？",
        "expected": "工作日",
    },
]


def _hit_in_results(results: List[Dict[str, Any]], expected: str) -> bool:
    text = "\n".join(str(item.get("memory", "")) for item in results)
    return expected in text


def _run_search(
    adapter: LiveMemoryAdapter,
    query: str,
    expected: str,
    run_id: str | None,
    top_k: int,
) -> Dict[str, Any]:
    started = time.perf_counter()
    results = asyncio.run(adapter.search(query, top_k=top_k, run_id=run_id))
    latency_ms = (time.perf_counter() - started) * 1000
    hit = _hit_in_results(results, expected)
    return {
        "run_scope": "same_run" if run_id else "cross_run",
        "query": query,
        "expected": expected,
        "hit": hit,
        "results": [
            {
                "memory": str(item.get("memory", "")),
                "score": item.get("score"),
                "run_id": item.get("run_id"),
                "agent_id": item.get("agent_id"),
                "user_id": item.get("user_id"),
            }
            for item in results[:top_k]
        ],
        "result_count": len(results),
        "latency_ms": round(latency_ms, 3),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-memory",
        description="Run real Chroma memory add/search across dialogs.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--case-ids", nargs="*", default=[])
    return parser


def run_live_memory(args: argparse.Namespace) -> Dict[str, Any]:
    asyncio.run(init_app_settings())

    state_path = Path(args.state).expanduser().resolve()
    state = load_state(state_path)
    if not state.get("token") or not state.get("base_url") or not state.get("user_id"):
        raise RuntimeError("state file has no token/base_url/user_id; run live_seed.py first")

    cases = [case for case in MEMORY_CASES if not args.case_ids or case["id"] in args.case_ids]
    if not cases:
        raise RuntimeError("no memory cases selected")

    session = requests.Session()
    api = LiveApi(
        base_url=state["base_url"],
        client=session,
        token=state["token"],
        timeout=(30.0, 120.0),
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    agent_name = f"{DEFAULT_AGENT_NAME}-{stamp}"
    agent = api.ensure_agent(
        name=agent_name,
        description="P5.6 real memory benchmark agent",
        system_prompt="你是企业知识助手，回答时应优先使用长期记忆中的用户偏好。",
        knowledge_ids=[],
        enable_memory=True,
        enable_multi_agent=False,
    )
    agent_id = str(agent.get("id") or agent.get("agent_id") or "")
    if not agent_id:
        raise RuntimeError("ensure_agent returned no agent id")

    dialog_a = api.create_dialog(f"{DEFAULT_DIALOG_PREFIX}-A-{stamp}", agent_id)
    dialog_b = api.create_dialog(f"{DEFAULT_DIALOG_PREFIX}-B-{stamp}", agent_id)
    run_a = str(dialog_a.get("dialog_id") or "")
    run_b = str(dialog_b.get("dialog_id") or "")
    if not run_a or not run_b:
        raise RuntimeError("create_dialog returned no dialog_id")

    adapter = LiveMemoryAdapter(run_id=run_a, user_id=state["user_id"], agent_id=agent_id)

    before = asyncio.run(adapter.search(cases[0]["query"], top_k=args.top_k, run_id=None))
    before_results = [
        {"memory": str(item.get("memory", "")), "score": item.get("score")}
        for item in before
    ]

    write_started = time.perf_counter()
    write_result = asyncio.run(
        adapter.add(
            messages=[{"role": "user", "content": case["fact"]} for case in cases],
            infer=False,
        )
    )
    write_latency_ms = (time.perf_counter() - write_started) * 1000
    written = write_result.get("results", [])
    written_items = [
        {
            "memory": str(item.get("memory", "")),
            "event": item.get("event"),
            "id": item.get("id"),
            "role": item.get("role"),
        }
        for item in written
    ]

    same_run_cases = [
        _run_search(adapter, case["query"], case["expected"], run_id=run_a, top_k=args.top_k)
        for case in cases
    ]
    cross_run_cases = [
        _run_search(adapter, case["query"], case["expected"], run_id=None, top_k=args.top_k)
        for case in cases
    ]

    latency_values = [case["latency_ms"] for case in same_run_cases + cross_run_cases]
    same_hits = sum(case["hit"] for case in same_run_cases)
    cross_hits = sum(case["hit"] for case in cross_run_cases)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    result = {
        "stage": "p5.6",
        "created_at": created_at,
        "user_id": state["user_id"],
        "agent_name": agent_name,
        "agent_id": agent_id,
        "run_a_dialog_id": run_a,
        "run_b_dialog_id": run_b,
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "base_url": state.get("base_url", ""),
        },
        "settings": {
            "top_k": args.top_k,
            "case_count": len(cases),
            "infer": False,
            "vector_db": "chroma",
        },
        "before_write": {
            "cross_run_result_count": len(before),
            "results": before_results,
        },
        "write": {
            "requested_facts": [case["fact"] for case in cases],
            "returned_item_count": len(written),
            "inserted_ids": [item.get("id") for item in written],
            "unique_ids": len({item.get("id") for item in written}),
            "items": written_items,
            "latency_ms": round(write_latency_ms, 3),
        },
        "searches": {
            "same_run": same_run_cases,
            "cross_run": cross_run_cases,
        },
        "summary": {
            "case_count": len(cases),
            "write_count": len(written),
            "search_count": len(same_run_cases) + len(cross_run_cases),
            "before_write_result_count": len(before),
            "same_run_hit_count": same_hits,
            "same_run_hit_rate": round(same_hits / len(cases), 4),
            "cross_run_hit_count": cross_hits,
            "cross_run_hit_rate": round(cross_hits / len(cases), 4),
            "pass_count": cross_hits,
            "pass_rate": round(cross_hits / len(cases), 4),
            "mean_recall_at_k": round(cross_hits / len(cases), 4),
            "mean_mrr": round(
                statistics.mean(
                    [
                        1.0 if case["hit"] else 0.0
                        for case in cross_run_cases
                    ]
                ),
                4,
            ),
            "latency_ms": latency_stats(latency_values),
        },
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"live_memory_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state["last_memory_agent_name"] = agent_name
    state["last_memory_agent_id"] = agent_id
    state["last_memory_run_a"] = run_a
    state["last_memory_run_b"] = run_b
    state["last_memory_at"] = created_at
    save_state(state_path, state)

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_live_memory(args)
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
