# ROS1 Noetic Simulation Robot Shortlist

## Timestamp

- 2026-07-29, after the `move_base` upstream import and build.

## Task Goal

Find a small open-source Gazebo robot that can provide TF, odometry, LiDAR,
URDF, maps, and navigation parameters for testing FireClaw's
`navigate_to_point` Tool against ROS1 Noetic `move_base`.

The user asked to review options before downloading anything.

## Environment

- Ubuntu 20.04 Focal
- ROS1 Noetic
- Gazebo Classic 11.15.1
- `move_base` source already imported and built under
  `extensions/navigation-move-base/ros_ws/`
- Gazebo ROS packages are already installed.

## Candidates Checked

### 1. TurtleBot3 Burger - recommended

- Repositories:
  - `ROBOTIS-GIT/turtlebot3`: about 2k GitHub stars
  - `ROBOTIS-GIT/turtlebot3_simulations`: about 506 GitHub stars
- Both repositories have an explicit `noetic` branch.
- The Noetic simulation includes `turtlebot3_gazebo`, Gazebo worlds, Xacro
  robot descriptions, laser/odometry simulation, and a spawn launch file.
- `turtlebot3_navigation` explicitly depends on `amcl`, `map_server`, and
  `move_base`.
- Local apt simulation shows 12 additional packages would be installed.
- Best initial FireClaw integration baseline because it is small, documented,
  reproducible, and has the lowest dependency burden among maintained options.

### 2. Clearpath Husky

- Repository: `husky/husky`, about 507 GitHub stars.
- Includes description, control, Gazebo, navigation, simulator, and
  visualization packages.
- Official Noetic binary packages and simulation/navigation tutorials exist.
- Local apt simulation shows 25 additional packages.
- More representative of a rugged firefighting UGV but is a larger four-wheel
  skid-steer platform with more sensor and control dependencies.

### 3. Clearpath Jackal

- Repository: `jackal/jackal`, about 183 GitHub stars.
- `jackal/jackal` defaults to `noetic-devel`; official Noetic simulator and
  navigation binary packages/tutorials exist.
- The separate `jackal/jackal_simulator` repository defaults to
  `melodic-devel` and exposes no branch named `noetic`, so source provenance is
  less clean than TurtleBot3.
- Local apt simulation shows 22 additional packages.
- Useful second-stage rugged UGV test, but not the simplest first integration.

### 4. adipandas/indoor_bot

- About 40 GitHub stars.
- A genuinely compact differential-drive Gazebo robot with simulated 2D
  LiDAR, IMU, camera, SLAM, AMCL, and navigation demos.
- Developed for ROS Melodic and only claims that other ROS1 versions should
  work. Last major activity is old.
- Suitable for reading or borrowing a minimal layout, not the primary Noetic
  test platform.

## Rejected Despite Star Count

`linorobot/linorobot` has about 1.1k stars, but its ROS1 README lists only
Indigo and Kinetic, GitHub exposes no Noetic branch, and the project is more
focused on configurable real hardware. `linorobot2` is actively maintained but
uses ROS2/Nav2, so neither is a good match for the current ROS1 `move_base`
integration.

## Current Recommendation

Use TurtleBot3 Burger first:

1. download/pin the `noetic` branches of `turtlebot3`,
   `turtlebot3_msgs`, and `turtlebot3_simulations`;
2. build them in a robot-specific overlay workspace rather than modifying
   upstream `move_base`;
3. launch the stock Gazebo world and verify `/scan`, `/odom`, TF, `/cmd_vel`,
   and `/move_base`;
4. send a goal manually before connecting FireClaw;
5. then exercise FireClaw's `navigate_to_point` Tool and cancellation/safety
   paths.

Do not download a candidate until the user selects one.

## Manual Download Inspection

- Timestamp: 2026-07-29 22:56:58 +08
- Inspected path: `/home/lpp/turtlebot3-noetic-src/`
- Observed only one extracted directory: `turtlebot3-main/`, about 41 MB.
- `turtlebot3_msgs` and `turtlebot3_simulations` were absent.
- The downloaded `turtlebot3_description/package.xml` uses `ament_cmake`
  package format 3 and version `2.3.7`.
- The tree contains `turtlebot3_navigation2` and no
  `turtlebot3_navigation`.

Conclusion: the browser downloaded the repository's default ROS2
main/rolling branch, not the ROS1 `noetic` branch. It cannot be used with the
current ROS1 Noetic, catkin, and `move_base` integration. Keep it separate or
delete it manually; do not import it into FireClaw.

Required replacements:

- `turtlebot3` branch `noetic`
- `turtlebot3_msgs` branch `noetic`
- `turtlebot3_simulations` branch `noetic`
