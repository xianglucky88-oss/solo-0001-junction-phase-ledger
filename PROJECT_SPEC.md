# 当前数据契约

设备证书给闭区间事件时间；接线、心跳、绿窗和报告桶是半开区间。只在可信时钟、接线及心跳共同支持时判读到达。序号缺口或不符合相位轮转的边沿不补齐。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：full 桶 N 相位：1 次确定绿灯、1 次确定非绿灯，比例 1/2；a-edge 尚未判定；a-rewire 未能归入任一接线；loop 的 N 观察时间为 239 ms。

入口为 `greenbook/cli.py`，归约编排在 `greenbook/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 换发重签

`examples/reissued.json` 换发 loop 设备时钟证书（换号、延长 ticks、偏移 +1）；`examples/receipt.json` 是接收方回执样本。重签计划在 `greenbook/resign.py`，回执校验在 `greenbook/receipt.py`。

- 成果按独立签件计量：`evidence`（证据包）、`cycle:<起点边沿>`（周期）、`bucket_report:bucket:<名>`（桶报表）。
- 回执只按 `type`/`key` 指名成果；观察引用为「检测器/接线/心跳」，**不含证书编号**。
- 只重签「成果内容 SHA-256 变了」或「所引用的证据变了」的成果；类别分 `renumbered_facts_unchanged`、`facts_changed_classification_unchanged`、`readjudication_required`，一个成果取最重者。
- 每项重签给出原始受影响时间段 `affected_time_ms.base`、候选时间段、来源链 `sources` 与逐条 `changes`。
- 变化检测对两版全量重算：不依赖旧报表列出的引用，故能发现原先无时间证据、换发后首次进入桶的到达，以及无到达移动但有效观察时长改变的桶。
- 计划作为新的 `resign_plan` 版本入库；重签项以不同 `new_key` 指向新成果，旧签件与旧成果版本仍可读、可 `replay`。
