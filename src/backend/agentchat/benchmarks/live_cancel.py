from __future__ import annotations

import argparse
import http.client
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from agentchat.benchmarks.live_utils import (
    DEFAULT_STATE_PATH,
    ApiError,
    LiveApi,
    load_state,
    save_state,
)
from agentchat.benchmarks.metrics import latency_stats


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_GROUND_TRUTH = REPO_ROOT / "docs" / "eval" / "live_rag_ground_truth.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval"
DEFAULT_AGENT_NAME = "LiveBench-CancelAgent"
DEFAULT_DIALOG_NAME = "LiveBench-CancelDialog"
DEFAULT_QUERY_IDS = [
    "live_q_cancel",
    "live_q_checkin",
    "live_q_wifi",
    "live_q_shuttle",
    "live_q_parking",
]
CANCEL_EVENT_TYPE = "stream_cancel"


def _knowledge_id_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if value is None:
        return []
    return [str(item) for item in value]


def _select_cases(
    ground_truth: Dict[str, Any],
    query_ids: List[str],
    rounds: int,
) -> List[Dict[str, Any]]:
    by_id = {case.get("id", ""): case for case in ground_truth.get("queries", [])}
    candidates = [by_id[query_id] for query_id in query_ids if query_id in by_id]
    selected: List[Dict[str, Any]] = []
    while len(selected) < rounds and candidates:
        index = len(selected) % len(candidates)
        selected.append(candidates[index])
    return selected[:rounds]


def _parse_sse_line(raw: Any) -> Optional[Dict[str, Any]]:
    if not raw:
        return None
    line = raw.decode("utf-8", errors="ignore") if isinstance(raw, bytes) else str(raw)
    if not line.startswith("data:"):
        return None
    payload = line[len("data:"):].strip()
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _find_cancel_summary(
    api: LiveApi,
    dialog_id: str,
    timeout_ms: float,
) -> Optional[Dict[str, Any]]:
    deadline = time.perf_counter() + timeout_ms / 1000.0
    while time.perf_counter() < deadline:
        try:
            history = api.get_dialog_history(dialog_id)
        except ApiError:
            history = []
        for record in history:
            if record.get("role") != "assistant":
                continue
            for event in record.get("events") or []:
                if event.get("type") == CANCEL_EVENT_TYPE:
                    return event.get("data") if isinstance(event.get("data"), dict) else {}
        time.sleep(0.2)
    return None


