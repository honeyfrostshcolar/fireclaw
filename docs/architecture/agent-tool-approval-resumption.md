# Agent Tool Approval Resumption

Real-mode bounded mutation Tools use a host-owned approval and consumption
chain. The LLM never turns its own request into authority.

## Flow

1. Robot Agent proposes an `execute_agent_tool` operation.
2. `AgentToolRuntime` runs schema validation and `before_tool_call` hooks.
3. The runtime hashes the final arguments, plugin owner, Tool contract,
   deployment profile, and mission/task/robot context.
4. Without an exact grant, the Robot Agent result becomes
   `awaiting_confirmation`.
5. Robot Gateway persists an `authorization_kind=agent_tool` request in the
   authoritative SQLite WAL store.
6. An authenticated operator with `task.confirm` approves the request.
7. Robot Gateway atomically resolves the request and persists a signed,
   short-lived `ExecutionAuthorization`.
8. `/confirm` submits the same structured task again with that authorization.
9. Robot Gateway verifies the signature, issuer, expiry, robot identity, and
   signed hashes. `AgentToolRuntime` independently recomputes the final scope
   and action hash.
10. Immediately before the Tool handler, the runtime atomically records a
    stable one-time operation in `authorization_uses`.
11. Only then does the handler execute.

Changing the Tool arguments, plugin owner, Tool schema, deployment profile,
mission/task/robot context, or hook-adjusted arguments invalidates the grant.

## Replay And Crash Semantics

The one-time operation ID is derived from the authorization ID and exact
action hash. Concurrent or later attempts to use the same grant for the same
action fail with `agent_tool_authorization_already_used`.

Consumption happens before the side effect. If FireClaw crashes after
consumption but before it can record the Tool result, it does not retry the
mutation automatically. The Tool outcome is uncertain and must be reconciled
before a new authorization is issued. This is intentionally stricter than
ordinary computer-agent retry behavior because a Robot Agent can influence
robot-local state and later physical execution.
