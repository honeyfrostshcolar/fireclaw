# Session Conversation State Design

## Goal

Add the first session/conversation state layer so FireClaw can group turns, recall records by session, and resolve a minimal multi-turn clarification.

## Scope

This phase stays deterministic and local. It does not add an LLM planner, vector memory, remote session service, or UI. Session state is persisted through existing JSONL memory records.

## Design

`FireClawAgent` accepts `session_id`, defaulting to `"default"`. Each task result includes:

- `session.session_id`
- `session.turn_index`
- `session.resolved_command`
- `session.context_used`

`turn_index` is derived from existing memory records in the same session. This keeps the session store local-first and avoids adding another persistence system.

`JsonlMemoryStore.latest_records()` accepts an optional `session_id`. Recall commands return recent records from the current session only when a session id is active.

Minimal multi-turn clarification:

- If the previous record in the same session has `status="clarify"`;
- and the new command contains a floor but not `救人`;
- then the agent resolves the command to `去<floor>救人` before planning.

This is intentionally narrow. It proves the session boundary without pretending to be a full dialogue manager.

## CLI

Add `--session-id` to the CLI. It passes the id into `FireClawAgent`.

## Testing

Add tests for:

- result and memory records include session metadata;
- turn index increments within a session;
- recall only returns current session records;
- two-turn clarification resolves `救人` followed by `二楼`;
- CLI accepts `--session-id`.

## Research Impact

Session state is a prerequisite for OpenClaw-like behavior: multi-turn clarification, operator correction, preference memory, and future LLM planner prompts all need a durable conversation boundary.
