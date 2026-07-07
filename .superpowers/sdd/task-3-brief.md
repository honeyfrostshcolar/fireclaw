### Task 3: Ten Evidence-First Gold Cases

**Files:**
- Create: `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl`

**Interfaces:**
- Consumes:
  - `data/rag/fire_rescue/chunks/parent_chunks.jsonl`
  - `data/rag/fire_rescue/indexes/dense/bge-m3/records.jsonl`
  - `load_dense_eval_cases(path: Path) -> list[DenseEvalCase]`
- Produces:
  - 10 valid JSONL rows for the v1 Chinese dense eval gold set.

- [ ] **Step 1: Inspect candidate evidence by topic**

Use read-only searches over parent chunks and index records. Start with topic terms that align with the spec:

```powershell
rg -n -i "thermal|victim|smoke|visibility|USAR|rubble|mobility|gas|odor|rehabilitation|confined|hazardous|Smokeview|FDS|sprinkler|collapse" data\rag\fire_rescue\chunks\parent_chunks.jsonl
```

Expected:

```text
Several candidate parent chunk lines with parent_id, doc_id, page metadata, and text.
```

- [ ] **Step 2: Select 10 parent chunks**

Select parents that satisfy all of these conditions:

```text
1. parent_id is present.
2. text is substantive, not cover/front matter/table of contents.
3. evidence supports a FireClaw task, safety, sensing, or robot capability question.
4. topics are diverse.
5. source metadata remains traceable.
```

Record each selected parent with:

```text
case_id
topic
Chinese query
gold_parent_ids
source_doc_id
expected_evidence_summary
notes
```

- [ ] **Step 3: Create the JSONL file**

Create `data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl` with exactly 10 rows. Each row must contain these fields with concrete values from Step 2: `case_id`, `topic`, `query`, `gold_parent_ids`, `gold_chunk_ids`, `expected_evidence_summary`, `source_doc_id`, and `notes`.

Before completing the step, run this structural check:

```powershell
$env:PYTHONPATH = ".deps;src"; python -c "from pathlib import Path; from fireclaw_core.rag.dense_eval import load_dense_eval_cases; cases=load_dense_eval_cases(Path('data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl')); assert len(cases)==10; assert all(c.case_id and c.query and c.gold_parent_ids for c in cases); print([c.case_id for c in cases])"
```

Expected:

```text
The command prints 10 case ids and exits with code 0.
```

- [ ] **Step 4: Validate the JSONL file with the loader**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -c "from pathlib import Path; from fireclaw_core.rag.dense_eval import load_dense_eval_cases; cases=load_dense_eval_cases(Path('data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl')); print(len(cases)); assert len(cases)==10"
```

Expected:

```text
10
```

- [ ] **Step 5: Run unit tests after adding data file**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"; python -m pytest --basetemp=pytest_tmp_dense_eval_cases_final tests/test_rag_dense_eval.py tests/test_rag_dense_cli.py -q
```

Expected:

```text
All tests pass
```

- [ ] **Step 6: Checkpoint without commit**

Run:

```powershell
git status --short --branch
```

Expected:

```text
?? data/rag/fire_rescue/eval/dense_gold_cases_zh_v1.jsonl
```

Other modified and untracked evaluation files may also appear.

---
