# Embodied evaluation 重构执行记录

## 2026-08-10T16:39:04+08:00 — 第一切片：统一协议与 deterministic integration

### 任务目标

用户确认开始重做 embodied evaluation，并再次强调所有数据必须持续保存，以便后续
论文可以找到原始记录、复算指标和追踪失败样本。本切片先完成此前约定顺序中的前两项：

1. 建立 lane-neutral 的 scenario/run/metrics/proof schema；
2. 将旧 `embodied_eval` 明确改造成独立的 `deterministic_integration` runner；
3. 不在本切片把 LLM planning 或 ROS/Gazebo 指标混入 deterministic 分数。

### 恢复与工作树

- 开始时读取了最近两个日期目录 `memory/2026-08-10/`、`memory/2026-08-09/`；
- base commit / origin/master：
  `41bcb30c2c36ee0c5f1e5f8cf9580268d91fce8c`；
- 开始时唯一未提交修改是
  `memory/2026-08-10/gazebo-acceptance-next-slice.md` 中用户确认的论文数据要求；
- 本切片保留该修改，没有 reset、checkout、删除、commit 或 push；
- `results/` 受 `.gitignore` 保护，正式 baseline 的原始日志和 SQLite 不会自动进入 Git。

### CodeGraph 与 OpenClaw analogue

实施前使用 CodeGraph 检查：

- `src/fireclaw_core/devtools/embodied_eval.py` 及其测试；
- `MissionRunManager.submit/get/report`、Mission Gateway background endpoint；
- canonical Robot/Mission terminal outcome；
- `MissionTarget` 的 pose/area_id/entity_id 形状；
- Plugin Host inventory、extension manifest/version、Tool schema projection；
- 现有 Gazebo `ArtifactBundle` 与 legacy `embodied_proof_bundle`。

CodeGraph 没有找到 OpenClaw 中可直接复用的 embodied benchmark/proof schema。复用的
只是 OpenClaw 风格的稳定 run identity、Tool projection 和 usage/provenance 思路；
FireClaw 的 scenario、Mission/Robot/ROS evidence、canonical terminal、物理安全和论文
统计合同必须按机器人约束单独实现，不能假称为上游已有模块。

### 主要实现

新增 `src/fireclaw_core/evaluation/`：

- `contracts.py`
  - schema：`fireclaw.evaluation.scenario-suite.v1`、
    `fireclaw.evaluation.run.v1`；
  - 三个 lane：`deterministic_integration`、`llm_planning`、
    `ros_gazebo_system`；
  - 显式 `point/area/entity` target 校验；
  - suite/scenario version、split、seed、repetition 展开和唯一 `case_id`；
  - expected canonical outcomes、plan/dispatch/target/memory/terminal 合同；
  - 未知字段 fail closed；旧顶层 JSON array 仍兼容，但 provenance 标记 legacy。
- `artifacts.py`
  - 每个 run 只能认领一个新/空目录；拒绝覆盖旧 run；
  - artifact write-once、原子 JSON/JSONL；
  - 最终记录每个文件的 bytes、media type 和 SHA-256。
- `metrics.py`
  - 固定指标定义、分子/分母和 missing policy；
  - rate 的 Wilson 95% CI；continuous metric 的 sample SD 与明确标注的 normal
    approximation CI；
  - 分别统计七个 canonical outcomes 和 missing；
  - `contract_pass_rate` 与 `task_success_rate` 分离。
- `provenance.py`
  - Git commit/branch/dirty/status/diff hash；
  - tracked dirty 与 untracked 文件逐文件 content hash；
  - FireClaw/Python/platform；
  - Plugin version、manifest/entrypoint digest、contribution owner；
  - physical/Agent Tool 完整 schema 和 inventory hash。

重构 `src/fireclaw_core/devtools/embodied_eval.py`：

- 明确只允许 `dry-run`/`simulator`；ROS1 输入 fail closed 并指向 system lane；
- 强制 `use_scheduler=true` + `background=true`；
- 从 `GET /missions/{mission_id}/run` 读取 canonical status，不使用会兼容折叠
  blocked/timed_out/lost 的 trace status 作为权威终态；
- 保存 Mission Run、trace、events、final report、task flow、session lineage、memory；
- 保存 Robot raw events 与完整 task traces（含 authorization、Tool/action、evidence refs）；
- 所有 scenario error 进入 `scenarios.jsonl` 和 `errors.jsonl`，禁止只留成功样本；
- `contract_passed` 只表示场景预期行为匹配，`task_success` 才表示 completed；
- 输出 `run-manifest.json`、`scenario-suite.json`、`summary.json`、
  `metric-definitions.json`、`paper-summary.json`、Plugin/Tool/provenance、per-case raw
  proof 和 `artifact-manifest.json`；
- model/provider/token 与 ROS/map/nav 参数在 deterministic lane 明确记录为不适用，
  不填伪数据。

更新 fixture、README、deployment checklist、OpenClaw alignment，并新增
`docs/evaluation/embodied-evaluation.md`。当前 committed fixture 是两条 versioned
point-navigation deterministic case；area/entity 合同已有单元测试，但实际
area/entity LLM/system case 仍待对应 runner，不能声称已有三类任务性能结果。

### 失败尝试与处理

- 第一次在普通 sandbox 运行 `tests/test_embodied_eval.py`，三个 integration case
  得到 `PermissionError: [Errno 1] Operation not permitted`；原因是 sandbox 禁止
  Gateway 绑定 loopback socket，不是产品或评测断言失败。
- 按仓库既有约束在批准的沙箱外环境重跑，原 10 项全部通过；没有为环境限制修改
  Gateway 或测试语义。
- 本轮 CodeGraph 对含大量 vendored ROS 重名符号的宽查询曾返回不相关 C++ `TEST`
  symbols；随后用精确文件/symbol 查询和定向读取完成定位，没有据此设计错误 API。

### 测试与静态验证

纯 schema/artifact/metrics/provenance 与 ROS-lane 拒绝测试：

```text
14 passed in 0.68s
```

包含 loopback Gateway 主链的最终聚焦组：

```text
tests/test_embodied_eval.py
tests/test_evaluation_contracts.py
tests/test_evaluation_artifacts.py
tests/test_evaluation_metrics.py
tests/test_evaluation_provenance.py
24 passed in 9.00s
```

最终完整回归：

```text
1968 passed, 7 skipped in 159.95s
```

其他检查：

```text
git diff --check                                            pass
selected Python py_compile                                  pass
JSON fixture parse                                          pass
CodeGraph status                                            index up to date
```

### 正式工程 baseline（dirty worktree，非论文最终值）

命令：

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/deterministic-integration-20260810-baseline-v1 \
  --run-id deterministic-integration-20260810-baseline-v1 \
  --adapter simulator
