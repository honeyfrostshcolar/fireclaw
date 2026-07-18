# Operator Authorization v2 Design

## Goal

Add a Gateway-level authorization layer that records who may approve, confirm, or cancel safety-sensitive robot tasks.

## Scope

This version adds:

- approval-required control decisions for high/critical risk actions;
- pending approval records with expiry;
- supervisor/admin approval enforcement;
- task cancellation permission enforcement;
- audit events for approval requested, approved, expired, denied, and cancel denied.

This version does not add:

- passwords, tokens, signatures, or external identity providers;
- real operator accounts;
- cryptographic non-repudiation;
- UI approval workflows;
- ROS-side permission enforcement.

## Policy

Roles keep the current default scopes:

- `operator`: submit, confirm own low/medium work, cancel active tasks;
- `supervisor`: operator scopes plus `safety.override`;
- `admin`: supervisor scopes plus `emergency.stop`.

Risk-based approval:

- low/medium: allowed if base scope is present;
- high/critical: requires `safety.override`;
- operators without `safety.override` receive `status="approval_required"`.

## Gateway Flow

When a task finishes with `status="awaiting_confirmation"` due to safety confirmation, Gateway records an `AuthorizationRequest`:

```text
authorization.requested
-> pending approval indexed by session_id
-> /confirm with supervisor/admin approves if not expired
-> confirmation task runs
```

If approval expires, Gateway records `authorization.expired` and denies confirmation.

Normal cancel now evaluates `task.cancel`. Denied cancels record `task.cancel_denied` and do not set the task cancel event.

## Research/Safety Impact

This separates operator intent from physical-action authorization. It gives FireClaw a clearer human-in-the-loop safety model and an auditable responsibility chain before any real ROS1 transport is connected.
