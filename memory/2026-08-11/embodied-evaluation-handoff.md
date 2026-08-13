# Embodied evaluation 阶段交接

## 2026-08-11T00:02:18+08:00 — 用户决定今日收尾

### 任务目标

重做 FireClaw embodied evaluation，分别建立 deterministic integration、真实
LLM planning 和 ROS/Gazebo system 三条可复现评测链；保留模型、seed、Plugin/Tool、地图、
导航参数、canonical terminal outcomes 和完整 proof bundle，为后续论文实验提供可信数据。

### 当前进展

本阶段主要工程目标已经完成：Gazebo 六条 acceptance lane 已形成 clean validation/test
proof；真实 Provider planning lane 已完成统一配置、协议修正、多 seed development fixture、
首轮/恢复分离指标和 clean-commit v3。用户认为当前已基本完成，决定今天停止，下次继续。

当前 Git 状态（写本记录之前）：

```text
branch  agent/embodied-evaluation-collision-calibration
HEAD    2eff71c docs(eval): record clean planning baseline
remote  origin/agent/embodied-evaluation-collision-calibration
status  clean and synchronized
```

重要提交：

```text
19617fc feat(eval): harden real-provider planning lane
2eff71c docs(eval): record clean planning baseline
```

### 已完成

1. Gazebo acceptance/system evaluation：
   - success、cancel、timeout、stall-recover、stall-escalate 和 collision positive-control；
   - scheduler-backed Mission Run、same-task suspend/resume、canonical terminal propagation；
   - Plugin-owned `navigate_to_point`，禁止静默回退到 `Ros1RobotAdapter.navigate_to_point`；
   - feedback、safe stop、diagnostics、recovery/escalation、final report、audit 和 collision proof；
   - clean validation/test aggregate 均通过六条合同。
2. LLM planning evaluation：
   - Gateway 和 runner 共用 `fireclaw.toml [provider]`；credential 不进入 artifact；
   - OpenAI-compatible provider 固定 `Accept-Encoding: identity`，兼容当前真实 endpoint；
   - point/area/entity typed target，point 缺省 `yaw` 规范化为 `0.0`；
   - 每轮恰好一个 Tool call，非法 Tool-count 原响应不执行，只允许一次 bounded repair；
   - `first_try_clean_rate`、`tool_protocol_valid_first_try_rate`、条件
     `planning_recovery_rate`、Tool violation/repair counts 等指标已进入统一 proof schema；
   - 冻结 fixture：
     `tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json`；
   - fixture SHA-256：
     `1d7b8ba2a08acae2e2875bb1a2c9b7bb9342aafbfc5d48577c5e09c07aa098f3`；
   - seeds `[0,17,42,123,999]`，point/area/entity 各 5 条，共 15 cases；明确
     `split=development`、`paper_ready=false`。
3. 验证：
   - 最终代码 full suite：`2006 passed, 7 skipped`；
   - fixture/planning scoped regression：`32 passed`；
   - v3 artifact 177 个条目 size/hash 复算 mismatch=0；
   - proof bundle 明文 API key 命中=0。

### clean-commit v3 关键结果

结果目录：
`results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v3/`

运行 provenance：

```text
code commit             19617fc48cd0eeb6e1ba89b9ce775d39135e2336
repository dirty        false
actual model            mimo-v2.5-pro
temperature             0
provider/seed/no-dispatch 15/15 each
```

指标：

```text
planning success                    15/15 = 1.000000
strict contract                     11/15 = 0.733333
first-try clean                     11/15 = 0.733333
Tool protocol valid first try       15/15 = 1.000000
recovery success                     4/4  = 1.000000 applicable
proposed                            15
blocked/escalated/runner error       0/0/0
Tool-count violation/repair          0/0
point strict contract                5/5
area strict contract                 1/5
entity strict contract               5/5
model calls total                   35
tokens total                        128834
mean latency                        26528.260708 ms/case
```

artifact manifest SHA-256：
`1174b631b14889cea4ff37dd4a7b6307e93b3c40812cc83a7c00a656fcb1048b`。

完整 v1/v2/v3 数据、命令、逐 case 原因和 proof hashes 见：
`memory/2026-08-10/embodied-evaluation-rework.md`。

