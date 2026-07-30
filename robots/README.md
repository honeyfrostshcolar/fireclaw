# FireClaw Robot Platforms

`robots/` contains robot-model and deployment assets used to exercise
FireClaw against real or simulated platforms.

Robot workspaces may provide:

- URDF/Xacro and meshes;
- Gazebo models, worlds, sensors, and controllers;
- TF, odometry, laser, and velocity-command interfaces;
- platform-specific maps and navigation parameters;
- trusted bringup launch files.

They do not register Agent Tools. Reusable capability Plugins and Runtime
algorithms remain under `extensions/`; the Robot Adapter connects those
capabilities to a selected platform.

Each imported upstream platform must pin its source repositories and commits.
Do not store nested Git repositories inside this repository.
