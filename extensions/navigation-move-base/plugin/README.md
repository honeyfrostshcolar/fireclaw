# Plugin Registration

The move_base Navigation Plugin owns its navigation Tool contributions,
bundled Navigation Skill, Adapter bindings, and lifecycle metadata.

The current built-in `navigate_to_point` compatibility definition already
registers that Tool. Extend or replace it only through a deliberate Plugin
Host migration; do not create a parallel registry entry.
