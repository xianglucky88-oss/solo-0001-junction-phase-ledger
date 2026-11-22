# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。

## 封道仿真（`closure_simulations`）

封道同时压两条转向限流线，故不接受逐转向最大值拼装，一律按整组转向需求联立判读。输入为可选增量：不含 `turns`/`count_windows`/`closures`/`demand_plans` 的旧版资料直接产出空清单。结构示例以 `examples/closure.json` 为准。

- `turns`：整组转向，各自挂到一个相位。
- `count_windows`：现场计数窗。`scope` 为 `subtotal`（独立转向小计，恰一个转向）或 `total`（共享探头总计，至少两个转向）。
- `closures`：封道时段与恰好两条限流线，每条给 `cap`。
- `demand_plans`：按封道提交的整组需求，必须覆盖全部转向；需求只允许非负整数或 `null`，`null` 表示未知，封道不把未知需求归零。

计数窗只有同时满足以下条件才成为联立约束，否则只作为排除记录并给出 `reasons`：

1. 明确标注同一 `batch`，且同批各窗的时间跨度完全一致；
2. 窗与封道时段完全对齐（`not_aligned`）；
3. 窗的整段落在同一接线段内，跨换接点判 `rewire_straddle`；
4. 该接线在窗内被可信心跳连续覆盖，缺口判 `heartbeat_gap`；
5. 可观测转向的相位与接线相位一致（`phase_mismatch`）；无接线判 `unwired`。

合格小计给出各转向边际范围 `[min, max]`；合格总计要求可观测转向需求之和等于计数。每个计划的结论为 `feasible`、`contradicted`（`violations` 指明边际、限流或哪个 `batch` 的总计矛盾，`contradicts` 汇总矛盾批次）或 `undetermined`（`missing_observations` 列出缺口，含未知需求、无观测与无法核验的限流）。

独立核对值（closure 示例）：plan-ok 为 feasible；plan-batch 的 N-left=4 落在边际 [3,4]、N-thru=7 落在边际 [6,7]，但整组合计 11 与批次 b1/b2 的总计 10 矛盾；loop-rewire 横跨 t=250 换接点，恒为 `rewire_straddle`；side-gap 与 side-total-gap 在 cl-3 同跨度下因心跳止于 450 而判 `heartbeat_gap`；plan-null 与两个全 `null` 计划均为 undetermined。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。
