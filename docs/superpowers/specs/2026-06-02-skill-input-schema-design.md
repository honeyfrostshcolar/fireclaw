# Skill Input Schema Design

## Goal

Let FireClaw skills declare JSON-compatible input schemas so planner tool schemas can expose real parameters instead of generic objects.

## Scope

This phase adds schema metadata and validation. It does not enforce runtime input validation in the executor yet and does not add per-field UI forms.

## Design

Add `input_schema` to `Skill`, defaulting to:

```json
{
  "type": "object",
  "additionalProperties": true
}
```

Built-in skills declare explicit schemas:

- `navigate_to_floor`, `search_for_victims`, `assess_victim`, `report_status`: require integer `floor`.
- `return_to_safe_zone`: empty object, no additional properties.

Subprocess skill manifests may declare `input_schema`. The loader validates it as a JSON-schema-like object with `type="object"`. Full JSON Schema validation is out of scope; this phase keeps a small structural guard.

`SkillRegistry.list_metadata()` includes `input_schema`, and `skill_metadata_to_tool_schema()` uses it as `function.parameters`.

## Testing

Add tests for:

- built-in skill metadata includes input schemas;
- manifest loader accepts a valid `input_schema`;
- manifest loader rejects malformed `input_schema`;
- tool schema uses `input_schema` instead of the generic fallback.

## Research Impact

Typed skill inputs make LLM planning more auditable and reduce invalid skill calls. This is a necessary step before connecting a real model client.
