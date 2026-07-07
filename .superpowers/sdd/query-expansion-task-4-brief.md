### Task 4: CLI Flags and Candidate Expansion Artifacts

**Files:**
- Modify: `src/fireclaw_core/rag/rag_cli.py`
- Modify: `tests/test_rag_dense_cli.py`
- Create: `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl`
- Create: `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl`

**Interfaces:**
- Consumes:
  - `load_query_expansions`
  - `evaluate_dense_retriever_with_expansion`
- Produces:
  - optional CLI flags:
    - `--query-expansions`
    - `--query-variants`
    - `--ranking-view`
    - `--small-top-k`
    - `--parent-aggregation`
    - `--fusion`
    - `--rrf-k`
    - `--require-reviewed-expansions`

- [ ] **Step 1: Write failing CLI test for expanded report config**

Add to `tests/test_rag_dense_cli.py`:

```python
def test_cli_eval_dense_index_with_expansion_and_parent_view_writes_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fireclaw_core.rag.dense_retrieval import FakeEmbeddingProvider, build_dense_index

    records_path = tmp_path / "records.jsonl"
    records_path.write_text(
        "\n".join(
            [
                json.dumps({"chunk_id": "wrong_1", "parent_id": "parent_wrong", "doc_id": "doc", "clean_text": "wrong rescue", "indexable": True}),
                json.dumps({"chunk_id": "wrong_2", "parent_id": "parent_wrong", "doc_id": "doc", "clean_text": "wrong victim", "indexable": True}),
                json.dumps({"chunk_id": "gold_1", "parent_id": "parent_gold", "doc_id": "doc", "clean_text": "rescue victim search", "indexable": True}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index_dir = tmp_path / "dense_index"
    build_dense_index(records_path, index_dir, FakeEmbeddingProvider(), batch_size=2)

    cases_path = tmp_path / "cases.jsonl"
    cases_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "topic": "rescue",
                "query": "rescue victim",
                "gold_parent_ids": ["parent_gold"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    expansions_path = tmp_path / "query_expansions.jsonl"
    expansions_path.write_text(
        json.dumps(
            {
                "case_id": "dense_zh_001",
                "query_zh": "rescue victim",
                "llm_query_en": "rescue victim search",
                "reviewed_query_en": "",
                "term_query": "victim search",
                "terms": ["victim search"],
                "status": "candidate",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "expanded_report.json"

    exit_code = main(
        [
            "eval-dense-index",
            "--provider",
            "fake",
            "--index-dir",
            str(index_dir),
            "--cases",
            str(cases_path),
            "--query-expansions",
            str(expansions_path),
            "--query-variants",
            "zh,en,terms",
            "--ranking-view",
            "parent",
            "--small-top-k",
            "10",
            "--top-k",
            "10",
            "--output",
            str(output_path),
        ]
    )

    assert exit_code == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["retrieval_config"]["query_variants"] == ["zh", "en", "terms"]
    assert report["retrieval_config"]["ranking_view"] == "parent"
    assert report["retrieval_config"]["small_top_k"] == 10
    assert "query_variants" in report["results"][0]
    printed = json.loads(capsys.readouterr().out)
    assert printed["retrieval_config"]["fusion"] == "rrf"
```

