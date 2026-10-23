# 测试

保留现行有效测试、冻结基线及必要夹具。启动和UI身份核验先读项目根AGENTS.md。用户最新要求先归档整个旧UI及其专属测试，再修三项P2；实施规格见Reviews/latest/identity-top20-bracket-p2-plan.md。

阶段0已完成：旧UI源码与10个专属测试模块（合计248项）已归档到`archive/legacy-ui/legacy-ui-v2-20261021-2351.zip`，带逐文件SHA256、CRC和测试去向清单；它们退出默认发现，不在PYTHONPATH，也不会被打包。共享队徽已迁到`football_simulator/ui/team_crest.py`。现行测试共51模块/492项（最后全量以本轮实际运行结果为准），不再引用历史717项。

`test_current_ui_only.py`用子进程实际运行`ui_v2_main.py`并回执MainWindow/page模块，验证旧`football_simulator.ui_v2`不可导入、40枚v3队徽、现行测试收集无旧模块/ImportError；候选包构建后设置`FS_CANDIDATE_APP`再核对PyInstaller模块清单。不要为执行旧报告命令重新解压旧UI到源码树。

后续测试产物统一放本轮唯一artifacts目录；Reviews/latest只保留摘要和必要证据入口。

## 完整回归

    QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python -m unittest discover -s tests

当前实际数量以归档完成后对最后代码运行的结果为准。历史532/717等数量只对应当时测试集合，不与现行全量混用，也不将定向复核冒充全量。

需要按模块隔离时：

    QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python tests/run_suite.py --json artifacts/tests/results.json --log-dir artifacts/tests/logs

## 定向回归

    QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest tests.test_trophy_integration tests.test_vnext_trophies tests.test_vnext_calendar tests.test_v2_rules

## 必要数据

- baseline/：旧实现的确定性冻结基线，继续保留。
- fixtures_vnext/s3-finalized.fsarchive：新版完整三季统计、冠军及UI验证。
- fixtures_vnext/s2-week25.fsarchive：新版冬窗状态验证。
- fixtures_vnext/old-rules-s3.fsarchive：旧规则拒绝测试。原先位于旧审查目录，清理前已按字节迁入，不能删除或伪造版本号。
- Reviews/identity_redesign_2026-10-20/stadiums.selected.json：用户确认的40主场名称，用于与生产规则独立比对。

tests.support负责数据根、配置副本、随机数和Qt生命周期隔离；测试不得写入用户存档或生产配置。Windows真实构建与运行仍延期。
