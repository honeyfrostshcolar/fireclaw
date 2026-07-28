# Python Environment Evaluation

## Goal

Evaluate whether the existing Conda environment
`/home/lpp/miniconda3/envs/py310` can become the primary FireClaw
development and RAG environment without duplicating the existing PyTorch/CUDA
installation.

## Environment facts

- Timestamp: 2026-07-28 09:xx +08
- `py310` Python: `3.10.4`
- `py310` size: `7.4G`
- Conda package cache size: `809M`
- `/home` free space: about `20G`
- `/` free space: about `44G`
- RAM: `14Gi` total, `8.4Gi` available at inspection time
- Swap: `9.3Gi` total, `8.9Gi` free
- Repository contract: `requires-python = ">=3.11"`
- Repository runtime dependency: `numpy>=1.26`
- `py310` NumPy before the temporary test: `1.24.3`
- `py310` has no pytest, FlagEmbedding, transformers, or
  sentence-transformers installed.
- `py310` PyTorch: `2.8.0+cu126`, installed by pip.
- `torch` directory size: about `1.6G`
- pip-installed NVIDIA runtime directory size: about `3.4G`
- The environment contains 364 extension modules whose filenames target
  CPython 3.10.

## Existing dependency-health result

`python -m pip check` is already non-clean. It reports missing JAX-related
dependencies for flax/optax/orbax-checkpoint, missing optional dependencies
for pybullet-planning, and inconsistent PyYAML metadata.

## Temporary compatibility test

Pytest and NumPy 1.26 were installed only under
`/tmp/fireclaw-py310-eval`; the Conda environment was not modified:

```bash
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pip install \
  --disable-pip-version-check \
  --no-cache-dir \
  --ignore-installed \
  --target /tmp/fireclaw-py310-eval \
  'pytest>=8,<9' \
  'numpy>=1.26,<2'
```

Sandboxed full-suite command:

```bash
PYTHONPATH=/tmp/fireclaw-py310-eval:/home/lpp/fireclaw-master/src \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest \
  tests -m 'not ros1_smoke'
```

Result:

```text
1437 passed, 169 failed, 6 deselected
```

Most failures were caused by the sandbox denying local sockets and by tests
that hard-code `.venv/bin/python`.

Representative host-side Gateway test:

```bash
PYTHONPATH=/tmp/fireclaw-py310-eval:/home/lpp/fireclaw-master/src \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest \
  tests/test_gateway.py::test_gateway_returns_health_and_state -q
```

Result:

```text
1 passed in 0.70s
```

Host-side full non-ROS suite:

```bash
PYTHONPATH=/tmp/fireclaw-py310-eval:/home/lpp/fireclaw-master/src \
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest \
  tests -m 'not ros1_smoke' -q --tb=short
```

Result:

```text
1547 passed, 59 failed, 6 deselected in 94.69s
```

The remaining failures were dominated by hard-coded `.venv/bin/python` and
`../../.venv/bin/python` subprocess paths. A few failures were current
behavior/assertion mismatches. No observed failure identified a Python
3.10-only incompatibility.

## Conclusion

Do not upgrade the existing environment's interpreter in place from Python
3.10 to Python 3.11. The environment contains many CPython 3.10 binary
extensions, including a roughly 5G pip-installed PyTorch/CUDA stack. An
in-place interpreter upgrade would invalidate those wheels and require
reinstallation anyway, while leaving a difficult-to-audit mixed environment.

The low-storage path is instead:

1. Keep `py310` on Python 3.10.
2. Deliberately make FireClaw support Python 3.10 by changing the project
   contract only after the remaining test-path assumptions are fixed.
3. Install pytest and NumPy 1.26 in place only after exporting the current
   package inventory and reviewing the pip dry-run.
4. Install an inference-only FlagEmbedding dependency set and reuse the
   existing PyTorch installation; do not install finetuning extras.
5. Run real CUDA tensor, BGE-M3 embedding, and reranker smoke tests before
   recording final dependency pins.

Model files and Hugging Face caches remain shared under
`/srv/lpp-extra/fireclaw`.

## FlagEmbedding dependency dry-run

