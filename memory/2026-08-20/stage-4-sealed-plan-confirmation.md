# 阶段 4：任务预览与执行一致（sealed PlanArtifact）

## 2026-08-20T23:03:11+08:00

### 任务目标

完成 operator-facing mission 的一致性与显式授权闭环：

1. `/plan-mission` 必须调用 canonical Mission Planner，而不是另写关键词展示逻辑；
2. 服务端生成不可变、可审计、有 TTL 的 `PlanArtifact`，绑定计划 digest、机器人、active Profile revision、
   readiness revision、runtime epoch、runtime mode、risk 与 required approvals；
3. 只有显式 `/plan-mission/confirm` 才能一次性消费 token；篡改、过期、重放、Profile/robot/readiness 漂移
   全部 fail closed；
4. 执行同一份已封存计划，禁止按原始 command 重规划、改机器人、失败后自动改派或修订；
5. Web/CLI 只展示 Gateway/Robot 的真实事件，不伪造步骤、进度或成功状态；
6. 禁止 `/tasks`、`/missions` 和旧自然语言 `fireclaw plan-mission` 绕过确认直接下发。

用户在本轮明确纠正范围：当前没有“去二楼救援”的产品概念，暂时只考虑二维平面。最终 operator-facing
Planner 与 sealed execution 合同只接受 `frame_id="map"` 且带有限 `x/y/yaw` 的 pose；模型工具 schema 不再
暴露 `floor`。底层 dataclass/历史状态读取仍保留旧字段以兼容已有记录，但新计划不能生成或执行楼层目标。

### 启动状态与恢复依据

- 阶段 2/3 改动已在同一工作树中完成但未提交，必须保留；启动时工作树为预期 dirty。
- 使用最近两天 memory：
  `memory/2026-08-20/stage-2-3-first-use-authoritative-web.md`、
  `memory/2026-08-19/stage-1-reproducible-baseline.md`。
- Python 解释器始终使用：
  `/srv/lpp-extra/miniconda3/envs/py310/bin/python`。
- 未提交、未 push，未运行真实机器人。

### CodeGraph / OpenClaw analogue

修改前用 CodeGraph 检查 FireClaw `MissionGateway.plan_mission`、HTTP handler、`MissionGatewayClient`、
`MissionAgent`、`MissionRunManager`、`MissionScheduler`、`MissionPlanner`、`LLMMissionPlanner`、Web dispatch
与评测调用链。

按根 `AGENTS.md` 的 OpenClaw-first 要求，先检查 OpenClaw：

- `openclaw/src/gateway/exec-approval-manager.ts`
- `openclaw/src/gateway/operator-approval-store.ts`
- `openclaw/src/gateway/operator-approval-runtime-token.ts`

复用的结构：request/status/created/expires/runtime epoch、token 只存 hash、`consumedAt`/`consumedBy`、一次性
消费与 replay conflict。FireClaw 的必要适配是机器人/Profile/readiness/risk 绑定、执行前物理状态重检、
simulation/real 隔离、计划 lineage 与二维 map pose。

### 最终实现

#### 1. PlanArtifact 与一次性消费

新增 `src/fireclaw_core/mission/plan_artifact.py`：

- opaque token 形如 `pa1.<artifact_id>.<secret>`；JSONL 永不落 raw token，只保存 SHA-256；
- canonical JSON 生成 `plan_digest` 与 `binding_digest`；
- 冻结 operator/session/robot/runtime epoch/mode/Profile revision/readiness revision/risk/approvals/TTL；
- `pending -> consumed|expired|invalidated|execution_failed` 单调状态与 `status_version`；
- append + flush + fsync 持久化，进程内锁保证 validate/consume 原子；重启加载时验证 schema、状态序列、
  payload digest、binding digest 与 timestamp；
- token 篡改、artifact/digest/version/operator/session/robot/runtime mismatch、过期、重放均返回稳定错误码；
- readiness revision 排除 `observed_at`、`evidence_id`、`last_seen_at` 等易变元数据，只绑定语义状态与来源/
  freshness；
- `validate_plan_2d()` 拒绝 `floor`、非 `map` frame、缺 pose、bool/NaN/Inf 或缺失 x/y/yaw。

#### 2. Gateway preview / confirm

`src/fireclaw_core/mission/mission_gateway.py`：

- `/plan-mission` 读取冻结 runtime identity、磁盘当前 Profile hash 和 fresh readiness；调用配置的 canonical
  Planner 恰好一次；进行 2D、registry、capability 与 task graph 验证后签发 artifact；
