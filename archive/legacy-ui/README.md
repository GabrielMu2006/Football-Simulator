# 旧UI被动归档

- 批次：`20261021-2351`
- 归档：`legacy-ui-v2-20261021-2351.zip`
- SHA-256：`e73701e2f934836b4c175d60518c08f395243dd0682794ba7ff5b6cc41ff5dad`
- 字节：`265,551`
- 成员：45（35 个 `football_simulator/ui_v2/` 源码文件 + 10 个旧UI专属测试模块，248 项）
- 逐文件原路径、SHA-256、字节数、测试去向往返清单：`legacy-ui-v2-20261021-2351.manifest.json`

这个 ZIP 是只读证据：不要解压回源码树，不要加入 `PYTHONPATH`、默认测试发现或应用包。当前共用队徽已迁到 `football_simulator/ui/team_crest.py`；正式入口仍是项目根 `ui_v2_main.py`。归档时的字节包含未提交变更；校验为 ZIP CRC + 每成员字节数 + 每成员 SHA256。
