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
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

走廊配对（上游计数器批次与下游有证到达）随 `corridors`/`counter_batches` 两个字段启用，缺一则拒绝；无这两个字段时成果结构与旧版完全一致。两份验收输入证据相同、站点绿灯率同为 1/2，仅批次可配对关系不同：

```sh
python3 -I tests/gen_fixtures.py
python3 -m greenbook preview tests/fixtures/corridor_contention.json
python3 -m greenbook preview tests/fixtures/corridor_disjoint.json
python3 -I tests/test_corridor.py
```

争用输入的确定绿灯范围为 [0,1]，分离输入为 [1,1]，二者配对量均为 3、无证书批次 1 辆单列不按零处理。规则细节见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
