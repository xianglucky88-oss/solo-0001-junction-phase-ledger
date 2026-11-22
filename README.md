# 路口信号相位数据账

离线重建单环控制器边沿、可信服务、周期与探头到达。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m greenbook preview examples/baseline.json
python3 -m greenbook preview examples/preempt.json
python3 -m greenbook publish examples/baseline.json ledger.sqlite original
python3 -m greenbook publish examples/changed.json ledger.sqlite revised
python3 -m greenbook history ledger.sqlite
python3 -m greenbook show ledger.sqlite original
python3 -m greenbook source ledger.sqlite original
python3 -m greenbook replay ledger.sqlite revised
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

预占（`preemptions`）打断普通轮转：成果在 `segments` 下分四类交付——普通完整周期 `normal_cycles`、可确认但不完整的普通段 `normal_partials`、预占段 `preemptions`、无法归类段 `unclassified`。恢复相位是显式证据，缺失时退出后的边沿不会假定回到首相位。`preempt.json` 演示开绿跨入预占且在非首相位恢复后仍闭合完整周期；`preempt_no_resume.json` 演示无恢复证据时的归类。验收测试：

```sh
python3 -m unittest discover -s tests -v
```

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
