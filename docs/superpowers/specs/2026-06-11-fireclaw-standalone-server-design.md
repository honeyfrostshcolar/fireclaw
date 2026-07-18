# FireClaw Standalone Server Design

> **目标**：让 FireClaw 可以像 OpenClaw 一样独立运行——启动一个持续运行的 Gateway 服务，操作员通过交互式 CLI 发送自然语言命令，看到 agent 在 Gazebo 仿真中执行任务。

## 背景

FireClaw 的核心模块（MissionAgent、MissionGateway、ROS1 adapter、Memory、TaskRegistry 等）已全部实现并通过测试（1040 passed, 6 skipped）。但当前只能通过 Python API 或 `mission_cli.py` 的一次性命令使用，缺少：

1. 一个持续运行的 Gateway HTTP 服务
2. 一个交互式操作员 CLI
3. 两者的"胶水层"——自动组装所有 store 和组件

## 设计范围

**做**：
- `fireclaw serve` — 启动 MissionGateway HTTP server，持续运行
- `fireclaw mission` — 交互式 CLI，连接 serve 实例
- 复用已有的 LLM planner 接入逻辑（`OpenAICompatProvider` + `SimpleProviderRuntime`）
- 配置文件和环境变量支持

**不做**：
- Web UI / WebSocket dashboard
- 多机器人管理 CLI（`fireclaw robot add` 等）
- Docker 部署
- 生产级安全认证（API token 已有基础，不扩展）

## 架构

```
┌─────────────────────────────────────────────────────┐
│  操作员终端                                           │
│  fireclaw mission --server http://localhost:8766     │
│  (interactive.py: 读命令 → submit → 轮询 events)     │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP
                   ▼
┌─────────────────────────────────────────────────────┐
│  Mission Gateway Server (serve.py)                   │
│  fireclaw serve --adapter ros1 --ros1-config gazebo  │
│                                                      │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  │
│  │MissionGateway│  │MissionAgent  │  │LLM Planner │  │
│  │  HTTP API    │→│ plan/dispatch │→│(OpenAI-comp)│  │
│  └─────────────┘  └──────────────┘  └────────────┘  │
│        │                │                             │
│  ┌─────┴──────┐  ┌──────┴──────┐                    │
│  │RobotRegistry│  │MemoryStore  │                    │
│  │TaskRegistry │  │TaskFlow     │                    │
│  │SubagentReg  │  │LineageStore │                    │
│  └────────────┘  └─────────────┘                    │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP (RobotSubagentClient)
                   ▼
┌─────────────────────────────────────────────────────┐
│  Robot Gateway (FireClawGateway)                     │
│  已有模块，ros1 adapter → Gazebo ROS nodes           │
└─────────────────────────────────────────────────────┘
```

## 组件设计

### 1. `fireclaw serve` 命令

**入口**：`fireclaw_core/serve.py`

**CLI 参数**：

| 参数 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `--adapter` | — | `simulator` | Robot adapter 类型 |
| `--ros1-config` | — | 无 | ROS1 配置文件路径 |
| `--host` | `FIRECLAW_HOST` | `127.0.0.1` | 监听地址 |
| `--port` | `FIRECLAW_PORT` | `8766` | 监听端口 |
| `--data-dir` | `FIRECLAW_DATA_DIR` | `./data` | 数据目录 |
| `--planner` | — | `deterministic` | 规划器类型 |
| `--provider-base-url` | `FIRECLAW_LLM_BASE_URL` | 无 | LLM API 地址 |
| `--provider-api-key` | `FIRECLAW_LLM_API_KEY` | 无 | LLM API key |
| `--model` | `FIRECLAW_LLM_MODEL` | `gpt-4o` | LLM 模型名 |
| `--llm-trace-path` | — | 无 | LLM trace 输出路径 |

**启动逻辑**：

```python
def start_server(
    *,
    adapter: str = "simulator",
    ros1_config: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8766,
    planner_type: str = "deterministic",
    provider_base_url: str | None = None,
    provider_api_key: str | None = None,
    model: str = "gpt-4o",
    llm_trace_path: str | None = None,
    data_dir: Path = Path("data"),
) -> MissionGateway:
    # 1. 确保 data_dir 存在
    # 2. 创建 robots.json 模板（如果不存在）
    # 3. 创建所有 store（JSONL/SQLite 文件在 data_dir 下）
    # 4. 构建 planner（复用 mission_cli._build_planner 逻辑）
    # 5. 创建 MissionAgent，注入所有 store
    # 6. 创建 MissionGateway，start()
    # 7. 注册 signal handler，Ctrl+C 优雅关闭
    # 8. 阻塞主线程
```

**关键复用**：
- `_build_planner()` 逻辑从 `mission_cli.py` 提取为共享函数
- `OpenAICompatProvider`、`SimpleProviderRuntime`、`LLMMissionPlanner` 已存在
- `MissionGateway.start()` 已存在
- 不配置 LLM 时使用 `MissionPlanner()`（硬编码规则），不报错

### 2. `fireclaw mission` 交互式 CLI

**入口**：`fireclaw_core/interactive.py`

**CLI 参数**：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--server` | `http://localhost:8766` | MissionGateway 地址 |
| `--timeout` | `30.0` | HTTP 请求超时（秒） |

**交互模式**：

