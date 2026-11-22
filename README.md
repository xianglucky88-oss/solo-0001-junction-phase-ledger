# 路口信号相位数据账

离线重建单环控制器边沿、可信服务、周期与探头到达。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m greenbook preview examples/baseline.json
python3 -m greenbook preview examples/rotation.json
python3 -m greenbook publish examples/baseline.json ledger.sqlite original
python3 -m greenbook publish examples/changed.json ledger.sqlite revised
python3 -m greenbook history ledger.sqlite
python3 -m greenbook show ledger.sqlite original
python3 -m greenbook source ledger.sqlite original
python3 -m greenbook replay ledger.sqlite revised
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

`rotation.json` 在序号缺报段上声明普通轮转：输出 `rotation_gaps`，含重建段的可证/可能服务、固定观测窗内各相位时长的可达上下界，以及复盘提案能否在同一条时序同时达到。求解细节见 `greenbook/rotation.py` 与 `tests/` 中的精确交叉核对。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
