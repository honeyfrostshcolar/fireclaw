# Memory Retrieval v1 Design

## Goal

Add a stronger local memory retrieval layer so FireClaw can answer task-history questions beyond "latest records".

## Scope

This phase does not add vector search, embeddings, external databases, or LLM-based memory summarization. It implements deterministic structured retrieval over JSONL memory records.

## Retrieval Fields

`JsonlMemoryStore.search_records(...)` supports:

- `session_id`
- `status`
- `intent`
- `target_floor`
- `command_contains`
- `limit`

The search returns newest matching records first. Existing `latest_records(...)` remains unchanged.

## Agent Behavior

The agent recognizes memory retrieval questions such as:

- `之前二楼救人成功了吗`
- `上次失败原因是什么`
- `查一下二楼救人记录`

It returns:

- `status="retrieved"`
- `memory.query`
- `memory.records`
- a concise message with match count

Retrieval does not execute skills and does not append a new memory entry, matching current recall behavior.

## Query Parsing

Keep parsing deterministic:

- floor expressions like `二楼` or `2楼` map to `target_floor`;
- `成功` maps to `status="succeeded"`;
- `失败` maps to `status="failed"`;
- `救人` maps to `intent="rescue_victim"`;
- otherwise query can fall back to command substring when useful.

## Testing

Add tests for:

- memory store filtering by status, floor, intent, command substring, and session;
- agent answers session-scoped successful rescue memory queries;
- agent answers failure-history queries without executing skills;
- empty retrieval returns `retrieved` with no records.

## Research Impact

This is the first practical step from append-only memory toward OpenClaw-like memory retrieval. It creates a deterministic baseline before introducing semantic/vector memory.
