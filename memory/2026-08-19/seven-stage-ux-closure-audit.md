# FireClaw 七阶段 UX 闭环审计

## 2026-08-19T23:13:53+08:00

### 任务目标

逐项评估用户提出的七阶段路线是否达到产品闭环：

1. 固化当前基线；
2. 首次使用闭环；
3. Web 权威状态；
4. 任务预览与执行一致；
5. 取消与恢复安全闭环；
6. 零手写配置与友好错误；
7. 后续体验与用户验证。

本轮只做源码、测试、文档和 Git 状态审计，不修改业务代码，不 stage/commit，不启动 Gateway、ROS、Gazebo 或机器人。

### 启动状态

- 已读取最近两个日期目录：`memory/2026-08-19/` 与 `memory/2026-08-17/`。
- 分支仍为 `agent/embodied-evaluation-collision-calibration`，相对其远端领先 6 个提交；HEAD 为 `d6ba36b`。
- tracked diff：29 个文件，`1867 insertions(+), 242 deletions(-)`；大量 UX/config/errors/resources/Web/tools/tests/docs 仍为 untracked。
- `data/robots/gazebo_turtlebot3/memory-runtime.sqlite3` 仍是未跟踪运行时数据库。
- `git diff --check` 无输出。
- 同日上一次续作审计已重新运行发布聚焦测试：`25 passed in 20.46s`，`node --check` 通过；2026-08-17 最近一次全量证据为 `2339 passed, 8 skipped`。

### CodeGraph / OpenClaw analogue 记录

本轮先使用 CodeGraph 检查当前 FireClaw 调用链：

- `src/fireclaw_core/infra/user_setup.py`：`setup_fireclaw`、`handle_setup`、`_resolve_simulation_source_root`、`_ensure_generated_simulation_profile`；
- `src/fireclaw_core/infra/daemon_manager.py`：`start_daemon`、`open_console`；
- `src/fireclaw_core/mission/mission_cli.py`：`handle_start`、`handle_stop`、`handle_open`；
- `src/fireclaw_core/mission/mission_gateway.py`：`readiness`、`plan_mission`、`submit_mission`、`cancel_mission`、`recover`、HTTP GET/POST handlers；
- `src/fireclaw_core/web_console/app.js`：readiness、preview、submit、cancel、recovery、config、friendly error paths；
- `src/fireclaw_core/config/*`：templates、discovery、schema、snapshots、secrets；
- `src/fireclaw_core/errors/*`：FriendlyError registry 与 resolver；
- Robot Gateway 已有的 resource-admission recovery request/confirm flow。

OpenClaw analogue 检查：

- `openclaw/src/gateway/server-methods/system-agent.ts` 明确规定 Gateway surface 不应自行安装或重启 daemon；FireClaw 的一键首次使用应由 CLI lifecycle orchestrator 完成，Web 只显示状态或触发受控请求。
- OpenClaw 的普通控制面没有消防机器人“权威物理停止证据”的直接 analogue；`stop_evidence`、里程计 freshness/dwell、驱动确认和急停升级必须作为 FireClaw-specific 安全边界实现，不能照搬聊天/daemon 的 stopped 语义。

### 七阶段结论

#### 1. 固化当前基线 — PARTIAL，接近完成但尚未交付

已完成：

- current wheel build 已能包含 Web HTML/CSS/JS、setup template 和 simulation catalog；
- deterministic simulation bundle、完整 distribution gate、isolated installed smoke、负向安全测试均已有通过证据；
- package resource traversal、bundle manifest completeness、provenance/license 和 partial-gate 误报已加固。

未闭环：