The current `py310` environment does not contain FlagEmbedding. A
non-mutating pip dry-run was started for `FlagEmbedding==1.4.0`.

Confirmed before a network timeout stopped dependency resolution:

- Existing `torch 2.8.0+cu126` satisfies FlagEmbedding's declared
  `torch>=1.6.0`; pip did not plan a PyTorch replacement.
- Unconstrained resolution selected `transformers 5.14.1`.
- That Transformers release wanted `huggingface-hub>=1.5.0`, which would
  replace the currently pinned/download-tested `huggingface-hub 0.36.2`.
- The resolver also planned datasets, accelerate, sentence-transformers,
  peft, ir-datasets, sentencepiece, and protobuf dependencies.
- Dependency resolution timed out while backtracking over Transformers
  metadata; no package was installed.

The FireClaw inference environment should therefore constrain the
Transformers 4.x line and Hugging Face Hub version instead of installing
FlagEmbedding unconstrained. FlagEmbedding finetuning extras are not needed.

## 2026-07-28 10:41:52 +08 - In-place py310 upgrade and validation

The existing `py310` environment was upgraded in place with FireClaw's
constrained development and RAG extras:

```bash
/home/lpp/miniconda3/envs/py310/bin/python3.10 -m pip install \
  --no-cache-dir \
  --upgrade \
  --upgrade-strategy only-if-needed \
  -e '.[dev,rag]'
```

The existing CUDA build of PyTorch was reused and was not reinstalled.
Validated versions:

```text
Python 3.10.4
numpy 1.26.4
torch 2.8.0+cu126
CUDA runtime 12.6
transformers 4.57.6
huggingface-hub 0.36.2
FlagEmbedding 1.4.0
pytest 8.4.2
```

All relevant imports succeeded, including `BGEM3FlagModel` and
`FlagReranker`.

The host-side non-ROS test suite now reports:

```text
1602 passed, 5 failed, 6 deselected in 118.47s
```

The five remaining failures are existing implementation/assertion
mismatches, not dependency or Python 3.10 import failures:

- doctor memory-eval status expects `pass` but receives `warn`;
- two memory CLI tests omit the now-required `--mission-id`;
- two mission gateway client tests expect the old `admin` default scope.

Real offline GPU inference was validated separately for each model on the
NVIDIA GeForce GTX 1650, using `batch_size=1`, `max_length=128`, and FP16:

```text
BGE-M3 output shape: (1, 1024)
BGE-M3 finite values: true
BGE-M3 vector norm: 1.0001856088638306
reranker scores: [0.11224365234375, -11.03125]
reranker finite values: true
relevant firefighting passage ranked first: true
```

Model locations and pinned revisions:

```text
/srv/lpp-extra/fireclaw/models/bge-m3
revision 5617a9f61b028005a4858fdac845db406aefb181

/srv/lpp-extra/fireclaw/models/bge-reranker-v2-m3
revision 953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e

/srv/lpp-extra/fireclaw/models/model-revisions.json
```

The two model directories occupy 4.3G total. The upgraded `py310`
environment occupies 7.6G. The root filesystem has 41G free and `/home` has
18G free after installation.

`pip check` remains non-clean because this shared environment already has
unrelated incomplete JAX/flax/optax/orbax and `pybullet-planning`
installations, plus inconsistent PyYAML metadata. These packages were not
expanded because they are not part of FireClaw's RAG runtime and would add
unnecessary storage and compatibility risk.

## 2026-07-28 11:01:40 +08 - Final regression and storage recovery

The five initial assertion failures were verified as stale tests after the
memory authority and least-privilege changes:

- memory CLI fixtures now pass the required mission ID and carry explicit
  `_embodied.runtime_mode` and `_embodied.sensitivity` metadata;
- the doctor fixture now evaluates under its actual `m1` mission scope;
- mission gateway client tests now expect the least-privilege default scopes
  `mission.approve,state.read,task.submit`, not `admin`.

Missing runtime or sensitivity metadata remains fail-closed. Production
retrieval was not weakened to make legacy fixtures pass.

