# `payload.entities` 结构化 Observation 接口规范

## 1. 这份接口解决什么问题

`payload.entities` 是感知适配器与 FireClaw Entity Memory 之间的 v1 接口。
目标检测器、跟踪器、传感器融合节点或机器人适配器，把已经结构化的实体候选写入
一条不可变 Observation；随后 `EntityExtractionPipeline` 把每个有效候选转换成
可审计的 `entity_mention` 事件。

当前实现不会从自由文本中抽取实体。也就是说，FireClaw 只记录上游算法明确提供的
实体、类型、置信度和位置，不会让 LLM 在解析文字时补出一个不存在的 victim、exit
或 hazard。

## 2. 完整示例

下面是一个 Observation 的 `payload`。Observation 自身的 `event_id`、
`mission_id`、`robot_id`、`observed_at` 和机器人自身位置属于外层 memory event，
不在这里重复。

```json
{
  "spatial_frame_scope": "mission",
  "entities": [
    {
      "name": "victim-track-7",
      "entity_kind": "victim",
      "confidence": 0.87,
      "source_track_namespace": "thermal-camera",
      "source_track_id": "7",
      "pose": {
        "frame_id": "building-map",
        "x": 12.4,
        "y": 6.8,
        "z": 0.2,
        "floor": "2",
        "uncertainty_radius_m": 1.5
      },
      "attributes": {
        "detector_class": "person",
        "thermal_peak_c": 36.8,
        "occluded": true
      }
    }
  ]
}
```

它表达的是：感知算法报告了一个名为 `victim-track-7` 的 victim 候选，单次感知
置信度为 `0.87`，对象位置是给出的 `pose`。它不表示操作员已经确认此人身份，
不表示位置绝对准确，也不表示 FireClaw 可以只凭这条记忆直接执行物理动作。

## 3. 顶层 Payload 字段

| 字段 | 类型 | 是否必填 | 含义 |
|---|---|---:|---|
| `entities` | object 数组 | 否 | 不提供时跳过实体抽取；提供时逐项独立校验。 |
| `spatial_frame_scope` | string | 否 | 精确值 `mission` 表示实体位置已经位于可供多机器人比较的共享 mission frame。缺失时不假设可以跨机器人比较。 |

`spatial_frame_scope="mission"` 是上游适配器作出的坐标语义声明，不是 FireClaw
自动执行的坐标变换。只有在对象位置已经正确转换到 mission frame 后，适配器才能
写入这个值。不能因为 robot-A 和 robot-B 都有一个叫 `odom` 或 `base_link` 的坐标系，
就把它们当成同一个共享坐标系。

Observation payload 可以包含其他业务字段，但这些字段不属于本规范。

## 4. 单个 Entity Item 字段

| 字段 | 类型 | 是否必填 | 含义 |
|---|---|---:|---|
| `name` | 非空 string | 是 | 检测标签、跟踪标签、呼号或可读别名；不是全局唯一实体 ID。 |
| `entity_kind` | enum string | 是 | FireClaw 实体语义分类，合法值见下一节。 |
| `confidence` | `[0, 1]` 内有限 number | 是 | 这一次感知结果的置信度；boolean 非法。它不是人工确认，也不是动作权限。 |
| `pose` | object | 否 | 被观察实体的位置，不是观察机器人的位置。 |
| `source_track_namespace` | 非空 string | 条件必填 | 跟踪器命名空间；必须与 `source_track_id` 同时提供。 |
| `source_track_id` | 非空 string | 条件必填 | 跟踪器内部 ID；必须与 `source_track_namespace` 同时提供。 |
| `attributes` | object | 否 | 检测器或领域特有的结构化属性；默认是 `{}`。 |

当前 extractor 会忽略 entity item 中不认识的顶层字段。上游不能依赖这些字段被写入
Entity projection；扩展数据应统一放入 `attributes`，并使用稳定、已记录的 key。

## 5. `entity_kind` 合法值

| 值 | 含义 |
|---|---|
| `victim` | 可能需要救援或评估的人员 |
| `responder` | 消防员、医护人员或其他现场响应人员 |
| `exit` | 出口、疏散开口或逃生点 |
| `room_or_zone` | 房间、走廊、楼层区域或任务区域 |
| `fire_source` | 已发现或疑似的火源 |
| `smoke_source` | 已发现或疑似的烟源 |
| `hazardous_material` | 危险物质、泄漏点或危化品源 |
| `obstacle` | 影响通行或操作的障碍物 |
| `robot` | 被作为环境实体观察到的机器人 |
| `equipment` | 救援、感知、灭火或现场设备 |
| `unknown` | 已有明确结构化候选，但类别确实无法确定 |

`unknown` 不能用来把任意文字或未处理的传感器输出塞进 Entity Memory。只有上游算法
已经形成一个明确候选、但暂时无法分类时，才使用它。

## 6. `pose` 字段

| 字段 | 类型 | 是否必填 | 含义 |
|---|---|---:|---|
| `frame_id` | 非空 string | 是 | 这个实体位置所属的坐标系 |
| `x` | 有限 number | 是 | x 坐标，单位 m |
| `y` | 有限 number | 是 | y 坐标，单位 m |
| `z` | 有限 number | 否 | 垂直坐标，单位 m；未知时省略 |
| `floor` | string | 否 | 建筑楼层标识，例如 `2`、`B1` |
| `uncertainty_radius_m` | `>= 0` 的有限 number | 否 | 保守位置不确定性半径，单位 m；默认 `0.0` |