### 当前问题

剩余问题已集中到 area planning：

- seeds 0/17/123/999 首次 proposal 仍混淆 Mission `intent=patrol` 与节点
  `task_type=navigation`，输出非法 `task_type=patrol + capability=navigate`；确定性 validator
  正确拒绝，四条随后全部恢复。
- seeds 0/123 最终生成两个串行 navigation nodes：“导航到区域入口”再“覆盖巡检”。这可能是
  合理分解，但当前 development fixture 强制 `expected_task_count=1`，所以不能在看到结果后
  直接放宽合同。需要先制定人工 annotation/graph-equivalence 规则。
- 真实 v3 没有触发 Tool-count repair；其正确性由 scripted integration tests 证明。v3 的
  recovery 来自 graph validation feedback 后 replanning，论文中不能混称为 Tool repair。
- 当前 15 cases 已参与开发调试，不是 held-out test，不能直接作为论文泛化结论。

### 当前结论

工程正确性已接近阶段完成：三层评测骨架、Gazebo 纵向闭环、真实模型调用、统一 proof、
安全失败闭合和可重建实验记录均已建立。v3 证明当前 development 场景 15/15 能生成 validated
task graph，但 strict contract 仍需区分首轮生成与恢复后成功。

研究层面尚不能仅凭该结果宣称顶会贡献。论文主线仍应围绕“受安全约束的分层诊断与恢复”，
并用冻结 validation/test、matched ablation、多模型/多 seed 和安全指标支持主张。

### 下一步

下次优先从以下决策开始，不需要重扫仓库：

1. 定义 area 合法 graph equivalence：一个复合 navigation/patrol node、两个串行阶段，或允许
   多个等价 task graph；记录人工标注原则，禁止事后按模型输出改标准。
2. 基于 task registry 将合法 `task_type-capability` pair 结构化投影到 Tool schema/context，
   不再只依赖 prompt。
3. 增加配置化 ablation 开关，在相同 prompt/fixture 下比较：repair off、bounded repair、
   schema-constrained；测 first-try、final success、recovery、rejection、latency、token。
4. development 决策完成后，冻结独立 validation/test targets 与新 seeds；此后不再根据 test
   输出调 prompt 或合同。
5. 之后再进入研究主线的 diagnostics variants：无诊断、仅摘要、摘要+按需证据、中心诊断、
   Robot 本地 typed diagnostics，并接入 Gazebo stall-recover/stall-escalate system proof。

### 下次建议先运行的命令

```bash
git status --short --branch
git log -2 --oneline --decorate
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_llm_planning_eval.py \
  tests/test_llm_deliberation_policy.py \
  tests/test_evaluation_metrics.py \
  tests/test_evaluation_contracts.py
```

若要核对 v3，不要重新调用 Provider，先读取：

```text
results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v3/summary.json
results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v3/paper-summary.json
results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v3/scenarios.jsonl
```

### 本次停止点

用户决定 2026-08-11 今日先结束，下次从 area graph annotation 与结构化
`task_type-capability` projection 继续。本交接文件仅为本次“记录一下”的本地修改，尚未
commit/push；除非用户下次明确要求，不自动提交或发起新的真实 Provider 运行。

## 2026-08-11T10:00:44+08:00 — 下一切片优先级复核

### 本次请求与检查

用户要求回顾此前工作并判断下一步。本次只做只读恢复和设计判断，没有修改业务代码、
运行测试、启动 ROS/Gazebo、调用真实 Provider、commit 或 push。

已检查：

- 最近两日 memory 与 2026-08-11 阶段交接；
- Git branch/status/log：HEAD 仍为 `2eff71c`，与 origin 同步，业务代码无未提交 diff，
  仅 `memory/2026-08-11/` 为未跟踪记录；
- CodeGraph 中 `score_planning_case`、`MISSION_GRAPH_PROPOSAL_TOOL`、
  `build_constrained_graph_proposal_tool`、`DEFAULT_TASK_TYPE_DEFINITIONS`、
  completion-contract compile 和 graph compile；
- 冻结 development fixture 的 area 命令/默认合同，以及 v3 area seed 0/17 的最终图；
- `area_id` 在 core、extensions 和 tests 中的当前落点。

