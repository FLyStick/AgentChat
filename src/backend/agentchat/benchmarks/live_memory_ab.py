from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

from agentchat.benchmarks.live_completion import _read_usage_totals
from agentchat.benchmarks.live_utils import (
    LiveApi,
    generate_password,
    load_state,
    save_state,
)
from agentchat.benchmarks.memory import LiveMemoryAdapter
from agentchat.settings import init_app_settings


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_FIXTURE = (
    REPO_ROOT
    / "src"
    / "backend"
    / "agentchat"
    / "benchmarks"
    / "fixtures"
    / "memory_live_ab"
    / "scenarios.json"
)
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval" / "live"
DEFAULT_STATE_PATH = REPO_ROOT / "docs" / "eval" / "live" / "memory_ab_state.json"
DEFAULT_BASE_URL = "http://127.0.0.1:7860"

ARMS = [
    {
        "key": "two_layer",
        "label": "两层（enable_memory=False）",
        "enable_memory": False,
        "user_suffix": "mem2l",
        "user_prefix": "ab2l",
        "agent_prefix": "LiveBench-MemoryTwo",
    },
    {
        "key": "three_layer",
        "label": "三层（enable_memory=True）",
        "enable_memory": True,
        "user_suffix": "mem3l",
        "user_prefix": "ab3l",
        "agent_prefix": "LiveBench-MemoryThree",
    },
]

AGENT_SYSTEM_PROMPT = (
    "你是企业服务助手。回答前应先利用当前对话上下文以及系统提供的用户记忆；"
    "如果上下文中没有用户提供过的相关信息，请明确说明没有记录，不要编造用户的偏好、约定或历史事实。"
    "回答用中文，简洁完整。"
)

UNKNOWN_MARKERS = (
    "没有记录",
    "没有你的相关记录",
    "没有相关信息",
    "未检索到",
    "无法确认",
    "不确定",
    "不知道",
    "没有提到",
    "没有查到",
    "暂未找到",
    "我没有这个信息",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text))


def _hint_terms(hint: str) -> List[str]:
    parts = [part.strip() for part in re.split(r"[,，、;；/\\|\s]+", hint or "") if part.strip()]
    return parts


def _fact_eval(fact: Dict[str, Any], answer: str) -> Dict[str, Any]:
    hint = str(fact.get("expected_hint") or fact.get("fact") or "")
    terms = _hint_terms(hint)
    normalized_answer = _normalize(answer)
    raw_variants = fact.get("expected_variants") or []
    variants: List[List[str]] = []
    for variant in raw_variants:
        if isinstance(variant, list):
            variants.append([str(item) for item in variant])
    if not variants:
        variants = [terms]

    variant_checks = []
    for variant in variants:
        hit_terms = [term for term in variant if _normalize(term) in normalized_answer]
        all_hit = bool(variant) and len(hit_terms) == len(variant)
        variant_checks.append(
            {
                "terms": variant,
                "hit_terms": hit_terms,
                "all_hit": all_hit,
            }
        )
    all_hit = any(item["all_hit"] for item in variant_checks)
    hit_terms = variant_checks[0]["hit_terms"] if variant_checks else []
    uncertain = any(marker in answer for marker in UNKNOWN_MARKERS)
    if all_hit and not uncertain:
        verdict = "used_correctly"
    elif all_hit and uncertain:
        verdict = "used_wrongly"
    else:
        verdict = "missing"
    return {
        "fact_id": fact.get("fact_id", ""),
        "fact": fact.get("fact", ""),
        "expected_hint": hint,
        "terms": terms,
        "hit_terms": hit_terms,
        "all_terms_hit": all_hit,
        "variant_checks": variant_checks,
        "uncertain_marker_hit": uncertain,
        "verdict": verdict,
    }


