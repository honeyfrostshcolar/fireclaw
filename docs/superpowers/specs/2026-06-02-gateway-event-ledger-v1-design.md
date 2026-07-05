# Gateway Event Ledger v1 Design

## Goal

Add task ids and append-only Gateway event records so FireClaw Gateway can expose task traces for status tracking, debugging, and incident review.

## Scope

- Add JSONL event ledger.
- Add `task_id` to Gateway task/confirm/cancel responses.
- Add event query endpoints.
- Derive first-version events from final agent results.

## Non-Goals

- No streaming or Server-Sent Events yet.
- No executor callbacks yet.
- No durable async task queue yet.
- No auth or operator identity yet.

## Event Shape

```json
{
  "event_id": "evt-...",
  "task_id": "task-...",
  "session_id": "operator-a",
  "type": "task.received",
  "timestamp": "...",
  "payload": {}
}
```

## Event Types

First version:

- `task.received`
- `task.planned`
- `safety.decided`
- `confirmation.pending`
- `confirmation.confirmed`
- `task.cancelled`
- `skill.succeeded`
- `skill.failed`
- `task.completed`

## Endpoints

- `GET /tasks/<task_id>`
- `GET /tasks/<task_id>/events`
- `GET /events/recent?session_id=<id>&limit=<n>`

`POST /tasks`, `POST /confirm`, and `POST /cancel` include `task_id` in their JSON result.

## Design Choice

Gateway v1 derives events from the completed `FireClawAgent.run(...)` result rather than modifying `PlanExecutor` with callbacks. This keeps the existing agent core stable and still provides useful task traces. Later versions can add live event streaming and step-start events from executor callbacks.
