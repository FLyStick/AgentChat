from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests

from agentchat.benchmarks.live_utils import (
    BACKEND_DIR,
    DEFAULT_BASE_URL,
    DEFAULT_KNOWLEDGE_NAME,
    DEFAULT_STATE_PATH,
    STATE_VERSION,
    ApiError,
    LiveApi,
    LiveBenchError,
    generate_password,
    load_state,
    save_state,
    utcnow_iso,
)


REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SOURCES_DIR = Path(__file__).resolve().parent / "fixtures" / "rag_live" / "sources"
DEFAULT_AB_SOURCES_DIR = Path(__file__).resolve().parent / "fixtures" / "rag_live_ab" / "sources"
DEFAULT_AB_QUERIES_FILE = DEFAULT_AB_SOURCES_DIR.parent / "queries.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "eval" / "live"
DEFAULT_AB_STATE_PATH = REPO_ROOT / "docs" / "eval" / "live" / "live_rag_ab_state.json"
DEFAULT_USER_NAME = "live_bench_0814"
DEFAULT_AB_KNOWLEDGE_NAME = "RagAb0814"
DEFAULT_AB_DATASET_NAME = "live_rag_ab_20260814"

KNOWLEDGE_DESC = "用于AgentChat真实链路RAG评测的酒店FAQ、项目手册与内部制度语料。"
AB_KNOWLEDGE_DESC = "用于P5.9 RAG真实A/B评测的21份业务语料：酒店服务、企业制度、项目运维与项目FAQ。"


