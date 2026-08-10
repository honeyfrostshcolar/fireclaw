#include <functional>
#include <memory>
#include <string>

#include <gazebo/common/Events.hh>
#include <gazebo/gazebo.hh>
#include <gazebo/physics/Collision.hh>
#include <gazebo/physics/Contact.hh>
#include <gazebo/physics/ContactManager.hh>
#include <gazebo/physics/PhysicsEngine.hh>
#include <gazebo/physics/World.hh>
#include <gazebo_msgs/ContactState.h>
#include <gazebo_msgs/ContactsState.h>
#include <geometry_msgs/Vector3.h>
#include <geometry_msgs/Wrench.h>
#include <ros/ros.h>

namespace fireclaw_gazebo_contact_monitor
{
namespace
{
geometry_msgs::Vector3 ToVector(const ignition::math::Vector3d &value)
{
  geometry_msgs::Vector3 result;
  result.x = value.X();
  result.y = value.Y();
  result.z = value.Z();
  return result;
}

bool HasModelPrefix(const std::string &collision_name,
                    const std::string &model_prefix)
{
  return collision_name.compare(0, model_prefix.size(), model_prefix) == 0;
}
}  // namespace

class FireClawGazeboContactMonitor final : public gazebo::WorldPlugin
{
public:
  void Load(gazebo::physics::WorldPtr world, sdf::ElementPtr sdf) override
  {
    if (!ros::isInitialized())
    {
      gzerr << "FireClaw contact monitor requires gazebo_ros_api_plugin\n";
      return;
    }
    if (!world || !world->Physics())
    {
      gzerr << "FireClaw contact monitor requires an initialized physics engine\n";
      return;
    }

    this->world_ = world;
    this->robot_model_ = sdf->Get<std::string>(
        "robot_model", "turtlebot3_burger").first;
    this->model_prefix_ = this->robot_model_ + "::";
    this->topic_ = sdf->Get<std::string>(
        "topic", "/fireclaw/acceptance/contacts").first;
    this->update_rate_hz_ = sdf->Get<double>("update_rate", 50.0).first;
    if (this->robot_model_.empty() || this->topic_.empty() ||
        this->topic_.front() != '/' || this->update_rate_hz_ <= 0.0)
    {
      gzerr << "FireClaw contact monitor received invalid fixed configuration\n";
      return;
    }

    this->contact_manager_ = world->Physics()->GetContactManager();
    if (!this->contact_manager_)
    {
      gzerr << "FireClaw contact monitor cannot access ContactManager\n";
      return;
    }
    // Acceptance needs a complete stream even before ROS subscribers connect.
    this->contact_manager_->SetNeverDropContacts(true);
    this->ros_node_.reset(new ros::NodeHandle("/"));
    this->publisher_ = this->ros_node_->advertise<gazebo_msgs::ContactsState>(
        this->topic_, 100, false);
    this->update_connection_ = gazebo::event::Events::ConnectWorldUpdateEnd(
        std::bind(&FireClawGazeboContactMonitor::OnWorldUpdateEnd, this));

    ROS_INFO_STREAM("FireClaw Gazebo contact monitor publishes "
                    << this->topic_ << " for model " << this->robot_model_);
  }

private:
  void OnWorldUpdateEnd()
  {
    const gazebo::common::Time sim_time = this->world_->SimTime();
    if (this->last_publish_time_ != gazebo::common::Time::Zero &&
        (sim_time - this->last_publish_time_).Double() <
            1.0 / this->update_rate_hz_)
    {
      return;
    }
    this->last_publish_time_ = sim_time;

    gazebo_msgs::ContactsState message;
    message.header.stamp = ros::Time(sim_time.sec, sim_time.nsec);
    message.header.frame_id = "world";
    const unsigned int contact_count = this->contact_manager_->GetContactCount();
    for (unsigned int index = 0; index < contact_count; ++index)
    {
      gazebo::physics::Contact *contact =
          this->contact_manager_->GetContact(index);
      if (!contact || !contact->collision1 || !contact->collision2)
      {
        continue;
      }
      const std::string collision1 = contact->collision1->GetScopedName();
      const std::string collision2 = contact->collision2->GetScopedName();
      const bool robot_is_first = HasModelPrefix(collision1, this->model_prefix_);
      const bool robot_is_second = HasModelPrefix(collision2, this->model_prefix_);
      if (!robot_is_first && !robot_is_second)
      {
        continue;
      }

      gazebo_msgs::ContactState state;
      state.info = "FireClaw Gazebo ContactManager observation";
      state.collision1_name = collision1;
      state.collision2_name = collision2;
      for (int point = 0; point < contact->count; ++point)
      {
        geometry_msgs::Wrench wrench;
        const gazebo::physics::JointWrench &source = contact->wrench[point];
        wrench.force = ToVector(
            robot_is_first ? source.body1Force : source.body2Force);
        wrench.torque = ToVector(
            robot_is_first ? source.body1Torque : source.body2Torque);
        state.wrenches.push_back(wrench);
        state.total_wrench.force.x += wrench.force.x;
        state.total_wrench.force.y += wrench.force.y;
        state.total_wrench.force.z += wrench.force.z;
        state.total_wrench.torque.x += wrench.torque.x;
        state.total_wrench.torque.y += wrench.torque.y;
        state.total_wrench.torque.z += wrench.torque.z;
        state.contact_positions.push_back(ToVector(contact->positions[point]));
        state.contact_normals.push_back(ToVector(contact->normals[point]));
        state.depths.push_back(contact->depths[point]);
      }
      message.states.push_back(state);
    }
    this->publisher_.publish(message);
  }

  gazebo::physics::WorldPtr world_;
  gazebo::physics::ContactManager *contact_manager_{nullptr};
  gazebo::event::ConnectionPtr update_connection_;
  std::unique_ptr<ros::NodeHandle> ros_node_;
  ros::Publisher publisher_;
  std::string robot_model_;
  std::string model_prefix_;
  std::string topic_;
  double update_rate_hz_{50.0};
  gazebo::common::Time last_publish_time_;
};

GZ_REGISTER_WORLD_PLUGIN(FireClawGazeboContactMonitor)
}  // namespace fireclaw_gazebo_contact_monitor
