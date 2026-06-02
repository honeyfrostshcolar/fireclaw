# FireClaw 项目说明（中文版）

> 这份文件是给项目作者阅读的中文说明版。真正给 agent 和工具执行的权威规则仍然是 `AGENTS.md`。代码、命令、路径、类名、API 名称、数据结构和错误信息仍建议保持英文。

## 项目目标

这个仓库用于构建 **FireClaw**：一个面向消防机器人的具身 agent 框架，整体参考 OpenClaw 的架构思想。

目标系统是在每台消防机器人上部署一个 agent。人类操作员可以用自然语言下达任务，例如 `去二楼救人`。agent 应该能够理解请求，拆解成可执行子任务，选择并运行合适的 skills/tools，调用机器人侧已经测试完成的算法，并记住之前的任务、观测、执行结果和操作员偏好。

`openclaw-main/` 是本地放置的 OpenClaw 参考源码。它应该被当成架构参考，而不是直接复制的代码。重点参考 OpenClaw 的 agent、session、tool、skill、memory、gateway 等模式，然后按照消防机器人场景进行改造。
如果 FireClaw 里的某个模块在 OpenClaw 里有明确对应物，先用 CodeGraph 去看 OpenClaw 的实现，再按它已经验证过的结构去仿造，之后只做 FireClaw 自己需要的适配。不要在 OpenClaw 已经有成熟形态的时候，自己重新想一套设计或 API。保留消防救援场景的约束，不要把 OpenClaw 的 UI、channel 或 transport 假设整包搬过来。

## 当前仓库状态

- FireClaw 根目录代码可能还处在搭建阶段。
- `openclaw-main/` 包含 OpenClaw 原始参考实现，里面可能有自己的 `AGENTS.md` 或局部说明。修改该目录前要先看它自己的规则。
- 根目录的 `AGENTS.md` 负责指导 `openclaw-main/` 之外的 FireClaw 开发。
- 不要再把本项目描述成旧的 leader-switching PyTorch 项目。那是从其他项目复制来的旧说明，已经不适用。

## 架构方向

FireClaw 应该围绕几个清晰边界设计：

- **自然语言接口：** 接收操作员指令，规范化任务意图；在涉及安全风险或信息不足时主动澄清。
- **规划器 / 任务拆解器：** 把人的意图拆解成有顺序、有条件的子任务计划。
- **Skill 运行时：** 把已经测试好的算法和机器人能力封装成可调用 skill，并明确输入、输出、前置条件和失败模式。
- **机器人适配层：** 隔离 ROS、仿真器、机器人 SDK、感知、导航、机械臂、通信、执行机构等接口，避免 agent 核心直接依赖硬件细节。
- **记忆系统：** 记录任务、计划、观测、执行结果、操作员修正、环境事实和可复用经验。
- **安全门控：** 在执行前拦截或升级处理不安全、不明确、物理上不可行的动作。
- **执行监控器：** 跟踪 skill 执行进度，处理可恢复失败，更新记忆，并向操作员汇报状态。

可以保留 OpenClaw 中有用的概念，例如 agent、session、tool、skill、gateway/control plane、本地优先状态、持久化记忆等。但在消防机器人场景中，物理安全、执行延迟、通信退化、传感器不确定性、多机器人协同和可审计执行日志，比普通聊天体验更重要。

## OpenClaw 优先实现流程

FireClaw 中只要某个模块在 OpenClaw 里有对应实现，设计或改代码前先按下面流程做：

- 先用 CodeGraph 查看 OpenClaw 相关源码。一般先用 `codegraph_context` 获取模块上下文，需要源码或调用链细节时，再用一次聚焦的 `codegraph_explore` 或 `codegraph_trace`。
- 在工作记录或 `memory/` 记录里写清楚：看了 OpenClaw 的哪些文件 / symbol，复用了什么结构，哪些地方因为消防机器人场景做了调整。
- 能复用 OpenClaw 成熟形态的就复用：session state、conversation state、tool calling、skills、memory retrieval、gateway/control plane、面向操作员的状态回报、本地持久化等，不要无理由重新设计。
- 复用的是结构，不是整包照搬。FireClaw 需要额外考虑 safety gate、operator confirmation、ROS2/simulator adapter、robot identity、执行审计日志、弱网/断连行为和 emergency stop 假设。
- 如果 OpenClaw 的设计不适合 FireClaw，要先说明原因，再引入新的 API 或模块边界。
- 对 OpenClaw 已经实现过的功能，不能让 LLM 在没查 upstream 的情况下凭空生成一套新模式。