During the next full run, CodeGraph's background SQLite synchronization grew
`.codegraph/codegraph.db-wal` to approximately 14G and filled `/home`.
This caused six infrastructure failures with `OSError: [Errno 28] No space
left on device`, including a partially written task queue JSONL record.

Recovery actions:

1. Gracefully terminated the stale CodeGraph daemon that retained the moved
   WAL file.
2. Moved the index temporarily to `/tmp`, ran
   `PRAGMA wal_checkpoint(TRUNCATE)`, and restored it.
3. Reduced the CodeGraph directory from approximately 15G to 949M.
4. Restored `/home` to approximately 14G free.
5. Added task queue recovery semantics: a non-newline-terminated malformed
   final JSONL record is treated as an uncommitted append, while corruption
   before the final record still raises.
6. Isolated the operator console test's task queue under `tmp_path`.

CodeGraph remains readable, but `codegraph status` reports 1,391,267
references from the interrupted run awaiting resolution and 12,611 removed
files pending sync. A full `codegraph sync` was intentionally not run during
this environment task because it could create another large WAL on `/home`.

Final host-side non-ROS regression:

```text
1609 passed, 6 deselected in 116.97s
```

The six deselected tests are marked `ros1_smoke`; no ROS master or real robot
was started for this Python/RAG environment validation.

Final conclusions:

- `py310` is usable as the FireClaw development and RAG environment.
- Existing CUDA PyTorch was reused without reinstalling it.
- Both local BGE models load and infer successfully on the GTX 1650.
- The complete non-ROS suite passes under the upgraded environment.
- The post-upgrade package inventory is stored at
  `/srv/lpp-extra/fireclaw/environment-backups/py310-after-fireclaw-20260728/pip-freeze.txt`.
- Remaining `pip check` findings are unrelated pre-existing shared-environment
  debt, not FireClaw RAG dependency conflicts.

## 2026-07-28 11:30:23 +08 - CodeGraph source-scope configuration

Added `/home/lpp/fireclaw-master/codegraph.json` with root-relative exclusions
for `openclaw/` and `emem-main/`. This keeps the FireClaw root index from
duplicating the two child projects, each of which already has its own
`.codegraph` directory.

The JSON was validated successfully. No `codegraph sync` or `codegraph index`
was run because the existing root and OpenClaw indexes still contain state
from interrupted runs, and an uncontrolled rebuild on `/home` could recreate
the previous large WAL.

## 2026-07-28 11:37:41 +08 - OpenClaw core-only CodeGraph scope

Added `/home/lpp/fireclaw-master/openclaw/codegraph.json`. The OpenClaw index
retains `src/` and `packages/` so agent, session, tool, skill, memory, gateway,
plugin, routing, model, and state call paths remain connected. It excludes
platform apps, extensions, UI, docs, examples, deployment, QA, repository
automation, bundled skill content, and other non-core reference material.

The JSON was validated successfully. The interrupted
`openclaw/.codegraph` database was not rebuilt or synced in this step.

## 2026-07-28 11:46:48 +08 - Development-readiness status recheck

The user asked whether the previous work had completed and whether the project
is ready for development.

Current worktree still contains uncommitted implementation and test changes,
plus new RAG lifecycle/indexing/runtime files and CodeGraph configuration.
Therefore the branch is not in a committed/release state, but the current
working tree is functionally usable for continued development.

Verification rerun:

```bash
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/lpp/miniconda3/envs/py310/bin/python3.10 -m pytest \
  tests -m 'not ros1_smoke' -q --tb=short
```

Sandboxed run result:

```text
1490 passed, 119 failed, 6 deselected in 52.97s
```

Those failures were dominated by `PermissionError: [Errno 1] Operation not
permitted` from local HTTP socket tests. This is a managed-sandbox limitation
and should not be treated as a code regression.

The same command was rerun outside the sandbox with local socket permission:

```text
1609 passed, 6 deselected in 114.60s
```

Conclusion:

- The `py310` environment is usable for FireClaw development and RAG work.
- The complete non-ROS suite passes in the correct host execution context.
- ROS1 smoke tests remain intentionally deselected; no ROS master or real robot
  was started.
- The current work should be reviewed, optionally committed, and only then
  treated as a stable baseline for future branches.
