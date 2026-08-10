# FireClaw Embodied Evaluation

## Purpose and separation

FireClaw uses three independent evaluation lanes so that a failure can be
attributed to the correct layer:

1. `deterministic_integration`: deterministic planning with simulated Robot
   execution; measures Mission/Gateway/terminal/report/data-contract behavior.
2. `llm_planning`: model planning against frozen state and Tool snapshots;
   measures plan validity, safety, diagnosis, Tool use, tokens, cost, and model
   latency without ROS/Gazebo noise. It uses the production
   `LLMMissionPlanner` and read-only `MissionDeliberationRuntime`, which can
   compile proposals but cannot construct a Gateway, scheduler, Robot Adapter,
   ROS node, or physical dispatch.
3. `ros_gazebo_system`: frozen plans/policies against live ROS/Gazebo;
   measures navigation, collision, feedback, recovery, timing, and system
   reliability without treating those failures as LLM errors. The six live
   Gazebo acceptance lanes now feed the same versioned result protocol through
   a proof collector; the collector itself never starts ROS or Gazebo.

Results from the three lanes must not be pooled into one success rate.

Collision instrumentation also has an auxiliary
`gazebo_collision_calibration` positive-control lane. It is not a fourth task
benchmark: it creates no Mission or Robot task and is always excluded from task
success, terminal-outcome, recovery, and collision-free denominators. Its only
purpose is to prove that the measurement path can detect a known physical
contact instead of validating only zero-collision runs.

## Scenario contract

`fireclaw.evaluation.scenario-suite.v1` is a strict JSON contract. Each concrete
case records:

- suite/scenario ID and version;
- `train`, `development`, `validation`, or `test` split;
- seed and repeat index;
- command, task type, expected capability, and expected canonical outcomes;
- exactly one explicit target type:
  - `point`: `frame_id` plus finite `pose.x`, `pose.y`, optional `pose.yaw`;
  - `area`: `frame_id` plus `area_id`;
  - `entity`: `frame_id` plus `entity_id`;
- plan, dispatch, target-contract, terminal, final-report, and memory
  expectations.

`llm_planning` cases additionally freeze:

- the capture timestamp, Robot registry/presence, environment facts,
  reservations, memories, corrections, external knowledge, and exposed belief
  IDs;
- expected planning status, intent, task count/types, and required bounded-loop
  operations;
- maximum model calls and maximum deterministic safety rejections;
- `requires_terminal_status=false` and `expected_dispatch_success=null`, which
  prevents planning-only scores from being confused with execution success.

The development planning fixture contains point, area, and entity targets. The
area case exercises the real safety rule for area entry: the model must inspect
the frozen `passage_open` and `structural_stable` beliefs before proposing the
graph. The deterministic compiler then injects Robot allocation, completion
evidence, timeout, resources, and recovery policy without dispatching it.

Seeds and repetitions are expanded into unique `case_id` values. Unknown JSON
fields fail validation instead of being silently ignored. The old top-level
scenario array remains readable, but its normalized provenance is marked
`legacy_format=true`.

## Canonical outcome semantics

The evaluator reads terminal state from `GET /missions/{mission_id}/run`, not
from the compatibility trace. It retains separate counts for `completed`,
`blocked`, `escalated`, `failed`, `timed_out`, `cancelled`, and `lost`.

Two result fields must not be confused:

- `contract_passed`: the observed behavior matches the scenario expectation;
- `task_success`: the Mission outcome is `completed` and every dispatched Robot
  subtask succeeded.

Thus an expected `timed_out` safety test may pass its contract while still
counting as a task failure.

## Run bundle and provenance

Each invocation claims one new/empty run directory. The directory is never
reused, every attempted case is retained, and artifacts are finalized with
size and SHA-256. The bundle includes:

- normalized suite and per-case records;
- Mission Run, trace, events, task flow, session lineage, memory and report;
- Robot events and full task traces, including authorization, Tool/action, and
  evidence references;
- Plugin versions plus manifest/entrypoint digests and full Tool schemas;
- Git commit, branch, dirty diff/status and changed-file content hashes;
- Python, FireClaw and platform versions;
- metric definitions, numerator/denominator, sample count, standard deviation,
  confidence interval method, missing-data reasons, and outcome histogram;