def _stream_completion_once(
    api: LiveApi,
    dialog_id: str,
    user_input: str,
) -> Dict[str, Any]:
    started = time.perf_counter()
    chunks: List[str] = []
    first_chunk_ms = None
    event_type_counts: Dict[str, int] = {}
    stream_error = None

    try:
        for event in api.stream_completion(dialog_id, user_input):
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
    except (
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ConnectionError,
        requests.exceptions.ReadTimeout,
        requests.exceptions.JSONDecodeError,
        json.JSONDecodeError,
    ) as exc:
        stream_error = f"{type(exc).__name__}: {str(exc)[:300]}"

    total_latency_ms = round((time.perf_counter() - started) * 1000, 3)
    answer = "".join(chunks)
    return {
        "user_input": user_input,
        "answer": answer,
        "answer_length": len(answer),
        "latency_ms": total_latency_ms,
        "first_chunk_ms": first_chunk_ms,
        "stream_completed": stream_error is None,
        "stream_error": stream_error,
        "event_type_counts": event_type_counts,
    }


def _search_memory(
    adapter: LiveMemoryAdapter,
    query: str,
    top_k: int = 8,
) -> List[Dict[str, Any]]:
    results = asyncio.run(adapter.search(query, top_k=top_k, run_id=None))
    return [
        {
            "memory": str(item.get("memory", "")),
            "score": item.get("score"),
            "run_id": item.get("run_id"),
        }
        for item in results[:top_k]
    ]


def _wait_memory_ready(
    adapter: LiveMemoryAdapter,
    query: str,
    top_k: int = 8,
    timeout: float = 25.0,
    poll_interval: float = 2.0,
) -> Dict[str, Any]:
    started = time.perf_counter()
    last_results: List[Dict[str, Any]] = []
    timed_out = False
    while True:
        try:
            last_results = _search_memory(adapter, query, top_k=top_k)
        except Exception as exc:
            last_results = []
            if time.perf_counter() - started > timeout:
                timed_out = True
                break
            time.sleep(poll_interval)
            continue
        if last_results:
            break
        if time.perf_counter() - started > timeout:
            timed_out = True
            break
        time.sleep(poll_interval)
    return {
        "ready": bool(last_results) and not timed_out,
        "timed_out": timed_out,
        "waited_ms": round((time.perf_counter() - started) * 1000, 3),
        "result_count": len(last_results),
        "top_results": last_results[:3],
    }