QUERY_SPECS = [
    {
        "id": "live_q_checkin",
        "query": "酒店几点可以办理入住，几点退房？",
        "difficulty": "easy",
        "facts": ["酒店入住时间为下午14:00，退房时间为中午12:00，前台24小时值班，行李可以寄存，延迟退房需提前联系前台确认房态。"],
    },
    {
        "id": "live_q_wifi",
        "query": "客房Wi-Fi密码是多少？",
        "difficulty": "easy",
        "facts": ["客房无线网络名称为GrandHotel-Guest，密码为CheckIn2026，公共区域免费连接，行政楼层网络无需密码自动连接。"],
    },
    {
        "id": "live_q_breakfast",
        "query": "早餐几点供应，住客需要另外付费吗？",
        "difficulty": "easy",
        "facts": ["自助早餐供应时间为06:30至10:00，地点在二楼餐厅，住客含双早，额外早餐每人68元，儿童1.2米以下免费。"],
    },
    {
        "id": "live_q_shuttle",
        "query": "去浦东机场的班车怎么预约？",
        "difficulty": "normal",
        "facts": ["酒店提供浦东机场免费班车，发车时间为每小时整点，上车地点为酒店正门右侧，需要至少提前30分钟在前台预约座位。"],
    },
    {
        "id": "live_q_gym",
        "query": "健身房开放时间和使用要求是什么？",
        "difficulty": "easy",
        "facts": ["健身房位于三楼的玻璃房，开放时间为06:00至22:00，住客凭房卡进入，18岁以下未成年人需要在监护人陪同下使用。"],
    },
    {
        "id": "live_q_pool",
        "query": "游泳池有什么规定？",
        "difficulty": "normal",
        "facts": ["游泳池水温恒定为28度，开放时间为07:00至21:00，最深1.6米，进入泳池区域必须穿泳衣并佩戴泳帽。"],
    },
    {
        "id": "live_q_pet",
        "query": "酒店可以带宠物入住吗，费用是多少？",
        "difficulty": "normal",
        "facts": ["酒店允许携带体重不超过15公斤的宠物入住，每只每晚加收清洁费200元，导盲犬免费，入住时需签署宠物协议。"],
    },
    {
        "id": "live_q_minibar",
        "query": "客房迷你吧怎么收费？",
        "difficulty": "easy",
        "facts": ["客房迷你吧首轮免费，包含两瓶矿泉水和两罐软饮，补充消费按价格表收费，退房时前台会核对迷你吧消费。"],
    },
    {
        "id": "live_q_laundry",
        "query": "洗衣服务多久能送回，加急怎么收费？",
        "difficulty": "normal",
        "facts": ["酒店提供24小时洗衣服务，普通洗衣当天20:00前送回，加急洗衣3小时送回，收费为普通服务的1.5倍。"],
    },
    {
        "id": "live_q_parking",
        "query": "酒店停车怎么收费？",
        "difficulty": "easy",
        "facts": ["停车场共三层，住客免费停车，非住客每小时20元每日封顶100元，新能源车位位于B2层并预留充电桩。"],
    },
    {
        "id": "live_q_deploy",
        "query": "项目后端启动命令是什么？",
        "difficulty": "easy",
        "facts": ["AgentChat后端启动命令是python -m uvicorn agentchat.main:app --host 0.0.0.0 --port 8000，启动前需要配置数据库和向量库环境变量。"],
    },
    {
        "id": "live_q_rag_chain",
        "query": "RAG检索链路用到哪些组件？",
        "difficulty": "normal",
        "facts": ["知识库问答使用RAG链路：文档上传后切成固定长度分块，写入Chroma向量库，可选同步Elasticsearch关键词索引，检索后经过重排返回结果。"],
    },
    {
        "id": "live_q_docker_deps",
        "query": "项目默认依赖哪些服务？",
        "difficulty": "easy",
        "facts": ["项目默认使用docker compose启动MySQL、Redis和MinIO三个依赖服务，后端配置通过config.yaml读取数据库、存储和模型凭证。"],
    },
    {
        "id": "live_q_completion_sse",
        "query": "对话流式接口是怎么调用的？",
        "difficulty": "normal",
        "facts": ["对话流式接口为POST /api/v1/completion，请求体包含user_input和dialog_id，响应使用SSE格式逐块返回内容事件。"],
    },
    {
        "id": "live_q_multi_agent",
        "query": "多Agent能力是怎么编排的？",
        "difficulty": "normal",
        "facts": ["多Agent能力由orchestrator编排，主Agent按固定关键词路由到客服、酒店和项目三个子Agent，每个子Agent持有独立ReAct链路。"],
    },
    {
        "id": "live_q_agent_create",
        "query": "创建Agent接口支持哪些配置？",
        "difficulty": "normal",
        "facts": ["Agent创建接口为POST /api/v1/agent，支持配置工具、模型、知识库、MCP服务器、记忆开关和多Agent开关。"],
    },
    {
        "id": "live_q_memory",
        "query": "记忆模块区分哪些层次，各自的作用是什么？",
        "difficulty": "hard",
        "facts": ["记忆模块区分短期记忆、摘要记忆和长期记忆，短期窗口超出阈值后触发摘要，长期记忆只保存跨对话的关键事实。"],
    },
    {
        "id": "live_q_rewrite",
        "query": "查询重写的作用是什么？",
        "difficulty": "normal",
        "facts": ["查询重写会将用户问题改写为多个候选query，再分别检索并合并结果，避免一次改写失败导致召回为空。"],
    },
    {
        "id": "live_q_rerank",
        "query": "Rerank阶段如何过滤低相关文档？",
        "difficulty": "normal",
        "facts": ["Rerank阶段使用gte-rerank-v2对候选文档重排，可以通过min_score和rerank_threshold过滤低相关文档。"],
    },
    {
        "id": "live_q_cancel",
        "query": "断流取消机制是怎么实现的？",
        "difficulty": "hard",
        "facts": ["断流取消由CancellableAsyncStream实现，前端调用stop_streaming_callback后触发request_cancel，终止正在进行的模型流式调用。"],
    },
    {
        "id": "live_q_leave",
        "query": "请病假需要提前多久申请？",
        "difficulty": "easy",
        "facts": ["员工请假需提前1个工作日提交申请，病假可当日提交并附医院证明，请假由直属主管审批，婚假和产假需同时提交人事备案。"],
    },
    {
        "id": "live_q_expense",
        "query": "报销大概多久能到账？",
        "difficulty": "normal",
        "facts": ["报销单需附原始发票，财务审核通过后5个工作日内打款，单笔超过5000元需要部门总监复核，超过50000元需要总经理审批。"],
    },
    {
        "id": "live_q_overtime",
        "query": "下班后继续干活，公司如何计酬？",
        "difficulty": "hard",
        "facts": ["加班需提前申请，工作日晚间加班按1.5倍时薪补偿，周末加班优先安排调休，法定节假日加班按3倍时薪补偿。"],
    },
    {
        "id": "live_q_onboarding",
        "query": "员工试用期和离职要求是什么？",
        "difficulty": "normal",
        "facts": ["员工入职当天签订劳动合同并领取工牌，试用期一般为3个月，试用期内离职需提前3天告知直属主管，正式员工需提前30天。"],
    },
    {
        "id": "live_q_performance",
        "query": "年度绩效考核分档和年终奖系数是什么？",
        "difficulty": "normal",
        "facts": ["年度绩效考核分为S、A、B、C四档，S档年终奖系数为2.0，A档为1.5，B档为1.0，C档无年终奖并进入改进计划。"],
    },
    {
        "id": "live_q_travel",
        "query": "出差住宿标准是多少？",
        "difficulty": "easy",
        "facts": ["差旅住宿标准为一线城市每晚700元，其他城市每晚450元，出差需要提前在OA系统提交差旅申请并关联预算项目。"],
    },
    {
        "id": "live_q_annual_leave",
        "query": "年假怎么计算和顺延？",
        "difficulty": "normal",
        "facts": ["员工每年享有10天带薪年假，转正后按入职月份折算，未休完年假可顺延至次年3月31日，逾期视为自动放弃。"],
    },
    {
        "id": "live_q_meeting",
        "query": "会议室预约有哪些规则？",
        "difficulty": "normal",
        "facts": ["会议室使用需提前在OA预约，超过30分钟未到达自动释放，会议结束后需关闭投影并带走个人物品，违规三次取消本月预约权限。"],
    },
    {
        "id": "live_q_allowance",
        "query": "公司提供哪些补贴，怎么发放？",
        "difficulty": "normal",
        "facts": ["公司提供每月500元餐饮补贴和200元交通补贴，补贴随工资发放，离职当月按在职天数折算发放。"],
    },
    {
        "id": "live_q_flex_time",
        "query": "弹性工作时间是什么？",
        "difficulty": "easy",
        "facts": ["弹性工作时间为上午9:30至下午18:30，核心协作时间为10:30至16:00，每日弹性申请需在当天上午9点前完成打卡备注。"],
    },
]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentchat-live-seed",
        description="把真实评测语料灌入运行中的 AgentChat 服务。",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端地址")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH, help="评测状态 JSON")
    parser.add_argument("--user-name", default=DEFAULT_USER_NAME, help="评测用户")
    parser.add_argument("--password", default=None, help="评测密码，缺省时从状态文件读取或生成")
    parser.add_argument("--email", default=None, help="评测用户邮箱")
    parser.add_argument("--knowledge-name", default=DEFAULT_KNOWLEDGE_NAME, help="知识库名称")
    parser.add_argument("--sources-dir", type=Path, default=DEFAULT_SOURCES_DIR, help="fixture 目录")
    parser.add_argument(
        "--queries-file",
        type=Path,
        default=None,
        help="query/ground truth 标注文件；默认使用内置 30 条 specs，P5.9 推荐传入 rag_live_ab/queries.json",
    )
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="ground truth 数据集名称，如 live_rag_ab_20260814",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="评测产物目录")
    parser.add_argument("--wait-timeout", type=float, default=180.0, help="每个文件索引成功的最长等待秒数")
    return parser