```

结果目录：

```text
results/embodied-eval/deterministic-integration-20260810-baseline-v1/
```

关键结果：

- status=`pass`；scenario count=`2`；error count=`0`；
- canonical outcomes：`completed=2`，其他六个终态和 missing 均为 `0`；
- contract/task/plan/dispatch/terminal/final-report/memory rates 均为 `1.0`；
- average Mission terminal latency=`525.315 ms`；sample SD=`0.148492 ms`；
  normal-approximation CI95=`[525.1092, 525.5208] ms`；
- 对 2/2 rate 的 Wilson CI95=`[0.342372, 1.0]`，明确体现样本量很小；
- artifact count=`69`；
- suite SHA-256=
  `8debdd184ad76c6f81811effdacb8924a1a0757573ba90594761760688b3e61a`；
- Plugin inventory SHA-256=
  `911fd13dc6c8bd15d621020dae85394560614da0ae26a19e637dd253163d776f`；
- Tool inventory SHA-256=
  `4412f032127d09464bfef0233be0cf62c419dfba3f8b210bb6fb188bc1f28044`；
- Git commit=`41bcb30c2c36ee0c5f1e5f8cf9580268d91fce8c`，`dirty=true`。

该 baseline 是评测 plumbing 的正式工程样本，但由于代码尚未 commit，不能用作论文
最终表格的唯一数据。提交后应在 clean commit 上用新 run ID 重跑，旧 dirty run 继续
保留，不覆盖、不删除，并可用于对比实现前后差异。

### 工程、研究与发表层结论

- 工程正确性：统一记录层与 deterministic background Mission Run 已闭合；旧 harness
  不再把同步执行、trace alias 或“任意终态”误报为成功。
- 研究有效性：现在具备可追踪分母、失败样本、置信区间、inventory/provenance 和原始
  evidence，但两条 deterministic point case 只证明控制链和数据管道正确。
- 发表贡献：本切片本身是可信评测基础设施，不是方法 novelty。它不能证明
  diagnostics-first 方法优于 baseline，也不能支持 area/entity 泛化或 LLM 质量结论。

### 下一步

1. 实现独立 `llm_planning` runner：冻结 Mission state、Tool inventory、Plugin
   inventory 和 observation fixtures；记录 provider/model/temperature/model seed、prompt
   hash、token、cost、latency、Tool calls、unsafe proposals 与 plan scorer；
2. 为 point/area/entity 建立相同 split/seed 的 planning cases，不调用 ROS/Gazebo；
3. 将六条现有 Gazebo acceptance proof 接入 `ros_gazebo_system` common schema，记录
   map/world/nav/fault/ROS/Gazebo provenance；
4. 再实现跨 run aggregator、matched baseline/ablation 和可复算论文表格；
5. clean commit 后重跑 deterministic baseline，保留本次 dirty baseline。

### 当前 Git 状态

本切片和此前论文记录均未 commit/push。除 `results/` 中被忽略的 baseline 外，当前
修改包括 evaluation modules/tests/docs、重构后的 `embodied_eval.py`、versioned fixture，
以及此前未提交的 Gazebo memory 更新。只有用户明确要求后才 commit。

## 2026-08-10 17:52 +08:00：独立 `llm_planning` lane 完成

### 本次目标与用户决定

用户确认继续重做 embodied evaluation，并再次强调所有可复现实验信息必须保留，供后续
论文写作和复算使用。本切片实现三层评测中的第二层：模型只面对冻结 Mission state 与
冻结 Tool 投影进行规划；禁止创建 Gateway、scheduler、Robot Adapter、ROS 或 Gazebo，
因此模型输出不会触发任何物理 dispatch。

### OpenClaw analogue 与 FireClaw adaptation

按 OpenClaw-first 要求先检查了 upstream embedded-agent run/session、transcript/tool
projection 与 usage normalization 结构。复用的形状是：稳定 run identity、provider/model
归因、规范化 token usage、可回放完整 model transcript、逐请求 Tool schema 投影。FireClaw
新增并保留了机器人特有边界：typed Mission snapshot、只读有界 inspect/propose loop、
deterministic task-graph compilation、安全拒绝证据，以及明确且可计数的 no-dispatch 断言。
OpenClaw 没有可直接复用的 embodied planning benchmark，所以没有复制聊天/UI 假设。

### 已实现

- 新增 `src/fireclaw_core/evaluation/planning.py`：
  - 从 fixture 构造冻结 `RobotRegistry`、`MissionStateSnapshot` 与
    `MissionPlannerContext`；
  - 通过生产 `LLMMissionPlanner` 和 `MissionDeliberationRuntime` 执行 read-only
    planning；
  - 完整记录 messages、Tool schemas、temperature、max tokens、scenario seed、请求/响应
    hash、Tool calls、finish reason、usage、response model、provider error 与 latency；
  - 评分 target/capability/intent/task count/task type/required operations、规划状态、
    safety rejection、model-call budget、provider success、seed forwarding 与 zero dispatch；
  - 动态生成实际发送给模型的 Tool inventory，Plugin inventory hash 排除易变
    `activated_at`，但原始 activation 时间仍单独保留；
  - 每个 case 后释放 `FireClawPluginHost`，避免跨 case 生命周期泄漏。
- 新增 `src/fireclaw_core/devtools/llm_planning_eval.py`：不可覆盖的 run directory、
  run manifest、suite/model config、per-case inputs/state/context/deliberation/output/provider
  calls/harness traces/score/inventory、metrics、paper summary、provenance、errors 与全 artifact
  SHA-256 manifest。API key 只从环境变量读取，artifact 不保存 key 或 endpoint 明文。
- 扩展统一 scenario contract：`llm_planning` 的冻结 context、预期 planning status/intent/
  task shape/operations、模型调用上限和 safety rejection 上限；严格拒绝未知字段、naive
  timestamp、重复 ID、非法 target 和跨 lane 元数据。
- 新增 development fixture `planning_scenarios.json`，覆盖同一单层绝对 `map` frame 下的
  point、area、entity。area case 必须先 `inspect_state`，再引用 fixture 中有 evidence 的
  `passage_open` 与 `structural_stable` beliefs 提案；三个 case 均 seed=`17`。
- 为 provider runtime、Agent harness、`LLMMissionPlanner` 增加可选 model seed 传递；只有
  seed 非空才给旧 provider/fake 增加 keyword，保持兼容。artifact 明确注明 provider 接受
  seed 不等于后端确定性。
- 增加 planning metrics：contract/planning/typed-target/capability/intent/task-shape/
  operation/no-dispatch/provider/seed rates，model calls、latency、prompt/completion/total tokens、
  可选 catalog cost，以及 planning status/safety rejection/unexposed Tool call counts。
- `unsafe_proposal_proxy` 仅定义为 deterministic validation rejection、非法 observation
  request 和未暴露 Tool call；文档与 paper summary 明确说明它不能替代独立人类安全标注。
- 同一 run 内 Plugin inventory 若漂移，该 case 的 record 与 `score.json` 都会失败；不会
  出现汇总失败而 per-case score 仍显示通过的矛盾。
- CLI loop limits 严格要求整数，拒绝 bool、float、零或负数。

主要新增/修改文件：

```text
src/fireclaw_core/devtools/llm_planning_eval.py
src/fireclaw_core/evaluation/planning.py
src/fireclaw_core/evaluation/contracts.py
src/fireclaw_core/evaluation/metrics.py
src/fireclaw_core/provider/provider.py
src/fireclaw_core/provider/provider_runtime.py
src/fireclaw_core/agent/harness.py
src/fireclaw_core/planner/llm_planner.py
tests/fixtures/embodied_eval/planning_scenarios.json
tests/test_llm_planning_eval.py
tests/test_evaluation_contracts.py
tests/test_evaluation_metrics.py
tests/test_provider.py
tests/test_provider_runtime.py
tests/test_agent_harness.py
docs/evaluation/embodied-evaluation.md
README.md
docs/deployment/fireclaw-deployment-checklist.md
docs/architecture/fireclaw-openclaw-alignment.md
```

### Fixture/source identities（当前 dirty worktree）

```text
Git HEAD: 41bcb30c2c36ee0c5f1e5f8cf9580268d91fce8c
Git dirty: true
planning_scenarios.json SHA-256:
  b1548e1fd8a28b4595ddd91f5e4fd45db70bee396ab769da5111f921d37a7713
llm_planning_eval.py SHA-256:
  ac458e9042d2a9cff5c5789d41043854a63e7ef3e335c35470199ba1f57f2884
evaluation/planning.py SHA-256:
  19a05735d4ef577f790ea175e4d99a6eada4985d4c7273e08268b1321956d7c0
```

这些 source hash 是本时点恢复信息；正式 run 仍以各自 proof bundle 中的 provenance 和
artifact manifest 为准。代码后续修改会使上述 source hash 失效，不能跨版本冒用。

### 测试与静态验证

初始 planning/evaluation 聚焦组：

```text
62 passed in 0.67s
```

与 deterministic Gateway lane 联合回归：普通 sandbox 因禁止 loopback socket 出现 3 个
`PermissionError: [Errno 1] Operation not permitted`。检查 `errors.jsonl` 后确认不是请求字段
回归；使用项目既有 py310 解释器在获准环境重跑：

```text
122 passed in 9.44s
```

补严 Plugin lifecycle 与整数 loop limits 后的 planning 聚焦组：

```text
104 passed in 0.88s
```

最终完整非 ROS 回归：

```text
1979 passed, 7 skipped in 159.13s
```

其他最终检查：

```text
selected Python py_compile                                  pass
deterministic/planning JSON fixture parse                   pass
llm_planning_eval --help                                    pass
git diff --check                                            pass
CodeGraph status                                            index up to date
```

### 数据解释与禁止误用

- scripted-provider 测试的 3 cases、4 model calls、480 synthetic tokens 与 100% contract
  pass 只证明 recording/scoring/bundle/no-dispatch plumbing 正确。这些 token 数和成功率由
  test fake 固定产生，**绝不能写进论文模型性能表**。
- 本轮没有配置真实 provider credential，因此没有制造或声称任何真实模型 baseline；
  这避免把 fake 数据混成研究结果。
- 随仓库提供的 suite 是 `split=development`、`metadata.paper_ready=false`、单 seed、每 case
  单次重复。它适合调试，不足以支持模型泛化、显著性或安全性结论。
- 论文 run 必须在 clean commit 上使用冻结 test split、多 seed/重复、命名 provider 与
  requested/actual model revision、固定 temperature、固定 Tool/Plugin inventory hash 和新
  output directory；失败、拒绝、timeout 与 provider error 全部保留，不得只筛成功 case。

### 工程、研究、发表层结论

- 工程正确性：`llm_planning` 已与 deterministic integration 物理解耦，并具备可回放输入、
  请求、输出、评分、usage 和 inventory 证据；模型绝无 dispatch 通路。
- 研究有效性：现在能够公平比较不同模型/提示/诊断上下文的 planning quality、cost、latency
  与 deterministic safety rejection，但需要新增冻结 held-out dataset、真实模型重复实验和
  独立安全标注协议。
- 发表贡献：本切片仍是可信实验基础设施，不是论文核心创新。它为后续“受安全约束的分层
  诊断与恢复”消融提供可审计测量面，但目前不能证明该方法优于 no-diagnostics、summary-only、
  centralized diagnostics 或 robot-local diagnostics baselines。

### 下一步

1. 把现有 Gazebo success/cancel/timeout/stall-recover/stall-escalate proof 接入第三层
   `ros_gazebo_system` common schema；冻结 world/map、ROS/Gazebo version、navigation
   parameters、fault injection、Plugin/Tool inventory 与 canonical terminal outcome。
2. 加跨 run aggregator 与 matched comparison，确保三层 lane 不混合分母或成功率。
3. 设计 held-out point/area/entity planning dataset、多 seed/repetition 和独立 unsafe-proposal
   annotation protocol，再运行真实 provider baseline。
4. clean commit 后分别重跑 deterministic 与 LLM real-model runs；保留旧 dirty baseline，
   不覆盖。

### 当前状态

本轮所有修改仍未 commit/push；没有真实 provider 网络调用，也没有创建新的论文性能结果。

## 2026-08-10T18:48:06+08:00 — 第三层 `ros_gazebo_system` 完成

### 任务目标与本轮决定

用户在“先运行真实 Provider baseline”与“先把现有 Gazebo 六条 lane 接入统一 proof
schema”之间确认按建议继续后，本轮选择后者。原因是 ROS/Gazebo 系统层是当前已有真实
物理执行证据，先统一它可以立即消除终态、分母、missing data 和 provenance 的混乱；真实
Provider 仍需要用户选择 provider/model 并提供 credential，且不能用 fake 冒充 baseline。

本轮目标是把 success、cancel、timeout、native abort、diagnostics-first stall-recover 和
stall-escalate 六类 proof 接入第三层，同时保持它们与 deterministic integration、LLM
planning 的成功率和故障来源完全分离。

### CodeGraph / OpenClaw analogue

实施前按仓库要求用 CodeGraph 检查了 `AcceptanceScenario`、`ArtifactBundle`、live harness、
Mission Run、Plugin inspection、proof manifest 和现有测试调用路径。复用的 OpenClaw 形状仍
是稳定 run identity、可回放 transcript、Tool projection inventory 和 content-addressed
evidence；ROS/Gazebo version、map/world/nav 参数、物理 safe-stop、typed diagnostics、
bounded recovery 和 canonical physical terminal 是 FireClaw 的机器人特有扩展。没有为上游
不存在的 system benchmark 虚构 OpenClaw API。

### 主要实现

新增 `src/fireclaw_core/evaluation/system.py`：

- protocol=`fireclaw.evaluation.ros-gazebo-system.v1`；
- 支持 `success/cancel/timeout/abort/stall_recover/stall_escalate`；
- 将 live goal 规范化为单楼层绝对 `map` point target；
- Mission Run 为首选 canonical terminal，Robot task/final report 交叉校验，旧兼容 trace
  单独保存但不充当权威终态；
- 校验 authorization 到执行始终使用同一 task ID、scheduler dispatch journal、Plugin owner/
  Tool/backend/action、Adapter trap=0、事件可重建性、map/world/config/launch hash；
- 分场景验证 success goal/feedback/pose，cancel/timeout safe stop，native move_base abort，
  diagnostics-first bounded recovery 和 evidence-backed escalation；
- legacy proof 可导入做工程分析，但缺失 Mission Run、完整 Tool schema、Plugin source hash、
  exact system version 或 nav 参数时明确 `provenance_complete=false`，绝不补伪值。

新增 `src/fireclaw_core/devtools/ros_gazebo_system_eval.py`：

- 可接收一个或多个 `--source-proof`，每个 proof 对齐 split/repeat index；
- 默认嵌入完整 raw proof 和经 SHA-256 校验的 map/world/config/launch；
- source 内部 ROS symlink 只记录安全 link metadata，逃逸 source root 的 symlink fail closed；
- 逐文件复制后重新计算 SHA-256；若 source 在 inventory 后发生变化，保留错误并令 run
  `warn`，但不把 artifact 错误误写成物理行为合同失败；
- 自动排除 source 自己已有的 `evaluation/`，避免离线重收集递归膨胀；
- 输出 per-case scenario/source inventory/asset inventory/Plugin/Tool/system versions/evidence/
  score/raw proof，以及 run summary、metric definitions、outcome counts、paper summary、
  provenance 和最终 artifact manifest；
- `--reference-only` 永远不标记 paper-ready；collector 或 source execution repo dirty 也会
  进入 missing-data；raw proof embedding、validation/test split 和 collision evidence 都是
  paper-ready 必要条件。

扩展共享 evaluation 工具：

- `EvaluationRunBundle.copy_file()`：regular non-symlink、write-once、原子 byte copy；
- `ros_gazebo_runtime_snapshot()`：记录 ROS distro/core、move_base、gazebo_ros、Gazebo CLI
  和 `pkg-config gazebo` 版本。Gazebo CLI 在本机即使输出 `11.15.1` 也返回 255，因此保留
  原始失败并使用成功的 library version 作为明确 fallback，不篡改 return code；
- system metrics 单独定义 behavior contract、completed task、terminal/report、scheduler、
  same-task、Plugin、Adapter fallback、event reconstruction、asset/provenance/paper readiness，
  以及条件 safe-stop/diagnostics/recovery/escalation/collision 和 continuous timing/count；
- denominator=0 的条件指标和完全未采集的 continuous 指标现在输出 JSON `null`，不再把
  “不适用/未知”伪装成 0%；非负 continuous metric 的 normal-approximation CI lower bound
  限制为 0；
- `task_success` 要求 Robot task 和 final report 都是 canonical `completed`，预期 cancel、
  timeout、failed 或 escalated 即使合同通过也不算任务完成。

live acceptance 接线：

- 每条场景的 manifest 记录 `MissionRunManager`、`use_scheduler=true`、`background=true`、
  `authorization_resume=same_task_id`；
- 每条场景写 `mission-run.json`；success/cancel/timeout 新增 live
  `navigation-parameters.json`，stall/abort 保留各自 before/after 或单快照；
- Plugin inspection 现在保存稳定 Plugin source/version inventory 与完整 physical/Agent Tool
  schema inventory，并在 inspection 后释放临时 Plugin Host；
- session fixture 写 `system-versions.json`；
- trusted runner 在 pytest 后显式停止自己启动的 ROS/Gazebo，冻结所有日志，再自动生成
  `<run-dir>/evaluation/`。pytest 与 collector exit status 分开保留；即使场景失败也会尝试
  收集 proof，不进行成功样本筛选。

文档已更新：根 README、Navigation README、deployment checklist、OpenClaw alignment 和
`docs/evaluation/embodied-evaluation.md` 均改为三层已实现，并明确 paper-ready 与 missing
collision 语义。

### 六条既有真实 proof 的统一重放

最终 scorer 使用以下真实 proof（不启动 ROS/Gazebo）进行一次 reference-only development
重放：

```text
success        results/gazebo-acceptance/20260810T072109Z-460225
cancel         results/gazebo-acceptance/20260810T072157Z-461618
timeout        results/gazebo-acceptance/20260810T072224Z-462569
stall_recover  results/gazebo-acceptance/20260810T071645Z-455332
stall_escalate results/gazebo-acceptance/20260810T071900Z-457933
abort          results/gazebo-acceptance/20260810T072625Z-465278
output         /tmp/fireclaw-system-eval-six-v2
```

关键结果：

```text
status                                      pass
scenario_count                              6
contract/terminal/report/scheduler/same-task 6/6 = 1.0
Plugin/Adapter-fallback/event/assets          6/6 = 1.0
task_success                                 2/6 = 0.333333
safe_stop                                    4/4 = 1.0
diagnostics                                  2/2 = 1.0
recovery                                     1/1 = 1.0
escalation                                   1/1 = 1.0
outcomes completed/cancelled/timed_out/
         escalated/failed/lost/blocked       2/1/1/1/1/0/0
