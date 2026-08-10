cmake_minimum_required(VERSION 3.10.2)
project(fireclaw_gazebo_contact_monitor)

find_package(catkin REQUIRED COMPONENTS
  gazebo_msgs
  geometry_msgs
  roscpp
)
find_package(gazebo REQUIRED)

catkin_package(
  LIBRARIES fireclaw_gazebo_contact_monitor
  CATKIN_DEPENDS gazebo_msgs geometry_msgs roscpp
)

include_directories(
  ${catkin_INCLUDE_DIRS}
  ${GAZEBO_INCLUDE_DIRS}
)
link_directories(${GAZEBO_LIBRARY_DIRS})

add_library(fireclaw_gazebo_contact_monitor SHARED
  src/fireclaw_gazebo_contact_monitor.cpp
)
target_compile_features(fireclaw_gazebo_contact_monitor PUBLIC cxx_std_14)
target_link_libraries(fireclaw_gazebo_contact_monitor
  ${catkin_LIBRARIES}
  ${GAZEBO_LIBRARIES}
)

install(
  TARGETS fireclaw_gazebo_contact_monitor
  LIBRARY DESTINATION ${CATKIN_PACKAGE_LIB_DESTINATION}
)
