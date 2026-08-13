# P1 七类故障注入软件/仿真闭环

## 2026-08-13T11:48:40+08:00 — 实现、全量回归与证据验收完成

### 任务目标

用户要求在 P0 五项完成无真机阶段的软件准备后，继续完成 P1 故障注入测试，覆盖：断网、Gateway 崩溃、
ROS master 重启、磁盘满、数据库锁、传感器失效和重复命令。用户明确要求逐项闭环，不应只完成一部分便
进入下一项。本轮目标是形成统一入口、真实可观察的故障、明确安全判据和不可覆盖的验收证据，而不是只
增加几个彼此分散的单元测试。

### 当前进展

- 七类软件/仿真场景已接入统一 `fireclaw fault-test` runner；
- 最终 live ROS 矩阵 `7/7 passed`；
- 最终证据包通过 hash、大小、路径、计数、状态和逐场景内容一致性校验；
- 全仓回归 `2138 passed, 8 skipped`，退出码 0；
- 默认 Gazebo Robot Profile 的既有持久安全冻结未删除、未绕过、未恢复。

### OpenClaw analogue 与 FireClaw 适配

前序已通过 CodeGraph 检查 OpenClaw 的服务生命周期形状，包括
`openclaw/src/daemon/service.ts::GatewayService`、
`openclaw/src/daemon/systemd.ts::installSystemdService` 和
`openclaw/src/daemon/systemd-unit.ts::buildSystemdUnit`，并搜索故障测试对应物。OpenClaw 没有 ROS graph、
物理传感器、SQLite 满盘时的机器人任务准入或实体动作安全门，因此不存在可以直接照搬的七类 embodied
fault-injection API。本轮复用已经在 FireClaw 中建立的 supervisor/service 生命周期、Gateway 权威状态和
`EvaluationRunBundle` 不覆盖证据结构；ROS、存储、传感器与危险动作前 fail-closed 判据作为消防机器人
特有适配，没有把 OpenClaw 的聊天/daemon 假设生搬到机器人侧。

### 通过标准

每个场景共同要求：故障确实被观察；危险执行未启动或按部署模式安全处置；没有重复任务或半提交状态；
故障解除后有界恢复，无法恢复时保持 fail-closed；结果写入可验证证据。统一报告只在七类场景全部通过且
ROS 场景实际启动、杀死并重启私有 `roscore` 时返回 `passed`。不带 `--live-ros` 时 ROS 只验证确定性
边界，报告为 `prepared`；显式要求 live ROS 而工具缺失时失败，不允许以 skip 冒充通过。

### 已实现

#### 权威存储 fail-closed

- `src/fireclaw_core/infra/runtime_state.py`
  - `SqliteAuthoritativeRuntimeStore` 新增可配置、受校验的 `busy_timeout_seconds`；
  - 新增测试专用 `max_page_count`，使用 SQLite 自身页上限触发真实 `SQLITE_FULL`，不填满宿主机磁盘；
  - 每个连接应用一致的 busy timeout/page limit。
- `src/fireclaw_core/gateway/gateway.py`
  - Gateway 可注入同路径的权威 store，便于隔离故障验收；
  - 新任务准入、去重记录创建、容量拒绝和接收事件持久化捕获 SQLite/OSError；
  - 返回 HTTP 503 对应的结构化 `status=unavailable`、稳定错误码、
    `task_execution_started=false` 和任务记录状态；
  - 错误码区分 `runtime_storage_full`、`runtime_database_locked`、
    `runtime_database_corrupt`、`runtime_storage_io_error`；
  - 清理未启动的内存 control，并尽力将已创建记录终态化，绝不在权威写入不确定时启动 worker；
  - `/state.runtime_storage` 暴露存储阻塞，成功提交后恢复 healthy。
- `src/fireclaw_core/infra/operator_readiness.py`
  - 存储 degraded 时统一状态为 blocked，提示修复后使用相同 `dedupe_key` 重试；
  - 不因 `/health` 仍活着而错误显示 READY。

#### 统一 runner 与 CLI

- 新增 `src/fireclaw_core/infra/fault_injection.py`：
  - 固定七场景注册表和精确 pytest node；
  - 每个场景独立子进程、120 秒有界超时；
  - 捕获进程启动错误与超时，不中止其余证据落盘；
  - 重复选择去重，显式空选择报错；
  - 每次创建新 run directory，不覆盖失败证据；
  - 写入逐场景 JSON、总报告和 content-addressed manifest；
  - verify 检查路径逃逸、缺失/多余文件、摘要、大小、run ID、schema、场景唯一性、计数、总状态和逐场景
    文件与报告一致性。
- `src/fireclaw_core/__main__.py`、`src/fireclaw_core/mission/mission_cli.py`
  - 新增 `fireclaw fault-test run|verify`；
  - 支持重复 `--scenario`、`--live-ros`、仓库根、artifact 目录和 JSON 输出。

#### 七类故障场景

- `network_disconnect`：实际回环 SSE 在非终态事件后断开，客户端以 cursor 自动重连，事件不重复；
- `gateway_crash`：真实 Gateway 子进程首次以 17 退出，supervisor 记录退出、清理和仿真有界重启；同时引用
  实机模式不自动重启并持久冻结的既有测试；