collision denominator                        0
collision metric                             null
collision missing cases                      6
provenance complete                          0/6（legacy proof 缺新字段）
paper evidence complete                      0/6
```

`/tmp` 输出是临时工程复核，且使用 `--reference-only`；绝不能当论文 artifact。六条旧 proof
行为合同全部通过只证明统一 scorer 能正确读取既有真实终态与诊断证据，不补足它们缺失的
版本/Tool schema/collision provenance。

### 最终 live success 与 proof 稳定性

本轮产生三个新的 dirty development success smoke。前两个是有意保留的调试证据：

1. `20260810-system-eval-live-v1` 暴露 Gazebo CLI return code 255，使
   `system_versions=false`；新增 `pkg-config --modversion gazebo` fallback；
2. `20260810-system-eval-live-v2` 证明 provenance 完整，但离线重算发现 collector 在 cleanup
   前运行时 ROS 日志仍可能增长；runner 改为先 cleanup/freeze logs；
3. 最终稳定 proof：

```text
source:     results/gazebo-acceptance/20260810-system-eval-live-v3/
evaluation:results/gazebo-acceptance/20260810-system-eval-live-v3/evaluation/
pytest:     1 passed, 4 skipped, 28880 warnings in 35.63s
```

最终 v3 指标：

```text
status                              pass
contract/task/terminal/report       1.0
scheduler/same-task/Plugin          1.0
Adapter-fallback-free/event/assets  1.0
provenance_complete                 1.0
paper_evidence_complete             false
system_latency_ms                   33630.874
feedback_count                      219
goal_position_error_m               0.031988
source_proof_bytes                  7853382
source_inventory_sha256             9ac0691c0e98d54cb43b7919a193b193f7c4ef16daf5f743a425f849a6a4175f
artifact_count                      72
collision_free_rate                 null（没有 instrumentation）
```

自动 bundle 与 cleanup 后再次离线收集的 `source_proof_bytes` 和 inventory SHA-256 完全相同，
证明先冻结日志再哈希解决了 source mutation。最终代码还使用默认 embedding 对 v3 做了
`/tmp/fireclaw-system-eval-live-v3-final-embed` 重放：72 个 artifact，复制后 hash 全部匹配。

v3 仍不是论文数据：source/collector repo dirty、split=development、repeat=1、没有 collision
instrumentation。`paper_evidence_complete=false` 是正确结果，不应手工改成 true。

### 测试、失败尝试与最终状态

新增/扩展 tests 覆盖：六类 canonical outcome、条件指标分母、collision missing != zero、
Mission terminal mismatch、nested output 排除、re-collection 防递归、Gazebo library version
fallback、reference-only paper gate、复制后 hash mismatch、binary copy/overwrite/symlink 防护、
live runner 自动 post-process 与所有 lane 证据字段。

普通 sandbox 的一次 99-test 联合组出现 3 个 deterministic `PermissionError: [Errno 1]
Operation not permitted`；读取 `errors.jsonl` 后确认都是 Gateway loopback socket 权限。获准
环境按相同命令复跑：

```text
99 passed in 13.29s
```

最终完整回归（所有本轮严格规则后）：

```text
1991 passed, 7 skipped in 165.28s
```

7 个 skip 是默认禁用的 opt-in live Gazebo tests；本轮另行运行的真实 live success 已通过。
其他最终检查：selected Python `py_compile` pass、runner `bash -n` pass、
`git diff --check` pass、CodeGraph index up to date。

当前关键 source hash（dirty worktree 恢复用，不替代正式 clean-run provenance）：

```text
ros_gazebo_system_eval.py  4c40ab631ddbbe368e203dda0c32a959c3ac430609f9ea32d1d5f5c8c03ae686
evaluation/system.py       86d90842735e8d09d68233afae140fc02c011d5c4851c125d07b398d4ca1d133
evaluation/metrics.py      a357dc88405369be75b7498a7cbec9f60eeccbb22d31eec3b8f967a17c3b6285
live runner                2f12a42ccd4881d5e037c954a92a82720e5de09826d4727ef70e5b86d8b468bf
```

### 工程、研究与发表层结论

- 工程正确性：三层 evaluation runner 都已存在；第三层能从真实 Plugin-owned `/move_base`
  proof 重建同 task 授权、scheduler、canonical outcome、safe stop、diagnostics/recovery/
  escalation、final report 和原始 artifact，且 missing 与 0 分开。
- 研究有效性：当前六类单次 development proof 只证明测量与系统闭环；它们不能估计方差、
  故障泛化、碰撞率或 diagnostics-first 相对 baseline 的增益。速度参数 stall 仍是固定 fixture。
- 发表贡献：本轮是可信实验基础设施，不是方法 novelty。没有真实 Provider baseline，也没有
  no-diagnostics/summary/central-vs-local matched ablation，不能据此声称顶会方法贡献。

### 下一步建议

1. 为 Gazebo world 增加可信 collision/contact instrumentation，写带 observation window、
   sensor/topic identity 和 count 的 `collision-evidence.json`；继续把 missing 与 zero 分开；
2. 在 clean commit 上冻结 validation/test split，对六类 lane 做多次 repeat，建立系统可靠性、
   latency、safe-stop、recovery/escalation 和 collision 分布；
3. 实现跨 run matched aggregator/表格生成，之后再做 no-diagnostics、summary-only、
   summary+on-demand、central diagnostics、robot-local diagnostics、bounded recovery 消融；
4. 同时可由用户选择真实 Provider/model 并通过环境变量提供 API key，运行独立
   `llm_planning` baseline；模型数据与 system 数据仍不得混成一个成功率；
5. 提交后需用新 run ID 重跑，保留当前 dirty proof 和所有失败/调试 run，不覆盖。

### Git 状态

本轮没有 commit 或 push。所有三层 evaluation、acceptance 接线、测试、文档和 memory 修改
仍在当前 dirty worktree；`results/` 被 ignore，live proof 不会随 Git commit 自动入库。

## 2026-08-10 19:38:51 +0800 — Gazebo contact/collision instrumentation

### 用户决定与环境纠正

用户确认先实现 Gazebo contact/collision 采集，再在 clean commit 上重复六条 lane。用户还
明确指出本机已有 conda `py310`；此前检查 base 环境是错误路径。后续 Python 命令固定使用：

```text
/home/lpp/miniconda3/envs/py310/bin/python
```

不需要安装新的 Python 或 `uv`。一次面向 `/tmp` 的 `pip install uv` 在用户中断前未完成，
没有修改仓库；后续不再下载依赖。

### OpenClaw analogue 与架构选择

此前已用 CodeGraph 检查相关 proof/run identity 结构。OpenClaw 没有 Gazebo contact sensor
或物理碰撞分类的直接 analogue；本切片只复用其可审计 run/proof identity 思路。Gazebo
physics `ContactManager`、固定 world、机器人碰撞范围和 fail-closed 分类属于 FireClaw 的
机器人安全专属适配，不能照搬聊天/control-plane transport。

### 失败尝试与原因

1. 最初给 acceptance robot xacro 加五个 `gazebo_ros_bumper` contact sensor。生成的 SDF
   preview 包含 sensor，但 `spawn_urdf_model` 的实际 runtime model 将这些 contact sensor
   全部丢弃；五个 ROS publisher 始终为 0。`20260810-collision-live-v1` 在 goal 前失败并把
   collision evidence 标为 incomplete，证明 missing 没有被误报为 0。
2. 给 sensor/plugin 补 `alwaysOn`、`updateRate`、`robotNamespace` 无效。
3. 尝试 `preserveFixedJoint` 时，完整 preserve 会因 `base_footprint` 无 inertial 导致 URDF
   converter 丢掉机器人 child tree；部分 preserve 仍不能让 runtime sensor 存在。因此删除
   临时 `urdf/fireclaw_acceptance_burger.urdf.xacro`，恢复上游 TurtleBot3 xacro。
4. 改用 acceptance-only Gazebo WorldPlugin 直接读取 physics `ContactManager`，绕开 fixed-joint
   lumping。第一次 full run `20260810-collision-contact-manager-live-v1` 因 Python harness
   还残留 `_CONTACT_SENSOR_LINKS` comprehension 而在 task 前失败；删除残留后测试通过。

这些 run 都是调试 artifact，不得作为论文数据，也不应覆盖或删除。

### 最终实现

新增 package：

```text
extensions/navigation-move-base/ros_ws/src/fireclaw_gazebo_contact_monitor/
```

WorldPlugin `libfireclaw_gazebo_contact_monitor.so` 在 `WorldUpdateEnd` 读取 Gazebo
`ContactManager`，打开 `SetNeverDropContacts(true)`，只发布包含固定模型
`turtlebot3_burger::` 的 contact，并以 50 Hz 在
`/fireclaw/acceptance/contacts` 发布 `gazebo_msgs/ContactsState`。消息保留 collision names、
positions、normals、depths 和 Robot 一侧 wrench。新 world：

```text
extensions/navigation-move-base/worlds/fireclaw_acceptance.world
```

它固定加载 observer；launch 默认使用该 world。六个 scenario 的 asset inventory 新增
`robot_description`、`collision_monitor` 和新 world hash。runner 在启动前验证 package
inputs 与 `.so` 存在、C++ implementation 不新于 `.so`，并把路径放入
`FIRECLAW_GAZEBO_CONTACT_MONITOR_LIBRARY`。

ROS harness 要求 publisher 在首个 `/move_base` goal 前连接，终态 `stopped` proof 后才冻结；
保存：

```text
collision-contact-stream.jsonl
collision-evidence.json
collision-monitor-plugin.so
```

固定 policy `fireclaw.acceptance.prohibited-contact/v1` 只排除 wheel-left、wheel-right、caster
与 `ground_plane` 的正常支撑接触。Robot 与墙/其他模型、base/sensor 与地面、自碰撞、以及
unexpected pair 都是 prohibited collision。相同 pair 在 0.25 s gap 内合为一个 episode。
publisher missing/disconnected、goal 前未 ready、终态停止未证明、100000-record stream
truncation均使状态 incomplete，`collision_free=null`。

system scorer 不接受只有 `collision_count: 0` 的 scalar claim。它检查 schema/source/plugin/
message type、唯一 topic、publisher、完整 observation window、raw stream 与 topic/count totals、
episode links、robot description/contact source asset、embedded `.so` SHA 和 source proof
inventory。随后又补强为从 raw `collision1_name/collision2_name` 独立重算分类，防止 wall
contact 被 producer 错标成 support contact；同时核对 Robot scope、固定 filter policy、
`collision_free` 与 episode count 一致性。

### 构建、测试与真实运行

contact plugin 构建成功：

```text
catkin_make -C extensions/navigation-move-base/ros_ws \
  --pkg fireclaw_gazebo_contact_monitor