- 关键实现仍大量为 `??`；`git ls-files` 不能列出 Web asset、package template、release tool/test，clean clone 不包含成果；
- runtime SQLite 未被精确忽略；
- 文档与界面仍有过度声明：README 声称 5 个核心命令完成完整生命周期和无需手写 TOML，但当前流程不成立；Web recovery 在缺乏对应证据时列出防跌落、雷达距离、E-STOP 正常；CLI `stop` 把 daemon 退出表述为机器人“安全停机”；
- FriendlyError registry 仍静态声称“电机使能已关闭”“绝对静止”“原地制动”“已限制速度”等，而 resolver 只做字符串插值，没有 evidence gate。

必须做：

1. 精确 `.gitignore` runtime DB/credential/log/generated state；
2. 建立 proposed change set，明确纳入源码、tests、docs、release tools、memory，排除 `data/robots/**`；
3. 先修完剩余安全/能力过度声明；
4. 从最终候选集运行全量 pytest、真实 wheel/bundle、isolated full gate；
5. 用户授权后形成可复现 commit/checkpoint。

#### 2. 首次使用闭环 — OPEN，只有分离的基础命令

已有：

- `setup` 能生成/复用 simulation Profile、materialize content-addressed deployment release，并记录 active Profile；
- `start` 有 daemon PID/status/idempotency；`open` 能探测运行状态后打开控制台；
- real setup 禁止自动 deploy，real `start` 有静态 safety preflight。

未闭环：

- `setup_fireclaw()` 明确不启动 Gateway，返回 `ready_to_start`，下一步仍写 `fireclaw deploy run`；不存在一条命令完成 setup → start → open；
- simulation setup 仍调用 `_resolve_simulation_source_root()` 寻找完整仓库，Profile 还引用源码树与 `robots/.../devel/setup.bash`；已发布 sidecar bundle 没有自动下载/校验/原子物化/可续接构建，因此普通 wheel install 后的首次使用不成立；
- real setup 不调用 `RosGraphDiscoverer` 做被动发现，只要求已有人工 Profile；
- 尚无从真实 wheel 安装开始的 product E2E。

必须做：

1. 实现 versioned simulation bundle resolver/materializer，验证 catalog/hash/license，原子展开到 runtime root；
2. 实现可续接的 ROS workspace build receipt/fingerprint，Profile 只引用 materialized runtime paths；
3. 在 CLI 层增加 simulation-only quickstart orchestration（setup → start → open）；Gateway/Web 不自行重启 daemon；
4. real flow 仅被动发现、生成 draft/diff、静态/现场预检并等待人工确认，任何失败路径不得运动；
5. 增加 clean venv/wheel → setup → start → readiness → open → repeat/resume → stop E2E，以及 real no-motion spy test。

#### 3. Web 权威状态 — OPEN，当前仍有硬编码和推断

未闭环证据：

- Web 初始 state 仍是 `mode: 'simulation'`、`activeRobotId: 'turtlebot3_burger'`、`profilePath: 'profiles/turtlebot3_burger.json'`；
- `renderOverview()` 无条件显示 `SIMULATION`；
- Gateway `readiness()` 返回 active robot/fleet doctor，但没有 active Profile、deployment/runtime mode、profile revision/fingerprint；
- recovery 页面在“没有 blockers”时把三条具体物理安全事实渲染为正常，未要求对应传感器证据。

必须做：

1. 为 Gateway 注入不可变 runtime identity（mode、active profile identity/path、profile hash/revision、robot id）；
2. 扩展 typed readiness contract，所有物理字段包含 value/source/observed_at/freshness/evidence_id；
3. Web 默认全部为 UNKNOWN，只消费 Gateway contract，删除 Profile/mode/传感器正常值的硬编码与 fallback 推断；
4. 增加 Gateway JSON contract tests 与浏览器级测试，覆盖 simulation/real/unknown/stale/missing evidence。

#### 4. 任务预览与执行一致 — OPEN，当前预览只是 UI 原型

未闭环证据：

