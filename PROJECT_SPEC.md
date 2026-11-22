# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例；`rotation.json` 演示序号缺报段仍按普通单环轮转时的可达性分析。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。

## 缺报段轮转约束（可选 `rotation_gaps`）

仅当某条 `controller_gap` 在输入中显式声明仍保持普通轮转时才启用；没有该字段时成果与历史版本字节一致。每条声明引用缺口两端的已知边沿，附带固定观测窗 `observation_window_ms`（半开）和每个相位的 `min_ms`/`max_ms` 持续时间。复盘人员可在 `proposal.durations_ms` 中给出拟采用的时长。

- 序号差决定缺失边沿数；两端相位必须与 `phase_order` 的轮转一致，否则状态为 `unproved`（原因 `rotation_conflict`）。端点无时钟证书时为 `unproved`（`endpoint_unclocked`）。
- 缺口建模为未知边沿时刻链 `t_0..t_n`：端点证书给闭区间绝对范围，相位时长给差分约束，全部用有理数（`Fraction`）求解。无可行时序时状态为 `infeasible`，不产生任何服务或时长界。
- 可行时输出每个重建相位段的可证服务 `certain`（所有可行时序一致的部分，可能为 null）、可能服务 `possible`，以及该段时长的可达上下界。
- `phase_duration_bounds_ms` 给观测窗内各相位被截断后总时长的可达上下界；窗口边界与可达时刻相交时按排序区域枚举，区域内截断项退化为线性表达式，由两段式精确单纯形求极值。
- 复盘提案先给逐相位是否落在界内，再给 `simultaneously_achievable`：把各相位目标作为等式加入同一约束系统，只有存在一条同时满足全部目标的时序才为 true。
- 重建段并入服务清单参与到达判读：探头只有在所有可行时序下都落在同一相位服务内才升为确定绿灯/非绿灯；可行时序之间不一致时保留 `ambiguous_service`，无适用约束时维持未证。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`，轮转求解在 `greenbook/rotation.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。