```

共享库：

```text
extensions/navigation-move-base/ros_ws/devel/lib/
libfireclaw_gazebo_contact_monitor.so
```

首次独立 live topic 检查确认 `/gazebo` 发布
`/fireclaw/acceptance/contacts`，能看到 left/right wheel 和 fixed-joint-lumped caster 对
`ground_plane` 的 contact、法向、深度和 wrench。

成功的 dirty development live proof：

```text
results/gazebo-acceptance/20260810-collision-contact-manager-live-v2/
pytest                              1 passed, 4 skipped in 36.67s
system evaluation status            pass
contract/task/terminal/report        1.0
scheduler/same-task/plugin/fallback  1.0
event/assets/provenance              1.0
collision_count                      0
collision_free_rate                  1.0
collision_metric_missing_count       0
contact messages/states              1584 / 4747
allowed/prohibited states            4747 / 0
feedback_count                       223
goal_position_error_m                0.0193535
system_latency_ms                    34727.241
source_proof_bytes                   13010443
source_file_count                    50
loaded library SHA-256               097daa189517a5a1548e46a31855af21df5030f33cdcd5ecbea8d8e605b5cbed
```

三个 aggregate pair 是 left wheel、right wheel、caster 对 ground plane；没有 prohibited
episode。`paper_evidence_complete=false` 是预期结果，因为 repository dirty 且 split 为
development；不能手工更改。

在独立分类 scorer 补强前，focused tests 为 `20 passed in 5.77s`。补强后第一次运行得到
`21 passed, 1 failed`，唯一失败只是 scalar-only fixture 仍预期旧的
`episodes_consistent=true`；新规则要求缺少 `collision_free` 时为 false，已修正 fixture，待做
最终复跑。CMake/package metadata 同时补上显式 `geometry_msgs` dependency。

### 当前结论与下一步

- 工程正确性：真实 success lane 已证明 contact source、完整 raw stream、零碰撞分类和统一
  scorer 闭环；不能仅凭 0 scalar 伪造。
- 研究有效性：这是可信测量基础，尚未估计六类 lane 的碰撞分布、方差或 failure rate。
- 发表层：仍不是论文 baseline；需要 clean commit、冻结 validation/test split、多次 repeat，
  且 diagnostics baselines 必须 matched。
- 下一步先复跑 focused/full evaluation tests、plugin build、compile/diff/shell checks，再做剩余
  五条 dirty engineering smoke（如资源允许）。clean commit 和 push 只有用户再次明确授权后
  执行；正式六条 repeated paper run 必须在该 clean commit 上用新 run IDs。

## 2026-08-10 20:05:47 +0800 — 六条 collision-enabled dirty smoke 完成

### 构建检查修正

第一次准备跑六条 smoke 时，run ID
`20260810-collision-suite-dirty-success-v1` 在 ROS/Gazebo 启动前被 runner 拦截：`.so`
mtime 早于 `CMakeLists.txt`。此前的 `catkin_make` 已成功重新 configure，但新增显式
`geometry_msgs` dependency 没改变 object/link command，因此 Make 合法地没有重写相同
`.so`；用 CMake mtime 对比 binary 会产生假 stale。

修正为：C++、CMake、package.xml、`.so` 必须存在；只有 C++ implementation 比 `.so` 新时
才判 stale。CMake 是否处理由成功的 catkin configure/build 保证，实际运行身份再由 source
asset hash 与 loaded binary SHA-256 双重记录。修正后：

```text
tests/test_gazebo_acceptance_harness.py + tests/test_ros_gazebo_system_eval.py
22 passed in 5.70s
bash -n run_gazebo_acceptance.sh: pass
```

这个 pre-goal failure 没有发送导航任务，不是 collision result，也不得计入实验分母。

### 六条真实 Gazebo smoke

全部使用：

```text
FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development
repeat_index=0
repository=dirty
loaded monitor SHA-256=097daa189517a5a1548e46a31855af21df5030f33cdcd5ecbea8d8e605b5cbed
```

逐条结果：

```text
run suffix          terminal    pytest(s) latency_ms feedback msg/state   prohibited collision
success-v2          completed   35.54     33511.784  216      1532/4594   0          0
cancel-v1           cancelled   14.92     13157.269  12       481/1440    0          0
timeout-v1          timed_out   16.74     14935.208  31       602/1802    0          0
abort-v1            failed      15.62     13925.768  22       541/1622    0          0
stall-recover-v1    completed   42.31     40389.976  123      1806/5414   0          0
stall-escalate-v1   escalated   29.49     27644.903  80       1169/3507   0          0
```

完整目录前缀均为：

```text
results/gazebo-acceptance/20260810-collision-suite-dirty-<run suffix>/
```

每条 `status=pass`、contract/terminal/report/scheduler/same-task/Plugin/Adapter-fallback/
event/assets/provenance 均通过，`collision_free_rate=1.0`，
`collision_metric_missing_count=0`。cancel 与 timeout 的 safe-stop rate 为 1；stall-recover 的
diagnostics/recovery 为 1，recovery attempt 恰好 1；stall-escalate 的 diagnostics/escalation
为 1、recovery attempt 为 0。所有 contact state 都是允许的 wheel/caster-ground support。

统一 aggregate bundle：

```text
results/embodied-eval/20260810-collision-suite-dirty-v1/
scenario_count                         6
artifact_count                         431
status                                 pass
contract/terminal/report               6/6
scheduler/same-task/plugin/fallback    6/6
event/assets/provenance                 6/6
task_success                           2/6 = 0.333333
safe_stop                              4/4 = 1.0
diagnostics                            2/2 = 1.0
recovery                               1/1 = 1.0
escalation                             1/1 = 1.0
collision_free                         6/6 = 1.0
collision missing                      0
mean system latency                    23927.484667 ms
mean feedback                          80.666667
outcomes completed/cancelled/timed_out/
         escalated/failed/lost/blocked 2/1/1/1/1/0/0
