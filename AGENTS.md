# Repository Guidelines

> Authoritative agent guide for FireClaw. User-facing replies should be in Chinese unless the user requests otherwise. Keep code, commands, paths, class names, API names, data shapes, and error messages in English.

## Project Mission

This repository is for building **FireClaw**: an embodied agent framework for firefighting robots, inspired by OpenClaw's architecture.

The target system installs one agent on each firefighting robot. A human operator can give natural-language tasks such as `去二楼救人`; the agent should understand the request, decompose it into executable subtasks, select and run appropriate skills/tools, coordinate with robot-side algorithms, and remember previous tasks, observations, outcomes, and operator preferences.

`openclaw-main/` is a local reference copy of OpenClaw. Treat it as architectural source material, not as code to blindly copy. Prefer studying OpenClaw's agent/session/tool/skill/memory/gateway patterns, then adapting useful parts to robotics constraints and this repository's codebase.
If a FireClaw module has a clear OpenClaw analogue, inspect the upstream implementation with CodeGraph first, then mirror the proven structure before adding FireClaw-specific adaptations. Do not invent a fresh design or API when OpenClaw already solves the same problem. Preserve the firefighting domain constraints, and do not wholesale copy OpenClaw UI/channel/transport assumptions when they do not fit rescue robotics.

## Current Repository Shape

- Root-level FireClaw code may still be incomplete or under construction.
- `openclaw-main/` contains the upstream OpenClaw reference implementation and its own scoped guides. Read its scoped `AGENTS.md` files before modifying files inside that subtree.
- This root `AGENTS.md` owns FireClaw work outside `openclaw-main/`.
- Do not describe this repository as the old leader-switching PyTorch project. That description came from another project and is obsolete here.

## Architecture Direction

Design FireClaw around small, explicit boundaries:

- **Natural-language interface:** accepts operator commands, normalizes intent, asks for clarification when safety-critical details are missing.
- **Planner/task decomposer:** turns operator intent into a plan with ordered or conditional subtasks.
- **Skill runtime:** exposes tested algorithms and robot capabilities as callable skills with typed inputs, outputs, preconditions, and failure modes.
- **Robot adapter layer:** isolates ROS, simulator, robot SDK, perception, navigation, manipulation, communication, and actuation APIs from the agent core.
- **Memory system:** records tasks, plans, observations, outcomes, operator corrections, environment facts, and reusable lessons.
- **Safety gate:** blocks or escalates unsafe, ambiguous, or physically impossible actions before execution.
- **Execution monitor:** tracks skill progress, retries recoverable failures, updates memory, and reports status to the operator.

Keep OpenClaw-like concepts when useful: agents, sessions, tools, skills, gateway/control plane, local-first state, and persistent memory. Adapt them for embodied robotics: physical safety, latency, degraded communication, sensor uncertainty, multi-robot coordination, and auditable execution logs matter more than chat convenience.

## OpenClaw-First Implementation Workflow

For FireClaw modules that have an OpenClaw counterpart, follow this workflow before designing or editing code:

- Use CodeGraph to inspect the relevant OpenClaw source first. Start with `codegraph_context` for broad module context, then use one focused `codegraph_explore` or `codegraph_trace` when source or flow details are needed.
- Record the OpenClaw analogue in the working notes or memory record: which files/symbols were inspected, what structure is being reused, and what is being changed for firefighting robotics.
- Reuse OpenClaw's proven module shape where it fits: session state, conversation state, tool calling, skills, memory retrieval, gateway/control plane, operator-facing status, and local persistence should not be redesigned from scratch without a concrete reason.
- Adapt instead of copying wholesale. FireClaw may need different safety gates, operator confirmation, ROS2/simulator adapters, robot identity handling, execution audit logs, degraded-network behavior, and emergency-stop assumptions.
- If OpenClaw's design does not fit FireClaw, state the reason explicitly before introducing a different API or module boundary.
- Do not let an LLM invent a fresh implementation pattern for an OpenClaw-existing feature until the upstream pattern has been checked.

## Build, Test, and Development Commands

This repository may evolve from a reference-only state into a mixed Python/TypeScript/robotics project. Before adding or running project-specific commands, verify the actual package files and local tooling.

Useful starting points:

```bash
find . -maxdepth 3 -type f | sort | head -200
find . -name AGENTS.md -print
```

For OpenClaw reference work inside `openclaw-main/`, follow `openclaw-main/AGENTS.md` and scoped guides. Do not apply OpenClaw's `pnpm`/Vitest commands to FireClaw root code unless the root has adopted the same toolchain.