- [ ] **Step 2: Run CLI test to verify it fails**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_cli_expanded_red tests/test_rag_dense_cli.py::test_cli_eval_dense_index_with_expansion_and_parent_view_writes_config -q
```

Expected:

```text
argparse error for unrecognized arguments: --query-expansions ...
```

- [ ] **Step 3: Add CLI arguments and dispatch**

Modify imports in `src/fireclaw_core/rag/rag_cli.py`:

```python
from fireclaw_core.rag.dense_eval import evaluate_dense_retriever_with_expansion
from fireclaw_core.rag.query_expansion import load_query_expansions
```

Add args to `eval-dense-index`:

```python
dense_eval.add_argument("--query-expansions", default=None)
dense_eval.add_argument("--query-variants", default="zh")
dense_eval.add_argument("--ranking-view", choices=["small", "parent"], default="small")
dense_eval.add_argument("--small-top-k", type=int, default=None)
dense_eval.add_argument("--parent-aggregation", choices=["max"], default="max")
dense_eval.add_argument("--fusion", choices=["none", "rrf"], default=None)
dense_eval.add_argument("--rrf-k", type=int, default=60)
dense_eval.add_argument("--require-reviewed-expansions", action="store_true")
```

Modify `_cmd_eval_dense_index`:

```python
    query_variants = _parse_csv_arg(args.query_variants)
    use_expanded_eval = (
        query_variants != ["zh"]
        or args.ranking_view != "small"
        or args.small_top_k is not None
        or args.query_expansions is not None
        or args.fusion is not None
        or args.require_reviewed_expansions
    )
    if use_expanded_eval:
        expansions_path = Path(args.query_expansions) if args.query_expansions else None
        expansions = load_query_expansions(expansions_path) if expansions_path is not None else {}
        report = evaluate_dense_retriever_with_expansion(
            retriever,
            cases,
            query_expansions=expansions,
            query_variants=query_variants,
            ranking_view=args.ranking_view,
            top_k=args.top_k,
            small_top_k=args.small_top_k,
            parent_aggregation=args.parent_aggregation,
            fusion=args.fusion,
            rrf_k=args.rrf_k,
            require_reviewed_expansions=args.require_reviewed_expansions,
            query_expansions_path=str(expansions_path) if expansions_path is not None else None,
        )
    else:
        report = evaluate_dense_retriever(retriever, cases, top_k=args.top_k)