```
🔥 FireClaw Mission Console
Connected to http://localhost:8766
Type 'help' for commands, 'quit' to exit.

fireclaw> 去二楼搜索幸存者
[plan] 已生成救援计划: rescue_victim (3 subtasks)
[dispatch] 任务 dispatched → robot-1
[events] robot-1: navigate_to_floor → in_progress
[events] robot-1: navigate_to_floor → completed (2.3s)
[done] 任务完成，耗时 8.6s

fireclaw> quit
```

**内置命令**：

| 命令 | 说明 |
|------|------|
| `help` | 显示帮助 |
| `status` | 显示当前任务状态 |
| `cancel <mission_id>` | 取消指定任务 |
| `quit` / `exit` | 退出 |

**核心逻辑**：

```python
def run_interactive(server_url: str, timeout: float = 30.0):
    client = MissionGatewayClient(server_url, timeout=timeout)
    print("🔥 FireClaw Mission Console")
    print(f"Connected to {server_url}")

    while True:
        try:
            command = input("fireclaw> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not command:
            continue
        if command in ("quit", "exit"):
            break
        if command == "help":
            print_help()
            continue
        if command == "status":
            show_status(client)
            continue
        if command.startswith("cancel "):
            cancel_mission(client, command[7:].strip())
            continue

        # 提交任务并轮询 events
        submit_and_poll(client, command)
```

**事件轮询**：

```python
def submit_and_poll(client: MissionGatewayClient, command: str):
    result = client.submit_mission(command)
    mission_id = result["mission_id"]

    # 轮询 events 直到任务结束
    seen_event_count = 0
    while True:
        events = client.get_mission_events(mission_id)
        new_events = events.get("events", [])[seen_event_count:]
        for event in new_events:
            display_event(event)
            seen_event_count += 1

        trace = client.get_mission_trace(mission_id)
        if trace.get("mission_status") in ("succeeded", "failed", "cancelled"):
            print(f"[done] 任务 {trace['mission_status']}")
            break

        time.sleep(0.5)
```

### 3. 主入口 `fireclaw_core/cli.py`

**路由**：

```python
def main():
    parser = argparse.ArgumentParser(prog="fireclaw")
    subparsers = parser.add_subparsers(dest="command")

    # serve 子命令
    serve_parser = subparsers.add_parser("serve", help="Start MissionGateway server")
    # ... 添加 serve 参数

    # mission 子命令
    mission_parser = subparsers.add_parser("mission", help="Interactive mission console")
    # ... 添加 mission 参数

    # 保留原有 plan/status/events 等子命令
    # ... 从 mission_cli.py 迁移

    args = parser.parse_args()
    if args.command == "serve":
        from fireclaw_core.serve import start_server
        start_server(...)
    elif args.command == "mission":
        from fireclaw_core.interactive import run_interactive
        run_interactive(...)
    # ...
```

### 4. 数据目录结构

```
data/
├── robots.json           # 机器人注册表
├── missions.jsonl        # 任务记录
├── memory.jsonl          # 记忆存储
├── memory.sqlite         # 记忆索引 (FTS5)
├── tasks.jsonl           # TaskRegistry
├── subagents.jsonl       # SubagentRegistry
├── lineage.jsonl         # SessionLineage
├── flows.jsonl           # TaskFlowRegistry
├── events/               # 事件日志
│   └── mission-xxx.jsonl
└── llm-traces/           # LLM 调用追踪
    └── trace-xxx.jsonl
```

`robots.json` 模板（serve 启动时如果不存在，自动生成）：

```json
{
  "robots": [
    {
      "robot_id": "robot-1",
      "base_url": "http://localhost:8765",
      "capabilities": ["navigate", "search", "manipulate"]
    }
  ]
}
```

## 文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/fireclaw_core/cli.py` | 新增 | 主入口，子命令路由 |
| `src/fireclaw_core/serve.py` | 新增 | Gateway server 组装和启动 |
| `src/fireclaw_core/interactive.py` | 新增 | 交互式 CLI |
| `tests/test_cli.py` | 新增 | CLI 路由测试 |
| `tests/test_serve.py` | 新增 | serve 组装逻辑测试 |
| `tests/test_interactive.py` | 新增 | 交互式 CLI 测试 |
| `examples/ros1-gazebo.yaml` | 新增 | ROS1 Gazebo 配置示例 |
| `pyproject.toml` | 修改 | 添加 `fireclaw` console_scripts 入口 |

## 测试策略

| 测试类型 | 覆盖内容 | 方式 |
|----------|----------|------|
| 单元测试 | `serve.py` 组装逻辑、`interactive.py` 命令解析 | pytest，mock gateway |
| 集成测试 | serve → submit → events 全链路 | 真实 MissionGateway，simulator adapter |
| E2E 测试 | interactive CLI → serve → robot gateway | 扩展已有 `test_embodied_gateway_e2e.py` |

## 验收标准

1. `fireclaw serve` 启动后，`curl http://localhost:8766/missions` 返回 200
2. `fireclaw mission` 提交"去二楼救人"，看到 events 流式输出
3. 配置 LLM 后，提交任意自然语言命令，planner 正确拆解
4. Ctrl+C 优雅关闭，JSONL store 数据完整
5. 不配置 LLM 时，硬编码 planner 正常工作，不报错
6. 全量测试：1040+ passed, 6 skipped（不退步）

## 不做的

- Web UI / WebSocket dashboard
- 多机器人管理 CLI（`fireclaw robot add` 等）
- Docker 部署
- 生产级安全认证
- 第三方插件动态加载
