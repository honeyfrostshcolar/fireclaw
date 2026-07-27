# Planner Memory Retrieval Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every memory item admitted into mission planning is isolated by mission, runtime, sensitivity, requester scope, and reusable-knowledge approval.

**Architecture:** Add an explicit `MemoryRetrievalScope` to low-level ranked retrieval, then route all Planner-facing memory through a new `PlannerMemoryContextBuilder`. The builder reloads or reconstructs canonical records from authoritative FireClaw stores, applies current-mission and approved-knowledge quotas, validates plugin effects, and returns a content-free audit decision without blocking planning when memory is degraded.

**Tech Stack:** Python 3.10-compatible dataclasses and typing, SQLite FTS5 projection, JSONL authority stores, existing FireClaw mission/facade/lifecycle/plugin APIs, pytest-compatible plain test functions.

---

## Scope And File Map

Create:

- `src/fireclaw_core/memory/planner_memory_context.py`: Planner request/result types, admission policy, source orchestration, plugin canonicalization, quotas, warnings, and audit decision.
- `tests/test_planner_memory_context.py`: focused isolation, approval, plugin, degradation, and deterministic-diagnostic tests with no pytest dependency.

Modify:

- `src/fireclaw_core/memory/memory_index.py`: allow exact `sensitivity` filtering in indexed candidate queries.
- `src/fireclaw_core/memory/memory_retrieval.py`: require and validate `MemoryRetrievalScope`; apply scope at candidate query and result admission.
- `src/fireclaw_core/memory/memory_eval.py`: require an explicit scope for offline retrieval evaluation.
- `src/fireclaw_core/plugin/plugin_runtime.py`: add diagnostic hook execution APIs without changing existing APIs or result shapes.
- `src/fireclaw_core/mission/mission_agent.py`: delegate Planner memory assembly to the builder and append its guard decision before validator decisions.
- `src/fireclaw_core/mission/mission_runtime.py`: construct and inject the builder with the already-created facade and lifecycle store.
- `tests/test_memory_retrieval.py`: migrate retrieval calls to explicit fixture scope and add low-level isolation tests.
- `tests/test_memory_eval.py`: pass an explicit evaluation scope.
- `tests/test_plugin_runtime.py`: verify content-free callback failure diagnostics and legacy API compatibility.
- `tests/test_memory_learning_loop.py`: replace historical unscoped expectations with current-mission/runtime and approval-gated expectations.
- `tests/test_mission_agent.py`: verify fail-open planning and memory-context audit insertion.
- `tests/test_mission_runtime.py`: verify runtime wiring.
- `memory/2026-07-24/planner-memory-retrieval-isolation.md`: persistent execution record and verification results.

Do not modify storage formats, facade tool schemas, ANN behavior, R*Tree behavior, consolidation, entity resolution, or reusable-knowledge approval formats.

## Focused Test Runner

The user explicitly chose not to create a Python 3.11 environment or install pytest. New tests must therefore avoid importing pytest and remain ordinary pytest-compatible `test_*` functions. Use this runner for RED/GREEN checks:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 - <<'PY'
import inspect
import runpy
import tempfile
from pathlib import Path

for filename in (
    "tests/test_planner_memory_context.py",
    "tests/test_mission_runtime.py",
):
    namespace = runpy.run_path(filename)
    for name, function in sorted(namespace.items()):
        if not name.startswith("test_") or not callable(function):
            continue
        parameters = inspect.signature(function).parameters
        if not parameters:
            function()
        elif tuple(parameters) == ("tmp_path",):
            with tempfile.TemporaryDirectory() as directory:
                function(Path(directory))
        else:
            raise RuntimeError(f"Unsupported test signature: {filename}:{name}{inspect.signature(function)}")
        print(f"PASS {filename}:{name}")
PY
```

Expected GREEN output: one `PASS` line per selected test and exit code `0`.

Because `tests/test_memory_retrieval.py` currently imports pytest, use the repository's existing lightweight test-function strategy only after replacing its single `pytest.raises` use with a plain `try`/`except` assertion, or run its selected functions through the same runner once it imports without pytest.

## Task 1: Require Explicit Low-Level Retrieval Scope

**Files:**

- Modify: `src/fireclaw_core/memory/memory_index.py:43-55`
- Modify: `src/fireclaw_core/memory/memory_index.py:456-475`
- Modify: `src/fireclaw_core/memory/memory_retrieval.py:12-17`
- Modify: `src/fireclaw_core/memory/memory_retrieval.py:39-52`
- Modify: `src/fireclaw_core/memory/memory_retrieval.py:121-160`
- Create: `tests/test_planner_memory_context.py`
- Modify: `tests/test_memory_retrieval.py`

- [ ] **Step 1: Write failing scope validation and isolation tests**

Start `tests/test_planner_memory_context.py` with dependency-free helpers and these tests:

```python
from __future__ import annotations

from pathlib import Path

from fireclaw_core.memory.embodied_memory import EmbodiedMemoryEvent
from fireclaw_core.memory.memory_index import SqliteMemoryIndex
from fireclaw_core.memory.memory_retrieval import MemoryRetrievalScope, MemoryRetriever


def _indexed_event(
    *,
    event_id: str,
    mission_id: str,
    runtime_mode: str,
    sensitivity: str,
    note: str,
) -> dict:
    return EmbodiedMemoryEvent(
        event_id=event_id,
        mission_id=mission_id,
        event_type="outcome",
        payload={"note": note},
        runtime_mode=runtime_mode,
        source_type="test",
        observed_at="2026-07-24T00:00:00+00:00",
        sensitivity=sensitivity,
    ).to_mission_record().to_dict()


