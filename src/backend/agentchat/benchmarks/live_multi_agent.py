from __future__ import annotations

import argparse
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
from agentchat.benchmarks.metrics import latency_stats


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval"
DEFAULT_AGENT_NAME = "LiveBench-MultiAgent"
DEFAULT_DIALOG_PREFIX = "LiveBench-MultiAgentDialog"

MULTI_AGENT_CASES = [
    {
        "id": "ma_leave",
        "query": "请病假需要提前多久申请？",
        "routes": ["policy_agent"],
    },
    {
        "id": "ma_hotel_checkin",
        "query": "酒店几点可以办理入住和退房？",
        "routes": ["hotel_agent"],
    },
    {
        "id": "ma_project_deploy",
        "query": "项目后端启动命令是什么？",
        "routes": ["project_agent"],
    },
    {
        "id": "ma_expense",
        "query": "报销多久能到账？",
        "routes": ["policy_agent"],
    },
    {
        "id": "ma_wifi",
        "query": "客房Wi-Fi密码是多少？",
        "routes": ["hotel_agent"],
    },
]


def _run_multi_agent_case(
    api: LiveApi,
    dialog_id: str,
    case: Dict[str, Any],
) -> Dict[str, Any]:
    query = case["query"]
    started = time.perf_counter()
    chunks: List[str] = []
    first_chunk_ms: float | None = None
    events: List[Dict[str, Any]] = []
    stream_error: str | None = None

    try:
        for event in api.stream_completion(dialog_id, query):
            if event.get("type") == "response_chunk":
                chunk = str((event.get("data") or {}).get("chunk", "") or "")
                if chunk:
                    if first_chunk_ms is None:
                        first_chunk_ms = round((time.perf_counter() - started) * 1000, 3)
                    chunks.append(chunk)
            elif event.get("type") == "event":
                events.append(event.get("data") or {})
    except (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.ReadTimeout,
        requests.exceptions.JSONDecodeError,
        json.JSONDecodeError,
    ) as exc:
        stream_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    total_latency_ms = round((time.perf_counter() - started) * 1000, 3)
    answer = "".join(chunks).strip()

    routes: List[str] = []
    plan_events = [event for event in events if event.get("event_type") == "agent_plan"]
    if plan_events:
        routes = [str(item) for item in (plan_events[-1].get("routes") or [])]

    started_names = [
        str(event.get("agent_name", ""))
        for event in events
        if event.get("event_type") == "sub_agent_start"
    ]
    ended_names = [
        str(event.get("agent_name", ""))
        for event in events
        if event.get("event_type") == "sub_agent_end"
    ]

    route_ok = sorted(routes) == sorted(case["routes"])
    pair_ok = bool(started_names) and started_names == ended_names
    error_events = [
        event
        for event in events
        if event.get("status") == "ERROR"
    ]
    response_ok = bool(answer) and stream_error is None
    lifecycle_ok = (
        any(event.get("event_type") == "agent_start" for event in events)
        and any(event.get("event_type") == "agent_plan" for event in events)
        and any(event.get("event_type") == "agent_end" for event in events)
    )
    tool_start_count = sum(
        event.get("status") == "START"
        and ("执行工具" in str(event.get("title", "")))
        for event in events
    )
    tool_end_count = sum(
        event.get("status") == "END"
        and ("执行工具" in str(event.get("title", "")))
        for event in events
    )
    sub_tool_calls = [
        int(event.get("tool_calls", 0) or 0)
        for event in events
        if event.get("event_type") == "sub_agent_end"
    ]
    sub_agent_tool_calls_total = sum(sub_tool_calls)

    case_ok = bool(
        route_ok
        and pair_ok
        and response_ok
        and lifecycle_ok
        and not error_events
    )

    return {
        "case_id": case["id"],
        "query": query,
        "expected_routes": case["routes"],
        "latency_ms": total_latency_ms,
        "first_chunk_ms": first_chunk_ms,
        "routes": routes,
        "route_ok": route_ok,
        "started_subagents": started_names,
        "ended_subagents": ended_names,
        "sub_agent_pair_ok": pair_ok,
        "has_response_chunk": bool(chunks),
        "lifecycle_ok": lifecycle_ok,
        "response_ok": response_ok,
        "stream_error": stream_error,
        "error_event_count": len(error_events),
        "error_events": [
            {
                "event_type": event.get("event_type"),
                "agent_name": event.get("agent_name"),
                "title": event.get("title"),
                "message": str(event.get("message", ""))[:500],
            }
            for event in error_events
        ],
        "tool_start_count": tool_start_count,
        "tool_end_count": tool_end_count,
        "sub_agent_tool_calls": sub_tool_calls,
        "sub_agent_tool_calls_total": sub_agent_tool_calls_total,
        "event_type_counts": {
            event_type: sum(
                1 for event in events if event.get("event_type") == event_type
            )
            for event_type in (
                "agent_start",
                "agent_plan",
                "sub_agent_start",
                "sub_agent_end",
                "agent_end",
                "agent_event",
            )
        },
        "answer_length": len(answer),
        "answer": answer,
        "case_ok": case_ok,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-multi-agent",
        description="Run real /api/v1/completion with enable_multi_agent=True.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--case-ids", nargs="*", default=[])
    return parser


