# 正式应用位置与版本核验

当前稳定版为 **V0.1.0**，GitHub 标签为 `V0.1.0`。发布下载资产为 `Football-Simulator-V0.1.0-macOS-arm64.zip`；本目录继续使用以下固定文件名，避免启动入口漂移。压缩包内应用版本为 `0.1.0`。

后续开发默认只生成候选包，不覆盖本目录；见 `docs/isolated-development.md`。只有用户明确要求发布/更新稳定版时才晋升正式包。

当前唯一macOS正式发布位置：

- `macos/Football Simulator UI v2.app`
- `macos/Football-Simulator-UI-v2-macOS.zip`

启动和验收必须先读项目根`AGENTS.md`。现行窗口为`football_simulator.ui.main_window.MainWindow`；正式源码入口`ui_v2_main.py`中的v2不代表旧UI。不要按应用名、文件日期或2.0.0版本号判断是否为本轮构建。

源码修改不会自动更新这里的.app。修改后的源码先测试，再从记录的来源快照构建候选；候选通过后替换正式包，并从明确的绝对可执行路径启动新进程复验。记录构建指纹、实际PID、模块身份、独立测试数据根及ZIP SHA256。

与当前包指纹对应的实施报告和启动回执才是验收依据；旧报告中的测试数量和旧截图只作追溯。源码测试或启动截图不等于包内真实点击/F5通过。

用户要求先将旧UI源码/专属测试归档到`archive/legacy-ui`后退出工作源码。该ZIP是被动源码归档，不是应当启动的旧应用，不加入应用包。

Windows继续不构建、不实测、不发布。共用队徽迁移仅允许Windows.spec必要静态导入路径同步，不代表Windows已验证。
