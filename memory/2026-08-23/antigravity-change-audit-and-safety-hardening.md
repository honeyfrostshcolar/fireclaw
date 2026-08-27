# Antigravity change audit and safety hardening

## 2026-08-23 22:45:52 +0800

### Task goal

- Reconstruct the recent FireClaw changes attributed by the user to Antigravity.
- Audit whether the implementation matches the intended embodied-agent and safety semantics.
- Repair concrete regressions without overwriting the user's large existing dirty worktree.

### Attribution and workspace scope

- Git records the current branch and aggregate changes, but not which local editing tool authored each uncommitted line. The Antigravity attribution can therefore only be reconstructed from recent memory records, timestamps, new files, and the current diff; it cannot be proven line-by-line.
- Branch observed: `agent/embodied-evaluation-collision-calibration`, seven commits ahead of its remote at audit start.
- The initial tracked diff covered about 100 files with roughly 7,067 insertions and 1,345 deletions, plus substantial untracked implementation and test files. The worktree was preserved; no unrelated change was reset or deleted.

### Reconstructed recent change themes

- Simulation/real configuration separation, fail-closed real templates, simulation runtime preparation, and release bundle/catalog work.
- Gateway-owned multi-turn mission clarification and canonical planning dialogue.
- Server-owned, one-use sealed `PlanArtifact` confirmation with runtime/profile/readiness binding.
- Runtime identity and readiness evidence envelopes.
- Mission CLI readline/history, typed HTTP errors, SSE reconnect/follow/cancel, live activity display, and thinking trail.
- Mission/Robot live pose projection and relative target grounding.
- Scheduler relay of Robot Agent decisions, skill lifecycle, and navigation feedback.
- Web Console readiness and mission progress updates.

### OpenClaw analogue inspected

- Used CodeGraph against the local `openclaw/` index.
- Inspected OpenClaw wizard/TUI progress behavior, especially `src/wizard/session.ts` and TUI components using `truncateToWidth`.
- Reused the proven shape that progress labels change only in response to actual progress events and that terminal truncation is display-width-aware.
- FireClaw adaptation retains robot/mission safety events, SSE cursor recovery, Chinese/CJK terminal rendering, and fail-honest physical-state semantics.

### Problems found

1. `Ros1RobotAdapter.get_robot_state()` indirectly called `rospy.init_node()` from a read-only pose probe. Without a ROS master this could block indefinitely; an unsandboxed full test run stalled after 36 tests in ROS registration.
2. The real ROS adapter and Mission state projection silently substituted the Gazebo pose `(-2.0, -0.5, 0.0)` when live TF was unavailable. This presented simulation configuration as authoritative real-world evidence.
3. Relative target grounding flattened every robot's pose into one list and could bind robot A's task to robot B's pose. Non-default yaw grounding checked only x, not `(x, y, yaw)`.
4. `require_dispatchable_readiness()` ignored Fleet Doctor admission evidence. The first fix was intentionally tightened, then refined after integration tests proved that Fleet Doctor `safe_state` is currently always `unknown` and non-blocking warning-only degradation is an expected simulation state.
5. Preview and physical dispatch were initially using the same admission gate. Planning/preview should remain side-effect-free; fleet admission must be enforced at confirmation/dispatch.
6. Time-based spinner stages claimed that connection, LLM work, and safety checks were happening even when no such event had occurred. Fixed-width Python slicing also broke CJK/emoji and narrow terminals; `NO_COLOR` incorrectly disabled the spinner itself.
7. Scheduler progress deduplication mixed string event IDs with tuple message keys and suppressed distinct retry events carrying the same text. Some messages claimed recovery had started before a recovery decision existed.
8. Repeated reads of the same frozen Mission snapshot continued the LLM loop, causing another policy call instead of terminating the no-progress loop.
9. SSE reconnect diagnostics were routed to stdout instead of stderr.
10. `Ros1MoveBaseBackend.get_status()` called a missing `_current_map_pose()` method.
11. The deterministic TurtleBot3 simulation companion bundle content had changed, but the packaged catalog SHA-256 was stale.
12. Runtime history, SQLite indexes, and lock files were visible as untracked private artifacts.
13. Several test files had extra blank lines at EOF, causing `git diff --check` failures.

### Implemented fixes

- `src/fireclaw_core/agent/robot.py`
  - Removed hidden ROS node initialization from state probing.
  - Real ROS state now returns `pose=None` when the runtime/TF is unavailable.
  - A configured `initial_pose` fallback is compatibility-only and restricted to explicit ROS dry runs.
  - Deduplicated base-frame candidates and divided the total TF timeout across them.
- `src/fireclaw_core/mission/mission_state.py`
  - Removed hard-coded navigation/Gazebo pose synthesis.
  - Rejects Boolean, non-finite, incomplete, or frame-less poses.
