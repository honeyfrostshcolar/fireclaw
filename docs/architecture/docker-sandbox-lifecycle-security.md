# Docker Sandbox Lifecycle Security

## Scope

This document defines the trusted lifecycle boundary for FireClaw
`computer_exec` and legacy executable Tool manifests. It complements the
deployment Tool policy and workspace path security policy.

## OpenClaw Analogue

FireClaw follows the relevant OpenClaw structure:

- `openclaw/src/agents/sandbox/docker.ts` gives containers deterministic names,
  inspects their state, removes stale containers with `rm -f`, and sends all
  Docker commands through one bounded runner;
- `execDockerRaw` applies `SANDBOX_COMMAND_MAX_BUFFER_BYTES`;
- sandbox configuration is included in the container config hash.

OpenClaw uses reusable session containers. FireClaw currently uses a fresh
short-lived container for each robotics Agent Tool invocation, so the same
principles are adapted to `create -> start --attach -> rm --force`.

## Lifecycle

For every process Tool call FireClaw:

1. checks that the configured image reference resolves to the configured
   immutable `image_digest`;
2. creates a unique `fireclaw-exec-<uuid>` container with audit labels;
3. starts and attaches to that exact container;
4. drains stdout and stderr concurrently;
5. terminates the Docker client on timeout or cancellation;
6. executes `docker rm --force <container-name>` for every terminal path;
7. returns a result only after cleanup succeeds.

If forced cleanup cannot be proven, the call raises `DockerLifecycleError`
instead of reporting successful or safely terminated execution.

## Workspace And Concurrency Bounds

The host workspace is mounted at `/workspace` as `readonly`. Container code
cannot persist arbitrary output into the bind mount. Durable changes must use
`computer_write_file`, which enforces a reserved internal path, regular-file
checks, per-file bytes, total workspace bytes, file count, atomic replacement,
and `expected_sha256` on overwrite.

Before process launch, FireClaw rechecks that the workspace contains no
symlinks or special files and remains within quota. A bounded semaphore limits
concurrent container invocations. The generic process sandbox accepts only
`network = "none"`; reviewed egress must be represented by a separate typed
Tool rather than Docker bridge access.

## Output Bound

`subprocess.run(capture_output=True)` is not used for container execution.
Dedicated pipe readers continuously drain stdout and stderr. Each stream tracks
the total observed byte count but retains at most `max_output_bytes` bytes.
Additional bytes are discarded while the pipe continues to drain, preventing
the child from blocking and preventing unbounded host memory growth. Decoded
tool output is then capped by the separate `max_output_chars` contract.

Gateway and model-provider responses use separate streaming byte limits, and
Gateway clients disable automatic redirects and environment proxy inheritance.
This prevents a peer from redirecting authenticated requests or forcing an
unbounded response allocation.

Results include:

- `stdout_bytes_observed` and `stderr_bytes_observed`;
- `stdout_truncated` and `stderr_truncated`;
- `timed_out` and `cancelled`;
- the ephemeral `container_name`;
- `container_cleanup_succeeded`;
- verified image identity metadata.

## Image Identity

The operator configures both:

```toml
image = "fireclaw-agent-sim:local"
image_digest = "sha256:<64-hex-docker-image-id>"
```

Obtain the image ID after the reviewed build:

```bash
docker image inspect --format '{{.Id}}' fireclaw-agent-sim:local
```

Before every invocation FireClaw inspects the human-readable image reference
and requires its current `.Id` to match `image_digest`. The container is then
created from the immutable image ID, not the mutable tag. A retagged or rebuilt
image therefore fails closed until an operator reviews and updates the digest.

## Remaining Deployment Responsibility

Digest pinning proves exact image content identity, not that the build pipeline
was trustworthy. Production deployments should additionally sign images,
publish an SBOM, scan dependencies, restrict the Docker registry, and admit
digest changes through an operator-controlled release process.
