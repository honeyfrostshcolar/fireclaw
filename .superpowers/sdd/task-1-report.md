# Task 1 Report - FireClaw Dense Retrieval Evaluation Core

Date: 2026-07-07

## 实现内容

完成 `FireClaw dense retrieval evaluation core`，仅修改允许范围内的三个文件：

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `.superpowers/sdd/task-1-report.md`

本次实现包含：

- `DenseEvalCase`
- `DenseEvalRetrievedHit`
- `DenseEvalCaseResult`
- `DenseEvalReport`
- `load_dense_eval_cases(path: Path) -> list[DenseEvalCase]`
- `evaluate_ranked_hits(cases, hits_by_case_id, *, top_k=10) -> DenseEvalReport`
- `hits_from_dense_results(hits: list[DenseHit]) -> list[DenseEvalRetrievedHit]`
- `evaluate_dense_retriever(retriever, cases, *, top_k=10) -> DenseEvalReport`

核心行为：

- 支持 UTF-8 / UTF-8 BOM JSONL 读取
- 校验 `case_id`、`query`、`gold_parent_ids`
- 拒绝重复 `case_id`
- 将 dense retriever 的 `DenseHit.record` 转成评估 hit 结构
- 计算 `Hit@1`、`Hit@5`、`Hit@10`、`MRR@10`、`gold_recall@10`
- 输出可序列化的 report / case result / hit 数据

## RED / GREEN

### RED 1: case loader

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_red tests/test_rag_dense_eval.py -q
```

Key output:

```text
ModuleNotFoundError: No module named 'fireclaw_core.rag.dense_eval'
```

### GREEN 1: case loader

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_green tests/test_rag_dense_eval.py -q
```

Key output:

```text
3 passed in 0.10s
```

### RED 2: metrics

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_metrics_red tests/test_rag_dense_eval.py -q
```

Key output:

```text
ImportError: cannot import name 'DenseEvalRetrievedHit' from 'fireclaw_core.rag.dense_eval'
```

### GREEN 2: metrics

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_metrics_green tests/test_rag_dense_eval.py -q
```

Key output:

```text
5 passed in 0.07s
```

### RED 3: conversion / runner

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_runner_red tests/test_rag_dense_eval.py -q
```

Key output:

```text
ImportError: cannot import name 'evaluate_dense_retriever' from 'fireclaw_core.rag.dense_eval'
```

### GREEN 3: full dense eval core

Command:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_core_final tests/test_rag_dense_eval.py -q
```

Key output:

```text
8 passed in 2.04s
```

## 测试结果

- `tests/test_rag_dense_eval.py`: `8 passed`
- 运行期间出现过 Windows pytest basetemp 清理权限问题，已用提升权限重跑并确认绿灯

## 改动文件

- `src/fireclaw_core/rag/dense_eval.py`
- `tests/test_rag_dense_eval.py`
- `.superpowers/sdd/task-1-report.md`

## 自检结论

Task 1 的 dense retrieval evaluation core 已完成并通过单测。实现与 brief 的核心接口一致，支持 case 加载、结果转换、指标计算和 retriever 评估。

## 2026-07-07 Fix Note: top_k guardrail

Reviewer found that `top_k` could be set below 10 while the report still exposed fixed `Hit@10`, `MRR@10`, and `gold_recall@10` metrics. That is semantically inconsistent, so I added an explicit guard:

- `evaluate_ranked_hits(..., top_k < 10)` now raises `ValueError("top_k must be at least 10")`
- `evaluate_dense_retriever(...)` now applies the same validation before querying the retriever
- Added a unit test to pin the guardrail in `tests/test_rag_dense_eval.py`

### RED

Command:

```powershell
python -m pytest --basetemp=pytest_tmp_dense_eval_topk_red tests/test_rag_dense_eval.py -q -k top_k_below_10
```

Key output:

```text
Failed: DID NOT RAISE <class 'ValueError'>
```

### GREEN

Command:

```powershell
python -m pytest --basetemp="$env:TEMP\pytest_tmp_dense_eval_topk_green" tests/test_rag_dense_eval.py -q
```

Key output:

```text
9 passed in 0.19s
```

## 2026-07-07 Fix Note: dense retriever guard test coverage

Reviewer noted that `evaluate_dense_retriever(..., top_k < 10)` was already guarded in production, but the test suite only pinned `evaluate_ranked_hits(..., top_k < 10)`.

I added a regression test in `tests/test_rag_dense_eval.py` that:
- defines `FakeRetriever.query` to raise `AssertionError` if it is ever called;
- calls `evaluate_dense_retriever(FakeRetriever(), [case], top_k=5)`;
- asserts `ValueError("top_k must be at least 10")`.

### RED

Command:

```powershell
& { $env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=pytest_tmp_dense_eval_topk_red tests/test_rag_dense_eval.py -q -k 'rejects_top_k_below_10_without_querying' }
```

Key output:

```text
FAILED tests/test_rag_dense_eval.py::test_evaluate_dense_retriever_rejects_top_k_below_10_without_querying
ValueError: top_k must be at least 10
```

### GREEN

Command:

```powershell
& { $env:PYTHONPATH = '.deps;src'; python -m pytest --basetemp=pytest_tmp_dense_eval_topk_green tests/test_rag_dense_eval.py -q -k 'rejects_top_k_below_10_without_querying' }
```

Key output:

```text
1 passed, 9 deselected in 0.25s
```

## Concerns

- `gold_recall@10` 目前按去重后的 gold parent 计算，符合“distinct gold parents”语义，但后续若 gold 规范变化需要同步说明。
- `page_start` / `page_end` 在转换层保留原值，当前没有强制归一化为整数；如果上游记录类型不稳定，后续可以补一层校验。
- CLI 接入、gold cases 数据文件和更大范围验证属于后续任务，本次未触碰。
