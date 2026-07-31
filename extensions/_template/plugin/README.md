# Plugin Registration

Place the Plugin entrypoint and lifecycle registration here. The Plugin may
register multiple Tool definitions and bundle multiple Skills. The generic
loader calls `register(api)` from the path declared in
`fireclaw.plugin.json`.

Do not implement the robotics algorithm in the registration layer.