def _scope(
    *,
    mission_id: str = "mission-current",
    runtime_mode: str = "real",
    sensitivities: tuple[str, ...] = ("standard",),
) -> MemoryRetrievalScope:
    return MemoryRetrievalScope(
        mission_ids=(mission_id,),
        runtime_modes=(runtime_mode,),
        allowed_sensitivities=sensitivities,
    )


def test_memory_retriever_requires_explicit_scope(tmp_path: Path) -> None:
    retriever = MemoryRetriever(SqliteMemoryIndex(tmp_path / "memory.sqlite"))
    try:
        retriever.retrieve("smoke")
    except TypeError:
        return
    raise AssertionError("retrieve() accepted an unscoped query")


def test_memory_retriever_filters_mission_runtime_and_sensitivity(tmp_path: Path) -> None:
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    for record in (
        _indexed_event(
            event_id="allowed",
            mission_id="mission-current",
            runtime_mode="real",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="other-mission",
            mission_id="mission-old",
            runtime_mode="real",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="simulation",
            mission_id="mission-current",
            runtime_mode="simulation",
            sensitivity="standard",
            note="smoke route",
        ),
        _indexed_event(
            event_id="restricted",
            mission_id="mission-current",
            runtime_mode="real",
            sensitivity="restricted",
            note="smoke route",
        ),
    ):
        index.upsert(record)

    results = MemoryRetriever(index).retrieve("smoke", scope=_scope(), limit=10)

    assert [result.record_id for result in results] == ["allowed"]


def test_memory_retriever_admits_restricted_when_scope_allows_it(tmp_path: Path) -> None:
    index = SqliteMemoryIndex(tmp_path / "memory.sqlite")
    index.upsert(_indexed_event(
        event_id="restricted",
        mission_id="mission-current",
        runtime_mode="real",
        sensitivity="restricted",
        note="smoke route",
    ))

    results = MemoryRetriever(index).retrieve(
        "smoke",
        scope=_scope(sensitivities=("standard", "restricted")),
    )

    assert [result.record_id for result in results] == ["restricted"]
```

- [ ] **Step 2: Run the three tests and verify RED**

Run the focused runner with only the three Task 1 functions.

Expected: import failure for `MemoryRetrievalScope`, proving the new boundary does not yet exist.

- [ ] **Step 3: Add exact sensitivity filtering to the SQLite projection**

Add the filter entry and documentation:

```python
_FILTER_COLUMN_MAP: dict[str, str] = {
    # Existing entries remain unchanged.
    "sensitivity": "sensitivity",
}
```

Update `SqliteMemoryIndex.search()` supported keys to include `sensitivity`. Merge the new key into the existing dictionary; do not replace the other entries.

- [ ] **Step 4: Add and enforce `MemoryRetrievalScope`**

Add imports from `embodied_memory`:

```python
from fireclaw_core.memory.embodied_memory import (
    MEMORY_RUNTIME_MODES,
    MEMORY_SENSITIVITY_LEVELS,
)
```

Add this dataclass before `RetrievedMemory`:

```python
@dataclass(frozen=True)
class MemoryRetrievalScope:
    mission_ids: tuple[str, ...]
    runtime_modes: tuple[str, ...]
    allowed_sensitivities: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.mission_ids or any(
            not isinstance(value, str) or not value.strip()
            for value in self.mission_ids
        ):
            raise ValueError("mission_ids must contain non-empty strings")
        if not self.runtime_modes or set(self.runtime_modes) - MEMORY_RUNTIME_MODES:
            raise ValueError("runtime_modes must contain valid memory runtime modes")
        if (
            not self.allowed_sensitivities
            or set(self.allowed_sensitivities) - MEMORY_SENSITIVITY_LEVELS
        ):
            raise ValueError(
                "allowed_sensitivities must contain valid memory sensitivity levels"
            )

    def admits(self, value: dict[str, Any]) -> bool:
        return (
            value.get("mission_id") in self.mission_ids
            and value.get("runtime_mode") in self.runtime_modes
            and value.get("sensitivity") in self.allowed_sensitivities
        )