## 构建、测试和开发命令

这个仓库后续可能会从参考源码状态逐步演化成 Python、TypeScript、ROS/机器人混合项目。在添加或运行项目命令之前，先确认当前实际存在的包文件和工具链。

有用的起点：

```bash
find . -maxdepth 3 -type f | sort | head -200
find . -name AGENTS.md -print
```

如果是在 `openclaw-main/` 内部做参考或修改，要遵守 `openclaw-main/AGENTS.md` 和它的局部规则。不要把 OpenClaw 的 `pnpm`、Vitest 等命令直接套用到 FireClaw 根目录，除非根目录也明确采用了同样工具链。

当新增 FireClaw 实现时，要一起补充窄范围验证命令，例如 skill schema 单测、planner 输出校验、memory 持久化测试、safety gate 决策测试、robot adapter dry-run 测试等。

## 编码风格和命名约定

FireClaw 结构逐步成型后，代码应遵循本仓库已有风格，并保持接口清晰、可维护。

- skill、robot adapter、planner 输出、memory 记录和 safety decision 优先使用类型化接口。
- skill wrapper 要保持薄封装：校验输入，调用已经测试完成的算法，捕获输出和错误，并暴露清晰元数据。
- 不要把机器人动作副作用藏在 planner 代码中。planner 决定应该做什么，adapter 和 skill 负责真正执行。
- 不要在生产逻辑中硬编码任务例子，除非它们是明确的 fixture 或文档化契约。
- 测试尽量保持确定性。对于 LLM 相关行为，应分别测试 schema 校验、路由、安全门控和 fallback 行为，而不是只测试模型生成质量。
- 注释可以用中文或英文，但应该解释非显然的机器人、安全、算法或集成逻辑。

## 测试准则

测试约定应跟随 FireClaw 后续采用的真实工具链。在工具链明确之前，不要再假设旧 PyTorch/unittest 项目的命令适用。

agent 和机器人相关改动，优先覆盖：

- skill schema 校验和 skill dispatch；
- planner 输出和任务拆解契约；
- memory 读写行为和检索过滤；
- safety gate 的 allow/block/escalate 决策；
- robot adapter 的 dry-run 行为；
- 仿真环境和真实机器人环境的隔离。

## 长期任务记录

项目会有很多跨 session 的长期研究和开发任务。为了避免后续 agent 重复探索，应在 `memory/YYYY-MM-DD/` 下维护持久化执行记录。

记录应足够详细，让后续 agent 能直接接上，而不是重新搜索上下文。建议包含：

- 任务目标；
- 已执行命令；
- 观察到的错误或测试结果；
- 已查看文件；
- 已修改文件；
- 当前假设；
- 当前结论；
- 下一步建议；
- 当天多次更新和想法变化的具体时间；
- 关键参数、阈值、随机种子、checkpoint、输出路径和指标值；
- 失败尝试以及被排除的原因；
- 用户偏好或已经做出的决定；
- 仍然不确定的问题或研究层面的风险。

开始或恢复任务时，优先列出 `memory/` 下日期目录，只读取最近两个日期目录的记录。除非最近记录缺失、明显过期、指向更早日期，或者用户明确要求，否则不要全量扫描旧记录。

继续旧任务时，先总结：

- 当前进展：
- 已完成：
- 当前问题：
- 下一步：
- 需要运行的命令：

优先增量推进，而不是重复之前的调查。

## 研究要求

这个项目不是玩具实现，也不是普通课程作业，而是面向机器人和 AI 研究的系统。长期目标可能包括高质量机器人或 AI 方向论文。因此讨论方法设计、模块设计、实验、消融、评价指标或论文写作时，要从研究贡献角度严肃判断。

始终区分三个层面：

1. **工程正确性：**
   - 代码是否能正确运行；
   - 接口、测试、依赖和部署假设是否正确；
   - 实现是否符合预期算法或 agent 行为。

2. **研究有效性：**
   - 方法动机是否清楚；
   - agent 架构对于机器人任务是否必要；
   - 相比标准具身 agent、机器人任务规划、多智能体方法是否有新意；
   - 实验能否支撑主张；
   - 消融、安全评估、真实/仿真机器人测试是否充分；
   - 方法是否容易被批评为启发式、增量式或证据不足。

3. **论文级贡献：**
   - 核心创新是什么；
   - 解决了什么问题缺口；
   - 为什么现有方法不够；
   - 需要什么证据才能让主张可信；
   - 当前设计更适合作为会议论文、期刊论文、workshop 论文，还是工程报告。