def run_live_multi_agent(args: argparse.Namespace) -> Dict[str, Any]:
    state_path = Path(args.state).expanduser().resolve()
    state = load_state(state_path)
    if not state.get("token") or not state.get("base_url"):
        raise RuntimeError("state file has no token/base_url; run live_seed.py first")

    cases = [
        case for case in MULTI_AGENT_CASES
        if not args.case_ids or case["id"] in args.case_ids
    ]
    if not cases:
        raise RuntimeError("no multi-agent cases selected")

    session = requests.Session()
    api = LiveApi(
        base_url=state["base_url"],
        client=session,
        token=state["token"],
        timeout=(30.0, 600.0),
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    agent_name = f"{DEFAULT_AGENT_NAME}-{stamp}"
    agent = api.ensure_agent(
        name=agent_name,
        description="P5.7 real multi-agent benchmark agent",
        system_prompt=(
            "你是企业多 Agent 调度助手，负责制度、酒店与项目三类场景。"
            "请根据用户问题完成编排并输出最终答案。"
        ),
        knowledge_ids=[],
        enable_memory=True,
        enable_multi_agent=True,
    )
    agent_id = str(agent.get("id") or agent.get("agent_id") or "")
    if not agent_id:
        raise RuntimeError("ensure_agent returned no agent id")

    cases_result: List[Dict[str, Any]] = []
    for index, case in enumerate(cases, start=1):
        dialog = api.create_dialog(
            f"{DEFAULT_DIALOG_PREFIX}-{stamp}-{index}",
            agent_id,
        )
        dialog_id = str(dialog.get("dialog_id") or "")
        if not dialog_id:
            raise RuntimeError("create_dialog returned no dialog_id")
        case_result = _run_multi_agent_case(api, dialog_id, case)
        case_result["dialog_id"] = dialog_id
        cases_result.append(case_result)

    ok_cases = [case for case in cases_result if case["case_ok"]]
    latency_values = [case["latency_ms"] for case in cases_result]
    first_chunk_values = [
        case["first_chunk_ms"]
        for case in cases_result
        if case["first_chunk_ms"] is not None
    ]
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    result = {
        "stage": "p5.7",
        "created_at": created_at,
        "agent_name": agent_name,
        "agent_id": agent_id,
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "base_url": state.get("base_url", ""),
        },
        "settings": {
            "enable_multi_agent": True,
            "case_count": len(cases),
            "case_ids": [case["id"] for case in cases],
            "orchestrator": "build_demo_orchestrator",
        },
        "cases": cases_result,
        "summary": {
            "case_count": len(cases),
            "pass_count": len(ok_cases),
            "pass_rate": round(len(ok_cases) / len(cases), 4),
            "route_match_count": sum(case["route_ok"] for case in cases_result),
            "route_match_rate": round(
                sum(case["route_ok"] for case in cases_result) / len(cases), 4
            ),
            "sub_agent_pair_count": sum(case["sub_agent_pair_ok"] for case in cases_result),
            "response_ok_count": sum(case["response_ok"] for case in cases_result),
            "error_case_count": sum(case["error_event_count"] > 0 for case in cases_result),
            "tool_start_count": sum(case["tool_start_count"] for case in cases_result),
            "tool_end_count": sum(case["tool_end_count"] for case in cases_result),
            "sub_agent_tool_calls_total": sum(
                int(case.get("sub_agent_tool_calls_total") or 0)
                for case in cases_result
            ),
            "latency_ms": latency_stats(latency_values),
            "first_chunk_ms": latency_stats(first_chunk_values),
        },
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"live_multi_agent_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state["last_multi_agent_name"] = agent_name
    state["last_multi_agent_id"] = agent_id
    state["last_multi_agent_dialog_ids"] = [case["dialog_id"] for case in cases_result]
    state["last_multi_agent_at"] = created_at
    save_state(state_path, state)

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_live_multi_agent(args)
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