Entity pose 与外层 Observation pose 表示的是两个不同对象：

```text
Observation.pose          = 观察机器人或传感器当时在哪里
payload.entities[i].pose = 被观察实体估计在哪里
```

Extractor 不会把 `Observation.pose` 偷填成 entity pose。如果适配器无法估计对象位置，
就应该省略 entity `pose`，不能拿机器人位置代替。

空间查询要求 `frame_id` 一致；查询指定楼层时还要求 `floor` 一致。3D 查询带 `z` 时，
没有 `z` 的历史记录不会被假定为可比较。Memory retrieval 期间不会隐式执行 TF 变换。

`uncertainty_radius_m` 会参与保守距离计算。例如 hazard 中心离机器人 `6 m`，
位置不确定性半径是 `2 m`，那么这个 hazard 的不确定区域边界最远可能已经靠近到
`4 m`。这样查询不会因为只看中心点而漏掉可能更近的危险区域。

## 7. Tracker Identity 的含义

下面两个字段只表示某个上游 tracker 的一条轨迹，不代表已经确认的真实世界身份：

```json
{
  "source_track_namespace": "thermal-camera",
  "source_track_id": "7"
}
```

FireClaw 会再结合外层 Observation 的 `robot_id`，形成类似
`robot-A:thermal-camera:7` 的内部 tracking identity。因此 robot-A 的 track `7`
和 robot-B 的 track `7` 默认仍是两个候选。名字相同、位置相近或本地 track ID 相同，
都不能触发自动跨机器人合并；重要实体的 merge 仍需要显式 resolution 和操作员确认。

## 8. 校验与失败行为

校验以单个 item 为边界：

- 没有 `entities` 时，不生成 mention，也不报错；
- `entities` 不是数组时，整批不抽取，并返回 issue；
- 数组中一个 item 非法时，只跳过该 item，其他合法 item 继续处理；
- 即使所有 item 都非法，原始 Observation 仍然保留；
- 对同一个不可变 Observation 重放相同 extractor 是幂等的，不会重复生成 mention。

以下情况会使 item 非法：缺少必填字段、`confidence` 超出 `[0, 1]`、pose 中出现
`NaN`/`Infinity`、`uncertainty_radius_m` 为负数、`attributes` 不是 object，或只提供
一个 tracking 字段。

## 9. 非法示例

错误地拿机器人位置代替对象位置：

```json
{
  "entities": [
    {
      "name": "possible-victim",
      "entity_kind": "victim",
      "confidence": 0.72,
      "pose": {"frame_id": "base_link", "x": 0, "y": 0}
    }
  ]
}
```

如果检测器并没有真正确定 victim 位于该对象相对坐标，那么这是上游生产者的语义
错误。适配器不能为了满足 schema 而直接填写机器人自身位置。

只提供了一半 tracker identity：

```json
{
  "name": "victim-track-7",
  "entity_kind": "victim",
  "confidence": 0.87,
  "source_track_id": "7"
}
```

由于缺少 `source_track_namespace`，这个 item 会被拒绝。

把自由文本当成 entities：

```json
{
  "entities": "thermal camera may have seen a person near the stairs"
}
```

这个 payload 会被拒绝，因为 `entities` 必须是明确的结构化数组。当前 extractor
不会调用 LLM 猜测缺失字段。

## 10. 感知适配器应该怎样生成它

1. 接收 detector 或 tracker 的原始输出。
2. 只把有明确映射规则的检测类别转换成 `entity_kind`。
3. 保留算法真实给出的 `confidence`；算法没有置信度时不能随意编一个默认值。
4. 同时存在 tracker namespace 和 track ID 时，把两者一起写入。
5. 把被检测对象的 pose 转到已知 frame；完成可靠 mission-frame 变换后才能声明 `spatial_frame_scope="mission"`。
6. 根据定位、标定、检测和坐标变换误差给出保守的 `uncertainty_radius_m`；无法估计位置时省略 pose。
7. 把算法私有信息放入 `attributes`。
8. 先持久化完整 Observation，再由 `EntityExtractionPipeline` 生成带证据链接的 mention。

一个简单的类别映射可以是：

```text
thermal detector class "person" -> entity_kind "victim"（仍只是 candidate）
door/egress detector "exit"     -> entity_kind "exit"
fire segmentation "flame"       -> entity_kind "fire_source"
gas detector classified leak     -> entity_kind "hazardous_material"
```

检测到 `person` 并不天然证明这个人就是 victim。只有当该映射属于已经记录并验证的
感知策略时，适配器才可以输出 `entity_kind="victim"`；下游状态仍是基于证据的
candidate，直到被更多证据佐证或经操作员 resolution。

## 11. Safety 边界

`payload.entities` 只向 memory 提供证据，不能授权物理动作。Entity 查询结果保持
advisory；导航、操作、灭火或救援前，仍必须结合当前传感器、机器人状态、任务授权、
skill preconditions 和 SafetyGate 重新验证。

## 12. 版本兼容

本文描述当前 `fireclaw.structured-observation-entity-extractor:v1` 行为。未来如果修改
必填字段、枚举含义、pose 语义或 identity 语义，必须升级 method/schema version。
仅增加旧 reader 可以安全忽略的可选 `attributes`，不需要升级 schema version。