```

Change the public signature:

```python
def retrieve(
    self,
    query: str,
    *,
    scope: MemoryRetrievalScope,
    limit: int = 10,
) -> list[RetrievedMemory]:
```

Replace the unfiltered lexical candidate call with:

```python
lexical_hits = self._scoped_lexical_hits(
    query,
    scope=scope,
    candidate_limit=limit * 3,
)
```

Add:

```python
def _scoped_lexical_hits(
    self,
    query: str,
    *,
    scope: MemoryRetrievalScope,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    assert self._index is not None
    by_id: dict[str, dict[str, Any]] = {}
    for mission_id in scope.mission_ids:
        for runtime_mode in scope.runtime_modes:
            for sensitivity in scope.allowed_sensitivities:
                hits = self._index.search(
                    query,
                    filters={
                        "mission_id": mission_id,
                        "runtime_mode": runtime_mode,
                        "sensitivity": sensitivity,
                    },
                    limit=candidate_limit,
                )
                for hit in hits:
                    if scope.admits(hit):
                        by_id[str(hit["record_id"])] = hit
    ordered = sorted(
        by_id.values(),
        key=lambda hit: (
            str(hit.get("created_at") or ""),
            str(hit.get("record_id") or ""),
        ),
        reverse=True,
    )
    return ordered[:candidate_limit]
```

This supplies exact index filters and repeats the same check on hydrated index rows. The later Planner builder performs the independent JSONL-authority check.

- [ ] **Step 5: Migrate existing retrieval tests to explicit fixture scopes**

Import `MemoryRetrievalScope`, add a `_test_scope()` helper using mission `mission-1`, runtime `simulation`, and standard sensitivity, then change every test call to:

```python
results = retriever.retrieve("smoke", scope=_test_scope())
```

Ensure `_make_index_record()` includes embodied runtime and sensitivity metadata matching `_test_scope()`. Replace the file's one `pytest.raises(AttributeError)` assertion with explicit `try`/`except`, then remove the pytest import so the lightweight runner can import the module.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run all `test_memory_retriever_*` functions in
`tests/test_planner_memory_context.py`. Compile
`tests/test_memory_retrieval.py`, and inspect every migrated call with `rg`;
the existing class-based pytest suite is outside the lightweight runner.

Expected: all new scope tests print `PASS`; the existing test module compiles;
no unscoped retrieval call remains.

- [ ] **Step 7: Record the checkpoint without committing**

Run:

```bash
git diff --check -- \
  src/fireclaw_core/memory/memory_index.py \
  src/fireclaw_core/memory/memory_retrieval.py \
  tests/test_memory_retrieval.py \
  tests/test_planner_memory_context.py
```

Expected: no output. Candidate commit subject, only if the user later explicitly authorizes commits: `feat(memory): require scoped ranked retrieval`.

## Task 2: Migrate Offline Evaluation To Explicit Scope

**Files:**

- Modify: `src/fireclaw_core/memory/memory_eval.py:78-83`
- Modify: `src/fireclaw_core/memory/memory_eval.py:111`
- Modify: `tests/test_memory_eval.py`

- [ ] **Step 1: Write the failing evaluation-scope test**

Add a test that constructs two same-text records in different missions, calls `evaluate_retrieval(..., scope=_scope(mission_id="mission-current"))`, and asserts only the current mission ID can satisfy `expected_record_ids`.

- [ ] **Step 2: Run it and verify RED**

Expected: `TypeError` because `evaluate_retrieval()` does not accept `scope`.

- [ ] **Step 3: Require scope in evaluation**

Import `MemoryRetrievalScope` and change the signature and call:

```python
def evaluate_retrieval(
    retriever: MemoryRetriever,
    cases: list[dict[str, Any]],
    *,
    scope: MemoryRetrievalScope,
    limit: int = 5,
) -> EvalReport:
    # Existing case parsing remains unchanged.
    retrieved = retriever.retrieve(case.query, scope=scope, limit=limit)
```

Update every test call with an explicit fixture scope. Do not create a global or wildcard evaluation scope.

- [ ] **Step 4: Run the dependency-free evaluation contract and verify GREEN**

Place the executable RED/GREEN contract in
`tests/test_planner_memory_context.py`. Update `tests/test_memory_eval.py` for
the later formal pytest run, then compile it. Expected: the dependency-free
contract passes and the cross-mission expected ID remains missing. Do not claim
the pytest-dependent module was executed.

- [ ] **Step 5: Record the checkpoint without committing**

Candidate commit subject: `test(memory): scope offline retrieval evaluation`.

## Task 3: Add Content-Free Plugin Hook Diagnostics

**Files:**

- Modify: `src/fireclaw_core/plugin/plugin_runtime.py:85-88`
- Modify: `src/fireclaw_core/plugin/plugin_runtime.py:224-271`
- Modify: `tests/test_plugin_runtime.py`

- [ ] **Step 1: Write failing diagnostic API tests**

Add dependency-free RED/GREEN tests to
`tests/test_planner_memory_context.py`, and mirror the public compatibility
assertions in `tests/test_plugin_runtime.py` for the later formal pytest run:

```python
def test_memory_hook_diagnostics_report_callback_exception_without_message() -> None:
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="broken.memory",
        callback=lambda payload: (_ for _ in ()).throw(
            RuntimeError("restricted victim name")
        ),
    )

    report = runtime.run_memory_hooks_with_diagnostics("filter", {"memories": []})

    assert report.effects == ()
    assert [failure.to_dict() for failure in report.failures] == [{
        "plugin_id": "broken.memory",
        "hook_name": "filter",
        "exception_class": "RuntimeError",
    }]
    assert "restricted victim name" not in repr(report)


def test_existing_memory_hook_api_keeps_list_shape_on_callback_exception() -> None:
    runtime = PluginRuntime()
    runtime.register_callable(
        hook_type="memory",
        hook_name="filter",
        plugin_id="broken.memory",
        callback=lambda payload: (_ for _ in ()).throw(RuntimeError("private")),
    )

    assert runtime.run_memory_hooks("filter", {"memories": []}) == []
```

Add the equivalent provider diagnostic assertion for `enrich_context`.

- [ ] **Step 2: Run the new tests and verify RED**

Expected: `AttributeError` for the missing diagnostic methods.

- [ ] **Step 3: Add immutable diagnostic result types**

Add:

```python
@dataclass(frozen=True)
class PluginHookFailure:
    plugin_id: str
    hook_name: str
    exception_class: str

    def to_dict(self) -> dict[str, str]:
        return {
            "plugin_id": self.plugin_id,
            "hook_name": self.hook_name,
            "exception_class": self.exception_class,
        }


@dataclass(frozen=True)
class PluginHookRun:
    effects: tuple[dict[str, Any], ...]
    failures: tuple[PluginHookFailure, ...]