def _wait_for_file(
    api: LiveApi,
    knowledge_id: str,
    file_name: str,
    timeout: float,
) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_rows: List[Dict[str, Any]] = []
    while True:
        files = api.select_knowledge_files(knowledge_id)
        matched = [row for row in files if row.get("file_name") == file_name]
        last_rows = matched or last_rows
        success = [row for row in matched if row.get("status") == "success"]
        if success:
            return success[-1]
        if time.monotonic() >= deadline:
            statuses = [f"{row.get('id')}:{row.get('status')}" for row in last_rows]
            raise LiveBenchError(f"文件 {file_name} 等待成功超时，当前状态: {statuses}")
        time.sleep(1)


async def _build_ground_truth(
    knowledge_id: str,
    seed_time: str,
    source_files: List[str],
    query_specs: List[Dict[str, Any]],
    dataset_name: str,
) -> Dict[str, Any]:
    from agentchat.services.rag.vector_stores import milvus_client

    collection = milvus_client.client.get_collection(knowledge_id)
    data = collection.get(
        limit=collection.count(),
        include=["metadatas", "documents"],
    )

    chunks: List[Dict[str, Any]] = []
    documents = data.get("documents") or []
    metadatas = data.get("metadatas") or []
    for index, metadata in enumerate(metadatas):
        if metadata.get("is_summary"):
            continue
        chunks.append(
            {
                "chunk_id": metadata.get("chunk_id", ""),
                "file_id": metadata.get("file_id", ""),
                "file_name": metadata.get("file_name", ""),
                "knowledge_id": metadata.get("knowledge_id", ""),
                "content": documents[index] if index < len(documents) else "",
            }
        )

    if not chunks:
        raise LiveBenchError(f"知识库 {knowledge_id} 中未读取到任何真实索引块")

    queries: List[Dict[str, Any]] = []
    for spec in query_specs:
        expected_ids = []
        for fact in spec["facts"]:
            matched = [chunk["chunk_id"] for chunk in chunks if fact in chunk["content"]]
            if not matched:
                raise LiveBenchError(f"ground truth fact 未命中任何索引块: {fact[:50]}")
            expected_ids.extend(matched)
        expected_chunk_ids = sorted(set(expected_ids))
        if not expected_chunk_ids:
            raise LiveBenchError(f"query {spec['id']} 没有可用的 expected_chunk_ids")
        queries.append(
            {
                "id": spec["id"],
                "query": spec["query"],
                "difficulty": spec["difficulty"],
                "expected_facts": spec["facts"],
                "expected_chunk_ids": expected_chunk_ids,
            }
        )

    return {
        "dataset_name": dataset_name,
        "created_at": seed_time,
        "knowledge_id": knowledge_id,
        "vector_store": "chroma",
        "source_files": source_files,
        "source_file_count": len(source_files),
        "indexed_chunk_count": len(chunks),
        "query_count": len(queries),
        "difficulty_counts": {
            difficulty: sum(1 for query in queries if query["difficulty"] == difficulty)
            for difficulty in sorted({query["difficulty"] for query in queries})
        },
        "queries": queries,
        "indexed_chunks": chunks,
    }