- preview 返回真实 plan/steps/robots/risk/approvals、artifact/token/digests/status version/session/TTL；
- `/plan-mission/confirm` 使用精确字段白名单，禁止 caller 发送 `command` 或 `plan` 覆盖；要求
  `operator_confirmed is True`；重新核对 runtime/Profile/readiness/registry/capability 后原子消费 token；
- real mode 可以预览，但 confirmation 固定 `real_dispatch_not_authorized`，不会 dispatch；
- `/tasks` 与 `/missions` 的 operator HTTP POST 固定返回 428 `plan_confirmation_required`；
- `src/fireclaw_core/gateway/serve.py` 使用 data dir 下持久 `plan-artifacts.jsonl`；confirm endpoint 使用 approval
  scope。

#### 3. 执行同一计划

- `MissionRunManager.submit_preplanned()` 保存 sealed plan、artifact ID、plan digest；后台加载该计划而不是 command；
- `MissionAgent.execute_sealed_plan()` 从不调用 Planner，执行前再次检查 2D/registry/capability/live presence；
- `MissionScheduler.schedule()` 的 sealed path 禁止 plan revision、retry/reassignment/recovery dispatch；失败要求
  新 preview + confirm；
- sealed run 的每个 Gateway event 带 artifact ID、plan digest 与 `plan_source=sealed_plan_artifact`；
- artifact 在 queue accept 后记录执行接受状态；queue 失败进入不可重放的 `execution_failed`。

#### 4. Web 与其他生产调用方

- Web preview 不再发送客户端 Profile；confirm 只发送 artifact binding 字段与显式 boolean；删除直接
  `/tasks` fetch、客户端伪造的 `move_base` 步骤/进度和本地 accepted/cancel timeline；
- 页面只渲染 server preview step 与 SSE Gateway/Robot event；初始状态仍由阶段 3 readiness authority 提供；
- `MissionGatewayClient` 新增 `preview_mission()`、`confirm_plan()`；旧 `submit_mission()` 在网络请求前拒绝；
- `fireclaw mission` 先显示 digest/robots/risk/steps，再等待输入 `yes`；拒绝或 EOF 时不下发；
- `fireclaw plan-mission` 旧自然语言直接下发入口返回 exit 2、`plan_confirmation_required` 与
  `robot_action_started=false`，引导使用 Web 或 `fireclaw mission`；
- deterministic embodied eval、真实 Gateway E2E、Mission E2E、serve E2E 全部迁移为显式 preview/confirm；
  eval proof bundle 新增去除 raw token 的 `plan-preview.json` 与 `plan-confirmation.json`。

#### 5. 二维平面边界

- deterministic `MissionPlanner` 删除 floor extraction/assignment，只从命令抽取一个或多个 2D point；只有楼层
  没有坐标时返回 clarify；
- `RuleBasedPlanner` 的 operator 文案统一为二维 map；
- one-shot `MISSION_PLAN_TOOL` 要求严格 `target={frame_id:"map", pose:{x,y,yaw?}}`，parser 再次做有限数/
  精确 key 校验并规范化 yaw；
- deliberation 的 task graph 与 active observation Tool schema 不再向模型暴露 floor，frame 固定为 `map`，
  point/area/entity 均属于当前二维 map；
- Web 模板、E2E、CLI、LLM policy 与断线恢复测试中的新任务示例全部改为二维坐标或 map area。

### 修改文件（阶段 4 主要范围）

生产：

- `src/fireclaw_core/mission/plan_artifact.py`（新增）
- `src/fireclaw_core/mission/mission_gateway.py`
- `src/fireclaw_core/mission/mission_gateway_client.py`
- `src/fireclaw_core/mission/mission_agent.py`
- `src/fireclaw_core/mission/mission_run.py`
- `src/fireclaw_core/mission/mission_scheduler.py`
- `src/fireclaw_core/mission/mission_planner.py`
- `src/fireclaw_core/mission/interactive.py`
- `src/fireclaw_core/mission/mission_cli.py`
- `src/fireclaw_core/planner/llm_planner.py`
- `src/fireclaw_core/planner/planner.py`
- `src/fireclaw_core/gateway/method_scopes.py`
- `src/fireclaw_core/gateway/serve.py`
- `src/fireclaw_core/devtools/embodied_eval.py`
- `src/fireclaw_core/web_console/app.js`、`index.html`
- `tools/release/distribution_check.py`
- architecture alignment 英中说明。

新增核心测试：

- `tests/test_plan_artifact.py`
- `tests/test_sealed_plan_gateway.py`
- `tests/test_sealed_plan_execution.py`

