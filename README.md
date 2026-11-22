# 路口信号相位数据账

离线重建单环与双环控制器边沿、可信服务、周期与探头到达。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m greenbook preview examples/baseline.json
python3 -m greenbook publish examples/baseline.json ledger.sqlite original
python3 -m greenbook publish examples/changed.json ledger.sqlite revised
python3 -m greenbook history ledger.sqlite
python3 -m greenbook show ledger.sqlite original
python3 -m greenbook source ledger.sqlite original
python3 -m greenbook replay ledger.sqlite revised
```

双环样本：`dual.json`（两环必然相遇）、`dual-possible.json`（至少一种合法时序）、`dual-conflict.json`（分看各合法、联立不可相遇）、`dual-partial.json`（一环缺报但早先年段仍发布）。

```sh
python3 -m greenbook preview examples/dual-conflict.json
python3 -m unittest discover -s tests
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。单环输入的口径与冻结版逐字节一致。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