- a compact paper summary that can be recomputed from `scenarios.jsonl` and
  `metric-definitions.json`.

For the deterministic lane, model/provider/token/cost and ROS/Gazebo/map fields
are explicitly recorded as not applicable rather than guessed. The LLM lane
records the requested and actual response model, provider identity, temperature,
scenario/model seed, full prompts, projected Tool schemas, Tool calls, finish
reason, usage, latency, optional catalog-based cost, and provider errors. A seed
is forwarded to the OpenAI-compatible request, but the bundle explicitly warns
that provider/model revisions and backend behavior can still be nondeterministic.
API keys are read from the shared config or its named environment variable and
are never written to the bundle. ROS/Gazebo fields remain explicitly not
applicable in this lane.

The ROS/Gazebo lane preserves the complete source proof, verifies every source
file and referenced map/world/config asset by SHA-256, and normalizes the live
point target, canonical Mission Run terminal, same-task authorization resume,
scheduler evidence, Plugin owner/backend, full Tool schema, feedback, stop
proof, diagnostics, recovery/escalation evidence, navigation parameters, and
ROS/Gazebo versions. Legacy proofs remain importable for engineering analysis,
but missing fields are reported; the collector never invents a version,
collision count, or Mission Run snapshot.

Collision evidence is fail-closed. The acceptance world loads a trusted
Gazebo `ContactManager` WorldPlugin before the first goal and records the full
normalized `/fireclaw/acceptance/contacts` stream through terminal stop. The
collector verifies the source asset, exact loaded shared-library hash, topic
publisher, observation window, raw-stream counts, robot scope, fixed filter
policy, per-record classification, and collision-episode links. Only
wheel/caster support contact with `ground_plane` is excluded; all other Robot
contacts are prohibited collisions. A scalar `collision_count: 0` without
that evidence is rejected as missing data.

The positive control dynamically spawns the repository-owned
`fireclaw_collision_calibration_probe` SDF through
`/gazebo/spawn_sdf_model` in a fixed shallow overlap with a stationary Burger.
It requires raw prohibited contact states and an episode involving the probe,
then removes it through `/gazebo/delete_model` and verifies model absence,
bounded Robot displacement, stopped odometry, no `/move_base` goal, and no
non-zero `/cmd_vel`. The independent scorer repeats the raw collision-pair
classification and verifies the SDF, observer binary, source proof, and asset
hashes; it does not trust a producer-side `detected=true` scalar.

## Deterministic integration command

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.embodied_eval \
  --scenarios tests/fixtures/embodied_eval/rescue_scenarios.json \
  --output-dir results/embodied-eval/<unique-run-id> \
  --adapter simulator
```

An output directory containing a previous run is rejected. Exit codes are
`0` for all contracts passed, `2` when one or more cases are retained as
warnings/failures, and `1` for configuration or runner errors.

## LLM planning command

```bash
cp fireclaw.example.toml fireclaw.toml
# Fill [provider].name, base_url, api_key/api_key_env, model, and optional catalog.
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.llm_planning_eval \
  --config fireclaw.toml \
  --scenarios tests/fixtures/embodied_eval/planning_scenarios.json \
  --output-dir results/embodied-eval/<unique-llm-run-id> \
  --temperature 0 \
  --model-catalog <optional-model-catalog.json>