- `/plan-mission` 是关键词 if/elif 生成的展示计划，没有调用真实 Mission planner，也没有 plan token/digest/TTL；
- `/tasks` 只读取 command，完全忽略 `target_robot` 和 `plan`，会重新规划；
- form submit 在无 preview 时调用 `parseTaskIntent(true)`，解析后自动执行，绕过显式确认；
- Web 在收到 accepted 后自行伪造 “move_base 巡航启动中” timeline/current step，不是 Gateway 事件；
- 当前测试只检查字段/字符串存在，不验证 preview artifact 被同一次执行消费。

必须做：

1. 让 preview 调用 canonical planner，生成 server-owned immutable PlanArtifact；
2. artifact 至少绑定 canonical plan digest、operator/session、robot、active profile revision、safety/readiness revision、created_at/expires_at、risk/required approvals；
3. 增加显式 confirm/consume endpoint，token 一次性使用，过期、状态漂移、profile 变化、robot 变化均 fail closed；
4. 执行层消费已封存 plan，不依据原始 command 重新规划；
5. 删除 direct auto-confirm 和前端伪造执行步骤，只渲染 Gateway/Robot events；
6. 增加 tamper/replay/expiry/state-drift/idempotency/robot-mismatch E2E。

#### 5. 取消与恢复安全闭环 — PARTIAL，仅前端语义修正

已有：

- Web 不再把 `mission.cancelled` / `task.cancelled` 直接映射为物理停止；
- 只有 `robot.stopping` 进入 stopping，只有携带 `physical_stop_confirmed=true` 与 object `stop_evidence` 的权威事件进入 stopped_confirmed；
- Robot Gateway 已有正式 resource-admission recovery request/confirm、request ID、policy/TTL state。

未闭环：

- Mission/Robot Adapter 没有生产 `robot.stopping`、`robot.stopped_confirmed` 或 `task.stopped`；
- cancellation 目前只表示 control signal/任务状态，请求后没有 odometry/driver/E-STOP evidence bridge；
- Mission Gateway `/recover` 只检查一个 boolean、清空 report 并发布 `safety.recovered`，没有复用 Robot Gateway 的 request/confirm、TTL 和再次准入评估；
- CLI `stop` 把 daemon 退出误报为机器人安全停机；
- Web recovery 成功 toast 和默认安全清单仍过度声明。

必须做：

1. 定义 versioned StopEvidence contract：source、odom linear/angular velocity、threshold、continuous dwell、observed_at、freshness、frame/controller/driver ack、optional E-STOP evidence；
2. Adapter 在真实 cancel acknowledgement 后发布 `robot.stopping`，只有 validator 通过才发布 stopped_confirmed/task.stopped；
3. cancellation ack/stop evidence 超时必须保持 UNKNOWN、告警并升级到现场急停，不可转换成 cancelled=stopped；
4. Mission Gateway/Web 复用 resource-admission request → confirm → fresh re-evaluation 流程；
5. 修正 daemon stop、recovery UI、docs 的物理状态措辞；
6. 增加 fake adapter contract、Gazebo odometry dwell、stale/spoofed evidence、通信中断和实机验收测试。任何 real dispatch 前此阶段必须闭环。

#### 6. 零手写配置与友好错误 — OPEN，组件存在但没有可信产品合同

未闭环证据：

- Plugin manifests 已有 `config_schema`，Gateway 却返回另一套硬编码 `get_core_config_schemas()`；
- `TemplateManager.render_profile_toml()` 的 5 个模板只有 robot/capability blocks，没有 `[deployment]`，不能直接 start；
- discovery API 返回 `active_topics/matched_topics`，Web 读取 `discovered_topics/suggested_rules`，自动映射不会工作；
- Web 不读取 active Profile，diff 以硬编码 simulation TOML 为旧值，并把字段字符串化塞进 `[robot]`；
- `/config/save` 接受任意路径，直接 `mkdir/write_text`，无 path boundary、完整 Profile/plugin schema 验证、原子替换或 SecretManager；导入的 `SecretManager` 未使用；
- snapshot 在新内容写入后创建，不证明上一版或新版本是可启动的 known-good；rollback 后也不复验；
- topic/action test 对大部分字段只检查 `/` 前缀或回显 configured value；
- FriendlyError resolver 没有 evidence input/gate，大量 registry 文案虚构已执行动作；多数 suggested action 在 Web 中只关闭 modal。