- `ros_master_restart`：确定性 graph provider 失败返回 degraded；live 场景使用随机端口、隔离
  `ROS_HOME/ROS_LOG_DIR` 的私有 `roscore`，验证可达、杀停不可达、重启恢复；
- `disk_full`：SQLite 最大页数触发真实 `database or disk is full`，返回
  `runtime_storage_full`，没有 worker 和半任务；
- `database_lock`：独立连接持有写锁，Gateway 方法和真实 HTTP 均在短超时返回 unavailable/503；解锁后
  同一 dedupe key 成功；
- `sensor_failure`：必需 lidar 健康证据无有效量程，Safety Gate 在导航前阻塞；
- `duplicate_command`：8 个线程并发同一 key，只产生 1 个 task ID、1 条权威记录和 1 次 worker 启动。

新增 `tests/test_fault_injection_scenarios.py` 和 `tests/test_fault_injection.py`，并扩展
`tests/test_operator_readiness.py`。文档新增
`docs/deployment/fault-injection-acceptance.md`，README 和部署 checklist 已加入运行/验收入口。

### 执行命令与结果

```text
python -m py_compile ...
  -> pass

pytest -q tests/test_fault_injection.py
  -> 7 passed in 0.28s

pytest -q tests/test_fault_injection.py tests/test_fault_injection_scenarios.py
          tests/test_operator_readiness.py tests/test_authoritative_runtime_state.py
  -> 39 passed, 1 skipped in 3.18s

pytest -q
  -> 2138 passed, 8 skipped in 179.82s

fireclaw fault-test run --live-ros --repository-root /home/lpp/fireclaw-master
          --artifact-dir /home/lpp/fireclaw-master/results/fault-injection --json
  -> status=passed, passed_count=7, scenario_count=7

fireclaw fault-test verify --run-dir
  /home/lpp/fireclaw-master/results/fault-injection/
  fault-injection-20260813T034747.681988Z-91390f69 --json
  -> status=valid, suite_status=passed, errors=[]

git diff --check
  -> pass
```

完整回归的 8 个 skip 中，1 个是 live private roscore 测试的默认显式 opt-in；统一最终验收使用
`--live-ros` 单独实际执行了该场景并通过。其余为仓库既有环境相关 skip。

### 最终证据

最终不可覆盖 run：

```text
/home/lpp/fireclaw-master/results/fault-injection/
fault-injection-20260813T034747.681988Z-91390f69
```

- `report.json` SHA-256：
  `a3c126f0c06da010d7053f72a725f8c2fee9a3d6c076e01309237f5275cd5ce0`
- `artifact-manifest.json` SHA-256：
  `e50b895778884cd1746a978452cadbffaea31ebe9cb0478f7f493a72750180e3`
- 七个 `scenarios/*.json` 全部在 manifest 中且 verify 通过。

### 失败尝试与修正（证据保留）

1. 首次统一 live run 保留于
   `fault-injection-20260813T033453.963680Z-6be5d837`：6/7，私有 roscore 场景超时。原因是 ROS 进程环境
   隔离和诊断不足；随后隔离 ROS home/log、捕获输出并改进进程组清理。
2. 第二次保留于 `fault-injection-20260813T033654.748656Z-204fa096`：6/7。新增诊断明确发现
   `ModuleNotFoundError: rosmaster`；原因是 source-tree CLI 的 `PYTHONPATH=src` 屏蔽 Noetic Python
   package。修正为根据 `roscore` 安装路径前置 ROS `dist-packages`，随后 live 重启通过。
3. 一次定向回归误写为不存在的 `tests/test_runtime_state_authoritative.py`，pytest 在收集前以 exit 4 退出、
   `no tests ran`；立即用仓库实际文件 `tests/test_authoritative_runtime_state.py` 重跑并通过。这不是产品代码
   失败，但保留记录避免下次重复错误。

两次失败 run 未删除或改写，这是“不丢弃不方便证据”的设计要求。

### 已完成

P1 的软件/仿真故障注入闭环已完成：统一入口、七类自动化场景、安全判据、恢复/阻塞语义、完整回归和可
验证证据均已落地。没有继续跳到下一产品项。

### 当前问题与诚实边界

当前没有真实机器人，因此本结论不能推断真实现场已经通过。未来仍需在目标部署上补做：真实网卡分区和
抖动、目标文件系统耗尽、实际 ROS 主机重启、物理传感器拔线，以及底盘 watchdog/急停对运动的独立
约束。这些属于现场层验收，不应拿仿真结果替代。工程层闭环本身不是研究创新，也不是功能安全认证；若要
形成论文级贡献，还需要系统化故障模型、对比基线、故障覆盖率、恢复时间/危险暴露指标、重复实验与真机
证据。

### 下一步

在没有真机时无需继续改写这七类闭环。取得实体平台后，先按
`memory/2026-08-13/real-hardware-safety-acceptance.md` 完成硬件安全 preflight/acceptance，再在隔离安全区
运行本矩阵的现场扩展。任何现场失败都保持 blocked，不通过删状态、换 dedupe key 或重启绕过。

### 需要运行的命令

日常软件回归：

```bash
fireclaw fault-test run --live-ros
fireclaw fault-test verify --run-dir results/fault-injection/<run_id>
```

未来实体平台到场后的命令和人工确认要求见
`docs/deployment/real-robot-hardware-safety-acceptance.md`；当前不要在无安全观察员/无测试区的情况下制造
真实网络、传感器或运动故障。