```

Add helper:

```python
def _parse_csv_arg(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise ValueError("CSV argument must contain at least one item")
    return items
```

- [ ] **Step 4: Create candidate query expansion cache**

Create `data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl` with exactly these 10 JSONL rows:

```jsonl
{"case_id":"dense_zh_001","query_zh":"在浓烟遮挡、光照差的室内火灾里，为什么应优先用热成像而不是普通RGB相机搜人？","llm_query_en":"In an indoor fire with dense smoke and poor lighting, why should thermal imaging be preferred over a regular RGB camera for victim search?","reviewed_query_en":"","term_query":"thermal imaging infrared camera victim detection fireground smoke low visibility RGB camera target detection firefighter search and rescue","terms":["thermal imaging","infrared camera","victim detection","fireground smoke","low visibility","RGB camera","target detection","firefighter search and rescue"],"status":"candidate","notes":"Candidate translation and terms for thermal victim search; user review required."}
{"case_id":"dense_zh_002","query_zh":"已知厂房平面图时，机器人如何规划远程气体扫描，尽快找出可燃气体泄漏源？","llm_query_en":"Given a known factory floor plan, how should a robot plan remote gas scanning to find combustible gas leak sources as quickly as possible?","reviewed_query_en":"","term_query":"remote gas detection methane leak TDLAS Remote Methane Leak Detector Next-Best-Smell coverage planning candidate locations information gain sensing time occupancy grid","terms":["remote gas detection","methane leak","TDLAS","Remote Methane Leak Detector","Next-Best-Smell","coverage planning","candidate locations","information gain","sensing time","occupancy grid"],"status":"candidate","notes":"Candidate translation and terms from the gas source detection miss-case analysis; user review required."}
{"case_id":"dense_zh_003","query_zh":"无人机在浓烟遮挡下怎样利用热成像发现被烟挡住的明火位置？","llm_query_en":"How can a UAV use thermal imaging to detect open flame locations that are obscured by dense smoke?","reviewed_query_en":"","term_query":"UAV thermal imaging flame detection smoke-obscured fire infrared thermal fire detection FlameFinder deep metric learning","terms":["UAV","thermal imaging","flame detection","smoke-obscured fire","infrared","thermal fire detection","FlameFinder","deep metric learning"],"status":"candidate","notes":"Candidate translation and terms for smoke-obscured flame detection; user review required."}
{"case_id":"dense_zh_004","query_zh":"建筑坍塌后，什么类型的机器人更适合进入狭小空洞搜索幸存者？","llm_query_en":"After a building collapse, what type of robot is better suited to enter narrow void spaces to search for survivors?","reviewed_query_en":"","term_query":"USAR void space search vine robot soft robot continuum robot confined spaces collapsed structure survivor search SPROUT","terms":["USAR","void space search","vine robot","soft robot","continuum robot","confined spaces","collapsed structure","survivor search","SPROUT"],"status":"candidate","notes":"Candidate translation and terms for void-space robot search; user review required."}
{"case_id":"dense_zh_005","query_zh":"坍塌废墟中的空洞入口危险度怎么分级，哪些情况对人类很难但对软体机器人更可行？","llm_query_en":"How should void entrance risk in collapsed rubble be classified, and which conditions are difficult for humans but more feasible for soft robots?","reviewed_query_en":"","term_query":"void entrance risk collapsed rubble soft robot vine robot constrained aperture unstable debris low clearance human entry risk USAR","terms":["void entrance risk","collapsed rubble","soft robot","vine robot","constrained aperture","unstable debris","low clearance","human entry risk","USAR"],"status":"candidate","notes":"Candidate translation and terms for USAR void entry risk; user review required."}
{"case_id":"dense_zh_006","query_zh":"消防救援机器人在采购前应该从哪些标准化能力维度做测试？","llm_query_en":"Before purchasing a firefighting or rescue robot, which standardized capability dimensions should be tested?","reviewed_query_en":"","term_query":"response robots standard test methods DHS-NIST-ASTM robot purchases representative test methods performance objectives lower capability thresholds mission capabilities operator proficiency","terms":["response robots","standard test methods","DHS-NIST-ASTM","robot purchases","representative test methods","performance objectives","lower capability thresholds","mission capabilities","operator proficiency"],"status":"candidate","notes":"Candidate translation and terms from the response robot test methods miss-case analysis; user review required."}
{"case_id":"dense_zh_007","query_zh":"大型建筑做消防预案时，消防队最关心哪些建筑消防系统和现场标识信息？","llm_query_en":"When preparing a fire preplan for a large building, which building fire protection systems and on-site signage information matter most to the fire service?","reviewed_query_en":"","term_query":"fire service features pre-incident planning building fire protection systems fire alarm sprinkler standpipe fire command center signage evacuation floor plans access points","terms":["fire service features","pre-incident planning","building fire protection systems","fire alarm","sprinkler","standpipe","fire command center","signage","evacuation","floor plans","access points"],"status":"candidate","notes":"Candidate translation and terms for building fire service features; user review required."}
{"case_id":"dense_zh_008","query_zh":"消防员连续作业多久、用完几瓶空呼后，必须进入rehab做补水和医疗评估？","llm_query_en":"When must firefighters enter rehab for hydration and medical evaluation after continuous work or after using SCBA cylinders?","reviewed_query_en":"","term_query":"SCBA self-contained breathing apparatus SCBA cylinder NFPA 1584 emergency incident rehabilitation self-rehab formal rehab medical evaluation hydration work-to-rest ratio vital signs","terms":["SCBA","self-contained breathing apparatus","SCBA cylinder","NFPA 1584","emergency incident rehabilitation","self-rehab","formal rehab","medical evaluation","hydration","work-to-rest ratio","vital signs"],"status":"candidate","notes":"Candidate translation and terms from the firefighter rehab threshold miss-case analysis; user review required."}
{"case_id":"dense_zh_009","query_zh":"在疑似有毒泄漏或危险品污染的坍塌现场，USAR队进入前必须检查哪些风险？","llm_query_en":"At a collapsed structure site suspected of toxic leakage or hazardous materials contamination, what risks must a USAR team check before entry?","reviewed_query_en":"","term_query":"USAR hazmat hazardous materials contaminated site contaminated environment PPE personal protective equipment go/no-go conditions risk-benefit analysis detection and monitoring decontamination clean entry points","terms":["USAR","hazmat","hazardous materials","contaminated site","contaminated environment","PPE","personal protective equipment","go/no-go conditions","risk-benefit analysis","detection and monitoring","decontamination","clean entry points"],"status":"candidate","notes":"Candidate translation and terms from the USAR hazmat entry safety miss-case analysis; user review required."}
{"case_id":"dense_zh_010","query_zh":"机器人要穿过有多个火源的区域时，怎样根据热辐射代价图规划一条更安全的路线？","llm_query_en":"When a robot must traverse an area with multiple fire sources, how can it plan a safer path using a thermal radiation cost map?","reviewed_query_en":"","term_query":"thermal radiation cost map fire-aware navigation thermal occupancy grid heat flux path planning multiple fire sources safe robot navigation thermally aware path planning","terms":["thermal radiation cost map","fire-aware navigation","thermal occupancy grid","heat flux","path planning","multiple fire sources","safe robot navigation","thermally aware path planning"],"status":"candidate","notes":"Candidate translation and terms for thermal radiation path planning; user review required."}
```

- [ ] **Step 5: Create candidate glossary**

Create `data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl` with exactly these JSONL rows:

```jsonl
{"entry_id":"remote_gas_detection_core","category":"remote_gas_detection","zh_aliases":["远程气体检测","气体扫描","可燃气体泄漏"],"en_terms":["remote gas detection","methane leak","TDLAS","Remote Methane Leak Detector","Next-Best-Smell","coverage planning","candidate locations","information gain","sensing time"],"source_parent_ids":["arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00001","arxiv_1801_06819_next_best_smell_mobile_robot_gas_detection__parent_00002"],"status":"candidate","notes":"Candidate terms extracted from the remote gas source detection miss-case analysis."}
{"entry_id":"response_robot_testing_core","category":"response_robot_test_methods","zh_aliases":["消防救援机器人测试","采购前测试","标准化能力测试"],"en_terms":["response robots","standard test methods","DHS-NIST-ASTM","robot purchases","representative test methods","performance objectives","lower capability thresholds","mission capabilities","operator proficiency"],"source_parent_ids":["nist_response_robot_test_methods_guide__parent_00003","nist_response_robot_test_methods_guide__parent_00016"],"status":"candidate","notes":"Candidate terms extracted from NIST response robot test method hits."}
{"entry_id":"firefighter_rehab_scba","category":"firefighter_rehab","zh_aliases":["空呼","空气呼吸器","消防员康复","补水和医疗评估"],"en_terms":["SCBA","self-contained breathing apparatus","SCBA cylinder","NFPA 1584","emergency incident rehabilitation","self-rehab","formal rehab","medical evaluation","hydration","work-to-rest ratio","vital signs"],"source_parent_ids":["usfa_emergency_incident_rehabilitation_fa_314__parent_00068","usfa_emergency_incident_rehabilitation_fa_314__parent_00102"],"status":"candidate","notes":"Candidate terms extracted from firefighter rehab threshold analysis."}
{"entry_id":"usar_hazmat_entry","category":"usar_hazmat_entry_safety","zh_aliases":["危险品","有毒泄漏","污染现场","进入前风险检查"],"en_terms":["USAR","hazmat","hazardous materials","contaminated site","contaminated environment","PPE","personal protective equipment","go/no-go conditions","risk-benefit analysis","detection and monitoring","decontamination","clean entry points"],"source_parent_ids":["insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00021","insarag_guidelines_2020_volume_iii_operational_field_guide__parent_00022"],"status":"candidate","notes":"Candidate terms extracted from INSARAG hazmat entry safety analysis."}
{"entry_id":"thermal_victim_search","category":"thermal_victim_search","zh_aliases":["热成像搜人","浓烟搜救","低能见度搜救"],"en_terms":["thermal imaging","infrared camera","victim detection","fireground smoke","low visibility","RGB camera","target detection","firefighter search and rescue"],"source_parent_ids":["arxiv_1910_03617_thermal_image_target_detection_firefighting__parent_00002"],"status":"candidate","notes":"Candidate terms for thermal victim search."}
{"entry_id":"thermal_radiation_navigation","category":"thermal_radiation_path_planning","zh_aliases":["热辐射代价图","热安全路径规划","多火源路径规划"],"en_terms":["thermal radiation cost map","fire-aware navigation","thermal occupancy grid","heat flux","path planning","multiple fire sources","safe robot navigation","thermally aware path planning"],"source_parent_ids":["arxiv_2603_19063_fire_as_a_service_robot_simulators_fire_dynamics__parent_00007"],"status":"candidate","notes":"Candidate terms for thermal radiation path planning."}
```

- [ ] **Step 6: Run CLI tests**

Run:

```powershell
$env:PYTHONPATH = ".deps;src"
python -m pytest --basetemp=$env:TEMP\pytest_tmp_dense_cli_expanded_green tests/test_rag_dense_cli.py -q
```

Expected:

```text
all tests in tests/test_rag_dense_cli.py pass
```

- [ ] **Step 7: Checkpoint without committing**

Run:

```powershell
git diff -- src/fireclaw_core/rag/rag_cli.py tests/test_rag_dense_cli.py data/rag/fire_rescue/eval/query_expansions_zh_v1.jsonl data/rag/fire_rescue/eval/glossary_candidates_zh_v1.jsonl
```

Expected:

```text
diff output shows CLI flags, CLI tests, and candidate expansion/glossary files
```
