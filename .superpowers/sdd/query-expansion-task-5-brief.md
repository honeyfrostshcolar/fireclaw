### Task 5: Documentation and Verification

**Files:**
- Modify: `docs/rag/dense-evaluation-walkthrough.zh-CN.md`
- Modify: `memory/2026-07-07/fireclaw-dense-miss-case-analysis.md`

**Interfaces:**
- Consumes:
  - final test commands;
  - BGE-M3 eval report paths.
- Produces:
  - updated user-facing explanation;
  - memory record with metrics and commands.

- [ ] **Step 1: Update dense evaluation walkthrough**

Append this section to `docs/rag/dense-evaluation-walkthrough.zh-CN.md`:

````markdown
## 可选优化评估：Query Expansion 与 Parent Aggregation

当前 strict dense baseline 不变，默认仍然只用中文 query 和 small chunk 排名。

新增优化只在显式打开参数时生效：

```text
--query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl
--query-variants zh,en,terms
--ranking-view parent
--small-top-k 50
```

含义是：

```text
中文原 query
+ 缓存的英文翻译 query
+ 候选专业术语 query
-> 分别 dense 检索
-> 用 RRF 融合
-> 按 parent_id 聚合排序
-> 仍然用原始 strict gold_parent_ids 计算 Hit@K / MRR@10 / gold_recall@10
```

注意：

```text
query_expansions_zh_v1.jsonl 和 glossary_candidates_zh_v1.jsonl 初始状态是 candidate。
candidate 表示这些翻译和术语由 LLM/agent 辅助生成，还需要人工审核。
正式论文实验应记录是否使用 candidate，或者先改成 reviewed 后再作为正式结果。
```
````

- [ ] **Step 2: Run focused unit tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_expansion_all tests/test_rag_query_expansion.py tests/test_rag_dense_ranking.py tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py tests/test_rag_dense_retrieval.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 3: Verify strict BGE-M3 baseline remains unchanged**

Run:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_report.json
```

Expected metrics:

```text
case_count = 10
hit_at_1 = 0.2
hit_at_5 = 0.4
hit_at_10 = 0.6
mrr_at_10 = 0.323611
gold_recall_at_10 = 0.6
```

- [ ] **Step 4: Run parent-only ablation**

Run:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_parent_report.json
```

Expected:

```text
command exits 0
report includes retrieval_config.ranking_view = "parent"
metrics may differ from strict baseline
```

- [ ] **Step 5: Run expanded parent ablation**

Run:

```powershell
$env:PYTHONPATH = 'src'
.\.venv-bge-m3\Scripts\python.exe -m fireclaw_core.rag.rag_cli eval-dense-index --provider bge-m3 --model-path .cache/models/bge-m3 --cases data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl --query-expansions data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl --query-variants zh,en,terms --ranking-view parent --small-top-k 50 --top-k 10 --output data/rag/fire_rescue/eval/runs/dense_bge_m3_zh_v1_expanded_parent_report.json
```

Expected:

```text
command exits 0
report includes retrieval_config.query_variants = ["zh", "en", "terms"]
report includes retrieval_config.fusion = "rrf"
metrics may differ from strict baseline
```

- [ ] **Step 6: Update memory with commands and results**

Append a timestamped section to `memory/2026-07-07/fireclaw-dense-miss-case-analysis.md` containing:

- implemented files and behavior;
- the exact pytest command and result line;
- strict baseline BGE-M3 command, report path, and metrics;
- parent-only ablation command, report path, and metrics;
- expanded parent ablation command, report path, and metrics;
- the caveat that `query_expansions_zh_v1.jsonl` and `glossary_candidates_zh_v1.jsonl` remain `candidate` until the user reviews them.

- [ ] **Step 7: Final diff check without committing**

Run:

```powershell
git status --short
git diff --check
```

Expected:

```text
git status shows the planned files modified/created
git diff --check exits 0
```

## Self-Review Checklist

- Spec coverage:
  - strict baseline preserved by default CLI path;
  - query expansion cache and reviewed/manual override covered;
  - professional terminology candidate files covered;
  - parent aggregation covered;
  - RRF fusion covered;
  - chunk embedding text enhancement excluded.
- Completeness scan:
  - no incomplete marker strings;
  - no incomplete task steps;
  - no unspecified file paths.
- Type consistency:
  - `QueryExpansion`, `QueryVariant`, `DenseEvalRetrievedHit`, `DenseEvalCaseResult`, and `DenseEvalReport` names are used consistently across tasks.
- Repository policy:
  - plan uses checkpoints rather than commits because commits require explicit user approval.