```

The runner and Gateway resolve the same `[provider]` table. Explicit runner
flags override TOML values. The credential may be supplied by
`[provider].api_key` or by the environment variable named by
`[provider].api_key_env`; the secret is never written to the proof bundle.

For the frozen 15-case real-provider development baseline, replace the
scenario path with
`tests/fixtures/embodied_eval/planning_scenarios_multiseed_development.json`.
It expands point/area/entity over seeds `0,17,42,123,999`, is explicitly
`paper_ready=false`, and must not be reported as a held-out test set.

Each case retains `input.json`, the derived authoritative state snapshot,
planner context, deliberation attempts/observations, compiled plan/task graph,
full provider calls, Agent Harness traces, score breakdown, and Plugin
inventory. Run-level files add the union of exact planning Tool schemas and
their hashes. `unsafe_proposal_proxy_rate` is defined as a deterministic
runtime rejection proxy (invalid plan/observation or unexposed Tool call); it
must not be presented as an independently annotated unsafe-plan rate.

Planning protocol v2 applies executable target defaults before scoring: a
point target that omits optional `yaw` is canonicalized to `yaw=0.0`. The host
still rejects any response that does not contain exactly one Tool call and
executes none of those calls, but it may issue one immediate repair request for
that decision. A second invalid response escalates. Proof records keep every
rejected response and repair prompt. Report `first_try_clean_rate` and
`tool_protocol_valid_first_try_rate` as raw model behavior, and the conditional
`planning_recovery_rate` as runtime-assisted recovery; do not merge them into a
single success claim.

## ROS/Gazebo system command

The trusted live runner creates the source acceptance proof and then
automatically writes its normalized evaluation bundle below
`<run-dir>/evaluation/`:

```bash
FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development \
FIRECLAW_GAZEBO_ACCEPTANCE_REPEAT_INDEX=0 \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

To normalize existing proofs into one comparative run, repeat
`--source-proof` and provide one aligned `--repeat-index` per proof:

```bash
/home/lpp/miniconda3/envs/py310/bin/python \
  -m fireclaw_core.devtools.ros_gazebo_system_eval \
  --source-proof results/gazebo-acceptance/<success-run-id> \
  --source-proof results/gazebo-acceptance/<cancel-run-id> \
  --repeat-index 0 \
  --repeat-index 0 \
  --split development \
  --output-dir results/embodied-eval/<unique-system-run-id>
```

The system summary reports all seven canonical terminal outcome counts and
keeps these concepts separate:

- `contract_pass_rate`: expected behavior was observed, including an expected
  cancel, timeout, failure, or escalation;
- `task_success_rate`: the physical task actually reached `completed`;
- conditional safe-stop, diagnostics, recovery, and escalation rates whose
  denominators include only applicable scenarios;
- `collision_metric_missing_count`: missing collision instrumentation is
  unknown data, never a zero-collision result.

A case is `paper_evidence_complete` only when both the source execution and
collector repositories are clean, the split is frozen `validation` or `test`,
system/Plugin/Tool/navigation/Mission provenance is complete, collision
evidence is explicit, and the raw source proof is embedded. `--reference-only`
is useful for local inspection but can never produce a paper-ready archival
bundle.

## Gazebo collision positive-control command

Run the calibration with its dedicated scenario. The trusted runner selects
the separate offline evaluator automatically:

```bash
FIRECLAW_PYTHON=/home/lpp/miniconda3/envs/py310/bin/python \
FIRECLAW_GAZEBO_ACCEPTANCE_SCENARIO="$PWD/extensions/navigation-move-base/config/acceptance/collision-calibration.yaml" \
FIRECLAW_GAZEBO_ACCEPTANCE_RUN_ID=<unique-calibration-run-id> \
FIRECLAW_GAZEBO_ACCEPTANCE_SPLIT=development \
  extensions/navigation-move-base/tests/acceptance/run_gazebo_acceptance.sh
```

The source proof contains `collision-injection.json`, the complete raw contact
stream, collision evidence, spawn/delete service responses, pose/stop evidence,
and the loaded observer binary. Its evaluation appears under
`<run-dir>/evaluation/` with lane `gazebo_collision_calibration` and
`task_metrics_applicable=false`. A failed positive control means collision
counts from task runs must not be treated as calibrated until the measurement
fault is resolved.

## Research limits

The deterministic suite, scripted-provider planning tests, and system proof
collector establish engineering correctness and trustworthy measurement
plumbing. The three-case planning fixture is a development contract, not a
model benchmark. Contact instrumentation has dirty development smoke coverage
for all six task lanes and one known-collision positive control; neither is a
clean, repeated paper baseline. No real-model
paper baseline is claimed until a named provider/model is run on a clean commit
with repeated seeds. Publication claims still require frozen test splits and matched scorers
across no-diagnostics, summary-only, summary plus on-demand evidence, central
diagnosis, robot-local diagnosis, and bounded-recovery baselines, followed by
repeated ROS/Gazebo and limited real-robot trials. Human or independently
specified labels are also required for a defensible unsafe-proposal metric.
