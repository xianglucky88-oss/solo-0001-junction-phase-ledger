# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。

## 走廊配对（corridors + counter_batches）

计数器批次只在同时发布 `corridors` 与 `counter_batches` 时生效，二者缺一即拒绝。每个批次带设备、原始 tick 时间窗和整数车数（允许 0）；车数仅来自该字段，信号 `edges` 永远不折算为车数。批次归属单条连接段，其设备必须等于该走廊的 `upstream_device`。

判定链：

1. 批次原始窗经上游设备时钟证书映射为 `possible_window_ms` 与 `certain_window_ms`（误差按两侧展开/收缩）。无任何证书覆盖的批次记 `unclocked`，车数进 `unclocked_vehicles`，不参与配对、不按零处理。
2. 每段证书窗按 `travel_time_ms: [min, max]` 展开为下游可行到达窗，区分可能窗与确定窗。
3. 下游只接受同时具备时钟、接线、心跳三证据的到达（`green`、`not_green`、`ambiguous_service`、`unproved_service`）。缺证据者按原因进 `excluded_downstream_arrivals`（无时钟/无接线/无心跳/行程窗外），`unproved_service` 也不能补足容量。
4. 可行关系构成（批次槽位 × 下游到达）二分图。`pairable_vehicles` 是最大可解释配对量；容量减去它是 `unevidenced_remainder`，表示有车但无有证到达，不是零、不是红灯。
5. 两个放行批次争用同一下游记录时，`guaranteed_green_range` 的 min/max 都在**同一最大匹配族**内计算：先在非确定绿灯子图求最大匹配得 min，在确定绿灯子图求最大匹配得 max，再各自补全为满配见证（`witnesses.minimum_green/maximum_green`）。不拼接不同配对的结论。
6. 只有 `state == green` 的配对计入保证绿灯；`ambiguous_service` 单列 `ambiguous_arrivals_unresolved`，不进 min/max，也不进站点绿灯率分母。

两份验收输入 `tests/fixtures/corridor_contention.json` 与 `corridor_disjoint.json` 证据完全相同，故 full 桶 N 绿灯率都为 1/2；差异仅在批次时间窗。独立核对值：争用输入 `pairable_vehicles=3`、保证绿灯范围 `[0,1]`、`unclocked_vehicles=1`；分离输入同配对量但范围 `[1,1]`。两者均放行 4 辆车，缺测的 1 辆始终单列。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`，走廊判定在 `greenbook/corridor.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。
