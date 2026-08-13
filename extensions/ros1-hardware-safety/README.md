# ROS1 Hardware Safety Witness

This trusted, service-only Plugin closes FireClaw's real-robot resource
admission recovery boundary. It never registers an LLM-callable Tool. In a
`real` Robot Agent it reasserts a hardware-owned stop and reports short-lived
evidence from independent ROS channels.

A positive `hardware_stop_v1` report requires all of the following after the
stop acknowledgement:

- healthy hardware watchdog with its stop output asserted;
- physical emergency stop active;
- motor/actuator driver disabled;
- brake engaged when the robot declares one;
- the exact configured `sensor_msgs/JointState` actuator inventory stationary
  for at least three samples and 0.75 seconds;
- independent `nav_msgs/Odometry` stationarity over the same lower bound.

Missing topics, stale values, unknown actuators, insufficient samples, a
rejected stop request, or any measured motion returns `unknown`/`moving` and
keeps admission frozen. Restarting FireClaw does not change that decision.

The Plugin is disabled by default because topic names and message fields are
vendor contracts. Copy the real Profile example, bind every field to the
hardware driver's authoritative interfaces, and validate it on the actual
robot. `brake.required = false` must be an explicit engineering decision for
a platform without a controllable brake; it is not inferred automatically.

The field workflow is exposed as `fireclaw hardware-safety preflight`,
`accept`, `verify`, and `report`. Preflight never actuates. Only the
operator-confirmed `stop_proof` scenario invokes the stop service; all
negative scenarios use the non-actuating observer and must be established by
an operator under the vendor safe-test procedure. Every confirmed attempt is
written as a content-digested, write-once acceptance bundle. See
`docs/deployment/real-robot-hardware-safety-acceptance.md`.

The hardware e-stop remains asserted after FireClaw admission recovery.
Recovery only permits future tasks to request resources; it neither restarts
the old task nor clears the physical safety chain.