同时迁移 Gateway/Web/client/interactive/serve/embodied eval/E2E/Planner/LLM/CLI/fault-injection/distribution tests。

### 测试过程、失败尝试与结论

1. 第一轮 Stage 4 核心窄测：45 passed；Gateway/Web/client 定向：69 passed。
2. 第一轮全量：`2377 passed, 8 skipped, 7 failed`。7 项均是旧生产/测试调用方 POST `/missions`，得到预期
   428；不是执行实现错误。
3. 迁移 eval/client/E2E 后定向集：先 `99 passed, 1 failed`；唯一失败是 fake robot 返回合法终态
   `completed`，断言只接受 `succeeded`。修正后通过。
4. 收紧 canonical Planner/LLM 为二维后，全量：`2379 passed, 8 skipped, 7 failed`。失败来自旧 SSE fake、
   LLM deliberation floor payload 与 CLI 多楼层夹具；全部迁移为 map pose/area 后，定向 `59 passed, 1 skipped`
   与 schema 回归 `42 passed`。
5. 第二轮全量：`2386 passed, 8 skipped in 286.47s`。
6. 发现旧 `fireclaw plan-mission` 仍直接 dispatch，判定为真实绕行面而非测试夹具；改为 fail closed，CLI
   回归 `53 passed`。
7. 最终全量（最后生产变更之后）：

```text
/srv/lpp-extra/miniconda3/envs/py310/bin/python -m pytest -q
2386 passed, 8 skipped in 286.89s (0:04:46)
```

其他检查：

```text
git diff --check                                      PASS
python -m py_compile（阶段 4 生产/测试模块）          PASS
node --check src/fireclaw_core/web_console/app.js     PASS
distribution checker tests                            6 passed
```

`python -m build` 因仓库本地 `build/` 目录遮蔽同名 build package 而不能启动；没有把它误判为代码失败，改用
不下载依赖的等价命令：

```text
python -m pip wheel . --no-deps --no-build-isolation --wheel-dir <output>
```

### 最终分发证据

最终目录：`/tmp/fireclaw-stage4-final-20260820-v2`

```text
fireclaw-0.1.0-py3-none-any.whl
923249 bytes, 249 files
SHA-256 ff9507c75ebe18ae046efdaf6df48189cb0e7118a1262f281d81f6def24b8de1

fireclaw-sim-turtlebot3-burger-v1.tar.gz
8749596 compressed bytes, 34482986 unpacked bytes, 652 files
SHA-256 5488b1e6d0f208bb309ea40a0b50b0620f06cf264b7e8a7f542b3b8c2f22b80e

Wheel PASS
Simulation Bundle PASS
Isolated Smoke PASS
Verdict: ALL PASSED
```

发布门已把 `fireclaw_core/mission/plan_artifact.py` 与
`fireclaw_core/mission/mission_gateway_client.py` 加入 wheel 必需文件，避免后续遗漏。

### 安全、研究与已知边界

- 未连接或运动真实机器人。real mode confirmation 在 Gateway 内 fail closed；这不是实机动作验收。
- `submit-subtask` 是指定 robot/command 的高级 atomic Tool 诊断入口，不是自然语言 mission submission；
  `MissionAgent.plan_and_submit` 仅保留为受信任的进程内兼容/测试 API，operator HTTP 与自然语言 CLI 均无法
  调用它绕过 artifact。
- PlanArtifact store 的原子性以单个 Gateway process 为 owner；runtime epoch 还会阻止其他进程消费本进程
  token。若未来支持共享多 writer store，需要数据库事务/跨进程锁，而不能复用当前 JSONL writer。
- 本阶段解决工程正确性、可审计性与安全授权一致性。risk 分类仍是确定性工程规则，未通过用户研究或事故数据
  校准；不能把本阶段描述为新的任务规划算法或论文贡献。
- 取消后的物理停止证据与正式两阶段恢复属于阶段 5，尚未由本阶段解决。

### 当前结论与下一步

阶段 4 的 operator-facing 工程合同已闭环：preview 是 canonical Planner 的不可变 artifact，confirm 一次性消费
同一计划，执行不重规划/改派，状态来自真实事件，2D map 边界明确，公开直接下发路径 fail closed，完整测试与
wheel 隔离安装发布门均通过。

下一步进入阶段 5：区分 cancel requested / robot acknowledged / physical stopped，用真实速度归零或硬件
StopEvidence 驱动三态，并接入正式 request/confirm 两阶段恢复。
