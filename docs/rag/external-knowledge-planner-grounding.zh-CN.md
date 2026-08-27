# 外部消防知识 RAG 接入任务规划

FireClaw 将外部消防资料与任务记忆分成两个命名空间：

- `mission_memory` 是当前任务的权威记录，必须经过任务、运行模式和敏感级别校验。
- `external_knowledge` 是只读参考资料，不能授权动作、声明现场状态或替代传感器复核。

## 构建外部知识索引

现有 PDF 提取和分块流程不变。索引准备阶段会给记录写入
`source_kind="external_knowledge"`。旧索引需要重新执行准备和构建：

```bash
PYTHONPATH=src python -m fireclaw_core.rag.rag_cli prepare-index \
  --small-chunks /path/to/small_chunks.jsonl \
  --output-dir /srv/lpp-extra/fireclaw/indexes/fire-knowledge/input

PYTHONPATH=src python -m fireclaw_core.rag.rag_cli build-bm25-index \
  --records /srv/lpp-extra/fireclaw/indexes/fire-knowledge/input/small_index_records.jsonl \
  --index-dir /srv/lpp-extra/fireclaw/indexes/fire-knowledge/bm25
```

每条可进入 Planner 的记录必须包含：

- `source_kind="external_knowledge"`
- 非空的 `chunk_id`、`clean_text` 和 `allowed_use`
- 至少一个来源字段：`source_url` 或 `source_file`

## 配置 MissionGateway

BM25 是资源占用最低的起点：

```toml
[planner]
type = "llm"

[mission.knowledge_rag]
backend = "bm25"
bm25_index_dir = "/srv/lpp-extra/fireclaw/indexes/fire-knowledge/bm25"
candidate_multiplier = 3
```

混合检索增加 dense 配置：

```toml
[mission.knowledge_rag]
backend = "hybrid"
bm25_index_dir = "/srv/lpp-extra/fireclaw/indexes/fire-knowledge/bm25"
dense_index_dir = "/srv/lpp-extra/fireclaw/indexes/fire-knowledge/dense"
embedding_provider = "bge-m3"
embedding_model_path = "/srv/lpp-extra/fireclaw/models/bge-m3"
device = "cuda"
candidate_multiplier = 3
rrf_k = 60
```

启动：

```bash
PYTHONPATH=src python -m fireclaw_core serve --config fireclaw.sim.toml
```

也可以使用同名 CLI 参数，例如 `--knowledge-rag-backend`、
`--knowledge-rag-bm25-index-dir` 和 `--knowledge-rag-embedding-model-path`。

## 规划时的安全边界

任务命令会在调用 Planner 前检索外部知识。结果以结构化引用进入
`MissionPlannerContext.external_knowledge`，文本长度有上限，并保留文档、页码、
发布者、权威等级和许可用途。标识、来源地址和其他元数据也有独立长度上限；
缺失或超限的必要来源信息、倒置页码不会进入 Planner。

LLM 只能在 `knowledge_refs` 中引用本次检索返回的 `knowledge_id`。引用未知 ID
会被 Planner parser 拒绝。外部资料中的任何命令式文字都按数据处理，实际动作仍
必须经过当前传感器复核、计划校验和 Safety Gate。