When adding FireClaw implementation, add narrow verification commands with the code being introduced, for example unit tests for skill schemas, planner output validation, memory persistence, safety gate decisions, and robot adapter behavior.

## Coding Style & Naming Conventions

Use idiomatic, maintainable code that follows the existing local patterns once the FireClaw structure exists.

- Prefer typed interfaces for skills, robot adapters, planner outputs, memory records, and safety decisions.
- Keep skill wrappers thin: they should validate inputs, call already-tested algorithms, capture outputs/errors, and expose clear metadata.
- Do not bury robot-specific side effects inside planner code. Planning decides what should happen; adapters and skills perform actions.
- Avoid hard-coded task examples in production logic unless they are explicit fixtures or documented contracts.
- Use deterministic behavior in tests where possible. For LLM-facing behavior, test schema validation, routing, safety gates, and fallback behavior separately from model quality.
- Comments may be Chinese or English, but should explain non-obvious robotics, safety, algorithmic, or integration logic.

## Testing Guidelines

Testing conventions should follow the toolchain introduced for FireClaw. Until then, do not assume the old PyTorch/unittest command set applies.

For agent and robotics changes, prefer focused tests for:

- skill schema validation and skill dispatch;
- planner outputs and task decomposition contracts;
- memory read/write behavior and retrieval filters;
- safety gate allow/block/escalate decisions;
- robot adapter dry-run behavior;
- simulator versus real-robot separation.

## Long-Running Work Policy

The user often works on long-running research and coding tasks across multiple agent sessions. Avoid repeating completed work unnecessarily.

For any multi-step task, including debugging, testing, refactoring, experiment analysis, paper-related code development, or research method design, maintain a persistent execution record under `memory/YYYY-MM-DD/`.

Each memory record should be detailed enough for a later agent to resume without rediscovering the same context. Prefer recording more concrete detail rather than terse summaries. Include:

- task goal
- commands already executed
- observed errors or test results
- files inspected
- files modified
- current hypothesis
- current conclusion
- next recommended step
- concrete timestamps for same-day updates and idea revisions
- important parameter values, thresholds, seeds, checkpoints, output paths, and metric values
- failed attempts and why they were rejected
- user preferences or decisions made during the session
- any remaining uncertainty or research-level concern

When starting work or resuming a task, first list date directories under `memory/` and identify the newest dates. Read memory records from only the most recent two date directories by default, then inspect the current diff/status if this directory is a git repository. Do not scan older memory unless the recent two days do not contain the relevant context, the user explicitly asks for older history, the recent records point to an older date, or the task clearly depends on older experimental results. Do not rescan the whole project from scratch unless the memory record does not exist, the record is clearly outdated, the project structure has changed significantly, or the user explicitly asks for a fresh full analysis.

Within the same date directory, records may contain multiple updates for the same topic. Prefer entries with explicit timestamps, and when ideas or decisions conflict, follow the latest timestamped update for that topic unless the user says otherwise.

When continuing previous work, first summarize:

- 当前进展：
- 已完成：
- 当前问题：
- 下一步：
- 需要运行的命令：

Prefer incremental continuation over restarting the same investigation.

## Research Expectations

The user is a graduate student building this as a research-oriented robotics and AI system, not as a toy implementation or course assignment. The long-term goal may include work suitable for high-quality robotics or AI venues. When discussing method design, module design, experiments, ablations, evaluation metrics, or paper writing, reason rigorously and consider the potential research contribution.

Always distinguish three levels:

1. Engineering correctness:
   - whether the code runs correctly;
   - whether interfaces, tests, dependencies, and deployment assumptions are correct;
   - whether the implementation matches the intended algorithm or agent behavior.

2. Research validity:
   - whether the method has a clear motivation;
   - whether the agent architecture is necessary for the robotics task;
   - whether the design has novelty compared with standard embodied-agent, robotics, task-planning, or multi-agent baselines;
   - whether experiments can support the claims;
   - whether ablations, safety evaluations, and real/simulated robot tests are sufficient;
   - whether the method could be criticized as heuristic, incremental, or lacking theoretical/experimental support.

3. Publication-level contribution:
   - what the core innovation is;
   - what problem gap it addresses;
   - why existing methods are insufficient;
   - what evidence is needed to make the claim convincing;
   - whether the current design is more suitable for a conference paper, journal paper, workshop paper, or engineering report.