```

- [ ] **Step 4: Add diagnostic execution while preserving legacy APIs**

Refactor hook iteration into:

```python
def _run_hooks_with_diagnostics(
    self,
    hook_type: str,
    hook_name: str,
    payload: dict[str, Any],
    *,
    strict_result_type: bool,
) -> PluginHookRun:
    effects: list[dict[str, Any]] = []
    failures: list[PluginHookFailure] = []
    for plugin_id, callback in self._callables.get((hook_type, hook_name), []):
        try:
            result = callback(dict(payload))
        except Exception as exc:
            logger.warning(
                "Plugin '%s' %s hook '%s' raised an exception; skipping.",
                plugin_id,
                hook_type,
                hook_name,
                exc_info=True,
            )
            failures.append(PluginHookFailure(
                plugin_id=plugin_id,
                hook_name=hook_name,
                exception_class=type(exc).__name__,
            ))
            continue
        if result is None:
            continue
        if not isinstance(result, dict):
            if strict_result_type:
                raise ValueError(
                    f"Plugin '{plugin_id}' {hook_type} hook '{hook_name}' "
                    "must return a dict or None."
                )
            failures.append(PluginHookFailure(
                plugin_id=plugin_id,
                hook_name=hook_name,
                exception_class="TypeError",
            ))
            continue
        effects.append(PluginHookEffect(plugin_id, hook_name, result).to_dict())
    return PluginHookRun(tuple(effects), tuple(failures))