必须做：

1. 建立单一 ProfileService，统一 active Profile load/draft/validate/diff/save/snapshot/rollback；
2. schema 以 Plugin manifest 为权威，提供规范化 adapter 给 CLI/Web；
3. 模板输出完整可启动 Profile，并分别经过 capability/deployment/plugin schema/dry-run plan 校验；
4. 统一 discovery JSON contract，并对真实 topic/action/service 做 typed probe；
5. 保存仅允许 active profile scope，先在临时文件完成解析与全量验证，再 fsync + atomic replace；秘密抽取到 SecretManager，仅保存 env/file refs；
6. snapshot 标记 profile hash、schema/runtime versions、validation receipt，rollback 只允许 verified snapshot 且恢复后重验；
7. FriendlyError 的安全状态与已采取动作只能来自 evidence/action receipts；没有证据统一显示 UNKNOWN；suggested action 必须有已注册 handler 和授权策略，否则只显示文字建议；
8. 增加 traversal/symlink/crash atomicity/raw-secret/schema parity/known-good rollback/live ROS contract tests。

#### 7. 后续体验与用户验证 — NOT STARTED

仓库中未发现用户侧 `fireclaw support`/自动脱敏支持包、安装升级向导、移动端服务端只读策略、引导式教学任务或 5–8 名新用户可用性实验记录。现有响应式 CSS 不等于移动端只读安全边界。

必须做：

1. 脱敏、大小受限、带 manifest 的 support bundle；
2. versioned install/upgrade plan、健康检查、失败自动回滚和审计 receipt；
3. 移动端在 Gateway scope/endpoint 层强制 read-only，而不是只隐藏按钮；
4. simulation guided tutorial，覆盖 preview/confirm/cancel/recovery；
5. 预注册可用性 protocol，记录 time-to-first-task、配置错误、求助、取消/恢复成功、unsafe misunderstanding；
6. 5–8 人只足够 formative/pilot study。若用于论文有效性主张，需要对照系统、任务随机化、更多受试者或重复测量、置信区间和安全错误分析。

### 推荐依赖顺序

1. **Gate A：阶段 1** — 先得到 clean、可复现、无过度安全声明的提交基线。
2. **Gate B：阶段 2 + 3** — 只开放 simulation quickstart，并让 Web 状态完全来自 Gateway。
3. **Gate C：阶段 4 + 5** — 建立 plan artifact/confirm 与物理停止/正式恢复两条安全合同；阶段 5 未闭环前禁止 real task dispatch。
4. **Gate D：阶段 6** — 配置系统成为唯一可信 Profile 写路径后，才宣称“零手写配置”。
5. **Gate E：阶段 7** — 交付支持、升级、移动端和用户研究。

### 工程 / 研究 / 发表判断

- 阶段 1–3 主要是工程正确性、交付和 HMI 可信性；必要但通常不是论文核心创新。
- 阶段 4 的 sealed plan contract 与阶段 5 的 uncertainty-aware stop evidence / recovery protocol 可以形成研究方法，但需要清晰安全属性、攻击/故障模型、标准 baseline、消融和 sim-to-real evidence。
- 阶段 6 可作为安全配置管理贡献的一部分；如果只做表单和模板，属于工程功能。
- 阶段 7 的 5–8 人试验适合形成性可用性评估，不足以单独支撑强泛化结论或顶会核心 claim。

### 本轮文件修改

- 新增本审计记录：`memory/2026-08-19/seven-stage-ux-closure-audit.md`。

### 下一步

从阶段 1 开始实施：先建立精确 proposed change set 和剩余过度声明清单，再修改 `.gitignore`、安全文案/状态投影，并执行最终提交前 release gate。本轮没有获得 stage/commit 授权。