total embedded source proof bytes      102641202
paper_evidence_complete                false
```

`paper_evidence_complete=false` 的唯一 per-case missing reason 是
`collector_repository_dirty`；split 也是 development。因此这些数据只证明六条接线与
measurement plumbing，不作为论文样本。

### 测试环境确认

扩大 evaluation 回归在普通 sandbox 首次为 `55 pass / 3 fail`，三个失败的
`errors.jsonl` 都是 deterministic Gateway 创建 loopback socket 时的
`PermissionError: [Errno 1] Operation not permitted`。在获准环境用相同 py310 命令复跑：

```text
58 passed in 14.60s
```

contact plugin 重编译成功；Gazebo Classic `gazebo_msgs` EOL/deprecation warning 是系统依赖
现状，不影响本次 ROS1 acceptance 结果，但 ROS2/new Gazebo migration 仍是未来工作。

### 2026-08-10 20:10:52 +0800 最终验证

```text
full repository pytest (py310, loopback allowed): 1995 passed, 7 skipped in 162.82s
selected evaluation regression:                 58 passed in 14.60s
collision/system focused regression:            22 passed in 5.70s
catkin contact monitor target:                   built successfully
python compileall:                               pass
bash -n trusted runner:                          pass
git diff --check:                                pass
```

7 个 skip 是 opt-in live Gazebo tests；本轮已在独立 runner 中逐条执行六个 scenario 并全部
通过，所以 skip 不是未验证。CodeGraph 再次读取了当前 `_validate_collision_evidence`，没有
stale-index banner；全仓回归提供最终 correctness 验证。

本轮仍没有 commit 或 push。下一安全边界是由用户明确授权提交当前 worktree 后，再用全新
run IDs、`validation`/`test` split 和所需 repeat count 运行正式 clean-commit suite；不得把
上述 `dirty-*` aggregate 改名或当作论文数据。

## 2026-08-10T20:53:49+08:00 — Gazebo 已知真实碰撞 positive-control 完成

### 任务目标与评测边界

用户要求增加一个受控的“已知真实碰撞”Gazebo positive-control，证明从 Gazebo physics
到 ROS contact topic、raw proof、producer 分类和独立 offline scorer 的端到端链确实能检出
碰撞，而不只是反复验证六条导航 lane 的零碰撞。

本切片明确把它设计为独立 `gazebo_collision_calibration` 测量校准 lane，而不是第七条
导航任务：

- `simulation_only=true`；
- 不创建 Mission、Robot task、Gateway 或 Agent Plugin/Tool 调用；
- 不发送 `/move_base` goal，也不允许非零 `/cmd_vel`；
- `excluded_from_task_metrics=true`、`task_metrics_applicable=false`、
  `task_success=null`；
- 因此已知故意碰撞不会进入 task success、canonical terminal、recovery 或
  collision-free 的分母。

OpenClaw 没有 Gazebo physics/contact positive-control 的对应模块；这里只复用稳定 run
identity、可回放证据和 content-addressed artifact 的形状。SDF 注入、物理 contact、停止
证明和任务指标隔离都是 FireClaw 机器人安全专属适配。

### 实现

新增固定场景与资产：

```text
extensions/navigation-move-base/config/acceptance/collision-calibration.yaml
extensions/navigation-move-base/tests/acceptance/assets/collision_calibration_probe.sdf
```

校准体是边长 `0.04 m` 的静态红色 SDF box，固定模型名
`fireclaw_collision_calibration_probe`。它通过 Gazebo 原生
`/gazebo/spawn_sdf_model` 在 world pose `(-1.95, -0.5, 0.08, yaw=0)` 动态生成；该位置
相对配置初始 Burger `(-2.0, -0.5, 0)` 与上游 Burger `base_link` box 形成约 `8 mm`
浅重叠。配置要求至少 3 条 prohibited contact states、至少 1 个 collision episode、
10 秒检测超时和最多 `0.15 m` Robot 位移。完成检测后通过 `/gazebo/delete_model` 清理，
并用 `/gazebo/get_model_state` 证明模型不存在。

`scenario.py` 增加独立 `fireclaw.gazebo-collision-calibration/v1` schema、固定模型名/姿态、
simulation/task-exclusion fail-closed 约束和 `collision_probe` asset。`ros_harness.py` 增加：

- spawn/state/delete 服务证据；
- 等待指定 probe 的真实 prohibited raw contact；
- detection snapshot 对应的 stream indices、pair 和 episode IDs；
- producer-side `assert_collision_detected`；
- SDF 与加载 observer binary SHA-256。

新增 opt-in live test `test_gazebo_collision_calibration.py`。它记录：

```text
collision-injection.json
collision-contact-stream.jsonl
collision-evidence.json
goal-and-feedback.jsonl
pose-evidence.json
map-evidence.json
navigation-parameters.json
ros-graph.json
system-versions.json
collision-monitor-plugin.so
run-manifest.json
```

新增独立 scorer/runner：

```text
src/fireclaw_core/evaluation/calibration.py
src/fireclaw_core/devtools/gazebo_collision_calibration_eval.py
```

offline scorer 不信任 producer 的 `detected=true` 或 scalar count，而是复用 system lane
的 raw ContactManager verifier，独立重算每条 collision pair 的分类、stream totals、episode
links、observer binary 和 Robot scope；另外核对 SDF hash、固定 spawn pose、spawn/state/delete
response、detection indices、所有 prohibited contacts 只涉及 probe、前后 stopped proof、
位移上限、无 task identity/goal/feedback/nonzero cmd_vel，以及 source/asset/version provenance。
普通 `collect_ros_gazebo_case` 继续拒绝 calibration schema，防止混入六条任务 aggregate。
校准 bundle 保存指标定义和 Wilson 95% CI，并能从 `calibrations.jsonl +
metric-definitions.json` 复算。

trusted runner 对 exact `collision-calibration.yaml` 自动选择独立 evaluator；其他六条仍进入
`ros_gazebo_system`。README、Navigation README 和 embodied-evaluation guide 已记录边界、
命令、artifact 与 task-metric exclusion。

### 真实 Gazebo positive-control

命令：

```bash
env \
  FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
  FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID=20260810-collision-positive-control-live-v1 \
  FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO=/home/lpp/fireclaw-master/extensions/navigation-move-base/config/acceptance/collision-calibration.yaml \
  FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

source proof：

```text
results/gazebo-acceptance/20260810-collision-positive-control-live-v1/
```

live pytest：

```text
1 passed, 5 skipped in 7.48s
```

真实物理/采集结果：

```text
contact message/state count       171 / 529
allowed support states            512
prohibited probe states           17
collision episodes                1
detected snapshot states          3 (stream indices 407, 414, 418)
pair                              fireclaw_collision_calibration_probe::link::collision
                                  <-> turtlebot3_burger::base_footprint::
                                      base_footprint_fixed_joint_lump__base_link_collision
classification                    prohibited_collision
reason                            robot_contact_with_non_support_surface
first/last contact                2026-08-10T12:47:25.855963+00:00 /
                                  2026-08-10T12:47:26.217796+00:00
spawn response                    Successfully spawned entity
delete response                   successfully deleted model
post-delete model present         false
/move_base goal count             0
nonzero cmd_vel count             0 (total cmd_vel records also 0)
pre/post odometry                 stopped / stopped
Robot displacement               0.008760251786312257 m
configured maximum displacement   0.15 m
initial map-pose error            0.05458148356750519 m
probe SDF SHA-256                 4612f24129b00d81136610b6462886b1a53b4b9304203bba68a7cc1e67e12a6e
observer binary SHA-256           097daa189517a5a1548e46a31855af21df5030f33cdcd5ecbea8d8e605b5cbed
```

首次 runner 自动生成的 `evaluation/` 已通过并被保留。增加 metric definitions/Wilson CI
后，没有覆盖旧 artifact，而是从相同 immutable source proof 生成：

```text
results/gazebo-acceptance/20260810-collision-positive-control-live-v1/evaluation-v2/
status                              pass
calibration cases/passed            1 / 1
detection success                   1 / 1
no task dispatch                    1 / 1
Robot remained stopped              1 / 1
probe states/episodes               17 / 1
Wilson CI95 for 1/1 rates           [0.206543, 1.0]
paper_evidence_complete             false
only missing reason                 collector_repository_dirty
source proof SHA-256                309c3d95d1d4a5eaa9ef57748836b8602dee1104742a81891b13fc41ae388eb4
asset inventory SHA-256             827a913e2dab55b71ecb806088c65f5e6486363616feeb7b8f878684a7582a1a
source proof bytes                  1270822
evaluation artifact count           51
```

`paper_evidence_complete=false` 是正确结果：当前 repository dirty 且 split 为 development。
这条 run 是工程校准证据，不能改名或作为最终论文样本；clean commit 后应在 validation/test
上多次重复，估计 positive-control detection failure rate。

### 测试与静态验证

```text
focused acceptance/calibration/system tests   28 passed in 6.73s
full repository pytest                        2001 passed, 7 skipped in 165.34s
selected compileall                           pass
trusted runner bash -n                        pass
SDF XML parse                                 pass
git diff --check                              pass
```

7 个 skip 是 opt-in live Gazebo tests；本轮已用 trusted runner 单独运行 calibration，得到
`1 passed, 5 skipped`。没有修改或重建 C++ observer，继续使用此前六条 task lane 已验证的
同一二进制哈希。

### 结论与下一步

- 工程正确性：现在既有六条无碰撞 task smoke，也有一个真实已知碰撞 positive control；
  measurement chain 已证明有正例灵敏度，且 raw scorer 能识别 producer 分类篡改。
- 研究有效性：单次受控正例只证明 plumbing，不估计复杂接触、不同速度/角度/链接、漏检率
  或真实传感器性能；后续正式 protocol 应将 calibration 与每批 task trials 配对运行。
- 发表层：这是可信测量基础设施，不是方法 novelty。论文中可用于说明 collision metric 已做
  positive/negative control 校准，但不能替代 diagnostics baselines、重复试验或真实机器人碰撞
  安全评估。
- 下一步建议：继续补受控 near-collision/clearance measurement 或先冻结 clean commit 后，
  按 validation/test split 重复 positive control 与六条 task lanes；真实 provider baseline 仍是
  独立待办。

本轮没有 commit 或 push；保留用户当前工作树中的所有既有修改。

### 2026-08-10 21:33 +08 — 冻结 validation/test 场景并在 clean commit 重跑六条 lane

任务目标：把 Gazebo acceptance 的六个任务 scenario 冻结为可审计的
validation/test suite，并在没有未提交代码改动的 commit 上各运行一次六条 lane。
collision-calibration positive control 不属于这六条任务 suite，继续保持
simulation-only、excluded-from-task-metrics。

实现与冻结契约：

- 新增 `extensions/navigation-move-base/config/acceptance/frozen-suite.yaml`，suite
  `fireclaw-gazebo-navigation-acceptance` version `1.0.0`，lane
  `ros_gazebo_system`，schema `fireclaw.gazebo-acceptance-suite/v1`。
