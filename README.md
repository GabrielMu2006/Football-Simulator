# Football Simulator（联赛观察型）

一个本地运行的足球联赛观察模拟器：创建新存档、推进 52 周赛季，在暖浅色杂志式
界面里追踪球队、球员、比赛、六项杯赛、数据与赛季故事。

**当前代码版本以`football_simulator/version.py`为准；同一版本号可能包含多次构建。** 启动和验收前先读[AGENTS.md](AGENTS.md)，核对实际入口、模块、构建指纹及运行进程。

当前发布：**V0.1.0**（重构后重新编号，macOS Apple Silicon）。下载见 [GitHub Releases](https://github.com/GabrielMu2006/Football-Simulator/releases/tag/V0.1.0)。
日常开发测试使用[隔离开发流程](docs/isolated-development.md)，不覆盖正式应用、不挂载游玩存档。

> 本轮只支持**新存档**。旧存档不迁移、不作为验收输入，原文件保留；旧裸 SQLite/JSON
> 存档会被明确拒绝并提示新建。正式入口仍是 `ui_v2_main.py`。

现行窗口为`football_simulator.ui.main_window.MainWindow`。旧`football_simulator/ui_v2/`已整体归档到`archive/legacy-ui/legacy-ui-v2-20261021-2351.zip`并移出工作源码；共用队徽现位于`football_simulator/ui/team_crest.py`。`ui_v2_main.py`、应用名称和虚拟环境名称不属于旧UI退役范围。归档ZIP是只读证据，不加入PYTHONPATH、测试发现或应用包。

## 2026-10 本轮改造

- 新赛事引擎：S1 无杯；S2 五杯 199 场；S3 起六杯 206 场。
  - 君临杯（aureole）62 场：四档四组双循环 + 两回合八强/半决赛/决赛；
  - 命途杯（confluence）31 场：32 队单场淘汰、逐轮无种子重抽；
  - 极境杯（starpath）46 场：CS 式 BU 瑞士轮 + 两回合八强/半决赛 + 中立决赛；
  - 不灭杯（ember）14 场：8 队双败、固定交叉落位、总决赛无重置；
  - 凌越杯（ascendant）46 场：登峰/涅槃组外循环、突围、八强单败；
  - 永世杯（zenith / Aeon Cup）7 场：冠军资格去重补位、荣誉种子固定签表。
- 加时 2×15 分钟、逐脚点球 0.75 与突然死亡；两回合取消客场进球；中立场无主场加成。
- 40 个俱乐部主场稳定绑定；S2 抽 3 个、S3 起抽 4 个中立决赛场地且同季不放回。
- 52 周赛历：冬窗 25–27、夏窗 50–52、升级附加赛 46–49，第 24/49 周结算。
- 逐场稳定球员/球队事实、零产出出场、赛季能力/评分/身价/奖项冻结；六杯个人奖与
  Top20 贡献解释。
- 观察三模式：自动、关键事项暂停（默认）、全部手动；同一命令与事务。
- 完整存档包 `.fsarchive`：数据库 + 配置 + manifest；导入先校验后原子安装。
- 新 UI：暖浅色主体 + 深色焦点比赛 + 转播式大比分；14 类页面在
  1280×800 / 1440×900 / 1680×980 实际渲染验收。

## 从源码运行

```bash
python3 -m venv .venv-ui-v2
.venv-ui-v2/bin/pip install -r requirements-ui-v2.txt
.venv-ui-v2/bin/python ui_v2_main.py
```

测试/截图可设置 `FS_DATA_ROOT=/path/to/temp`，把 saves/backups/trash/current_save
与配置整体重定向到独立目录。

## 核心内容

- 40 支球队、每队 11 人：1 门将 + 4 后卫 + 3 中场 + 3 前锋；球队/球员/比赛/赛事全部
  使用稳定 ID，默认球员与真实球员身份分离。
- 250 名真实球员：50 名初始进入一级联赛，其余进入后续选秀池。
- 一级/次级联赛各 20 队 38 轮；次级联赛球队荣誉分 0，但冠军保留永世杯（Aeon Cup）资格；
  球员次级联赛 0.35 是 Top20 权重，不是球队荣誉分。
- 转会允许不同位置、每方最多三人的交换包；双方总身价差不超过 20，交换后位置人数不超限，同一球员同窗只转会一次。另有能力审核、选秀、升降级、升级附加赛和历史归档。
- 存档：新建 / 初始化 / 打开 / 移入回收站 / 恢复 / 清空 / 备份 / 完整导出导入。

## 界面导航

- 侧栏六个观察入口：总览 / 比赛 / 赛事 / 球队 / 球员 / 历史；工具区：转会 / 选秀 / 存档。
- 顶栏：前进/后退、面包屑、存档切换、赛季周次、全局搜索、唯一主动作、取消/完成本周后停止。
- 深色焦点比赛区使用转播式大比分；表格页使用主表滚动，内容页最多一个主纵向滚动面。
- 队徽使用 `team_badges_40_v3/PNG` 正式资源（v2 保留为历史回退）；比分、队名、年份全部原生文字叠加。

### 快捷键

| 快捷键 | 功能 |
|---|---|
| Ctrl/Cmd+K | 全局搜索 |
| Alt+← / Alt+→ | 后退 / 前进 |
| F5 | 刷新当前页（保留路由、筛选、滚动） |

## 最新版应用

当前正式macOS发布位置（含八座奖杯）：

- 应用：`release/macos/Football Simulator UI v2.app`
- 压缩包：`release/macos/Football-Simulator-UI-v2-macOS.zip`
- 验证记录：对应本轮任务的`Reviews/latest/`报告及与包指纹对应的构建/运行身份记录。

源码改动不会自动更新此应用。修复后的包必须重新构建并核对实际启动路径/PID，不能只凭同名应用或2.0.0版本号判断是当前代码。

旧应用、旧DMG和旧Windows压缩包已清理。Windows构建与实测继续延期，构建脚本仍保留。

## 测试

现行测试、基线和必要夹具在`tests/`，运行说明见`tests/README.md`。本轮先归档旧UI专属测试；实际测试数量及归档用例去向分别记录，旧报告数字不作为当前全量结果。

```bash
QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python -m unittest discover -s tests
QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python tests/run_suite.py --json artifacts/tests/results.json --log-dir artifacts/tests/logs
```

后续测试截图和日志统一写入`artifacts/tests/`，该目录不进入git。`Reviews/latest/`只保留最近验证摘要、必要日志与少量代表截图。

## 构建

```bash
./build_macos_ui_v2_app.sh
```

macOS脚本包含规则、40队徽、球场氛围图与八奖杯资源。默认临时构建产物仍在`build-ui-v2/`与`dist-ui-v2/`；保留发布产物位于`release/macos/`。不自动发布、推送或合并。

## 存档位置

- 数据根：源码运行默认项目根，打包后为 `~/Library/Application Support/Football Simulator/`
  （Windows 为 `%APPDATA%\Football Simulator\`）。
- 每个存档目录包含 `save.sqlite3`（schema v2）与 `config.json`；
  `current_save.txt`、`backups/`、`trash/` 位于数据根。
- 完整导出包 `.fsarchive` = 数据库一致性副本 + `config.json` + `manifest.json`。
- 旧版 `state.json` / 旧 schema 不迁移、不读取；原文件保留。

## 项目结构

```text
ui_v2_main.py                      唯一入口（新 UI + 新存档）
football_simulator/game/           稳定身份、52 周日历、六杯、引擎、结算/奖项/故事
football_simulator/persistence_v2/ schema v2、world + 规范化投影、完整导入导出
football_simulator/app/            应用服务与只读 DTO 查询
football_simulator/ui/             暖浅色外壳与 14 类页面
football_simulator/ui/team_crest.py 共用队徽（v3优先，v2备用，Retina缓存）
archive/legacy-ui/                已退役旧UI源码/专属测试的被动ZIP及指纹清单
football_simulator/rules/          已确认规则与球场 JSON（SHA-256）
assets/                            应用图标（app.icns / app.ico / PNG）
team_badges_40_v3/                 40 支球队队标 V3（PNG，正式）
team_badges_40_v2/                 40 支球队队标 V2（历史回退）
real_player_star_pool_200.md       真实球员池文档（200 基础）
real_player_additions_50.md        真实球员池新增 50 人文档（已合并进配置）
scripts/                           图标生成、DMG 打包、Windows 构建脚本
tests/                             冻结测试 / 页面验收测试 / 基线
build_macos_ui_v2_app.sh           macOS PyInstaller 构建
build_windows_ui_v2.bat            Windows 构建快捷入口（调用 scripts/build_windows.ps1）
"Football Simulator UI v2.spec"            macOS PyInstaller 配置
"Football Simulator UI v2 Windows.spec"    Windows PyInstaller 配置
release/macos/                     macOS 发布产物（本地）
release/windows/                   Windows 构建说明（实测延期，旧二进制已清理）
```

大型应用与ZIP不进入git；如需发布，另行执行发布流程。
