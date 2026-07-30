# Runtime Wrapper

Place only the thin wrapper needed to invoke the isolated algorithm Runtime.
Keep heavy dependencies owned by the Plugin package.

For ROS capabilities, prefer a Robot Adapter binding. For subprocess Tools,
use a structured JSON request/response contract without shell parsing.
