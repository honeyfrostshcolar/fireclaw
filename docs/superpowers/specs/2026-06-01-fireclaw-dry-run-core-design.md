# FireClaw Dry-Run Core Design

## Goal

Build the first runnable FireClaw core as a pure Python dry-run system. The first version should prove the main agent loop:

1. accept a Chinese natural-language firefighting command;
2. decompose it with a deterministic rule/template planner;
3. validate the plan through a safety gate;
4. execute dry-run skills in order;
5. persist a task memory record.

The target demo command is `去二楼救人`.

## Scope

Included in the first version:

- Python package for the FireClaw core.
- Rule/template planner, not an LLM planner.
- Dry-run robot adapter, not ROS or real hardware.
- Minimal skill set required for a rescue command.
- JSONL-based local memory.
- Focused unit tests and one end-to-end agent run test.

Out of scope for the first version:

- ROS1/ROS2 integration.
- Real robot SDK integration.
- LLM-based task planning.
- Multi-robot coordination.
- Vector memory or semantic retrieval.
- UI, web server, or mobile client.
- Real hazard detection, fire suppression, mapping, SLAM, or perception algorithms.

## Architecture

The first implementation should create a small Python package named `fireclaw_core`.

### `fireclaw_core.agent`

Owns the top-level `FireClawAgent` orchestration flow.

Responsibilities:

- receive an operator command;
- call the planner;
- call the safety gate;
- execute the plan through the executor;
- write memory;
- return a structured run result.

The agent should not contain robot-specific side effects or skill logic. It coordinates components through explicit interfaces.

### `fireclaw_core.planner`

Contains a deterministic rule/template planner.

The first planner recognizes commands matching rescue intent with a floor target, including forms such as:

- `去二楼救人`
- `去2楼救人`
- `到二楼救人`
- `前往二楼救人`

The planner extracts the target floor and generates an ordered plan:

1. `navigate_to_floor`
2. `search_for_victims`
3. `assess_victim`
4. `report_status`
5. `return_to_safe_zone`

If the command cannot be parsed, the planner returns a clarification-needed result instead of producing an unsafe or guessed plan.

### `fireclaw_core.skills`

Defines the skill contract and registry.

Each skill should expose:

- `name`
- `description`
- input schema or input dataclass
- output schema or output dataclass
- dry-run execution function
- metadata for safety and observability

The registry maps skill names to implementations and provides lookup for the executor and safety gate.

The first built-in skills are:

- `navigate_to_floor`
- `search_for_victims`
- `assess_victim`
- `report_status`
- `return_to_safe_zone`

All built-in skills must be dry-run only. They may simulate results, but must not call hardware, ROS, network robot APIs, or local deployment-specific scripts.

### `fireclaw_core.robot`

Provides a dry-run robot adapter.

The adapter simulates robot actions such as navigation and status reporting. It should return structured results that look like future real robot adapter results, so the executor and skills do not need major changes when ROS2 is introduced later.

### `fireclaw_core.safety`

Contains the safety gate.

The first safety gate validates:

- the planner produced an executable plan;
- every step references a registered skill;
- the target floor is present and recognized;
- the run is explicitly in dry-run mode;
- no step requests real hardware execution.

Safety decisions should be structured as `allow`, `block`, or `clarify`.

### `fireclaw_core.executor`

Executes plan steps in order.

Responsibilities:

- resolve each skill from the registry;
- call the skill with validated inputs;
- collect step results;
- stop on failure;
- return a structured execution result.

The executor should write enough information for later memory and debugging, including step name, inputs, outputs, status, and error message.

### `fireclaw_core.memory`

Stores task records as local JSONL.

Each memory entry should include:

- timestamp;
- original operator command;
- parsed intent;
- generated plan;
- safety decision;
- step results;
- final status;
- dry-run flag.

The first version only needs append and list/read behavior. Search and semantic retrieval can be added later.

## Data Flow

For `去二楼救人`:

1. `FireClawAgent.run(command)` receives the command.
2. Planner extracts floor `2` and rescue intent.
3. Planner emits five ordered steps.
4. Safety gate checks the plan and registry.
5. Executor runs dry-run skills:
   - navigate to floor 2;
   - search for victims;
   - assess victim;
   - report status;
   - return to safe zone.
6. Memory appends the complete run record.
7. Agent returns a structured result to the caller.

## Error Handling

If the planner cannot parse the command, the agent should return a clarification result and should not execute any skills.

If the safety gate blocks the plan, the agent should return the safety reason and should not execute any skills.

If a skill is missing, the safety gate should block execution before the executor starts.

If a skill fails during execution, the executor should stop later steps, mark the run failed, and memory should record the partial execution.

If memory write fails, the agent should still return the execution result but include a memory error field. Memory failure should not be hidden.

## Testing Strategy

Add focused tests for:

- parsing `去二楼救人` into floor `2`;
- generating the expected five-step rescue plan;
- returning clarification for unknown or incomplete commands;
- blocking execution when a required skill is missing;
- executing all five dry-run skills successfully;
- writing a JSONL memory record after a completed run;
- guaranteeing dry-run skills do not call real robot APIs.

The first version should prefer deterministic unit tests. LLM quality, ROS integration, and real robot behavior are outside this test scope.

## Research and Engineering Rationale

This first version is an engineering foundation, not yet a publication-level research contribution. Its value is to establish clean boundaries for later research work:

- planner replacement with LLM or hybrid planning;
- skill abstraction for tested robotics algorithms;
- safety gate instrumentation;
- memory and execution log design;
- future comparison between rule-based and model-based task decomposition.

The design should stay small enough to test rigorously. Adding real robot integration or LLM planning too early would make failures harder to localize and would weaken the engineering base.

## Acceptance Criteria

The first implementation is accepted when:

- a Python test suite can run locally;
- `去二楼救人` produces and executes the five-step dry-run plan;
- unknown commands request clarification instead of executing;
- missing skills are blocked before execution;
- each completed run writes a structured JSONL memory entry;
- no first-version code path controls real hardware.