- `src/fireclaw_core/mission/plan_artifact.py`
  - Added fleet admission enforcement for physical dispatch.
  - Allows only fresh `ready`, or fresh `degraded` with the specifically non-blocking `fleet_doctor_warnings` reason.
  - Keeps preview side-effect-free through `require_admission=False`, while still requiring fresh online robot evidence and binding the full admission revision.
  - Grounds relative pose only against the assigned robot's fresh map-frame `(x, y, yaw)` evidence.
- `src/fireclaw_core/mission/interactive.py`
  - Removed fabricated elapsed-time stages.
  - Added terminal-cell-aware CJK/emoji truncation and single-line sanitization.
  - Separated interactivity from color capability; `NO_COLOR` no longer disables activity indication.
  - Routed reconnect warnings to stderr and made monitor shutdown reusable.
- `src/fireclaw_core/mission/mission_scheduler.py`
  - Uses stable string dedupe keys; distinct event IDs remain distinct even when messages repeat.
  - Replaced speculative recovery claims with truthful “等待任务策略判定” wording.
- `src/fireclaw_core/mission/mission_deliberation.py`
  - Repeated unchanged state reads now terminate as `blocked/repeated_state_read` rather than consuming more LLM turns.
- `extensions/navigation-move-base/plugin/move_base.py`
  - Restored `_current_map_pose()` as a fail-honest TF wrapper without goal/config fallback.
- `src/fireclaw_core/resources/simulation_bundles/turtlebot3-burger-v1.json`
  - Updated deterministic archive SHA-256 to `9979e28cd5b464ed0876c43d6d3ea4b3c8163bceb7d2645447345c8dab06380f`.
- `.gitignore`
  - Added Mission CLI history, Mission SQLite/lock files, and embodied memory index patterns.
- Added or strengthened focused tests for every repaired boundary; corrected an integration test to declare a 65,536-token fake model context instead of relying on the deliberately conservative unknown-model default.

### Commands and observed results

- Recent memory and Git state/diff inspection were completed before edits.
- Root and `openclaw/` CodeGraph indexes were used before structural code exploration.
- Initial sandbox full suite:
  - `python -m pytest -q`
  - `2249 passed, 187 failed, 8 skipped`; failures were predominantly sandbox loopback-socket permission errors.
- Initial unsandboxed suite stalled after 36 passing tests because the new pose probe called `rospy.init_node()` without a ROS master; the run was interrupted after identifying the stack.
- First repaired focused suite: `73 passed`.
- Broader repaired focused suite: `107 passed`, with only two sandbox socket failures.
- Navigation/Gateway/readiness targeted run after refinement: `117 passed, 1 skipped`, then the remaining three regressions were fixed.
- LLM-context/SSE/no-progress targeted run: `42 passed, 1 skipped`.
- Deterministic bundle build:
  - 652 files
  - 8,750,503 compressed bytes
  - 34,487,269 unpacked bytes
  - SHA-256 `9979e28cd5b464ed0876c43d6d3ea4b3c8163bceb7d2645447345c8dab06380f`
- Bundle, user lifecycle, and embodied-eval run: `33 passed`.
- Final unsandboxed full suite:
  - `/home/lpp/miniconda3/envs/py310/bin/python -m pytest -q`
  - `2446 passed, 8 skipped in 290.21s`
- Final `git diff --check`: passed with no output.

### Engineering conclusion

- The current combined worktree is test-clean and the concrete safety/UX regressions found in this audit are repaired.
- The most serious resolved issue was treating a configured simulation pose as live physical evidence and allowing a read-only state endpoint to block on ROS initialization.
- No commit was created, and the pre-existing broad Antigravity/user worktree remains intact.

### Research/publication impact

- These changes improve engineering correctness, safety evidence integrity, and auditability. They do not by themselves constitute a research contribution.
- Robot-scoped evidence binding and fail-honest state projection are necessary foundations for credible experiments. Publication claims still require explicit uncertainty models, safety baselines, ablations, failure-injection metrics, and sim-to-real evidence.

### Remaining uncertainty and recommended next work

- Real ROS node lifecycle is still not explicitly owned by `FireClawGateway.start()`. The safe current behavior is unknown pose until an external/live ROS runtime has initialized the node. Add a bounded, explicit ROS runtime lifecycle/preflight contract before enabling real-mode plan confirmation.
- Audit Mission-to-Robot authorization binding: the scheduler confirms Robot actions by task/session, but the Mission plan digest is not yet visibly carried through that call boundary.
- Audit `structured_task_from_mission_subtask`, which currently projects a compatibility `risk_level="low"`; prove that downstream tool-level risk derivation cannot be weakened or migrate the sealed risk explicitly.
- Review the consume-before-submit ordering in plan confirmation so a scheduler submission exception cannot leave ambiguous artifact/run state.
- Before committing, split the very large dirty worktree into coherent commits (configuration separation, planning dialogue/sealed plan, terminal UX/progress, pose/readiness safety, simulation bundle/release) and inspect the two intentional tracked deletions.
