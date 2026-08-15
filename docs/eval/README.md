# 评测证据目录

按“证据性质”拆成三个子目录，避免把固定回归、真实链路结果和待办设计混在一起。

## `offline/`

- 内容：P2/P3 的可复现离线基准与指标口径。
- 示例：`rag_p3_before_after.json`、`memory_dedup_p3.json`、`token_budget_p3.json`、`METRICS_DEFINITIONS.md`、`REPORT_TEMPLATE.md`。
- 特点：只依赖 Python 标准库，任何环境重跑结果应一致，用于回归与解释，不冒充真实链路。

## `live/`

- 内容：P5 已完成的真实服务链路原始 JSON，以及后续执行生成的真实链路结果。
- 示例：`live_rag_*.json`、`live_rag_ab_*`、`live_rag_comparison_*.json`、`live_completion_*.json`、`live_cancel_*.json`、`live_memory_*.json`、`live_multi_agent_*.json`。
- 特点：需要 conda `agentchat` 后端、Docker 依赖和真实 Embedding/LLM；面试材料只能引用这个目录里的原始 JSON 来支撑“服务链路实测”。
- 归档规则：live benchmark 脚本默认输出到本目录，每次运行生成带时间戳的新文件，不覆盖旧结果。
- P5.9 RAG A/B 设计与执行记录：`RAG_COMPARISON_DESIGN.md`；最新正式对比 `live_rag_comparison_20260814_182215.json`（Rerank 48/50），历史 Rerank 403 对照 `live_rag_comparison_20260814_173430.json`，ground truth `live_rag_ab_ground_truth.json`。

## `upcoming/`

- 内容：尚未执行、已确定口径的评测设计。
- 示例：`MEMORY_COMPARISON_DESIGN.md`（P5.10 两层 vs 三层）。
- 特点：这些文档只定义怎么做和面试可以说什么；对应 `live_*.json` 落盘之前，数字不能进入简历。

## 目录外文件

`docs/eval/` 根目录暂时可能保留运行中后端占用的 debug 日志（如 `backend_p55_dbg.log`）。后端停止后应归并到 `live/` 或删除，根目录不作为评测产物归档位置。