async def run_seed(args: argparse.Namespace) -> Dict[str, Any]:
    os.chdir(BACKEND_DIR)

    sources_dir = Path(args.sources_dir).expanduser().resolve()
    if not sources_dir.is_dir():
        raise LiveBenchError(f"fixture 目录不存在: {sources_dir}")
    sources = sorted(sources_dir.glob("*.txt"))
    if not sources:
        raise LiveBenchError(f"fixture 目录没有 txt 文件: {sources_dir}")

    ab_mode = args.queries_file is not None or args.dataset_name is not None
    if ab_mode:
        if args.state == DEFAULT_STATE_PATH:
            args.state = DEFAULT_AB_STATE_PATH
        if args.sources_dir == DEFAULT_SOURCES_DIR:
            sources_dir = DEFAULT_AB_SOURCES_DIR.expanduser().resolve()
            sources = sorted(sources_dir.glob("*.txt"))
        if args.queries_file is None:
            args.queries_file = DEFAULT_AB_QUERIES_FILE

    query_specs = QUERY_SPECS
    if args.queries_file is not None:
        queries_path = Path(args.queries_file).expanduser().resolve()
        if not queries_path.is_file():
            raise LiveBenchError(f"queries 文件不存在: {queries_path}")
        loaded = json.loads(queries_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, list) or not loaded:
            raise LiveBenchError(f"queries 文件不是非空列表: {queries_path}")
        query_specs = loaded

    state = load_state(args.state)
    user_name = args.user_name or state.get("user_name") or DEFAULT_USER_NAME
    password = args.password or state.get("password") or generate_password()
    if ab_mode and args.knowledge_name == DEFAULT_KNOWLEDGE_NAME:
        knowledge_name = DEFAULT_AB_KNOWLEDGE_NAME
    else:
        knowledge_name = args.knowledge_name or state.get("knowledge_name") or DEFAULT_KNOWLEDGE_NAME
    knowledge_desc = AB_KNOWLEDGE_DESC if ab_mode else KNOWLEDGE_DESC
    dataset_name = args.dataset_name or (DEFAULT_AB_DATASET_NAME if ab_mode else "live_rag_hotel_faq")
    seed_time = utcnow_iso()

    state.update(
        {
            "version": STATE_VERSION,
            "created_at": state.get("created_at") or seed_time,
            "last_seed_at": seed_time,
            "base_url": args.base_url,
            "user_name": user_name,
            "password": password,
            "knowledge_name": knowledge_name,
        }
    )

    with requests.Session() as client:
        api = LiveApi(args.base_url, client)
        user_data = api.ensure_user(user_name, password, args.email)
        state["token"] = user_data.get("access_token", "")
        state["user_id"] = user_data.get("user_id", "")
        save_state(args.state, state)

        knowledge = api.find_or_create_knowledge(knowledge_name, knowledge_desc)
        knowledge_id = knowledge["id"]
        state["knowledge_id"] = knowledge_id
        save_state(args.state, state)

        existing_files = api.select_knowledge_files(knowledge_id)
        uploaded_files: List[Dict[str, Any]] = []

        for source in sources:
            existing = next(
                (
                    row
                    for row in existing_files
                    if row.get("file_name") == source.name and row.get("status") == "success"
                ),
                None,
            )
            if existing:
                uploaded_files.append(
                    {
                        "source_file": source.name,
                        "file_id": existing.get("id"),
                        "file_name": existing.get("file_name") or source.name,
                        "oss_url": existing.get("oss_url", ""),
                        "status": "success",
                    }
                )
                continue

            sign_url = api.upload_file(source.name, source.read_bytes())
            api.mount_file(knowledge_id, sign_url)
            row = _wait_for_file(
                api,
                knowledge_id,
                source.name,
                args.wait_timeout,
            )
            uploaded_files.append(
                {
                    "source_file": source.name,
                    "file_id": row.get("id"),
                    "file_name": row.get("file_name") or source.name,
                    "oss_url": row.get("oss_url") or sign_url,
                    "status": row.get("status"),
                }
            )

        state["files"] = uploaded_files
        save_state(args.state, state)

    await _init_settings_for_chroma()
    ground_truth = await _build_ground_truth(
        knowledge_id,
        seed_time,
        [source.name for source in sources],
        query_specs,
        dataset_name,
    )
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ground_truth_file_name = (
        "live_rag_ab_ground_truth.json" if ab_mode else "live_rag_ground_truth.json"
    )
    ground_truth_path = output_dir / ground_truth_file_name
    ground_truth_path.write_text(
        json.dumps(ground_truth, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = {
        "stage": "p5.9" if ab_mode else "p5.2",
        "created_at": seed_time,
        "base_url": args.base_url,
        "user_name": user_name,
        "knowledge_id": knowledge_id,
        "knowledge_name": knowledge_name,
        "dataset_name": dataset_name,
        "files": uploaded_files,
        "chunk_count": len(ground_truth["indexed_chunks"]),
        "source_count": len(sources),
        "query_count": len(ground_truth["queries"]),
        "difficulty_counts": ground_truth["difficulty_counts"],
        "queries_file": str(args.queries_file) if args.queries_file else None,
        "ground_truth_file": str(ground_truth_path),
    }
    result_path = output_dir / ("live_seed_ab_result.json" if ab_mode else "live_seed_result.json")
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


async def _init_settings_for_chroma() -> None:
    from agentchat.settings import init_app_settings

    await init_app_settings()


def main(argv=None) -> None:
    args = _build_parser().parse_args(argv)
    result = asyncio.run(run_seed(args))
    sys.stdout.buffer.write((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
