# 路口信号相位数据账

离线重建单环控制器边沿、可信服务、周期与探头到达。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m greenbook preview examples/baseline.json
python3 -m greenbook publish examples/baseline.json ledger.sqlite original
python3 -m greenbook publish examples/changed.json ledger.sqlite revised
python3 -m greenbook history ledger.sqlite
python3 -m greenbook show ledger.sqlite original
python3 -m greenbook source ledger.sqlite original
python3 -m greenbook replay ledger.sqlite revised

# 换发设备时钟证书：对回执做重签计划，作为独立新成果发布
python3 -m greenbook resign ledger.sqlite original examples/reissued.json examples/receipt.json reissue-plan
python3 -m greenbook show ledger.sqlite reissue-plan
python3 -m greenbook replay ledger.sqlite reissue-plan
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

## 换发重签计划

`resign` 读取一个已发布的成果版本、一个换发了时钟证书的候选输入，以及接收方回执。回执按 `type`/`key` 列出收到的成果（`evidence`、`cycle:<起点边沿>`、`bucket_report:bucket:<名>`）及其引用的 `services`、`observations`、`arrivals`；观察引用为「检测器/接线/心跳」，不含证书编号，所以事后换号不会让已收下的回执失效。

计划对两版都重算再比对，只对「自身成果内容变了」或「回执引用的证据变了」的成果重签，并被要求逐项给出：

- `affected_time_ms.base`：受影响的**原始**时间段（按成果窗口裁剪）；
- `sources`：可解释来源链，含到达/观察区段/心跳/接线，换号时附 `certificate_reissue` 记录；
- `changes`：逐条变化，带 `reason` 与分类。

三类结果分开记，一个成果取其最重的一类：

| 类别 | 含义 |
| --- | --- |
| `renumbered_facts_unchanged` | 只是证书换号，事实不变 |
| `facts_changed_classification_unchanged` | 数值/区间变了，归类不变 |
| `readjudication_required` | 归类或判定要改判 |

`action` 区分 `resign`（已收到且要重签）、`resign_if_received`（要变但没收到）、`keep`（收到且不变）、`not_signed`（没收到也不变）。重签项给出与旧件不同的 `new_key`（`<旧键>@<候选版名>`），计划本身作为新的 `resign_plan` 版本入库；旧签件与旧成果版本原样可读、可 `replay`。

计划不把旧报表当索引：两版的每个到达、每个观察区段都重新扫描。因此能找出原先没有时间证据、换发后**首次**进入某桶的到达（`base_time_ms` 为空，靠来源链解释），以及**没有任何到达移动、却因观察区段增减而改变有效时长**的桶（`observation_duration_changed`，由相关区段的 `observation_interval_changed` 解释）。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。测试：`python3 -m unittest discover -s tests`。