不要只让代码通过测试。如果改动影响研究方法、agent 架构、安全机制、可解释性、记忆设计或论文主张，要明确说明研究层面的影响。需要诚实判断：如果想法不够支撑顶会，应直接指出，并给出加强方向。

## 安全和机器人约束

消防机器人工作在危险物理环境中，机器人动作应视为安全关键行为。

- 自然语言命令本身不足以授权危险或不可逆动作。必要时要加入显式确认或 safety gate。
- 状态不确定时，优先 fail-safe：停止、保持位置、请求澄清或升级给操作员。
- skill 元数据应说明所需传感器、机器人状态假设、环境假设、超时行为和急停行为。
- 决策和 skill 调用要记录到足以事后复盘事故的程度。
- 仿真行为和真实机器人行为必须隔离。不能让仿真默认配置静默驱动真实硬件。
- 不要硬编码私有部署路径、机器人凭据、网络地址或 secrets。

## Git、提交和 PR

早期阶段目录可能不是 git 仓库。如果不是，应明确说明，而不是假设可以运行 git 命令。

当 git 可用时：

- 修改前检查 status，不覆盖用户改动。
- 只有用户明确要求时才提交 commit。
- commit subject 要简洁、聚焦。中文 commit message 可以接受。
- PR 或交接总结应包括行为变化、验证命令、影响模块和已知缺口。

## 安全和产物管理

不要提交私有数据集、大 checkpoint、包含敏感现场信息的机器人日志、凭据、实时地图、私有网络配置或 secrets。优先使用 CLI 参数、示例配置文件和文档化环境变量，不要硬编码。

生成产物应放在明确目录中，例如 `results/`、`logs/`、`checkpoints/` 或 `memory/`。大型或敏感输出默认保持 untracked，除非用户明确要求纳入版本控制。

## CodeGraph 使用说明

本项目已经配置 CodeGraph MCP server，也就是 `codegraph_*` 工具。CodeGraph 是基于 tree-sitter 解析得到的代码知识图谱，记录每个 symbol、edge 和 file。它适合回答结构性代码问题，比普通文本搜索更快、更准。

### 什么时候优先用 CodeGraph

结构性问题优先用 CodeGraph，例如谁调用谁、修改某个函数会影响什么、某个类在哪里定义、函数签名是什么。普通文本内容、注释、日志字符串、报错文案等 literal text 查询，才优先用 grep/read。

| 问题 | 工具 |
|---|---|
| “X 在哪里定义？”/ “找名为 X 的 symbol” | `codegraph_search` |
| “谁调用了函数 Y？” | `codegraph_callers` |
| “Y 调用了什么？” | `codegraph_callees` |
| “X 如何到达/变成 Y？” | `codegraph_trace` |
| “修改 Z 会影响什么？” | `codegraph_impact` |
| “看 Y 的签名 / 源码 / docstring” | `codegraph_node` |
| “给某个任务/区域的聚焦上下文” | `codegraph_context` |
| “一次查看多个相关 symbol 源码” | `codegraph_explore` |
| “查看某路径下有哪些文件” | `codegraph_files` |
| “检查索引是否健康” | `codegraph_status` |

### 使用原则

- 架构或工作流问题优先用 `codegraph_context`，必要时再用一次 `codegraph_explore` 看源码。
- 具体 flow 问题优先用 `codegraph_trace`，不要自己用 grep 和 callers 一步步重建路径。
- 相信 CodeGraph 的 AST 结果。除非它提示索引过期或未初始化，否则不要再用 grep 重复验证。
- 找 symbol 不要先 grep，先用 `codegraph_search`。
- 只想要任务上下文时，不要串联 `codegraph_search` + `codegraph_node`，直接用 `codegraph_context`。
- 不要循环调用很多次 `codegraph_node`，需要看多个相关 symbol 时用一次 `codegraph_explore`。
- 如果 CodeGraph 提示某些文件 edited since last index sync，就只读取提示中的那些文件确认最新内容。没有出现在提示中的文件可认为索引是新鲜的。
- 对 FireClaw 来说，要更积极地用 CodeGraph 先看 OpenClaw 的对应模块，再决定怎么实现。已有 upstream 形状的就尽量复用并适配，不要让 LLM 在 OpenClaw 已经实现过的地方重新发明模块形状。

### 如果 `.codegraph/` 不存在

如果 MCP 返回 `not initialized`，应询问用户是否运行：

```bash
codegraph init -i
```

当前本仓库已经初始化过 CodeGraph，可以直接使用。
