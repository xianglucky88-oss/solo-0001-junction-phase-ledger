# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。单环口径不因双环输入而改变：`baseline.json` 与 `changed.json` 的成果与冻结版逐字节相同。

## 双环控制器

`examples/dual.json`、`dual-possible.json`、`dual-conflict.json`、`dual-partial.json` 是双环样本。两个环各自有控制器、相位轮转和边沿记录，且各自按单环规则独立重建；联合层只对报告桶做分类，不改写、合并或撤销任一环的服务。因此：

- 每环的 `services`、`unusable_edges`、`cycles` 与把它单独作为单环输入时完全一致，`rings.members` 原样给出分环结果。
- 两环若相位名不重复，`phase_order` 为两环相位之并集；`crossing.rings` 给出每一环在越障点必须保持的相位。越障需要两环在同一瞬间同时处于各自相位：证明之间允许一环比另一环先到并等待，但等待不能把两个不相交的服务窗口连起来。
- `crossings` 逐桶给出：

| 状态 | 含义 |
| --- | --- |
| `certain` | 两环必然同时处于越障相位；`certain` 与 `possible` 给出共同满足的时间段 |
| `possible` | 至少存在一种合法时序（含两种先后与同时）使两环在该桶内相遇；`possible` 给时间段，`orderings` 给可行先后 |
| `conflict` | 两环各自都有合法的越障服务，但窗口彼此不相交，无法在同一越障点相遇；不撤销分环服务 |
| `incomplete` | 某环因缺报（序号缺口或相位不轮转）在该桶没有越障服务；其它桶（含更早的年段）照常发布 |
| `unproved` | 某环在该桶没有越障服务，且没有缺报可归因（例如精度不足或时钟证据缺失） |

`orderings` 只由时间段的相对关系推出，取值为 `环甲<环乙`、`环乙<环甲`、`simultaneous` 的子集；环在 `rings` 数组中的次序或标识符不得决定谁先跨。`missing` 列出缺环，`reason` 取 `missing_reports`、`no_evidence` 或 `no_shared_crossing`。

双环输入的 `rings` 须恰好两项且标识符唯一、相位互不重叠；`crossing` 须为每一环指明越障相位且该相位属于该环。边沿可用 `ring` 指明归属，或在相位名不重复时按 `phase` 归属；也可放在各环对象的 `edges` 中。构造非法时命令以状态 2 退出且不发布结果。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`，双环联合层在 `greenbook/rings.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。