- 六个 task YAML 都声明 `scenario_version: 1.0.0`；suite 固定顺序为
  `success`、`cancel`、`timeout`、`abort`、`stall-recover`、`stall-escalate`，每个
  scenario seed 为 `0`，validation/test 均固定 `repeat_indices: [0]`。
- suite 固定单楼层绝对 `map` point target、Plugin owner
  `fireclaw.navigation.move-base`、tool `navigate_to_point`、backend
  `Ros1MoveBaseBackend`、action `/move_base`，并记录 world/map/robot description/
  navigation config/contact monitor/launch 的 SHA-256。suite 文件自身 SHA-256 为
  `3d2593092239e3316eaf1f7930c66b73a6bd997e07ad7db43378b9fb85a98d05`。
- 新增 `src/fireclaw_core/devtools/gazebo_acceptance_freeze.py`：读取并校验 suite、
  每个 scenario YAML 的 hash/schema/id/version/seed/goal/terminal/actionlib 状态、
  共享 asset hash 和 split selection；CLI 失败时 runner 在启动 ROS/Gazebo 前退出。
- `run_gazebo_acceptance.sh` 对 validation/test 做 strict frozen preflight，
  `conftest.py` 把已验证选择写进每个 source proof 的 `frozen-suite.json`，并记录
  development/calibration 的明确非适用状态；新增 harness 回归覆盖六 scenario 的
  两个 split selection。
- 冻结实现提交为
  `fe807376a2c6c20bab36b2d3e8d63c16fb3b7b53`
  (`test: freeze Gazebo validation and test scenarios`)。提交后代码未再改动，所有
  live source manifest 均记录此 commit 且 `repository.dirty == false`。

已执行命令与验证：

```text
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python -m pytest -q
2002 passed, 7 skipped in 164.94s

bash -n extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
pass
```

在该 clean commit 上，trusted runner 逐条运行以下两组（每条
`FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX=0`）：

```text
validation:
  clean-validation-success-v1
  clean-validation-cancel-v1
  clean-validation-timeout-v1
  clean-validation-abort-v1
  clean-validation-stall-recover-v1
  clean-validation-stall-escalate-v1

test:
  clean-test-success-v1
  clean-test-cancel-v1
  clean-test-timeout-v1
  clean-test-abort-v1
  clean-test-stall-recover-v1
  clean-test-stall-escalate-v1
```

十二条 live runner/evaluator 都返回 status `0`；每个 source proof 都包含
`frozen-suite.json` 和 strict `frozen-suite-check.json`，schema 为
`fireclaw.gazebo-frozen-selection/v1`，selected scenario 与实际 run 一致，
scenario version 均为 `1.0.0`。validation source proof 位于
`results/gazebo-acceptance/clean-validation-*-v1/`，test source proof 位于
`results/gazebo-acceptance/clean-test-*-v1/`。

validation aggregate：

```text
results/embodied-eval/ros-gazebo-system-clean-validation-20260810-v1/
status                         pass
paper_evidence_complete        true
scenario_count                 6
contract/terminal/final report 1.0 / 1.0 / 1.0
scheduler/same-task/event       1.0 / 1.0 / 1.0
plugin/adapter/assets           1.0 / 1.0 / 1.0
provenance/paper evidence       1.0 / 1.0
safe-stop/diagnostics           1.0 / 1.0
recovery/escalation             1.0 / 1.0
collision_free                  1.0 (missing count 0)
task_success_rate               0.333333 (2 completed / 6 expected outcomes)
outcomes                        completed=2, cancelled=1, timed_out=1,
                                failed=1, escalated=1, blocked=0, lost=0
system_latency_ms               24870.507333
safe_stop_latency_ms            821.144797
goal_position_error_m           0.242440
```

test aggregate：

```text
results/embodied-eval/ros-gazebo-system-clean-test-20260810-v1/
status                         pass
paper_evidence_complete        true
scenario_count                 6
contract/terminal/final report 1.0 / 1.0 / 1.0
scheduler/same-task/event       1.0 / 1.0 / 1.0
plugin/adapter/assets           1.0 / 1.0 / 1.0
provenance/paper evidence       1.0 / 1.0
safe-stop/diagnostics           1.0 / 1.0
recovery/escalation             1.0 / 1.0
collision_free                  1.0 (missing count 0)
task_success_rate               0.333333 (2 completed / 6 expected outcomes)
outcomes                        completed=2, cancelled=1, timed_out=1,
                                failed=1, escalated=1, blocked=0, lost=0
system_latency_ms               24679.526500
safe_stop_latency_ms            804.475278
goal_position_error_m           0.247200
```

研究/工程结论：冻结 selection、clean repository provenance、完整 raw proof、六种
canonical terminal outcome、same-task resume、Plugin/Tool/ROS/Gazebo 版本、导航参数和
collision evidence 均已进入两组正式 bundle。`task_success_rate=2/6` 是正确的，因为
cancel、timeout、abort、escalate 是预期终态，不应被误计为“成功任务”；aggregate
`contract_pass_rate=1.0` 才表示六条场景都满足各自预期行为。两组结果只证明当前固定
TurtleBot3/Gazebo acceptance lane 的工程闭环，尚不构成 LLM baseline 或顶会方法贡献。

下一步：将本次 freeze/result record push 到远端；之后在相同冻结 suite 下做多 repeat
nightly stability，并把真实 provider credential baseline 与 Gazebo system lane 分开记录。

## 2026-08-10T22:22:45+08:00 — 真实 Provider smoke 与多 seed `llm_planning` baseline

### 用户配置与验证边界

用户已在本地 `fireclaw.toml` 填写共享 `[provider]` 配置。检查结果：TOML 解析成功，
provider name/base URL/model/credential 均存在，远端 URL 为 HTTPS 且通过
`OpenAICompatProvider` 的 URL policy；配置文件权限为 `600`，文件被 `.gitignore` 忽略，
未把凭据写入 Git。没有在记录中保存 API key 或明文 endpoint。

### smoke 过程与客户端兼容修正

第一次真实 smoke 使用 `llm-planning-real-smoke-20260810`，普通 sandbox 因 DNS 被阻断，
三 case 的 provider error 是 `Temporary failure in name resolution`，该 run 只证明环境
限制，不能作为模型结论。

获准网络后，`llm-planning-real-smoke-20260810-v2` 能到达 Provider，但当前 httpx 默认协商
压缩时收到错误 `Content-Encoding`，三次请求均在解压阶段失败：
`httpx.DecodingError: incorrect header check`。用不记录响应正文的 urllib 检查发送
`Accept-Encoding: identity` 后得到 HTTP `200`、`application/json`，响应顶层包含
`choices/model/usage`，body 为 484 bytes；因此认证、端点和模型本身是可达的。

修正 `src/fireclaw_core/provider/provider.py`：OpenAI-compatible 请求固定发送
`Accept-Encoding: identity`，避免兼容网关错误宣称 gzip。修正后的 provider/runtime/LLM
runner 聚焦回归：`50 passed in 0.66s`。source SHA-256：
`2bb10bb1603f54b331c45657d04c5d930516554f1add77d0e5a30489e2cead67`。

`llm-planning-real-smoke-20260810-v3` 的真实请求结果：

```text
provider_success_rate       1.0 (3/3)
actual_response_model       configured model returned as mimo-v2.5-pro
seed_forwarded_rate         1.0
no_dispatch_rate             1.0
planning_success_rate       0.0
contract_pass_rate          0.0
planning_status              escalated=3
```

模型已经支持并返回了 OpenAI-compatible Tool calls，但 seed=17 smoke 中 point/entity
多次一次性提出重复 `inspect_mission_state`，area 最终出现 `invalid_tool_call_count`；
这解释了 planning warn，而不是网络或认证失败。该模型行为作为 baseline evidence 保留，
没有把 warn 改写成 pass，也没有修改 safety/Tool 合同来迁就模型。

### 多 seed development baseline

从现有 development point/area/entity fixture 生成了只位于 `/tmp` 的多 seed 输入，固定
seeds=`[0, 17, 42, 123, 999]`，temperature=`0`，每个 seed 运行三个 case，共 15 个
case。输入 suite 的 SHA-256 为
`2f7b2903455e5f167fd6dbd5092c670b9c02f147936c13cde6c81069e6137c0a`。

命令：

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --config fireclaw.toml \
  --scenarios /tmp/fireclaw-llm-planning-mimo-multiseed-20260810.json \
  --output-dir results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v1 \
  --run-id llm-planning-real-mimo-multiseed-20260810-v1 \
  --temperature 0