def _load_scenarios(fixture_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(fixture_path.read_text(encoding="utf-8"))
    return data["scenarios"]


def _select_scenarios(
    scenarios: List[Dict[str, Any]],
    scenario_ids: List[str],
    limit: int,
) -> List[Dict[str, Any]]:
    by_id = {item["scenario_id"]: item for item in scenarios}
    if scenario_ids:
        selected = [by_id[item] for item in scenario_ids if item in by_id]
    else:
        selected = scenarios[:limit]
    if not selected:
        raise RuntimeError("no memory scenarios selected")
    return selected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-memory-ab",
        description=(
            "Run real /api/v1/completion two-layer vs three-layer memory comparison. "
            "Each scenario seeds facts in a dialog, then probes from a fresh dialog."
        ),
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--scenario-ids", nargs="*", default=[])
    parser.add_argument("--top-k-memory", type=int, default=8)
    parser.add_argument("--wait-memory-timeout", type=float, default=25.0)
    return parser


def _arm_summary(
    arm: Dict[str, Any],
    cases: List[Dict[str, Any]],
    usage_delta: Dict[str, Any],
) -> Dict[str, Any]:
    fact_results = [
        result
        for case in cases
        for result in case["fact_results"]
    ]
    used = sum(item["verdict"] == "used_correctly" for item in fact_results)
    missing = sum(item["verdict"] == "missing" for item in fact_results)
    wrong = sum(item["verdict"] == "used_wrongly" for item in fact_results)
    passed = sum(case["case_pass"] for case in cases)
    latencies = [
        case["probe"]["latency_ms"]
        for case in cases
    ] + [
        turn["latency_ms"]
        for case in cases
        for turn in case["seed_turns"]
    ]
    probe_latencies = [case["probe"]["latency_ms"] for case in cases]
    first_chunks = [
        case["probe"]["first_chunk_ms"]
        for case in cases
        if case["probe"]["first_chunk_ms"] is not None
    ]
    total_facts = len(fact_results)
    return {
        "arm": arm["key"],
        "label": arm["label"],
        "enable_memory": arm["enable_memory"],
        "case_count": len(cases),
        "fact_count": total_facts,
        "fact_recall": round(used / total_facts, 4) if total_facts else 0.0,
        "missing_rate": round(missing / total_facts, 4) if total_facts else 0.0,
        "used_wrongly_rate": round(wrong / total_facts, 4) if total_facts else 0.0,
        "case_pass_rate": round(passed / len(cases), 4) if cases else 0.0,
        "mean_total_latency_ms": round(statistics.mean(latencies), 3)
        if latencies
        else None,
        "mean_probe_latency_ms": round(statistics.mean(probe_latencies), 3)
        if probe_latencies
        else None,
        "mean_first_chunk_ms": round(statistics.mean(first_chunks), 3)
        if first_chunks
        else None,
        "usage_delta": usage_delta,
    }


def run_live_memory_ab(args: argparse.Namespace) -> Dict[str, Any]:
    asyncio.run(init_app_settings())

    scenarios = _select_scenarios(
        _load_scenarios(Path(args.fixture).expanduser().resolve()),
        args.scenario_ids,
        args.limit,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_stamp = datetime.now().strftime("%m%d_%H%M%S")
    session = requests.Session()
    base_api = LiveApi(
        base_url=args.base_url,
        client=session,
        timeout=(30.0, 300.0),
    )

    arm_results: Dict[str, Dict[str, Any]] = {}
    scenario_results: List[Dict[str, Any]] = []
    state_payload: Dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "arms": {},
    }

    for arm in ARMS:
        arm_cases: List[Dict[str, Any]] = []
        scenario_identities: List[Dict[str, Any]] = []
        usage_delta_accum = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "call_count": 0,
        }

        for scenario in scenarios:
            scenario_id = scenario["scenario_id"]
            scenario_suffix = scenario_id.rsplit("_", 1)[-1]
            user_name = f"{arm['user_prefix']}_{short_stamp}_s{scenario_suffix}"
            password = generate_password()
            user = base_api.ensure_user(
                user_name, password, f"{user_name}@bench.local"
            )
            user_id = str(user.get("user_id") or "")
            agent = base_api.ensure_agent(
                name=f"{arm['agent_prefix']}-{scenario_id}-{stamp}",
                description="P5.10 two-layer vs three-layer memory benchmark agent",
                system_prompt=AGENT_SYSTEM_PROMPT,
                knowledge_ids=[],
                enable_memory=arm["enable_memory"],
                enable_multi_agent=False,
            )
            agent_id = str(agent.get("id") or agent.get("agent_id") or "")
            agent_name = str(agent.get("name") or "")
            if not user_id or not agent_id:
                raise RuntimeError(
                    f"{arm['key']} {scenario_id} user/agent creation failed"
                )

            scenario_identities.append(
                {
                    "scenario_id": scenario_id,
                    "user_name": user_name,
                    "password": password,
                    "user_id": user_id,
                    "agent_name": agent_name,
                    "agent_id": agent_id,
                }
            )
            usage_before = _read_usage_totals(base_api, agent_name)

            seed_dialog = base_api.create_dialog(
                f"{arm['agent_prefix']}-seed-{scenario_id}-{stamp}",
                agent_id,
            )
            seed_dialog_id = str(seed_dialog.get("dialog_id") or "")
            if not seed_dialog_id:
                raise RuntimeError(f"{arm['key']} {scenario_id} seed dialog failed")

            seed_turns = [
                _stream_completion_once(base_api, seed_dialog_id, turn)
                for turn in scenario.get("fact_turns", [])
            ]

            memory_evidence = None
            if arm["enable_memory"]:
                adapter = LiveMemoryAdapter(
                    run_id=seed_dialog_id,
                    user_id=user_id,
                    agent_id=agent_id,
                )
                memory_evidence = _wait_memory_ready(
                    adapter,
                    scenario["probe_question"],
                    top_k=args.top_k_memory,
                    timeout=args.wait_memory_timeout,
                )

            probe_dialog = base_api.create_dialog(
                f"{arm['agent_prefix']}-probe-{scenario_id}-{stamp}",
                agent_id,
            )
            probe_dialog_id = str(probe_dialog.get("dialog_id") or "")
            if not probe_dialog_id:
                raise RuntimeError(f"{arm['key']} {scenario_id} probe dialog failed")
            probe_case = _stream_completion_once(
                base_api,
                probe_dialog_id,
                scenario["probe_question"],
            )
            fact_results = [
                _fact_eval(fact, probe_case["answer"])
                for fact in scenario.get("gold_facts", [])
            ]
            case_pass = bool(fact_results) and all(
                item["verdict"] == "used_correctly" for item in fact_results
            )

            usage_after = _read_usage_totals(base_api, agent_name)
            scenario_usage = {
                key: int(usage_after.get(key, 0)) - int(usage_before.get(key, 0))
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "call_count",
                )
            }
            for key in usage_delta_accum:
                usage_delta_accum[key] += scenario_usage[key]

            arm_cases.append(
                {
                    "scenario_id": scenario_id,
                    "title": scenario.get("title", ""),
                    "user_id": user_id,
                    "agent_id": agent_id,
                    "seed_dialog_id": seed_dialog_id,
                    "probe_dialog_id": probe_dialog_id,
                    "probe_question": scenario["probe_question"],
                    "seed_turns": seed_turns,
                    "memory_evidence": memory_evidence,
                    "probe": probe_case,
                    "fact_results": fact_results,
                    "case_pass": case_pass,
                }
            )

        summary = _arm_summary(arm, arm_cases, usage_delta_accum)
        summary.update(
            {
                "scenario_count": len(arm_cases),
                "identity_count": len(scenario_identities),
            }
        )
        arm_results[arm["key"]] = summary
        state_payload["arms"][arm["key"]] = {
            "enable_memory": arm["enable_memory"],
            "scenario_count": len(arm_cases),
            "scenario_identities": scenario_identities,
        }

        for index, case in enumerate(arm_cases):
            entry = None
            if index >= len(scenario_results):
                scenario_results.append(
                    {
                        "scenario_id": case["scenario_id"],
                        "title": case["title"],
                    }
                )
            scenario_results[index][arm["key"]] = case

    deltas = {}
    if "two_layer" in arm_results and "three_layer" in arm_results:
        two = arm_results["two_layer"]
        three = arm_results["three_layer"]
        deltas = {
            "fact_recall": round(three["fact_recall"] - two["fact_recall"], 4),
            "case_pass_rate": round(
                three["case_pass_rate"] - two["case_pass_rate"], 4
            ),
            "mean_probe_latency_ms": round(
                three["mean_probe_latency_ms"] - two["mean_probe_latency_ms"], 3
            )
            if two.get("mean_probe_latency_ms") is not None
            and three.get("mean_probe_latency_ms") is not None
            else None,
        }

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {
        "stage": "p5.10",
        "created_at": created_at,
        "dataset_name": "memory_live_ab_30",
        "scenario_count": len(scenario_results),
        "judge_method": "discriminative_hint_match",
        "design": "seed_dialog_facts_plus_fresh_probe_dialog",
        "environment": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "base_url": args.base_url,
            "fixture": str(Path(args.fixture).expanduser().resolve()),
        },
        "settings": {
            "limit": args.limit,
            "scenario_ids": args.scenario_ids,
            "top_k_memory": args.top_k_memory,
            "wait_memory_timeout_s": args.wait_memory_timeout,
        },
        "arms": arm_results,
        "comparison": deltas,
        "scenarios": scenario_results,
    }

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"live_memory_comparison_{stamp}.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    save_state(Path(args.state).expanduser().resolve(), state_payload)
    result["output_file"] = str(output_path)
    return result


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = run_live_memory_ab(args)
    sys.stdout.buffer.write(
        (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


if __name__ == "__main__":
    main()