def _run_cancel_case(
    api: LiveApi,
    dialog_id: str,
    query_case: Dict[str, Any],
    *,
    hold_ms: float,
    close_after_ms: float,
    first_chunk_timeout_ms: float,
    history_timeout_ms: float,
    threshold_ms: float,
) -> Dict[str, Any]:
    query = query_case.get("query", "")
    started = time.perf_counter()
    headers = api._headers()
    headers["Accept"] = "text/event-stream"

    conn: Optional[http.client.HTTPConnection] = None
    raw_response = None
    first_chunk_at: Optional[float] = None
    close_at: Optional[float] = None
    event_type_counts: Dict[str, int] = {}
    tool_events: List[Dict[str, Any]] = []
    stream_error: Optional[str] = None
    first_chunk_preview = ""
    close_timer: Optional[threading.Timer] = None
    close_timer_fired = threading.Event()

    def _close_connection() -> None:
        close_timer_fired.set()
        try:
            if conn is not None:
                sock = getattr(conn, "sock", None)
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    try:
        parsed = urlparse(api.base_url)
        conn = http.client.HTTPConnection(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            timeout=max(first_chunk_timeout_ms / 1000.0, 60.0),
        )
        conn.request(
            "POST",
            "/api/v1/completion",
            body=json.dumps(
                {
                "user_input": query,
                "dialog_id": dialog_id,
                "file_url": None,
                }
            ).encode("utf-8"),
            headers={
                **headers,
                "Content-Type": "application/json",
            },
        )

        if close_after_ms > 0:
            close_timer = threading.Timer(close_after_ms / 1000.0, _close_connection)
            close_timer.daemon = True
            close_timer.start()

        raw_response = conn.getresponse()
        if raw_response.status >= 400:
            body_text = raw_response.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"HTTP {raw_response.status}: {body_text[:300]}")

        if conn.sock is not None:
            conn.sock.settimeout(0.2)
        while True:
            try:
                raw = raw_response.readline()
            except (TimeoutError, socket.timeout):
                if close_timer_fired.is_set():
                    break
                continue
            except OSError as exc:
                if "timed out" not in str(exc).lower():
                    raise
                if close_timer_fired.is_set():
                    break
                continue
            if not raw:
                break

            event = _parse_sse_line(raw)
            if not event:
                continue
            event_type = event.get("type", "")
            event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1
            data = event.get("data") or {}
            if event_type == "event":
                if data.get("tool_name"):
                    tool_events.append(
                        {
                            "tool_name": data.get("tool_name"),
                            "status": data.get("status"),
                            "tool_type": data.get("tool_type"),
                            "duration_ms": data.get("duration_ms"),
                        }
                    )
            elif event_type == "response_chunk":
                chunk = str(data.get("chunk", "") or "")
                if chunk:
                    first_chunk_at = time.perf_counter()
                    first_chunk_preview = chunk[:120]
                    if hold_ms > 0:
                        time.sleep(hold_ms / 1000.0)
                    break
    except Exception as exc:
        if not close_timer_fired.is_set():
            stream_error = f"{type(exc).__name__}: {str(exc)[:300]}"
    finally:
        if close_timer is not None:
            close_timer.cancel()
        if raw_response is not None:
            raw_response.close()
        if conn is not None:
            conn.close()
        close_at = time.perf_counter()

    stream_started = bool(
        first_chunk_at is not None
        or close_timer_fired.is_set()
    )
    cancel_summary = (
        _find_cancel_summary(api, dialog_id, history_timeout_ms)
        if stream_started and stream_error is None
        else None
    )
    cancel_summary_found = cancel_summary is not None
    server_cancelled = bool(
        cancel_summary and cancel_summary.get("cancelled") is True
    )
    cancel_to_terminate_ms = (
        cancel_summary.get("cancel_to_terminate_ms")
        if cancel_summary is not None
        else None
    )
    total_duration_ms = (
        cancel_summary.get("total_duration_ms")
        if cancel_summary is not None
        else None
    )

    tool_start_count = sum(item["status"] == "START" for item in tool_events)
    tool_end_count = sum(item["status"] == "END" for item in tool_events)
    tool_error_count = sum(item["status"] == "ERROR" for item in tool_events)
    terminated_ok = (
        server_cancelled
        and cancel_to_terminate_ms is not None
        and cancel_to_terminate_ms <= threshold_ms
    )
    case_ok = bool(
        stream_started
        and stream_error is None
        and cancel_summary_found
        and terminated_ok
    )
    return {
        "query_id": query_case.get("id", ""),
        "query": query,
        "difficulty": query_case.get("difficulty", "normal"),
        "dialog_id": dialog_id,
        "first_chunk_ms": (
            round((first_chunk_at - started) * 1000, 3)
            if first_chunk_at is not None
            else None
        ),
        "client_close_ms": round((close_at - started) * 1000, 3),
        "hold_ms": hold_ms,
        "close_after_ms": close_after_ms,
        "closed_before_first_chunk": bool(
            close_timer_fired.is_set() and first_chunk_at is None
        ),
        "hold_ms_actual": (
            round((close_at - first_chunk_at) * 1000, 3)
            if first_chunk_at is not None
            else None
        ),
        "first_chunk_preview": first_chunk_preview,
        "stream_error": stream_error,
        "event_type_counts": event_type_counts,
        "tool_events": tool_events,
        "tool_call_count": tool_start_count,
        "tool_end_count": tool_end_count,
        "tool_error_count": tool_error_count,
        "cancel_summary": cancel_summary,
        "cancel_summary_found": cancel_summary_found,
        "server_cancelled": server_cancelled,
        "cancel_to_terminate_ms": cancel_to_terminate_ms,
        "total_duration_ms": total_duration_ms,
        "terminated_ok": terminated_ok,
        "case_ok": case_ok,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-cancel",
        description="Abort real /api/v1/completion SSE streams and record server-side cancellation latency.",
    )
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--query-ids", nargs="*", default=DEFAULT_QUERY_IDS)
    parser.add_argument("--hold-ms", type=float, default=100.0)
    parser.add_argument(
        "--close-after-ms",
        type=float,
        default=5000.0,
        help="close the SSE connection at this fixed delay even when no chunk arrived",
    )
    parser.add_argument("--first-chunk-timeout-ms", type=float, default=120000.0)
    parser.add_argument("--history-timeout-ms", type=float, default=15000.0)
    parser.add_argument("--threshold-ms", type=float, default=500.0)
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="create a new benchmark agent instead of reusing the last one",
    )
    return parser