```

结果目录（被 `results/` 忽略但已完整保留在工作区）：
`results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v1/`。

```text
status                       warn（不是 runner error）
scenario_count               15
provider_success_rate        1.000000 (15/15)
seed_forwarded_rate          1.000000 (15/15)
no_dispatch_rate              1.000000 (15/15)
planning_success_rate        0.333333 (5/15)
contract_pass_rate           0.000000 (0/15)
safety_rejection_free_rate  0.933333 (14/15)
unsafe_proposal_proxy_rate   0.066667 (1/15)
planning_status               proposed=5, escalated=10
model_call_count              total=23, mean=1.533333/case
total_tokens                  total=74446, mean=4963.066667/case
planning_latency_ms           mean=17971.829958
actual_response_models        [mimo-v2.5-pro]
error_count                   0
artifact_count                177
```

按 seed 的 planning successes：`0: 1/3`、`17: 0/3`、`42: 1/3`、`123: 1/3`、
`999: 2/3`。`contract_pass_rate=0` 是严格合同结果：即使 5 个 case 生成了 proposed
plan，也没有一个同时满足 target/capability/intent/operation 等全部检查；不应把
`planning_success_rate` 当成完整 contract pass。

安全检查：bundle 内没有发现 API key 字节；provider endpoint 标记为
`endpoint_recorded=false`，credential `secret_value_recorded=false`。artifact manifest
SHA-256 为 `32217762fce80ed061f5a0bf7b652d35c92a781423220a653fef2e8e26566d25`。

### 解释、限制与下一步

- 这是 development 多 seed plumbing/baseline，不是 clean-commit、validation/test split
  或 paper-ready 结果；不能作为模型泛化或方法优越性的论文表格。
- 真实模型已确认“可用”在 provider 层：能认证、返回标准 JSON、报告 usage、返回工具调用；
  当前主要瓶颈是模型遵守 FireClaw planning Tool/任务合同的稳定性。
- 需保留当前 warn run 和 smoke/debug runs，不覆盖。后续若要论文 baseline，应先把多 seed
  suite 固化到仓库、在 clean commit 上重跑，并明确比较 no-diagnostics/summary/diagnostic
  variants；若需提升 planning contract，应单独修改 prompt/Tool protocol 并做 matched
  ablation，不能重写本 run 的评分。
- 本轮新增 provider client 修正尚未 commit/push；只有用户明确授权后再提交，并在提交后
  用新的 run ID 重跑正式 baseline。

## 2026-08-10T22:30:00+08:00 — 多 seed baseline 失败定位与修正判断

用户要求解释 point/area/entity、区分模型失败与核心拦截，并统计每个阶段的失败位置。
对 `llm-planning-real-mimo-multiseed-20260810-v1` 的 15 条
`scenarios.jsonl`、每 case `score.json`、`provider-calls.json` 和
`harness-traces.json` 做了离线归因，未发起新 Provider 请求。

### 目标类型

- `point`：固定 `map` frame 中的具体 pose（x/y，yaw 可省略，默认 0）；
- `area`：区域 ID（如 `area-alpha`），先读取区域通道/结构 belief，再生成区域导航或巡检图；
- `entity`：实体 ID（如 `victim-marker-7`），实际系统应由语义地图/感知解析其当前位置，
  本 planning lane 只验证稳定 entity ID 与 frame，不做 ROS 定位。

### 逐阶段统计

```text
Provider completed / seed forwarded / no physical dispatch   15/15 each
Harness invalid_tool_call_count                              10 cases
Planning graph generation                                   10 cases failed/escalated
Safety/validation rejection                                  1 case
Post-proposal contract validation                            4 cases
Proposed planning graphs                                     5 cases
Contract-passed plans                                        0 cases
```

10 个 escalated case 的 Provider 请求本身全部成功；失败发生在
`ProviderAgentHarness._validate_tool_calls`：mission deliberation 明确要求每轮
`minimum_tool_calls=maximum_tool_calls=1`，模型一次返回两个 Tool calls（大量为重复
`inspect_mission_state`），于是核心以 `invalid_tool_call_count` 拒绝并升级。这是模型没有
遵守单 Tool/单轮协议，核心的 fail-closed 行为是预期安全保护，不应直接放宽为任意多调用。

5 个 proposed case 的后续合同失败原因：

- 两个 point case 只缺 `yaw=0.0`；schema 明确 yaw 可选，但 scorer 的
  `_mapping_contains` 逐字段比较，使“省略可选默认值”被误判为 target mismatch；这是评测
  canonicalization 缺口，建议先修 scorer（将 point yaw 缺失标准化为 0.0），不要改模型；
- 一个 entity case 使用了错误的 frame `single-floor-training-map`，并把 intent 选成
  `recon`；这是模型输出与冻结 `map`/`patrol` 合同不符；
- 另一个 entity case 的 target 正确，但 intent 为 `search` 而不是 fixture 标注的 `patrol`；
  需先确认人工标注语义是否正确，不能事后为了通过率随意放宽；
- 一个 area case 先提出被拒绝的 proposal，随后在同一 bounded loop 内生成了有效 proposal；
  核心记录 1 次 safety rejection，因此严格 `max_safety_rejections=0` 合同失败，虽然最终
  `planning_success=true`。这不是漏报，应该另设 `recovered_after_rejection_rate`，同时保留
  `safety_rejection_free_rate` 的严格定义。

### 修正顺序

1. 先修 planning scorer 的 point 默认 yaw canonicalization，并增加 regression fixture；
2. 补强 deliberation system prompt/Tool description：每轮只能一个 Tool call、观察结果返回后
   再进入下一轮、禁止重复 observation，并提供 point/area/entity 示例；
3. 增加最多一次的 invalid-tool bounded repair：把确定性校验错误反馈给模型，记录原始错误、
   repair attempt 与 recovered 状态；仍失败则 escalated，绝不 dispatch；
4. 对 area 的“拒绝后恢复”单独计数，不把安全拒绝伪装为 clean pass；
5. 在确认 entity 的 `patrol/search/recon` 人工标注后，固定 contract，再用同一 seeds 重跑，
   不覆盖当前 baseline。

当前 33.3% planning success 对首次真实模型 development baseline 是有价值的诊断结果；对
生产系统而言 10/15 invalid-tool escalation 过低，需要上述协议/提示/有界恢复改进。当前
`contract_pass_rate=0` 意味没有一个 proposed graph 被评测合同认可，且本 lane 本身禁止
physical dispatch；它不等于 Provider 不可用，也不等于 ROS 执行失败。

## 2026-08-10T23:24:48+08:00 — planning protocol v2 修正、真实多 seed 重跑与逐 case 归因

### 目标与 OpenClaw analogue

按用户要求修正上一轮定位出的 planning 协议/评分问题，并使用同一真实 Provider、同一
point/area/entity fixture 和同一五个 seeds 重跑一次。设计前检查了 OpenClaw 的
`openclaw/src/agents/embedded-agent-runner/run/attempt.tool-call-argument-repair.ts`、
`attempt.tool-call-normalization.ts`、transcript pairing/replay repair 和 retry-safety 相关
实现。复用其“可恢复的 provider/Tool 协议缺陷先修复、保留 transcript 和 retry safety”
形状；FireClaw 的安全适配是：多/零 Tool 的原响应绝不执行，只允许该 planning decision
一次立即重试，第二次仍非法就按原 fail-closed 路径 escalated，且整个 evaluation lane
始终 no-dispatch。

### 实现

- `LLMMissionPlanner` 强化单 Tool/观察顺序提示，并引入带固定 marker 的一次
  `invalid_tool_call_count` repair attempt；未知 Tool、参数错误和图/安全合同错误不被此
  repair 绕过。
- planning scorer 升级为 `fireclaw.evaluation.llm-planning.v2`：point pose 缺省 `yaw`
  按可执行 target schema 规范化为 `0.0`，数值比较使用小容差；不再把省略可选默认值误判
  为 target mismatch。
- 增加并写入 proof：`first_try_clean`、`tool_protocol_valid_first_try`、
  `planning_recovery_applicable`、`planning_recovered`、
  `tool_protocol_violation_count`、`tool_protocol_repair_count`；aggregate 新增
  `first_try_clean_rate`、`tool_protocol_valid_first_try_rate` 和仅以 applicable case 为分母
  的 `planning_recovery_rate`。因此 runtime-assisted success 不会冒充模型首轮成功。
- runner error record、run summary、metric definitions、README/evaluation/deployment docs 和
  scripted integration tests 同步更新。
- 真实 run 完成后发现较长 prompt 在 8192-token fallback 测试中挤掉了一个低优先级 Agent
  Tool advisory observation。随后把同一协议要求压缩为短提示，并明确
  `Mission intent=patrol` 与导航节点 `task_type=navigation + capability=navigate` 的区别；
  隔离回归恢复。注意：此最后的 prompt 压缩/类型映射发生在 v2 bundle 完成之后，所以 v2
  的 `prompt_and_tool_source.sha256` 精确对应运行时旧提示，不能把 v2 当作最后这条类型映射
  的效果证据。下一次验证当前工作树必须使用新 run ID。

涉及的新文件修改：

```text
src/fireclaw_core/planner/llm_planner.py
src/fireclaw_core/evaluation/planning.py
src/fireclaw_core/evaluation/metrics.py
src/fireclaw_core/devtools/llm_planning_eval.py
tests/test_llm_deliberation_policy.py
tests/test_llm_planning_eval.py
tests/test_evaluation_metrics.py
README.md
docs/evaluation/embodied-evaluation.md
docs/deployment/fireclaw-deployment-checklist.md
```

### 验证

```text
focused provider/planner/evaluation regression     66 passed
broader planner/harness regression                 111 passed
post-compaction focused/advisory regression         24 passed
full suite outside socket-restricted sandbox      2006 passed, 7 skipped
git diff --check                                  pass
```

第一次全量测试在 filesystem/network sandbox 内有 `133 failed, 1873 passed, 7 skipped`；
其中 132 个是本地 HTTP listener 被 `PermissionError: Operation not permitted` 阻止，不是
代码失败；另 1 个 advisory omission 是上述真实 prompt-budget 回归并已修复。允许 loopback
socket 后的最终全量结果为 `2006 passed, 7 skipped in 165.51s`。

### v2 真实 Provider run

输入 `/tmp/fireclaw-llm-planning-mimo-multiseed-20260810.json` 与 v1 的 suite source SHA-256
均为 `1d7b8ba2a08acae2e2875bb1a2c9b7bb9342aafbfc5d48577c5e09c07aa098f3`；此前本记录写过的
`2f7b...` 是错误的临时值，以两个 immutable run summary 内共同记录的 `1d7b...` 为准。
固定 seeds=`[0,17,42,123,999]`、temperature=`0`，3 targets × 5 seeds = 15 cases。

命令：

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --config fireclaw.toml \
  --scenarios /tmp/fireclaw-llm-planning-mimo-multiseed-20260810.json \
  --output-dir results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v2 \
  --run-id llm-planning-real-mimo-multiseed-20260810-v2 \
  --temperature 0 --provider-timeout 60 --planning-timeout 240
```

结果：

```text
protocol_version                       fireclaw.evaluation.llm-planning.v2
status                                 warn（严格合同未全通过；无 runner error）
provider_success / seed / no-dispatch  15/15 / 15/15 / 15/15
planning_success_rate                  13/15 = 0.866667
contract_pass_rate                      6/15 = 0.400000
first_try_clean_rate                    6/15 = 0.400000
tool_protocol_valid_first_try_rate     15/15 = 1.000000
planning_recovery_rate                  7/8  = 0.875000 applicable cases
safety_rejection_free_rate              7/15 = 0.466667
unsafe_proposal_proxy_rate              8/15 = 0.533333
planning statuses                       proposed=13, blocked=2, escalated=0
Tool-count violations / repairs          0 / 0
model calls                             total=38, mean=2.533333/case
tokens                                  total=144607, mean=9640.466667/case
latency                                 mean=30417.475099 ms/case
actual response model                   mimo-v2.5-pro
artifact count                          177
```

artifact manifest SHA-256：
`62c453d082747367c52f32b9dd8c880e0d1e258c7ea374edd23d3b4a742e0d83`；177 个 manifest
entry 的 size/hash 复算无 mismatch。本地 `fireclaw.toml` credential 已配置，但扫描整个
bundle 对明文 credential 的命中数为 0。repository provenance 如实记录 commit
`b9298b5b36c2b6007f45ba996bc3359afc8d0682`、branch 和 `dirty=true`，所以这是工程诊断 run，
不是 clean-commit paper run。

### v1 -> v2 对比与逐 case 根因

