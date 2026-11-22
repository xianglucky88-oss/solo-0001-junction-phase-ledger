# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。`preempt.json` 是带预占的历史变体（四相位环、开绿跨入预占、在非首相位 E 恢复）；`preempt_no_resume.json` 去掉恢复相位证据，退出后的边沿一律不得假定回到首相位。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 预占证据与分段口径

可选的 `preemptions` 数组中，每条记录给出控制器自身 tick 域上的 `entry_tick`、`exit_tick`，以及可选的 `resume_phase`。入口和退出沿用控制器时钟证书换算设备时间，证据落在 `entry_time_ms`、`exit_time_ms` 与 `clock_refs`；时钟未覆盖时只让该段毫秒窗口降级为 `null`，不改变结构性归属。`entry_tick < exit_tick` 必须成立，各预占 tick 跨度不得重叠，`resume_phase` 必须出现在 `phase_order` 中；退出之后第一条边沿的相位若与 `resume_phase` 矛盾，整包拒绝。

边沿按 tick 落入半开区间 `[entry_tick, exit_tick)` 判为预占内部。预占会打断普通轮转：跨越入口或退出边界的相邻边沿既不重建服务，也不计入普通不可用边。不能简单裁掉预占区间再照旧组周期——一条边沿是否属于普通轮转会改变下一条记录能否闭合周期。没有 `resume_phase` 时，退出之后的所有边沿标记为无法重入环（stranded），不得假定从首相位补齐，它们进入无法归类段且不产生服务或周期。

成果在 `segments` 下分四类交付，互不混用口径：

- `normal_cycles`：普通完整周期。仍以 `phase_order` 首相位锚定；即使物理恢复发生在其他相位，也要等环转回首相位才闭合新周期。周期含 width 个确认服务段（width+1 条边），按 run 内位置核对服务键，因为预占可能消耗整段序号。
- `normal_partials`：可确认但不完整的普通段。`missing_head`/`missing_tail` 标明缺失方向，`reason` 取 `preempted_head`、`preempted_tail`、`open_head`、`open_tail` 或 `no_complete_cycle`。段与相邻周期按半开拼贴共享边界边；没有可确认服务区间（单条悬空边）时毫秒窗口与统计为 `null`。
- `preemptions`：预占段，含内部边沿、入口/恢复边引用、原始设备时间证据及 `certain_ms`/`possible_ms` 窗口；退出 tick 超过最后一条边沿时 `open` 为真。
- `unclassified`：无法归类段，原因为 `no_resume_evidence`（无恢复证据的退出尾部）或 `no_confirmed_service`（序号缺口、相位跳变等导致没有确认服务）。

服务、到达和报告桶的既有口径与字段保持不变；预占内部的到达因没有兼容服务只能得到 `unproved_service`。无 `preemptions` 的旧档成果除新增 `segments` 外逐键不变。