### 新确认的结构性缺口

1. `score_planning_case` 仍按精确 node count 与 task-type multiset 比较，不支持版本化的
   graph-equivalence/semantic-stage rubric。
2. `propose_task_graph` Tool schema 分别枚举所有 task types 和当前机器 capabilities，形成
   两者的笛卡尔积；它没有把 `TaskTypeRegistry.allowed_capabilities` 投影为合法 pair。
   因而 schema 允许 `patrol+navigate`，随后 completion compiler 才 fail closed 拒绝。
3. 更重要的是，当前 `area_id` 主要是 typed target/schema；Navigation Plugin 的真实
   runtime proof 是 `navigate_to_point -> move_base`。尚未发现可信的 area semantic-map
   resolver、area-entry pose、coverage path、`navigate_to_area`/`patrol_area` Tool 或
   area coverage completion evidence 合同。
4. v3 的两个两节点图把“到区域入口”和“区域全覆盖巡检”都标成
   `task_type=navigation + capability=navigate`。第一节点可理解为 transit，第二节点当前没有
   与 completion goal 匹配的可执行/可证明 runtime 语义。因此不能仅为提高 development
   分数而把一节点与两节点直接宣布为等价。

### 当前结论

下一切片应先定义 **area 任务的可执行语义与标注合同**，再改 scorer 或重跑 Provider。
需要明确区分 Mission `intent=patrol`、transit navigation、area coverage/patrol capability、
area-to-pose/path resolution 和各阶段 completion evidence。若当前只打算评测“抵达区域”，
应新建 suite version 并把命令改成 reach-area；若要保留“巡检区域”，应先定义/实现 area
coverage 的 Tool/runtime/adaptor 边界，不能让 `navigate_to_point` 的完成合同代替覆盖巡检。

在该语义决定之后，再做 registry-driven `task_type-capability` schema/context projection，
并以固定 prompt/fixture 做 projection off/on、repair off/on matched development ablation。
当前 v3、fixture 和原 scorer 必须原样保留，不能回写历史结果。独立 validation/test split
只能在这些 development 决策冻结后建立。

### 下一推荐步骤

1. 写 versioned area annotation/execution contract，预先规定目标解析、合法阶段、依赖、
   capability、completion evidence 和等价规则；先不要改 v3 分数。
2. 决定 area scenario 是 reach-area 还是 true area-patrol；研究主线更适合后者，但需新增
   coverage capability/runtime contract。
3. 从 `TaskTypeRegistry` 动态生成合法 task-type/capability pair 投影；deterministic compiler
   继续作为最终权威校验，schema 不能取代安全 gate。
4. 将 scorer 从 exact task count 升级为带 annotation-version 的 semantic-stage rubric，
   同时保留 exact-graph 指标供诊断。
5. 完成 scripted/unit regression 后再征得用户同意运行付费 Provider development ablation；
   此前不运行 v4。

### 下次建议验证命令

```bash
/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q \
  tests/test_completion_contract.py \
  tests/test_mission_graph_proposal.py \
  tests/test_llm_deliberation_policy.py \
  tests/test_llm_planning_eval.py \
  tests/test_evaluation_contracts.py \
  tests/test_evaluation_metrics.py
git diff --check
```

## 2026-08-11T10:19:03+08:00 — 用户否决 area 能力扩展

用户明确指出无需再围绕 area execution/coverage 做新的创新。上一条把评测歧义扩展成
`area patrol` Tool/runtime/task-type 设计属于过度设计，后续不实施。

最新决定：

- 不新增 area patrol/coverage Tool、runtime、adapter 或任务类型；
- 不为提高 v3 分数而改写既有 fixture、scorer 或历史 artifact；
- 将 area `1/5` strict pass 和缺少真实 area execution proof 如实记录为当前 limitation；
- 当前 embodied-evaluation/Gazebo/Provider 切片可视为阶段完成，不再运行 v4；
- 若继续研究实验，直接复用已有 stall-recover/stall-escalate substrate 做最小 matched
  diagnostics ablation，不借此扩张新架构；若用户暂不开展实验，则在当前 clean/pushed
  commit 停止即可。