def run_live_cancel(args: argparse.Namespace) -> Dict[str, Any]:
    state_path = Path(args.state).expanduser().resolve()
    state = load_state(state_path)
    if not state.get("token") or not state.get("base_url"):
        raise RuntimeError("state file has no token/base_url; run live_seed.py first")

    ground_truth = json.loads(
        Path(args.ground_truth).expanduser().resolve().read_text(encoding="utf-8")
    )
    cases_input = _select_cases(ground_truth, list(args.query_ids), args.rounds)
    if not cases_input:
        raise RuntimeError("no ground-truth queries selected")

    session = requests.Session()
    api = LiveApi(
        base_url=state["base_url"],
        client=session,
        token=state["token"],
        timeout=(30.0, 300.0),
    )

    agent_name = state.get("last_cancel_agent_name") or DEFAULT_AGENT_NAME
    if args.fresh:
        agent_name = f"{DEFAULT_AGENT_NAME}-{datetime.now().strftime('%H%M%S')}"

    system_prompt = (
        "You are a hotel FAQ and internal policy assistant. "
        "Always retrieve knowledge first and answer from the retrieved facts. "
        "Keep the answer in Chinese, concise and fact-based. "
        "Do not reveal query rewrite lists or internal tool processing."
    )
    agent = api.ensure_agent(
        name=agent_name,
        description="P5.5 live cancellation benchmark agent",
        system_prompt=system_prompt,
        knowledge_ids=_knowledge_id_list(state.get("knowledge_id")),
        enable_memory=True,
        enable_multi_agent=False,
    )
    agent_id = str(agent.get("id") or agent.get("agent_id") or "")
    if not agent_id:
        raise RuntimeError("ensure_agent returned no agent id")

    cases: List[Dict[str, Any]] = []
    for index, query_case in enumerate(cases_input, start=1):
        dialog_name = f"{DEFAULT_DIALOG_NAME}-{datetime.now().strftime('%H%M%S')}-{index}"
        dialog = api.create_dialog(dialog_name, agent_id)
        dialog_id = str(dialog.get("dialog_id") or "")
        if not dialog_id:
            raise RuntimeError("create_dialog returned no dialog_id")
        cases.append(
            _run_cancel_case(
                api,
                dialog_id,
                query_case,
                hold_ms=args.hold_ms,
                close_after_ms=args.close_after_ms,
                first_chunk_timeout_ms=args.first_chunk_timeout_ms,
                history_timeout_ms=args.history_timeout_ms,
                threshold_ms=args.threshold_ms,
            )
        )

    cancel_values = [
        case["cancel_to_terminate_ms"]
        for case in cases
        if case.get("cancel_to_terminate_ms") is not None
    ]
    first_chunk_values = [
        case["first_chunk_ms"]
        for case in cases
        if case.get("first_chunk_ms") is not None
    ]
    total_duration_values = [
        case["total_duration_ms"]
        for case in cases
        if case.get("total_duration_ms") is not None
    ]

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    settings = {
        "rounds": len(cases),
        "query_ids": [case["query_id"] for case in cases],
        "hold_ms": args.hold_ms,
        "close_after_ms": args.close_after_ms,
        "first_chunk_timeout_ms": args.first_chunk_timeout_ms,
        "history_timeout_ms": args.history_timeout_ms,
        "threshold_ms": args.threshold_ms,
    }
    result = {
        "stage": "p5.5",
        "created_at": created_at,
        "dataset_name": ground_truth.get("dataset_name", ""),
        "knowledge_id": state.get("knowledge_id", ""),
        "agent_name": agent_name,
        "agent_id": agent_id,
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "base_url": state.get("base_url", ""),
        },
        "settings": settings,
        "cases": cases,
        "summary": {
            "rounds": len(cases),
            "cancel_summary_found_count": sum(
                case["cancel_summary_found"] for case in cases
            ),
            "server_cancelled_count": sum(case["server_cancelled"] for case in cases),
            "terminated_ok_count": sum(case["terminated_ok"] for case in cases),
            "pass_count": sum(case["case_ok"] for case in cases),
            "pass_rate": round(sum(case["case_ok"] for case in cases) / len(cases), 4)
            if cases
            else 0.0,
            "threshold_ms": args.threshold_ms,
            "cancel_to_terminate_ms": latency_stats(cancel_values),
            "first_chunk_ms": latency_stats(first_chunk_values),
            "total_duration_ms": latency_stats(total_duration_values),
            "tool_call_count": sum(case["tool_call_count"] for case in cases),
            "tool_error_count": sum(case["tool_error_count"] for case in cases),
        },
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"live_cancel_{timestamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    state["last_cancel_agent_name"] = agent_name
    state["last_cancel_dialog_ids"] = [case["dialog_id"] for case in cases]
    state["last_cancel_at"] = created_at
    save_state(state_path, state)

    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_live_cancel(args)
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
