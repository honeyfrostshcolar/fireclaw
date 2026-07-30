# P1 Docker Sandbox Lifecycle Security

## Timestamp

- 2026-07-30 21:10:15 +08

## Task Goal

Close three Docker `computer_exec` risks:

1. killing only the `docker run` client on timeout could leave the container
   running;
2. `capture_output=True` retained unbounded stdout/stderr before truncation;
3. a mutable image tag was accepted without immutable identity verification.

## OpenClaw Analogues Inspected

- `openclaw/src/agents/sandbox/docker.ts`
  - named containers;
  - state inspection and `rm -f` lifecycle cleanup;
  - centralized Docker command execution;
  - config identity labels/hashes.
- `execDockerRaw` in the same file
  - applies `SANDBOX_COMMAND_MAX_BUFFER_BYTES` before converting output.

OpenClaw uses reusable session containers. FireClaw currently uses one fresh
container per short-lived process Tool invocation, so the lifecycle was
adapted to `create -> start --attach -> rm --force`.

## Implementation

### Dedicated Docker runtime

Added `src/fireclaw_core/agent/docker_sandbox.py`:

- `DockerSandboxRuntime`;
- `DockerImageIdentity`;
- `BoundedCommandResult`;
- `DockerLifecycleError`;
- `run_bounded_process`.

`ComputerSandbox` remains responsible for workspace and Tool contracts and now
delegates Docker lifecycle operations to this adapter.

### Named container and forced cleanup

Each invocation:

1. creates `fireclaw-exec-<uuid>`;
2. adds sandbox, invocation, workspace hash, and image ID labels;
3. uses `docker create --init`;
4. executes with `docker start --attach --interactive`;
5. always calls `docker rm --force <name>`.

The cleanup path runs after success, process failure, timeout, cancellation,
and Python exceptions. A cleanup failure raises `DockerLifecycleError`; it is
not reported as a successful or safely terminated Tool result.

### Bounded streaming capture

- stdout and stderr are drained concurrently by dedicated threads.
- Each stream retains at most `SandboxProfile.max_output_bytes`.
- Bytes beyond the limit are counted and discarded while the pipe continues
  to drain.
- Decoded output is separately capped by the existing `max_output_chars`.
- Hard configuration limits prevent `max_output_bytes` above 16 MiB per stream
  or `max_output_chars` above 1,000,000.
- Results record observed byte counts and per-stream truncation flags.

### Immutable image identity

`SandboxProfile` now supports `image_digest`.

- An enabled sandbox with an image but no digest fails during profile
  validation.
- The digest must match `sha256:<64 lowercase hex>`.
- Before each invocation, FireClaw runs `docker image inspect` on the
  human-readable image reference and requires `.Id == image_digest`.
- Container creation uses the immutable image ID rather than the mutable tag.
- The verified image ID and available repository digests are returned for
  execution audit.

The compatibility Agent CLI now requires
`--legacy-skill-sandbox-image-digest` whenever
`--legacy-skill-sandbox-image` is supplied.

## Files Added

- `src/fireclaw_core/agent/docker_sandbox.py`
- `tests/test_docker_sandbox_lifecycle.py`
- `docs/architecture/docker-sandbox-lifecycle-security.md`
- `memory/2026-07-30/p1-docker-sandbox-lifecycle-security.md`

## Main Files Modified

- `src/fireclaw_core/agent/computer_tools.py`
- `src/fireclaw_core/policy/deployment.py`
- `src/fireclaw_core/agent/agent_cli.py`
- `fireclaw.example.toml`
- `README.md`
- `docs/architecture/deployment-tool-policy.md`
- OpenClaw alignment and gap roadmap documents
- affected deployment, CLI, planner, sandbox, and path-policy tests

Existing uncommitted P0/P1/ROS changes were preserved.

## Verification

- Core focused lifecycle/policy/path/planner tests:
  - `46 passed`
- Compatibility CLI and executable Tool tests:
  - `36 passed`
- Broader Docker/Gateway integration set:
  - `133 passed in 45.57s`
- Python compile:
  - passed
- `git diff --check`:
  - passed before the final record update

The current machine does not have a `docker` executable:

```text
/bin/bash: docker: command not found
```

Therefore the runtime was verified with deterministic fake-Docker integration
tests and real subprocess pipe/timeout tests, but not against a live Docker
daemon. The deployment machine must build the image, record
`docker image inspect --format '{{.Id}}' <image>`, configure `image_digest`,
and run one live smoke test.

## Current Conclusion

Normal timeout/cancellation/error paths can no longer leave an untracked
container under the FireClaw runtime contract. Output is bounded while it is
read rather than after full buffering, and mutable tags no longer select the
executed image without an immutable ID match.

## Remaining Step

Completed:

- `/home/lpp/miniconda3/envs/py310/bin/python -m compileall -q src tests`
- `git diff --check`
- complete regression:
  - `1942 passed, 7 skipped in 164.37s`

No implementation or automated regression work remains for this item. A live
Docker smoke test remains a deployment-machine prerequisite because this host
does not have Docker installed. Commit only when explicitly requested.
