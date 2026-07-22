# Merge rag-dev and fireclaw_memory into master

Date: 2026-07-22
Status: Complete locally; not pushed

## Task Goal

Merge the RAG and embodied-memory development branches into master while
preserving the newer mission-planning guard and audit behavior already on
master.

## Repository Finding

The three branches had unrelated Git histories:

- master root: 1334832
- rag-dev root: 8a5dd2c
- fireclaw_memory root: 4af5712

Both merges therefore required --allow-unrelated-histories. Shared snapshot
files were resolved in favor of master first, then feature-specific integration
files were restored from each development branch.

## Commands and Changes

- Checked status, branch graph, roots, merge bases, and tree diffs.
- Merged rag-dev with --no-commit and -X ours.
- Kept master mission-planning audit code.
- Added the complete src/fireclaw_core/rag package, RAG tests, evaluation data,
  reports, plans, specs, and development records.
- Restored the RAG dependency numpy>=1.26 in pyproject.toml.
- Created merge commit 6d19616 (Merge rag-dev into master).
- Merged fireclaw_memory with --no-commit and -X ours.
- Restored all memory integration changes relative to master across agent,
  execution, gateway, mission, safety, subagent, task, and memory modules.
- Added the embodied-memory implementation, eMEM reference tree, entity memory,
  consolidation, reconciliation, lifecycle, tools, tests, design docs, and
  development records.
- Updated the mission-memory record-type assertion for episode,
  entity_mention, and entity_resolution.

## Verification

- python -m compileall -q src/fireclaw_core: passed.
- Memory focused tests: 45 passed.
- RAG tests not requiring numpy: 21 passed.
- Cross-module regression: 210 passed, 2 failed.
- The two failures invoke workspace echo_policy through a python3 executable.
  This Windows host only exposes python, so both fail with WinError 2. The
  failure is environment-specific and is not caused by the merged memory path.
- Full RAG collection was blocked because numpy is not installed in the current
  Python environment. The dependency is declared in pyproject.toml.
- git diff --cached --check: passed after removing an extra EOF blank line.

## Conclusion and Next Step

Both feature histories and implementations are present on local master. Push
master only after reviewing the two local merge commits and, preferably,
installing project dependencies and running the full suite in the intended
development environment.
