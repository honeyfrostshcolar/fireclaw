# Planner Tool Schema Serialization Design

## Goal

Serialize FireClaw skill metadata into stable planner/tool schemas and build a structured planner request payload for future LLM or local model clients.

## Scope

This phase does not call any model provider. It does not implement OpenAI-specific API calls. It creates model-agnostic JSON-compatible schema payloads that can later be adapted to OpenAI tools, local model prompts, or ROS-side planners.

## Design

Add `src/fireclaw_core/tool_schema.py` with:

- `skill_metadata_to_tool_schema(skill_metadata)`;
- `build_tool_schemas(skills)`;
- `build_planner_request(command, context)`;
- `planner_response_schema()`.

Each skill tool schema includes:

- `type="function"`;
- `function.name`;
- `function.description`;
- generic object `parameters`;
- FireClaw-specific metadata under `x-fireclaw`.

Because FireClaw does not yet have per-skill input schemas, `parameters` stays permissive:

```json
{
  "type": "object",
  "additionalProperties": true
}
```

The FireClaw metadata remains explicit so the model/client can reason about runtime, sensors, dry-run constraints, retries, and failure categories.

`build_planner_request()` returns a stable payload:

- `command`;
- `session`;
- `recent_records`;
- `tools`;
- `response_schema`;
- concise planner instructions.

`LLMToolCallingPlanner` will use `build_planner_request()` instead of manually building its request, while keeping backward-compatible request keys for existing tests.

## Testing

Add tests for:

- single skill metadata serializes into a tool schema;
- tool schemas are sorted by name;
- planner request includes tools, session context, recent records, response schema, and instructions;
- `LLMToolCallingPlanner` sends the enriched request to its client.

## Research Impact

This makes the planning interface auditable and reproducible. Future LLM experiments can report exactly what tool schemas and planner instructions were supplied, which is important for robotics-agent ablations and publication-quality evaluation.