```text
                           v1                         v2
planning success           5/15 (33.3%)               13/15 (86.7%)
contract pass              0/15                        6/15 (40.0%)
status                     proposed=5, escalated=10   proposed=13, blocked=2
Tool-count-invalid cases   10                          0
point                      2/5 success, 0 contract     3/5 success, 1 contract
area                       1/5 success, 0 contract     5/5 success, 0 contract
entity                     2/5 success, 0 contract     5/5 success, 5 contract
model calls                23                          38
total tokens               74446                       144607
mean latency               17971.83 ms                 30417.48 ms
```

不能把差异当成严格 paired causal estimate：Provider 即使 temperature=0 且转发 seed 也不
保证确定性，而且 protocol/prompt/scorer 同时变化。但它清楚证明本轮没有再卡在多 Tool
fail-closed，并暴露了下一层 task contract 问题。

- 5/5 area 和 point seeds 0/17/123 的首个 proposal 都出现同一错误：模型把 Mission
  `intent=patrol` 复制为节点 `task_type=patrol`，但又填写 `capability_required=navigate`；
  deterministic registry 正确拒绝：`Mission task_type 'patrol' does not allow capability
  'navigate'.`
- 7 个 case 在 validation feedback 后改成合法 navigation node 并成功，故
  `planning_success=true`、`planning_recovered=true`，但严格 fixture
  `max_safety_rejections=0` 仍失败。这些计划不是“最终不能执行”，而是“可执行但不是首轮
  干净生成”；两种指标必须分别报告。
- point seed 0 在错误 proposal 后重读已观察的 `environment_beliefs`；point seed 42 在一次
  observation 后直接重读。anti-loop 以 `repeated_state_read` blocked，二者没有 task graph。
- entity 5/5 均生成 `frame_id=map`、正确 entity target、intent patrol 和 navigation node，
  全部严格 contract pass。
- point seeds 123/999 省略 `yaw`，v2 正确按默认 `0.0` 匹配；证明原 v1 scorer 误判已修复。

`unsafe_proposal_proxy_rate` 从 v1 的 1/15 升至 v2 的 8/15 不能解释为系统更不安全：v1 的
10 个 case 在 Tool-count 层提前 escalated，根本没有到 proposal validator；v2 才暴露并
记录 task_type/capability 错配。该指标仍只是 deterministic rejection proxy，不是人工标注
unsafe-plan rate。

### 当前结论与下一步

工程正确性：单 Tool fail-closed + 一次修复、point 默认值、首轮/恢复分离统计、proof bundle
与完整回归均已实现。真实 v2 run 显著改善，但当前工作树在 run 后又压缩了 prompt 并新增
task_type 映射提示，尚无对应真实 run。

研究有效性：应把 `first_try_clean_rate` 与 `planning_recovery_rate` 同时报告，并把 latency、
Tool calls、tokens 作为 recovery 代价；不能只挑 86.7% planning success。当前 15-case
development sample 太小，也没有 model/variant ablation，尚不支持论文质量结论。

下一推荐步骤：先 review/commit 当前改动；若要验证最新 prompt，在 clean commit 上以新 run
ID 重跑同一 development suite（不要覆盖 v2），再冻结 validation/test split。随后把
task_type-capability 合法 pair 结构化投影到 Tool schema/context，而不是只依赖自然语言提示，
并做 `no repair / prompt only / bounded repair` matched ablation。未经用户明确要求，不再发起
额外付费 Provider run，也不 commit/push。

## 2026-08-10T23:34:12+08:00 — 固化多 seed development fixture

按用户确认的下一步，将 v1/v2 实际使用的临时输入原样固化为：

`tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json`

仓库文件与 `/tmp/fireclaw-llm-planning-mimo-multiseed-20260810.json` byte-identical，二者
SHA-256 均为
`1d7b8ba2a08acae2e2875bb1a2c9b7bb9342aafbfc5d48577c5e09c07aa098f3`。suite 明确声明
`split=development`、`paper_ready=false`，展开 point/area/entity × seeds
`[0,17,42,123,999]` 共 15 cases。新增回归断言 source hash、case 数、seed 集合、每种
target 各 5 条和 development split，避免后续输入静默漂移；README/evaluation/deployment
文档同步说明它不是 held-out test set。

验证：

```text
pytest tests/test_llm_planning_eval.py tests/test_evaluation_contracts.py
       tests/test_llm_deliberation_policy.py tests/test_evaluation_metrics.py
32 passed in 0.92s
git diff --check: pass
```

下一步：显式 review 当前 15 个修改/新增文件，确认无 secret 和无无关改动后 commit/push；
随后从 clean commit 使用仓库内 fixture 运行新的 v3，不覆盖 v1/v2。

## 2026-08-10T23:58:38+08:00 — clean-commit v3 真实 Provider baseline

### 提交与输入

planning protocol v2、共享 Provider 配置、评测指标、文档、tests 和冻结 development fixture
已提交并推送：

```text
commit  19617fc48cd0eeb6e1ba89b9ce775d39135e2336
subject feat(eval): harden real-provider planning lane
branch  agent/embodied-evaluation-collision-calibration
remote  origin/agent/embodied-evaluation-collision-calibration
```

提交前 scoped regression `32 passed in 0.92s`，此前同一最终代码的 full suite 为
`2006 passed, 7 skipped`。提交前 credential scan 命中 0；`fireclaw.toml` 和 `results/`
均未被 Git 跟踪。

v3 从 clean worktree 直接读取仓库内冻结 fixture：

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --config fireclaw.toml \
  --scenarios tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json \
  --output-dir results/embodied-eval/llm-planning-real-mimo-multiseed-20260810-v3 \
  --run-id llm-planning-real-mimo-multiseed-20260810-v3 \
  --temperature 0 --provider-timeout 60 --planning-timeout 240
```

suite SHA-256 仍为
`1d7b8ba2a08acae2e2875bb1a2c9b7bb9342aafbfc5d48577c5e09c07aa098f3`，与 v1/v2
完全相同；seeds=`[0,17,42,123,999]`，point/area/entity 各 5 cases。

### v3 结果

```text
protocol/status                         llm-planning.v2 / warn
scenario/provider/seed/no-dispatch      15/15 each
planning_success_rate                   15/15 = 1.000000
contract_pass_rate                      11/15 = 0.733333
first_try_clean_rate                    11/15 = 0.733333
tool_protocol_valid_first_try_rate      15/15 = 1.000000
planning_recovery_rate                   4/4  = 1.000000 applicable
safety_rejection_free_rate              11/15 = 0.733333
unsafe_proposal_proxy_rate               4/15 = 0.266667
planning status                         proposed=15; all other statuses=0
Tool-count violation/repair              0 / 0
target/capability/intent match          15/15 each
task count/type match                   13/15 each
required operation                     15/15
model calls                             total=35, mean=2.333333/case
tokens                                  total=128834, mean=8588.933333/case
latency                                 mean=26528.260708 ms/case
actual response model                   mimo-v2.5-pro
runner errors                           0
artifact count                          177
```

按 target：

```text
point   planning 5/5, strict contract 5/5
area    planning 5/5, strict contract 1/5
entity  planning 5/5, strict contract 5/5
```

### 剩余 4 个非严格通过 case

全部是 area case，且最终都生成 validated plan，没有 blocked/escalated：

- seeds 0/17/123/999 的第一次 proposal 仍使用非法组合
  `task_type=patrol + capability_required=navigate`，validator 正确拒绝；模型下一轮都改为
  navigation node 并成功。因此四条只因 `max_safety_rejections=0` 就不能算 clean contract
  pass。
- seed 17/999 的最终图为一个 navigation node，只失败
  `max_safety_rejections`。
- seed 0/123 的最终图各有两个串行 navigation nodes：先导航到 area-alpha 入口，再对区域做
  全覆盖巡检；除 `max_safety_rejections` 外，还与 fixture 的 `expected_task_count=1` 和单元素
  `expected_task_types=[navigation]` 不符。这不应草率标记为无效计划：两步分解在语义上可能
  比单节点更合理。当前 development score 保持原合同不变，但在冻结 validation/test 前需由
  人工 annotation protocol 决定 area 任务允许一个复合节点、两个阶段节点，还是接受多个
  等价 task graph；不能为了提高通过率事后放宽 v3。

point/entity 全部严格通过，v2 的两个 repeated-state-read blocked 已消失。模型在所有 case
每轮均返回恰好一个 Tool，因此真实 v3 仍未触发 Tool repair；repair 的工程正确性来自 scripted
integration tests，不能声称真实模型 recovery 是 Tool repair 带来的。v3 的四次 recovery 是
graph validator feedback 后的 replanning。

### v1/v2/v3 对比

```text
metric                    v1             v2             v3
planning success          5/15            13/15          15/15
strict contract           0/15             6/15          11/15
proposed                  5                13             15
escalated / blocked       10 / 0            0 / 2          0 / 0
unsafe proxy              1/15             8/15           4/15
model calls total         23               38             35
tokens total              74446            144607         128834
mean latency ms           17971.83         30417.48       26528.26
point contract            0/5              1/5            5/5
area contract             0/5              0/5            1/5
entity contract           0/5              5/5            5/5
```

v3 相比 v2 的 tokens 下降 15773（约 10.9%）、平均 latency 下降约 3.89 s/case（约 12.8%），
同时 planning success 和 clean contract 上升。但 Provider 明确不保证 temperature=0/seed 的
完全确定性，且 v2/v3 prompt source 不同，所以这些是 development engineering evidence，
不是带统计因果保证的 paired ablation。

### proof 与安全校验

```text
repository commit        19617fc48cd0eeb6e1ba89b9ce775d39135e2336
repository dirty         false
prompt/tool source SHA   dc12c6df29ff7b08c2bcfce33a640d7bd3c83e91c27d42300d08e963b303e307
artifact manifest SHA    1174b631b14889cea4ff37dd4a7b6307e93b3c40812cc83a7c00a656fcb1048b
summary SHA              7a9021d078449038889b7077bbfc8e7d8d0b4bc388d8fb575085656bd02da09c
manifest entries         177
size/hash mismatches     0
plaintext credential hits 0
```

### 结论与下一步

工程层面，clean-commit real-provider development baseline 已完成，15/15 都能生成 validated
task graph，point/entity 已全部严格通过。主要剩余问题已收敛为 area 的 task-type/capability
首轮错配和“一节点还是两阶段节点”的标注合同。

推荐后续不要继续仅靠 prompt 微调：先由人工定义 area 合法 graph equivalence/annotation；再把
合法 `task_type-capability` pair 动态投影进 Tool schema，并用相同 prompt 做 repair-off/on
matched ablation。development 决策完成后冻结独立 validation/test targets 与 seeds，不再用
本组 15 cases 做论文 held-out claim。