```

Keep existing methods returning lists:

```python
def _run_hooks(
    self,
    hook_type: str,
    hook_name: str,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    report = self._run_hooks_with_diagnostics(
        hook_type,
        hook_name,
        payload,
        strict_result_type=True,
    )
    return list(report.effects)
```

Add:

```python
def run_memory_hooks_with_diagnostics(
    self,
    hook_name: str,
    payload: dict[str, Any],
) -> PluginHookRun:
    return self._run_hooks_with_diagnostics(
        "memory", hook_name, payload, strict_result_type=False
    )


def run_provider_hooks_with_diagnostics(
    self,
    hook_name: str,
    payload: dict[str, Any],
) -> PluginHookRun:
    return self._run_hooks_with_diagnostics(
        "provider", hook_name, payload, strict_result_type=False
    )
```

- [ ] **Step 5: Run dependency-free plugin diagnostic tests and verify GREEN**

Expected: the new diagnostic functions in
`tests/test_planner_memory_context.py` pass. Statically compile
`tests/test_plugin_runtime.py`; do not claim its full pytest suite ran.

- [ ] **Step 6: Record the checkpoint without committing**

Candidate commit subject: `feat(plugin): expose content-free hook diagnostics`.

## Task 4: Build Current-Mission Memory Admission

**Files:**

- Create: `src/fireclaw_core/memory/planner_memory_context.py`
- Modify: `tests/test_planner_memory_context.py`

- [ ] **Step 1: Write failing request-validation and current-memory tests**

Add tests for:

1. Empty command, mission, requester, invalid runtime, invalid scope value, negative limit, and limit above `100` raise `ValueError`.
2. A standard embodied record matching mission/runtime is admitted.
3. Another mission, another runtime, restricted-without-scope, and missing embodied metadata are omitted.
4. Restricted memory is admitted with `memory.restricted.read`.
5. Corrections are admitted only for the current mission/runtime.
6. Facade events are canonicalized and deduplicated against indexed records.
7. `runtime_mode=None` produces no indexed, correction, facade, or reusable history.
8. Restricted-scope grant and reusable-source availability are reported even
   when the permitted source returns no records.

Use real `EmbodiedMemoryStore`, `MissionMemoryStore`, `MissionMemoryFacade`, and `MemoryRetriever` wherever possible. Use fixed timestamps and IDs.

- [ ] **Step 2: Run the new tests and verify RED**

Expected: import failure for `PlannerMemoryContextBuilder`.

- [ ] **Step 3: Add public request, warning, and result types**

Create:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fireclaw_core.infra.log_redaction import redact_dict
from fireclaw_core.memory.embodied_memory import (
    EMBODIED_METADATA_KEY,
    MEMORY_RUNTIME_MODES,
    EmbodiedMemoryEvent,
)
from fireclaw_core.memory.memory_lifecycle import MissionMemoryLifecycleStore
from fireclaw_core.memory.memory_retrieval import (
    MemoryRetrievalScope,
    MemoryRetriever,
)
from fireclaw_core.memory.mission_memory_facade import (
    MEMORY_RESTRICTED_READ_SCOPE,
    MemoryAccessContext,
    MissionMemoryFacade,
)
from fireclaw_core.mission.mission_memory import MissionMemoryRecord, MissionMemoryStore
from fireclaw_core.mission.mission_planning_audit import GuardDecision

MAX_PLANNER_MEMORIES = 100
MAX_PLANNER_CORRECTIONS = 100


@dataclass(frozen=True)
class PlannerMemoryContextRequest:
    command: str
    mission_id: str
    runtime_mode: str | None
    requester_id: str
    scopes: frozenset[str]
    max_memories: int = 5
    max_corrections: int = 3

    def __post_init__(self) -> None:
        for name, value in (
            ("command", self.command),
            ("mission_id", self.mission_id),
            ("requester_id", self.requester_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.runtime_mode is not None and self.runtime_mode not in MEMORY_RUNTIME_MODES:
            raise ValueError("runtime_mode must be a valid memory runtime mode")
        if any(not isinstance(scope, str) or not scope.strip() for scope in self.scopes):
            raise ValueError("scopes must contain non-empty strings")
        if not 0 <= self.max_memories <= MAX_PLANNER_MEMORIES:
            raise ValueError("max_memories must be between 0 and 100")
        if not 0 <= self.max_corrections <= MAX_PLANNER_CORRECTIONS:
            raise ValueError("max_corrections must be between 0 and 100")


@dataclass(frozen=True)
class MemoryContextWarning:
    code: str
    source: str
    count: int = 1
    record_id: str | None = None
    exception_class: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "code": self.code,
                "source": self.source,
                "count": self.count,
                "record_id": self.record_id,
                "exception_class": self.exception_class,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class PlannerMemoryContextResult:
    memories: tuple[dict[str, Any], ...] = ()
    corrections: tuple[dict[str, Any], ...] = ()
    warnings: tuple[MemoryContextWarning, ...] = ()
    omitted_counts: dict[str, int] = field(default_factory=dict)
    restricted_access_granted: bool = False
    reusable_knowledge_available: bool = False

    def guard_decision(self) -> GuardDecision:
        degraded = bool(self.warnings)
        return GuardDecision(
            layer="memory_context",
            status="allow",
            reason=(
                "memory_context_degraded"
                if degraded
                else "memory_context_validated"
            ),
            message=(
                "Planner memory context was validated with degraded sources."
                if degraded
                else "Planner memory context passed isolation checks."
            ),
            details={
                "accepted_memories": len(self.memories),
                "accepted_corrections": len(self.corrections),
                "omitted_counts": dict(sorted(self.omitted_counts.items())),
                "warning_codes": sorted({warning.code for warning in self.warnings}),
                "restricted_access_granted": self.restricted_access_granted,
                "reusable_knowledge_available": self.reusable_knowledge_available,
            },
        )
```

- [ ] **Step 4: Add builder initialization and canonical admission helpers**

Implement:

```python
class PlannerMemoryContextBuilder:
    def __init__(
        self,
        *,
        memory_retriever: MemoryRetriever | None = None,
        mission_memory: MissionMemoryStore | None = None,
        facade: MissionMemoryFacade | None = None,
        lifecycle: MissionMemoryLifecycleStore | None = None,
        plugin_runtime: Any | None = None,
    ) -> None:
        self._memory_retriever = memory_retriever
        self._mission_memory = mission_memory
        self._facade = facade
        self._lifecycle = lifecycle
        self._plugin_runtime = plugin_runtime

    @staticmethod
    def _allowed_sensitivities(request: PlannerMemoryContextRequest) -> tuple[str, ...]:
        if (
            "admin" in request.scopes
            or MEMORY_RESTRICTED_READ_SCOPE in request.scopes
        ):
            return ("standard", "restricted")
        return ("standard",)

    @staticmethod
    def _canonical_record(
        record: MissionMemoryRecord,
        request: PlannerMemoryContextRequest,
        allowed_sensitivities: tuple[str, ...],
    ) -> tuple[dict[str, Any] | None, str | None]:
        metadata = record.content.get(EMBODIED_METADATA_KEY)
        if not isinstance(metadata, dict):
            return None, "legacy_metadata_missing"
        runtime_mode = metadata.get("runtime_mode")
        sensitivity = metadata.get("sensitivity")
        if record.mission_id != request.mission_id:
            return None, "mission_scope_mismatch"
        if runtime_mode != request.runtime_mode:
            return None, "runtime_mode_mismatch"
        if sensitivity not in allowed_sensitivities:
            return None, "restricted_scope_denied"
        try:
            event = EmbodiedMemoryEvent.from_mission_record(record)
        except ValueError:
            return None, "legacy_metadata_missing"
        return {
            "memory_scope": "current_mission",
            "record_id": event.event_id,
            "record_type": event.event_type,
            "source_mission_id": event.mission_id,
            "runtime_mode": event.runtime_mode,
            "sensitivity": event.sensitivity,
            "content": redact_dict(dict(event.payload)),
            "advisory_only": True,
            "can_authorize_action": False,
            "requires_current_state_revalidation": True,
        }, None
```

Add `mission_scope_mismatch` and `authority_record_missing` to the stable warning-code set because candidate rows can be stale or maliciously mismatched. These codes contain no content.

- [ ] **Step 5: Implement current indexed records, facade events, and corrections**

The `build()` method must:

1. Return an empty valid result immediately when `runtime_mode is None`.
2. Load a current-mission authority map with `mission_memory.list_records(mission_id=...)`.
3. Call `memory_retriever.retrieve()` with one mission, one runtime, and derived sensitivities.
4. Reject any retrieved ID missing from the authority map.
5. Call `facade.get_current_context(MemoryAccessContext(...))`; reconstruct current envelopes from returned event IDs/payloads and deduplicate by record ID.
6. Search corrections with `mission_id=request.mission_id` and `record_type="correction"`, then pass every record through `_canonical_record`.
7. Catch each dependency independently and emit only the stable source warning and exception class.

Use one internal accumulator:

```python
def omit(
    code: str,
    source: str,
    *,
    count: int = 1,
    record_id: str | None = None,
    exception: Exception | None = None,
) -> None:
    omitted_counts[code] = omitted_counts.get(code, 0) + count
    warnings.append(MemoryContextWarning(
        code=code,
        source=source,
        count=count,
        record_id=record_id,
        exception_class=type(exception).__name__ if exception is not None else None,
    ))
```

Never pass `str(exception)` to warnings, logs, or audit details.

- [ ] **Step 6: Run current-mission tests and verify GREEN**

Expected: all Task 4 tests pass, and no returned item has a mission/runtime/sensitivity mismatch.

- [ ] **Step 7: Record the checkpoint without committing**

Candidate commit subject: `feat(memory): add planner context admission boundary`.

## Task 5: Add Approval-Gated Reusable Knowledge

**Files:**

- Modify: `src/fireclaw_core/memory/planner_memory_context.py`
- Modify: `tests/test_planner_memory_context.py`

- [ ] **Step 1: Write failing reusable-knowledge tests**

Add tests that use `MissionMemoryLifecycleStore.approve_knowledge()` and `revoke_knowledge()` to verify:

1. An approved `operator_preference` from another mission is admitted.
2. The original raw correction is not admitted across missions.
3. Revoked knowledge is absent.
4. Simulation-derived knowledge is absent in real mode unless approval includes `real`.
5. Current-mission items consume quota before reusable knowledge.
6. Lifecycle failure leaves current-mission items intact and adds `reusable_knowledge_unavailable`.
7. Returned knowledge content is the separately approved redacted payload and never follows `source_event_ids`.

- [ ] **Step 2: Run tests and verify RED**

Expected: reusable items are missing because the builder does not query lifecycle storage.

- [ ] **Step 3: Add authoritative reusable canonicalization**

Add:

```python
@staticmethod
def _canonical_knowledge(record: Any) -> dict[str, Any]:
    return {
        "memory_scope": "reusable_knowledge",
        "knowledge_id": record.knowledge_id,
        "knowledge_type": record.knowledge_type,
        "title": record.title,
        "tags": list(record.tags),
        "source_mission_id": record.source_mission_id,
        "runtime_mode": record.source_runtime_mode,
        "applicable_runtime_modes": list(record.applicable_runtime_modes),
        "content": redact_dict(dict(record.content)),
        "advisory_only": True,
        "can_authorize_action": False,
        "requires_current_state_revalidation": True,
    }
```

In `build()`, call:

```python
knowledge = self._lifecycle.list_knowledge(
    include_revoked=False,
    limit=MAX_PLANNER_MEMORIES,
)
```

Recheck `record.status == "approved"` and `request.runtime_mode in record.applicable_runtime_modes`. Reverse the lifecycle result for newest-first deterministic ordering. Do not load `source_event_ids`. Fill only:

```python
remaining = max(0, request.max_memories - len(current_memories))
selected_memories = current_memories[:request.max_memories]
selected_memories.extend(reusable_memories[:remaining])
```

- [ ] **Step 4: Run reusable-knowledge tests and verify GREEN**

Expected: approved applicable knowledge crosses missions; raw corrections and revoked/mismatched knowledge do not.

- [ ] **Step 5: Record the checkpoint without committing**

Candidate commit subject: `feat(memory): gate cross-mission planner knowledge`.

## Task 6: Reauthorize Plugin Filter, Rerank, And Enrichment

**Files:**

- Modify: `src/fireclaw_core/memory/planner_memory_context.py`
- Modify: `tests/test_planner_memory_context.py`

- [ ] **Step 1: Write failing plugin authority tests**

Add tests verifying:

1. Filter and rerank can remove or reorder known IDs.
2. Replacing `content` for a known ID has no effect because canonical content is restored.
3. A forged `operator_approved=true` item with an unknown ID is omitted.
4. An enrichment item can add a current authoritative record by `record_id`.
5. An enrichment item can add approved applicable knowledge by `knowledge_id`.
6. Revoked or runtime-mismatched knowledge IDs are rejected.
7. Plugin callback exceptions produce `plugin_filter_failed`, `plugin_rerank_failed`, or `plugin_enrichment_failed` with exception class only.
8. Final quota still prioritizes current-mission records over reusable knowledge.

- [ ] **Step 2: Run tests and verify RED**

Expected: plugin effects are not applied by the builder.

- [ ] **Step 3: Add ID-only canonical resolution**

Create a canonical map keyed by:

```python
("current_mission", record_id)
("reusable_knowledge", knowledge_id)
```

Resolve plugin values with:

```python
def _plugin_key(value: dict[str, Any]) -> tuple[str, str] | None:
    record_id = value.get("record_id")
    if isinstance(record_id, str) and record_id:
        return ("current_mission", record_id)
    knowledge_id = value.get("knowledge_id")
    if isinstance(knowledge_id, str) and knowledge_id:
        return ("reusable_knowledge", knowledge_id)
    return None
```

For filter/rerank, keep only keys already present in the pre-hook canonical map and replace every plugin object with the canonical object. Unknown keys increment `plugin_record_unverified`.

- [ ] **Step 4: Run diagnostic filter and rerank hooks**

Call:

```python
report = self._plugin_runtime.run_memory_hooks_with_diagnostics(
    hook_name,
    {"command": request.command, "memories": list(memories)},
)
```

Map each `report.failures` entry to the hook's stable warning code. Process effects sequentially so plugin order remains deterministic. A malformed effect is treated as an unverified effect, not as authority.

- [ ] **Step 5: Reauthorize provider enrichment**

Call `run_provider_hooks_with_diagnostics("enrich_context", ...)`. For every item:

- a `record_id` is reloaded from the full current-mission authority map and passed through `_canonical_record`;
- a `knowledge_id` is reloaded from the approved applicable lifecycle map;
- correction records go only to the correction collection;
- unknown, revoked, legacy, restricted, mission-mismatched, or runtime-mismatched items are omitted.

Ignore all plugin-provided `content`, `operator_approved`, `memory_scope`, `runtime_mode`, and `sensitivity` fields.

- [ ] **Step 6: Apply deterministic final quotas**

After hooks, deduplicate again. Select current-mission memories first, reusable knowledge second, and corrections independently:

```python
current = [
    item for item in memories
    if item["memory_scope"] == "current_mission"
]
reusable = [
    item for item in memories
    if item["memory_scope"] == "reusable_knowledge"
]
final_memories = current[:request.max_memories]
final_memories.extend(
    reusable[:max(0, request.max_memories - len(final_memories))]
)
final_corrections = corrections[:request.max_corrections]
```

- [ ] **Step 7: Run plugin admission tests and verify GREEN**

Expected: plugins control ordering/removal only; every returned object equals builder-created canonical content.

- [ ] **Step 8: Record the checkpoint without committing**

Candidate commit subject: `feat(memory): reauthorize planner memory plugins`.

## Task 7: Integrate Builder With MissionAgent And Planning Audit

**Files:**

- Modify: `src/fireclaw_core/mission/mission_agent.py:3-15`
- Modify: `src/fireclaw_core/mission/mission_agent.py:74-146`
- Modify: `src/fireclaw_core/mission/mission_agent.py:641-810`
- Modify: `tests/test_memory_learning_loop.py`
- Modify: `tests/test_mission_agent.py`

- [ ] **Step 1: Write failing Agent integration tests**

Add tests verifying:

1. `_retrieve_planner_context()` still returns `(memories, corrections)` for compatibility but no longer returns unscoped historical records.
2. Current mission/runtime corrections are returned.
3. A Builder source exception does not block a valid mission plan.
4. Raw plugin `enrich_context` memory is not appended after Builder validation.
5. A Planner audit record receives exactly one `memory_context` decision before the validator decision.
6. The decision contains counts and warning codes, not memory content or exception messages.

- [ ] **Step 2: Run tests and verify RED**

Expected: old `_retrieve_planner_context()` still performs unscoped retrieval and raw plugin enrichment.

- [ ] **Step 3: Inject or construct the Builder**

Add the typed constructor argument:

```python
planner_memory_context_builder: PlannerMemoryContextBuilder | None = None,
```

After existing memory fields are assigned:

```python
self.planner_memory_context_builder = (
    planner_memory_context_builder
    or PlannerMemoryContextBuilder(
        memory_retriever=memory_retriever,
        mission_memory=mission_memory,
        facade=(
            mission_memory_tools.facade
            if mission_memory_tools is not None
            else None
        ),
        lifecycle=memory_lifecycle,
        plugin_runtime=plugin_runtime,
    )
)
```

- [ ] **Step 4: Add an internal result-producing method and preserve the tuple wrapper**

Implement:

```python
def _build_planner_memory_context(
    self,
    command: str,
    *,
    mission_id: str,
    max_memories: int = 5,
    max_corrections: int = 3,
) -> PlannerMemoryContextResult:
    scopes = frozenset(
        self.operator.control_scopes
        if self.operator is not None
        else {"state.read"}
    )
    return self.planner_memory_context_builder.build(
        PlannerMemoryContextRequest(
            command=command,
            mission_id=mission_id,
            runtime_mode=self.embodied_runtime_mode,
            requester_id=(
                self.operator.operator_id
                if self.operator is not None
                else "mission-agent"
            ),
            scopes=scopes,
            max_memories=max_memories,
            max_corrections=max_corrections,
        )
    )


def _retrieve_planner_context(
    self,
    command: str,
    *,
    mission_id: str | None = None,
    max_memories: int = 5,
    max_corrections: int = 3,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if mission_id is None:
        return [], []
    result = self._build_planner_memory_context(
        command,
        mission_id=mission_id,
        max_memories=max_memories,
        max_corrections=max_corrections,
    )
    return list(result.memories), list(result.corrections)
```

Delete the old retrieval, working-memory append, facade append, correction search, and raw filter/rerank logic from this method.

- [ ] **Step 5: Use the Builder result in `plan_and_submit()`**

Replace tuple retrieval and the raw provider enrichment block with:

```python
memory_context_result = self._build_planner_memory_context(
    command,
    mission_id=mission_id,
)
context = MissionPlannerContext(
    available_robots=[
        entry
        for entry in self.registry.enabled_entries()
        if entry.robot_id in online_robot_ids
    ],
    retrieved_memories=list(memory_context_result.memories),
    operator_corrections=list(memory_context_result.corrections),
)
planning_result = self.planner.plan(command, context=context)
```

Do not invoke `enrich_context` anywhere else in `MissionAgent`.

- [ ] **Step 6: Append the memory decision before validator decisions**

Import `replace` from `dataclasses`. Immediately after planning:

```python
if planning_result.audit_record is not None:
    planning_result = replace(
        planning_result,
        audit_record=append_guard_decision(
            planning_result.audit_record,
            memory_context_result.guard_decision(),
            final_status=planning_result.audit_record.final_status,
            final_message=planning_result.audit_record.final_message,
            mission_id=mission_id,
        ),
    )
elif memory_context_result.warnings:
    logger.warning(
        "Planner memory context degraded",
        extra={
            "mission_id": mission_id,
            "warning_codes": sorted({
                warning.code for warning in memory_context_result.warnings
            }),
        },
    )
```

This must occur before `_with_validator_decision()` is called. The guard status remains `allow`; memory cannot authorize an action and memory degradation cannot block planning.

- [ ] **Step 7: Update learning-loop expectations**

Change tests that previously called `_retrieve_planner_context()` without `mission_id` or expected another mission's correction/outcome. Seed embodied metadata and pass the current mission/runtime for admitted cases. Convert future-mission operator preferences to `approve_knowledge(..., knowledge_type="operator_preference")`.

- [ ] **Step 8: Run Agent tests and verify GREEN**

Expected: current scoped context reaches the Planner; historical raw records do not; valid planning succeeds during memory degradation; audit ordering is memory context then validator.

- [ ] **Step 9: Record the checkpoint without committing**

Candidate commit subject: `feat(mission): isolate planner memory context`.

## Task 8: Wire Runtime Construction And Verify End To End

**Files:**

- Modify: `src/fireclaw_core/mission/mission_runtime.py:9-20`
- Modify: `src/fireclaw_core/mission/mission_runtime.py:73-204`
- Modify: `tests/test_mission_runtime.py`
- Modify: `memory/2026-07-24/planner-memory-retrieval-isolation.md`

- [ ] **Step 1: Write the failing runtime wiring test**

Build a runtime with `mission_memory`, `memory_index`, lifecycle paths, and `embodied_runtime_mode="real"`. Assert:

```python
assert agent.planner_memory_context_builder is not None
assert agent.planner_memory_context_builder._memory_retriever is agent.memory_retriever
assert agent.planner_memory_context_builder._mission_memory is agent.mission_memory
assert agent.planner_memory_context_builder._facade is agent.mission_memory_tools.facade
assert agent.planner_memory_context_builder._lifecycle is agent.memory_lifecycle
```

If avoiding private assertions, expose a content-free `status()` method returning booleans for configured sources and assert that instead.

- [ ] **Step 2: Run it and verify RED**

Expected: the runtime does not yet inject a prebuilt Builder.

- [ ] **Step 3: Construct and inject the Builder**

Initialize `facade: MissionMemoryFacade | None = None` before embodied-memory setup. After facade/lifecycle construction, create:

```python
planner_memory_context_builder = PlannerMemoryContextBuilder(
    memory_retriever=memory_retriever,
    mission_memory=memory_store,
    facade=facade,
    lifecycle=memory_lifecycle,
    plugin_runtime=plugin_runtime,
)
```

Pass it to `MissionAgent(...)`. Legacy runtime mode still receives a Builder, but its request runtime is `None`, so it cannot perform historical or cross-mission retrieval.

- [ ] **Step 4: Run all focused tests**

Run the lightweight runner across the new dependency-free isolation module and
the exact new module-level integration tests added to the non-pytest-dependent
Agent/runtime modules:

```text
tests/test_planner_memory_context.py
tests/test_mission_agent.py
tests/test_mission_runtime.py
```

Invoke only the new named Agent/runtime functions, not every pre-existing test
in those large modules. Compile all modified test files, including
`test_memory_retrieval.py`, `test_memory_eval.py`, `test_plugin_runtime.py`,
and `test_memory_learning_loop.py`.

Expected: every new isolation/integration function passes and all modified
tests compile. Record that the full pytest suite was not run by user choice;
do not install a new environment.

- [ ] **Step 5: Perform static and unscoped-call checks**

Run:

```bash
PYTHONPATH=src /home/lpp/miniconda3/envs/py310/bin/python3.10 -m compileall -q \
  src/fireclaw_core/memory \
  src/fireclaw_core/mission \
  src/fireclaw_core/plugin \
  tests/test_planner_memory_context.py \
  tests/test_memory_retrieval.py \
  tests/test_memory_eval.py \
  tests/test_plugin_runtime.py \
  tests/test_memory_learning_loop.py \
  tests/test_mission_agent.py \
  tests/test_mission_runtime.py
rg -n '\.retrieve\(' src tests
git diff --check
```

Expected:

- compileall exits `0`;
- every `MemoryRetriever.retrieve()` call includes `scope=`;
- `git diff --check` prints nothing.

- [ ] **Step 6: Inspect the final diff for safety invariants**

Confirm from the diff:

- no global or wildcard retrieval scope exists;
- no plugin payload becomes canonical content;
- no raw historical correction crosses missions;
- no source event is loaded when reusable knowledge is read;
- no warning or audit detail contains record content or exception messages;
- no memory result changes action authorization or bypasses current safety gates;
- memory dependency failure cannot change a valid plan into a blocked result.

- [ ] **Step 7: Update the persistent execution record**

Write:

```markdown
# Planner Memory Retrieval Isolation

## Goal
Enforce mission/runtime/sensitivity/approval isolation for Planner memory.

## OpenClaw/Emem Analogue
- Files and symbols inspected.
- Structure reused.
- Firefighting-specific safety adaptations.

## Commands
- Exact RED commands and observed failures.
- Exact GREEN commands and pass counts.
- Compile and diff checks.

## Files Modified
- Exact file list.

## Decisions
- Cross-mission corrections require approved operator-preference knowledge.
- Plugins may only reference authoritative IDs.
- Memory fails closed while planning fails open.

## Remaining Gaps
- Official Python 3.11/pytest suite was not run by user choice.
- Independent dense retrieval remains a separate subproject.
```

- [ ] **Step 8: Record the final checkpoint without committing**

Candidate commit subject: `feat(memory): enforce planner retrieval isolation`. Do not run `git commit` unless the user explicitly authorizes it.

## Final Acceptance Checklist

- [ ] `MemoryRetriever.retrieve()` has no default/global scope.
- [ ] SQLite candidates are exact-filtered by mission, runtime, and sensitivity.
- [ ] JSONL/facade authority is checked again before Planner admission.
- [ ] Current corrections cannot cross missions.
- [ ] Cross-mission operator preferences require approved reusable knowledge.
- [ ] Revoked and runtime-inapplicable knowledge is absent.
- [ ] Plugin filter/rerank/enrichment cannot forge IDs, content, approval, or scope.
- [ ] Callback failures create content-free warning/audit diagnostics.
- [ ] Current-mission evidence has quota priority.
- [ ] `MissionAgent` appends one non-blocking memory-context audit decision before validator decisions.
- [ ] Memory degradation does not block an otherwise valid plan.
- [ ] All focused Python 3.10 checks pass or unsupported tests are documented.
- [ ] No unrelated dirty-worktree changes were reverted or included.
