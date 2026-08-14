from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

from agentchat.benchmarks.live_utils import (
    DEFAULT_STATE_PATH,
    ApiError,
    LiveApi,
    load_state,
    save_state,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GROUND_TRUTH = REPO_ROOT / "docs" / "eval" / "live_rag_ground_truth.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval"
DEFAULT_AGENT_NAME = "LiveBench-HotelFAQ"
DEFAULT_DIALOG_NAME = "LiveBench-HotelFAQ-Dialog"

HOTEL_IDS = [
    "live_q_checkin",
    "live_q_wifi",
    "live_q_breakfast",
    "live_q_shuttle",
    "live_q_gym",
    "live_q_pool",
    "live_q_pet",
    "live_q_minibar",
    "live_q_laundry",
    "live_q_parking",
]
PROJECT_IDS = [
    "live_q_deploy",
    "live_q_rag_chain",
    "live_q_docker_deps",
    "live_q_completion_sse",
    "live_q_multi_agent",
    "live_q_agent_create",
    "live_q_memory",
    "live_q_rewrite",
    "live_q_rerank",
    "live_q_cancel",
]
INTERNAL_IDS = [
    "live_q_leave",
    "live_q_expense",
    "live_q_overtime",
    "live_q_onboarding",
    "live_q_performance",
    "live_q_travel",
    "live_q_annual_leave",
    "live_q_meeting",
    "live_q_allowance",
    "live_q_flex_time",
]

_LITERAL_RE = re.compile(
    r"[A-Za-z][A-Za-z0-9_./:-]*|\d+(?:\.\d+)?(?::[0-9]+)?"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def _literal_terms(text: str) -> List[str]:
    return [match.group(0) for match in _LITERAL_RE.finditer(text)]


def _fact_coverage(expected_fact: str, answer: str) -> Dict[str, Any]:
    normalized_fact = _normalize(expected_fact)
    normalized_answer = _normalize(answer)
    full_match = bool(normalized_fact) and normalized_fact in normalized_answer
    terms = list(dict.fromkeys(_literal_terms(normalized_fact)))
    matched_terms = [term for term in terms if term in normalized_answer]
    if terms:
        coverage = len(matched_terms) / len(terms)
    else:
        coverage = 1.0 if full_match else 0.0
    return {
        "full_match": full_match,
        "term_count": len(terms),
        "matched_term_count": len(matched_terms),
        "matched_terms": matched_terms,
        "coverage": round(coverage, 4),
    }


def _case_coverage(case: Dict[str, Any]) -> Dict[str, Any]:
    fact_results = case.get("fact_results", [])
    if not fact_results:
        return {
            "fact_count": 0,
            "average_term_coverage": 0.0,
            "full_match_count": 0,
        }
    average_coverage = statistics.mean(item["coverage"] for item in fact_results)
    full_match_count = sum(item["full_match"] for item in fact_results)
    return {
        "fact_count": len(fact_results),
        "average_term_coverage": round(average_coverage, 4),
        "full_match_count": full_match_count,
    }


def _p90(values: Sequence[float]) -> float | None:
    """Return the 90th percentile, supporting single-sample runs."""
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(values, n=10)[8], 3)


def _knowledge_id_list(value: Any) -> List[str]:
    """Normalize a single knowledge id or a list into a list of ids."""
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    return [str(item) for item in value]


def _read_usage_totals(api: LiveApi, agent_name: str) -> Dict[str, int]:
    data = api.get_usage(agent=agent_name, delta_days=1)
    count_data = api.request(
        "POST",
        "/api/v1/usage_count",
        json={"agent": agent_name, "model": None, "delta_days": 1},
    ).get("data") or {}

    input_tokens = 0
    output_tokens = 0
    call_count = 0
    for date_group in data.values():
        agent_group = (date_group or {}).get("agent", {})
        entry = agent_group.get(agent_name, {}) or {}
        input_tokens += int(entry.get("input_tokens", 0) or 0)
        output_tokens += int(entry.get("output_tokens", 0) or 0)
    for date_group in count_data.values():
        agent_group = (date_group or {}).get("agent", {}) or {}
        call_count += int(agent_group.get(agent_name, 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "call_count": call_count,
    }


def _select_query_cases(
    ground_truth: Dict[str, Any],
    limit: int,
    query_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    by_id = {case.get("id", ""): case for case in ground_truth.get("queries", [])}
    if query_ids:
        selected = [by_id[query_id] for query_id in query_ids if query_id in by_id]
    else:
        per_domain = max(1, math.ceil(limit / 3))
        selected: List[Dict[str, Any]] = []
        for domain_ids in (HOTEL_IDS, INTERNAL_IDS, PROJECT_IDS):
            selected.extend(
                by_id[query_id] for query_id in domain_ids if query_id in by_id
            )
        selected = selected[:limit]
    return selected


def _run_completion_case(
    api: LiveApi,
    dialog_id: str,
    query_case: Dict[str, Any],
) -> Dict[str, Any]:
    query = query_case.get("query", "")
    started = time.perf_counter()
    chunks: List[str] = []
    first_chunk_ms: float | None = None
    event_type_counts: Dict[str, int] = {}
    tool_events: List[Dict[str, Any]] = []
    error_events: List[Dict[str, Any]] = []
    stream_error: str | None = None

    try:
        for event in api.stream_completion(dialog_id, query):
            event_type = event.get("type", "")
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
            data = event.get("data") or {}
            if event_type == "response_chunk":
                chunk = str(data.get("chunk", "") or "")
                if chunk:
                    if first_chunk_ms is None:
                        first_chunk_ms = round(
                            (time.perf_counter() - started) * 1000, 3
                        )
                    chunks.append(chunk)
            elif event_type == "event":
                status = data.get("status")
                tool_name = data.get("tool_name")
                if tool_name:
                    tool_events.append(
                        {
                            "tool_name": tool_name,
                            "status": status,
                            "tool_type": data.get("tool_type"),
                            "duration_ms": data.get("duration_ms"),
                        }
                    )
                if status == "ERROR":
                    raw_error = data.get("error") or {}
                    if isinstance(raw_error, dict):
                        error_message = str(raw_error.get("message", ""))
                    else:
                        error_message = str(raw_error)
                    error_events.append(
                        {
                            "title": data.get("title"),
                            "error": error_message[:500],
                        }
                    )
    except (requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.ReadTimeout,
            requests.exceptions.JSONDecodeError,
            ApiError,
            json.JSONDecodeError) as exc:
        stream_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    total_latency_ms = round((time.perf_counter() - started) * 1000, 3)
    answer = "".join(chunks)
    failed_placeholder = "知识盲区" in answer
    has_content = bool(answer.strip())
    completed = stream_error is None

    tool_start_count = sum(
        item["status"] == "START" for item in tool_events
    )
    tool_end_count = sum(item["status"] == "END" for item in tool_events)
    tool_error_count = sum(item["status"] == "ERROR" for item in tool_events)
    knowledge_called = any(
        item["tool_name"] == "retrival_knowledge" for item in tool_events
    )
    knowledge_ok = (
        knowledge_called
        and tool_start_count > 0
        and tool_error_count == 0
        and tool_end_count == tool_start_count
    )
    answer_starts_with_rewrite_list = bool(
        re.match(r"^\s*\[[\"']", answer)
    )
    no_knowledge_evidence = any(
        marker in answer
        for marker in ("知识库中暂未收录", "未检索到相关内容", "No relevant documents found.")
    )
    knowledge_content_ok = (
        knowledge_ok
        and not answer_starts_with_rewrite_list
        and not no_knowledge_evidence
    )

    fact_results = [
        _fact_coverage(fact, answer)
        for fact in query_case.get("expected_facts", [])
    ]
    case = {
        "query_id": query_case.get("id", ""),
        "query": query,
        "difficulty": query_case.get("difficulty", "normal"),
        "latency_ms": total_latency_ms,
        "first_chunk_ms": first_chunk_ms,
        "has_content": has_content,
        "stream_completed": completed,
        "stream_error": stream_error,
        "failed_placeholder": failed_placeholder,
        "event_type_counts": event_type_counts,
        "tool_events": tool_events,
        "tool_call_count": tool_start_count,
        "tool_end_count": tool_end_count,
        "tool_error_count": tool_error_count,
        "knowledge_called": knowledge_called,
        "knowledge_ok": knowledge_ok,
        "answer_starts_with_rewrite_list": answer_starts_with_rewrite_list,
        "no_knowledge_evidence": no_knowledge_evidence,
        "knowledge_content_ok": knowledge_content_ok,
        "error_events": error_events,
        "fact_results": fact_results,
        "coverage": _case_coverage({"fact_results": fact_results}),
        "answer_length": len(answer),
        "answer": answer,
    }
    case["case_ok"] = bool(
        has_content
        and completed
        and not failed_placeholder
        and not error_events
    )
    return case


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-completion",
        description="Use the real /api/v1/completion SSE link for fact-based questions.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument(
        "--ground-truth",
        type=Path,
        default=DEFAULT_GROUND_TRUTH,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=15, help="default: 15 (5 per domain)")
    parser.add_argument("--query-ids", nargs="*", default=[], help="explicit ground truth ids")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="create a new agent and dialog instead of reusing state",
    )
    return parser


def run_live_completion(args: argparse.Namespace) -> Dict[str, Any]:
    state_path = Path(args.state).expanduser().resolve()
    state = load_state(state_path)
    if not state.get("token") or not state.get("base_url"):
        raise RuntimeError("state file has no token/base_url; run live_seed.py first")

    ground_truth = json.loads(
        Path(args.ground_truth).expanduser().resolve().read_text(encoding="utf-8")
    )
    cases_input = _select_query_cases(ground_truth, args.limit, args.query_ids)
    if not cases_input:
        raise RuntimeError("no ground-truth queries selected")

    session = requests.Session()
    api = LiveApi(
        base_url=state["base_url"],
        client=session,
        token=state["token"],
        timeout=(30.0, 300.0),
    )

    agent_name = state.get("last_agent_name") or DEFAULT_AGENT_NAME
    if args.fresh:
        agent_name = f"{DEFAULT_AGENT_NAME}-{datetime.now().strftime('%H%M%S')}"

    system_prompt = (
        "你是企业知识助手，负责回答酒店FAQ、项目手册和内部制度相关的问题。"
        "请先检索知识库，只能依据检索到的内容回答；不要编造事实。"
        "答案用中文，简洁完整，并保留关键数字、时间、名称和费用信息。"
        "必须调用 retrival_knowledge；只能依据工具返回的原文回答，"
        "不得输出查询改写列表、候选 query 或工具内部处理过程。"
    )
    agent = api.ensure_agent(
        name=agent_name,
        description="P5.4 live completion benchmark agent",
        system_prompt=system_prompt,
        knowledge_ids=_knowledge_id_list(state.get("knowledge_id")),
        enable_memory=True,
        enable_multi_agent=False,
    )
    agent_id = str(agent.get("id") or agent.get("agent_id") or "")
    if not agent_id:
        raise RuntimeError("ensure_agent returned no agent id")

    dialog_id = state.get("dialog_id") if not args.fresh else None
    dialog_name = state.get("dialog_name") or DEFAULT_DIALOG_NAME
    dialog = None
    if dialog_id:
        for item in api.list_dialogs():
            if item.get("dialog_id") == dialog_id:
                dialog = item
                break
    if dialog is None:
        dialog_name = f"{dialog_name}-{datetime.now().strftime('%H%M%S')}" if args.fresh else dialog_name
        dialog = api.create_dialog(dialog_name, agent_id)
        dialog_id = str(dialog.get("dialog_id") or "")
        if not dialog_id:
            raise RuntimeError("create_dialog returned no dialog_id")

    dialog_agent_name = ""
    dialogs = api.list_dialogs()
    for item in dialogs:
        if item.get("dialog_id") == dialog_id:
            dialog_agent = item.get("agent") if isinstance(item.get("agent"), dict) else item
            dialog_agent_name = str(
                dialog_agent.get("name", "")
                if isinstance(dialog_agent, dict)
                else ""
            )
            break

    usage_before = _read_usage_totals(api, agent_name)
    cases: List[Dict[str, Any]] = []
    for query_case in cases_input:
        cases.append(_run_completion_case(api, dialog_id, query_case))
    usage_after = _read_usage_totals(api, agent_name)

    usage_delta = {
        key: int(usage_after.get(key, 0)) - int(usage_before.get(key, 0))
        for key in ("input_tokens", "output_tokens", "total_tokens", "call_count")
    }
    models_used = api.request("GET", "/api/v1/usage/models_list").get("data") or []

    ok_cases = [case for case in cases if case["case_ok"]]
    knowledge_ok_cases = [case for case in cases if case["knowledge_ok"]]
    knowledge_content_ok_cases = [
        case for case in cases if case["knowledge_content_ok"]
    ]
    fact_coverages = [case["coverage"]["average_term_coverage"] for case in cases]
    full_match_count = sum(case["coverage"]["full_match_count"] for case in cases)
    fact_count = sum(case["coverage"]["fact_count"] for case in cases)
    latency_values = [case["latency_ms"] for case in cases]
    first_chunk_values = [
        case["first_chunk_ms"]
        for case in cases
        if case["first_chunk_ms"] is not None
    ]

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {
        "stage": "p5.4",
        "created_at": created_at,
        "dataset_name": ground_truth.get("dataset_name", ""),
        "knowledge_id": state.get("knowledge_id", ""),
        "agent_name": agent_name,
        "agent_id": agent_id,
        "dialog_id": dialog_id,
        "dialog_agent_name": dialog_agent_name,
        "agent_name_confirmed": dialog_agent_name == agent_name,
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "base_url": state.get("base_url", ""),
        },
        "cases": cases,
        "summary": {
            "query_count": len(cases),
            "stream_completed_count": sum(case["stream_completed"] for case in cases),
            "case_ok_count": len(ok_cases),
            "case_ok_rate": round(len(ok_cases) / len(cases), 4),
            "knowledge_ok_count": len(knowledge_ok_cases),
            "knowledge_ok_rate": round(len(knowledge_ok_cases) / len(cases), 4),
            "knowledge_content_ok_count": len(knowledge_content_ok_cases),
            "knowledge_content_ok_rate": round(
                len(knowledge_content_ok_cases) / len(cases), 4
            ),
            "rewrite_list_start_case_count": sum(
                case["answer_starts_with_rewrite_list"] for case in cases
            ),
            "no_knowledge_evidence_case_count": sum(
                case["no_knowledge_evidence"] for case in cases
            ),
            "tool_error_case_count": sum(case["tool_error_count"] > 0 for case in cases),
            "latency_ms": {
                "mean": round(statistics.mean(latency_values), 3),
                "p50": round(statistics.median(latency_values), 3),
                "p90": _p90(latency_values),
                "max": round(max(latency_values), 3),
            },
            "first_chunk_ms": {
                "count": len(first_chunk_values),
                "mean": round(statistics.mean(first_chunk_values), 3)
                if first_chunk_values
                else None,
                "p50": round(statistics.median(first_chunk_values), 3)
                if first_chunk_values
                else None,
                "max": round(max(first_chunk_values), 3)
                if first_chunk_values
                else None,
            },
            "fact_term_coverage_joined": round(
                statistics.mean(fact_coverages), 4
            ),
            "full_fact_match_count": full_match_count,
            "full_fact_match_rate": round(
                full_match_count / fact_count, 4
            )
            if fact_count
            else 0.0,
            "usage_delta": usage_delta,
            "usage_snapshot_after": usage_after,
            "models_used": models_used,
            "answered_tokens": sum(case["answer_length"] for case in cases),
        },
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"live_completion_{timestamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state["last_agent_name"] = agent_name
    state["agent_id"] = agent_id
    state["dialog_id"] = dialog_id
    state["dialog_name"] = dialog_name
    state["last_completion_at"] = created_at
    save_state(state_path, state)

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_live_completion(args)
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
