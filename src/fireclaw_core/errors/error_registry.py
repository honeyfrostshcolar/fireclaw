"""FriendlyError registry mapping error codes to standardized friendly templates."""
from __future__ import annotations

from fireclaw_core.errors.friendly_errors import (
    FriendlyErrorTemplate,
    UNVERIFIED_ACTION_STATUS,
    UNVERIFIED_ROBOT_STATUS,
)

FRIENDLY_ERROR_REGISTRY: dict[str, FriendlyErrorTemplate] = {
    # ==========================================
    # 1. 传感器类 (Sensor Errors)
    # ==========================================
    "sensor_no_lidar_data": FriendlyErrorTemplate(
        error_code="sensor_no_lidar_data",
        severity="critical",
        what_happened="未接收到激光雷达 (LiDAR) 数据，主题 {topic_name} 无有效消息或已超时。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查激光雷达硬件供电、USB/以太网连接，以及雷达驱动节点是否正在运行。",
        suggested_actions=[
            {"label": "重启雷达驱动", "action": "restart_lidar"},
            {"label": "检查传感器状态", "action": "check_sensors"},
        ],
    ),
    "sensor_no_thermal": FriendlyErrorTemplate(
        error_code="sensor_no_thermal",
        severity="warning",
        what_happened="热成像/红外传感器无数据输入 ({topic_name})，无法获取火源热力分布。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查红外热像仪连接及温度传感器驱动。",
        suggested_actions=[
            {"label": "检查红外设备", "action": "check_thermal"},
            {"label": "降级至可见光搜寻", "action": "fallback_rgb"},
        ],
    ),
    "sensor_no_gas": FriendlyErrorTemplate(
        error_code="sensor_no_gas",
        severity="warning",
        what_happened="气体传感器（可燃气体/烟雾/有毒气体）未上报有效读数 ({sensor_id})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查气体采集模块串口通信与传感器探头预热状态。",
        suggested_actions=[
            {"label": "重置气体传感器", "action": "reset_gas_sensor"},
            {"label": "自检传感板", "action": "self_test_sensors"},
        ],
    ),
    "sensor_degraded": FriendlyErrorTemplate(
        error_code="sensor_degraded",
        severity="warning",
        what_happened="传感器 {sensor_name} 出现数据丢包、频次下降或噪声异常，感知质量降级。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查现场烟雾粉尘遮挡，清洁传感器镜头或校准传感器参数。",
        suggested_actions=[
            {"label": "清理标定", "action": "calibrate_sensor"},
            {"label": "继续降速运行", "action": "continue_degraded"},
        ],
    ),
    "sensor_timeout": FriendlyErrorTemplate(
        error_code="sensor_timeout",
        severity="warning",
        what_happened="传感器 {sensor_name} 在过去 {timeout} 秒内未更新数据，已超时。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查通信总线负载与传感器节点心跳。",
        suggested_actions=[
            {"label": "刷新连接", "action": "refresh_stream"},
            {"label": "诊断总线", "action": "diag_bus"},
        ],
    ),
    "sensor_unavailable": FriendlyErrorTemplate(
        error_code="sensor_unavailable",
        severity="warning",
        what_happened="请求的传感器设备 {sensor_id} 未就绪或未挂载。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="核对机器人硬件配置清单与已启用的传感器插件。",
        suggested_actions=[
            {"label": "查看硬件清单", "action": "view_hardware"},
            {"label": "重新扫描设备", "action": "rescan_devices"},
        ],
    ),
    "sensor_health_unknown": FriendlyErrorTemplate(
        error_code="sensor_health_unknown",
        severity="warning",
        what_happened="无法获取传感器 {sensor_name} 的健康诊断状态。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查 diagnostic_aggregator 或传感器健康监测节点。",
        suggested_actions=[
            {"label": "运行诊断检查", "action": "run_diagnostics"},
        ],
    ),
    "sensor_evidence_invalid": FriendlyErrorTemplate(
        error_code="sensor_evidence_invalid",
        severity="warning",
        what_happened="传感器证据数据格式不合法或校验和不匹配: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查传感器通信协议版本与数据帧解析器。",
        suggested_actions=[
            {"label": "重置帧同步", "action": "resync_frames"},
        ],
    ),

    # ==========================================
    # 2. ROS 通信类 (ROS Communication Errors)
    # ==========================================
    "ros_master_unreachable": FriendlyErrorTemplate(
        error_code="ros_master_unreachable",
        severity="critical",
        what_happened="无法连接到 ROS Master ({master_uri})，连接超时 ({timeout}s)。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="确认 roscore 是否已在目标主机启动，检查 ROS_MASTER_URI 及网络可达性。",
        suggested_actions=[
            {"label": "检查 roscore", "action": "check_roscore"},
            {"label": "重试连接", "action": "retry_connection"},
        ],
    ),
    "ros_topic_timeout": FriendlyErrorTemplate(
        error_code="ros_topic_timeout",
        severity="warning",
        what_happened="ROS 主题 {topic_name} 超过预设时间未发布新消息。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="使用 'rostopic hz {topic_name}' 确认是否有节点在发布数据。",
        suggested_actions=[
            {"label": "检查主题发布", "action": "check_topic"},
        ],
    ),
    "ros_action_aborted": FriendlyErrorTemplate(
        error_code="ros_action_aborted",
        severity="warning",
        what_happened="ROS Action 动作目标被服务端中止: {action_name}，原因: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查 Action Server 状态及导航/执行层返回的具体中止原因。",
        suggested_actions=[
            {"label": "重试动作", "action": "retry_action"},
            {"label": "重置 Action 客户端", "action": "reset_action_client"},
        ],
    ),
    "ros_reconnection_exhausted": FriendlyErrorTemplate(
        error_code="ros_reconnection_exhausted",
        severity="critical",
        what_happened="ROS 重连尝试次数已耗尽 ({attempts} 次)，未能恢复通信。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="排查网络物理链路、交换机状态及机器人主控机运行状况。",
        suggested_actions=[
            {"label": "手动强制重连", "action": "force_reconnect"},
            {"label": "查看网络配置", "action": "check_network"},
        ],
    ),
    "ros_graph_discovery_failed": FriendlyErrorTemplate(
        error_code="ros_graph_discovery_failed",
        severity="warning",
        what_happened="ROS 计算图拓扑探测失败，无法发现必需的节点或服务: {missing_nodes}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查 launch 文件是否完整启动，确认各节点命名空间配置是否正确。",
        suggested_actions=[
            {"label": "重新扫描节点", "action": "rescan_nodes"},
            {"label": "查看 rosnode list", "action": "list_nodes"},
        ],
    ),

    # ==========================================
    # 3. 网关与网络类 (Gateway & Network Errors)
    # ==========================================
    "gateway_connection_failed": FriendlyErrorTemplate(
        error_code="gateway_connection_failed",
        severity="critical",
        what_happened="无法连接至网关服务 ({gateway_url})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查网关守护进程是否在运行，验证端口与防火墙规则。",
        suggested_actions=[
            {"label": "重启网关服务", "action": "restart_gateway"},
            {"label": "检查网络连通性", "action": "ping_gateway"},
        ],
    ),
    "gateway_request_failed": FriendlyErrorTemplate(
        error_code="gateway_request_failed",
        severity="warning",
        what_happened="网关请求失败 ({endpoint}): {error_message}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查请求参数是否符合 API 规范，查看网关日志中的详细错误。",
        suggested_actions=[
            {"label": "重发请求", "action": "retry_request"},
        ],
    ),
    "network_disconnect": FriendlyErrorTemplate(
        error_code="network_disconnect",
        severity="critical",
        what_happened="与指挥中心/操作端的主网络连接已断开 ({interface})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查无线基站覆盖、现场中继器及机载无线网卡。",
        suggested_actions=[
            {"label": "搜索可用网络", "action": "scan_wifi"},
            {"label": "启用返航模式", "action": "enable_rth"},
        ],
    ),
    "gateway_crash": FriendlyErrorTemplate(
        error_code="gateway_crash",
        severity="critical",
        what_happened="网关子进程意外崩溃或退出 (退出码: {exit_code})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="查看崩溃栈日志 (/var/log/fireclaw/gateway.log) 定位崩溃根因。",
        suggested_actions=[
            {"label": "手动拉起网关", "action": "start_gateway"},
            {"label": "导出崩溃转储", "action": "dump_logs"},
        ],
    ),

    # ==========================================
    # 4. 安全门与授权类 (Safety & Authorization Errors)
    # ==========================================
    "authorization_missing": FriendlyErrorTemplate(
        error_code="authorization_missing",
        severity="warning",
        what_happened="执行此操作需要操作员授权，但当前未提供有效令牌或审批。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="请通过操作端或控制台提交审批申请，或输入授权 Token。",
        suggested_actions=[
            {"label": "申请授权", "action": "request_auth"},
            {"label": "取消操作", "action": "cancel_op"},
        ],
    ),
    "authorization_denied": FriendlyErrorTemplate(
        error_code="authorization_denied",
        severity="warning",
        what_happened="操作请求已被操作员或安全策略明确拒绝: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="核对权限等级或联系现场指挥员重新评估任务。",
        suggested_actions=[
            {"label": "确认并返回", "action": "ack_return"},
        ],
    ),
    "safety_blocked": FriendlyErrorTemplate(
        error_code="safety_blocked",
        severity="critical",
        what_happened="安全规则拦截了危险操作: {rule_name} (原因: {reason})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="评估现场安全边界，消除物理隐患或调整安全规则阈值。",
        suggested_actions=[
            {"label": "查看拦截详情", "action": "view_safety_rule"},
            {"label": "请求安全复核", "action": "request_safety_override"},
        ],
    ),
    "safety_requires_confirmation": FriendlyErrorTemplate(
        error_code="safety_requires_confirmation",
        severity="info",
        what_happened="当前任务涉及高风险操作 ({action_description})，需要人工二次确认。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="仔细核对现场视频与参数，确认无误后点击'确认执行'。",
        suggested_actions=[
            {"label": "确认执行", "action": "confirm_action"},
            {"label": "放弃执行", "action": "abort_action"},
        ],
    ),
    "real_robot_blocked": FriendlyErrorTemplate(
        error_code="real_robot_blocked",
        severity="warning",
        what_happened="真实机器人执行门禁已开启，非实机认证模式下禁止下发驱动指令。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="如需在实机上执行，请配置 --allow-real-robot 标志并完成实机安全确认。",
        suggested_actions=[
            {"label": "切换至仿真模式", "action": "switch_sim"},
            {"label": "实机认证解锁", "action": "unlock_real_robot"},
        ],
    ),
    "emergency_stop_active": FriendlyErrorTemplate(
        error_code="emergency_stop_active",
        severity="critical",
        what_happened="急停 (E-Stop) 按钮被按下或软件急停被触发: {source}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="排除现场紧急情况后，旋转旋钮释放物理急停，并在控制台执行复位。",
        suggested_actions=[
            {"label": "解除急停状态", "action": "clear_estop"},
            {"label": "全系统自检", "action": "system_self_test"},
        ],
    ),

    # ==========================================
    # 5. 执行与任务类 (Execution & Task Errors)
    # ==========================================
    "planner_failed": FriendlyErrorTemplate(
        error_code="planner_failed",
        severity="warning",
        what_happened="任务规划器未能为目标 '{goal}' 生成可行的分解路径: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="尝试提供更详细的自然语言指示，或分步下达子任务。",
        suggested_actions=[
            {"label": "重新规划", "action": "replan"},
            {"label": "简化任务目标", "action": "simplify_goal"},
        ],
    ),
    "skill_execution_failed": FriendlyErrorTemplate(
        error_code="skill_execution_failed",
        severity="warning",
        what_happened="技能 '{skill_name}' 执行中断或失败: {error_message}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="查看技能执行日志或尝试重新执行该技能。",
        suggested_actions=[
            {"label": "重试技能", "action": "retry_skill"},
            {"label": "跳过此技能", "action": "skip_skill"},
        ],
    ),
    "action_failed": FriendlyErrorTemplate(
        error_code="action_failed",
        severity="warning",
        what_happened="底层原子动作 '{action_name}' 执行失败: {detail}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查机器人机构物理限位或重置执行器驱动。",
        suggested_actions=[
            {"label": "单步重试", "action": "retry_atomic_action"},
        ],
    ),
    "task_cancelled": FriendlyErrorTemplate(
        error_code="task_cancelled",
        severity="info",
        what_happened="任务 '{task_id}' 已被操作员或系统策略取消。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="操作员可以下达新的搜救或巡检任务。",
        suggested_actions=[
            {"label": "创建新任务", "action": "new_task"},
        ],
    ),
    "task_timed_out": FriendlyErrorTemplate(
        error_code="task_timed_out",
        severity="warning",
        what_happened="任务 '{task_id}' 执行时间超过上限 ({timeout}s)。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查行进路线上是否有障碍物阻塞或增加任务超时阈值。",
        suggested_actions=[
            {"label": "延长超时重试", "action": "extend_retry"},
            {"label": "放弃任务", "action": "abort_task"},
        ],
    ),
    "precondition_failed": FriendlyErrorTemplate(
        error_code="precondition_failed",
        severity="warning",
        what_happened="执行操作的前置条件未满足: {condition}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="先完成前置步骤（如完成定位建图或传感器就绪）后再执行该操作。",
        suggested_actions=[
            {"label": "执行前置依赖", "action": "run_prerequisites"},
        ],
    ),

    # ==========================================
    # 6. 配置类 (Configuration Errors)
    # ==========================================
    "config_save_failed": FriendlyErrorTemplate(
        error_code="config_save_failed",
        severity="warning",
        what_happened="保存配置文件失败 ({profile_path}): {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查磁盘写权限、目录是否存在以及磁盘剩余空间。",
        suggested_actions=[
            {"label": "重试保存", "action": "retry_save_config"},
            {"label": "检查权限", "action": "check_permissions"},
        ],
    ),
    "config_rollback_failed": FriendlyErrorTemplate(
        error_code="config_rollback_failed",
        severity="warning",
        what_happened="回滚至历史配置快照失败 ({snapshot_id}): {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查历史快照文件完整性或选择其他快照版本。",
        suggested_actions=[
            {"label": "查看快照列表", "action": "list_snapshots"},
        ],
    ),
    "config_validation_failed": FriendlyErrorTemplate(
        error_code="config_validation_failed",
        severity="warning",
        what_happened="配置文件校验失败: {validation_errors}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="根据校验错误修正配置文件中的键值与类型。",
        suggested_actions=[
            {"label": "查看配置格式要求", "action": "view_config_schema"},
        ],
    ),
    "snapshot_not_found": FriendlyErrorTemplate(
        error_code="snapshot_not_found",
        severity="warning",
        what_happened="找不到指定的配置快照 ({snapshot_id})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="列出所有可用快照并核对快照 ID。",
        suggested_actions=[
            {"label": "查看可用快照", "action": "list_snapshots"},
        ],
    ),

    # ==========================================
    # 7. 基础设施类 (Infrastructure Errors)
    # ==========================================
    "disk_full": FriendlyErrorTemplate(
        error_code="disk_full",
        severity="critical",
        what_happened="机器人主存储磁盘空间不足 ({path}，剩余 {free_space})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="清理旧日志 (/var/log/fireclaw)、历史录像或扩充存储卡。",
        suggested_actions=[
            {"label": "清理临时缓存", "action": "clean_disk_cache"},
            {"label": "查看磁盘占用", "action": "check_disk"},
        ],
    ),
    "database_lock": FriendlyErrorTemplate(
        error_code="database_lock",
        severity="warning",
        what_happened="数据库/状态存储被其他进程锁定 ({db_path})。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查是否有并发运行的 FireClaw 实例或卡死的维护脚本。",
        suggested_actions=[
            {"label": "重试获取锁", "action": "retry_db_lock"},
            {"label": "清理僵尸锁", "action": "clear_stale_lock"},
        ],
    ),
    "daemon_start_failed": FriendlyErrorTemplate(
        error_code="daemon_start_failed",
        severity="critical",
        what_happened="FireClaw 后台守护进程启动失败: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="查看系统日志 systemctl status fireclaw 或手动运行 fireclaw doctor。",
        suggested_actions=[
            {"label": "运行环境体检", "action": "run_doctor"},
            {"label": "查看详细日志", "action": "view_service_log"},
        ],
    ),
    "checkpoint_error": FriendlyErrorTemplate(
        error_code="checkpoint_error",
        severity="warning",
        what_happened="写入或恢复状态检查点失败: {reason}。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查检查点目录写入权限与磁盘空间。",
        suggested_actions=[
            {"label": "强制新建检查点", "action": "create_checkpoint"},
        ],
    ),

    # ==========================================
    # 8. 机器人状态类 (Robot State Errors)
    # ==========================================
    "robot_offline": FriendlyErrorTemplate(
        error_code="robot_offline",
        severity="critical",
        what_happened="目标机器人 ({robot_id}) 处于离线状态，未响应心跳。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查机器人主电源、机载电脑启动状态及无线通讯连接。",
        suggested_actions=[
            {"label": "发送唤醒探测", "action": "ping_robot"},
            {"label": "刷新机器人列表", "action": "refresh_fleet"},
        ],
    ),
    "low_battery": FriendlyErrorTemplate(
        error_code="low_battery",
        severity="critical",
        what_happened="机载动力电池电量过低 ({battery_level}%)，低于安全作业红线。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="立即引导机器人返回充电桩或安排现场人员更换备用电池。",
        suggested_actions=[
            {"label": "立即返航", "action": "return_to_base"},
            {"label": "切换节能驻留", "action": "enter_low_power"},
        ],
    ),
    "target_unreachable": FriendlyErrorTemplate(
        error_code="target_unreachable",
        severity="warning",
        what_happened="无法规划通往目标点 ({target_location}) 的可行路径，存在不可跨越的障碍。",
        robot_safe_status=UNVERIFIED_ROBOT_STATUS,
        action_taken=UNVERIFIED_ACTION_STATUS,
        next_steps="检查目标点是否落在障碍物内部，或手动指定替代路径点。",
        suggested_actions=[
            {"label": "选择就近替代点", "action": "select_alternate_goal"},
            {"label": "刷新局部代价地图", "action": "clear_costmaps"},
        ],
    ),
}