Do not merely make code pass tests. If a change affects the research method, agent architecture, safety mechanism, interpretability, memory design, or claimed contribution, explicitly explain the research-level impact. Be honest and critical: if an idea is not strong enough for a top-tier venue, say so directly and suggest how to strengthen it. Do not exaggerate novelty or claim publication potential without evidence.

## Safety & Robotics Constraints

Firefighting robots operate in dangerous physical environments. Treat robot actions as safety-critical.

- Natural-language commands are not enough authority for unsafe or irreversible actions. Add explicit confirmation or safety gate logic where appropriate.
- Prefer fail-safe behavior: stop, hold position, request clarification, or escalate to the operator when state is uncertain.
- Skill metadata should document required sensors, robot state assumptions, environment assumptions, timeout behavior, and emergency stop behavior.
- Log decisions and skill invocations enough to reconstruct what happened after an incident.
- Keep simulation-only behavior separate from real-robot behavior. Never let a simulator default silently drive real hardware.
- Do not hard-code private deployment paths, robot credentials, network addresses, or secrets.

## Commit & Pull Request Guidelines

This directory may not be a git repository in early stages. If it is not, report that rather than assuming git commands are available.

When git is available:

- Inspect status before editing and do not overwrite user changes.
- Commit only when explicitly asked.
- Use concise, scoped commit subjects. Chinese commit messages are acceptable.
- PR or handoff summaries should include changed behavior, validation commands, affected modules, and known gaps.

## Security & Artifacts

Do not commit private datasets, large checkpoints, robot logs with sensitive site details, credentials, live maps, private network configs, or secrets. Prefer CLI arguments, config files with examples, and documented environment variables over hard-coded values.

Generated artifacts should go under clearly named output directories such as `results/`, `logs/`, `checkpoints/`, or `memory/`, with large or sensitive outputs kept untracked unless the user explicitly says otherwise.

<!-- CODEGRAPH_START -->
## CodeGraph

This project has a CodeGraph MCP server (`codegraph_*` tools) configured. CodeGraph is a tree-sitter-parsed knowledge graph of every symbol, edge, and file. Reads are sub-millisecond and return structural information grep cannot.

### When to prefer codegraph over native search

Use codegraph for **structural** questions: what calls what, what would break, where X is defined, what X's signature is. Use native grep/read only for **literal text** queries (string contents, comments, log messages) or after you already have a specific file open.

| Question | Tool |
|---|---|
| "Where is X defined?" / "Find symbol named X" | `codegraph_search` |
| "What calls function Y?" | `codegraph_callers` |
| "What does Y call?" | `codegraph_callees` |
| "How does X reach/become Y? / trace the flow from X to Y" | `codegraph_trace` |
| "What would break if I changed Z?" | `codegraph_impact` |
| "Show me Y's signature / source / docstring" | `codegraph_node` |
| "Give me focused context for a task/area" | `codegraph_context` |
| "See several related symbols' source at once" | `codegraph_explore` |
| "What files exist under path/" | `codegraph_files` |
| "Is the index healthy?" | `codegraph_status` |

### Rules of thumb

- **Answer directly; do not delegate exploration.** For "how does X work" or architecture questions, answer with 2-3 codegraph calls: `codegraph_context` first, then one `codegraph_explore` for the source of the symbols it surfaces. For a specific flow ("how does X reach Y"), start with `codegraph_trace` from -> to, then one `codegraph_explore` for the bodies.
- **Trust codegraph results.** They come from a full AST parse. Do not re-verify them with grep unless CodeGraph reports stale files or missing initialization.
- **Do not grep first** when looking up a symbol by name. `codegraph_search` is faster and returns kind, location, and signature in one call.
- **Do not chain `codegraph_search` + `codegraph_node`** when you just want context; `codegraph_context` is one call.
- **Do not loop `codegraph_node` over many symbols.** One `codegraph_explore` call returns several symbols' source grouped in a single capped call.
- **Index lag:** when a codegraph response starts with "Some files referenced below were edited since the last index sync", read those specific files for accurate content. Files not in that banner are fresh and codegraph is authoritative for them. `codegraph_status` also lists pending files under "Pending sync".
- For FireClaw work, use CodeGraph aggressively on OpenClaw analogues before designing new modules. Reuse the upstream shape when it already exists, then adapt only the parts that differ for firefighting robotics. Do not let an LLM invent a brand-new module shape for something OpenClaw already implements.

### If `.codegraph/` does not exist

The MCP server returns "not initialized." Ask the user: "I notice this project doesn't have CodeGraph initialized. Want me to run `codegraph init -i` to build the index?"
<!-- CODEGRAPH_END -->
