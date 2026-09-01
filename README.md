# Football Simulator UI v2

一个本地运行的足球联赛模拟游戏：创建存档、初始化球队与球员，按周推进赛季，
在路由式数据工作台中查看联赛、杯赛、转会、选秀、荣誉和球员历史。

**当前版本：v1.0.2**（两回合淘汰赛展示规则：首回合不预写晋级方；次回合显示两回合总比分，客场进球优势标 A，点球大战标 P）

版本历史：

- v1.0.2 — 两回合淘汰赛（优胜者杯）展示优化：首回合不再预写晋级方；次回合比分追加
  两回合总比分括号，并按引擎规则标注 A（客场进球优势）/ P（点球大战）；单场淘汰赛
  点球大战同样标 P；发布 DMG 与 Windows zip。
- v1.0.1 — 修复 macOS Tahoe（26.x）全屏下"新建/打开/初始化/移入回收站"存档操作时
  反复滑动并退回桌面；存档列表/回收站重建不再产生临时顶层窗口。
- v1.0.0 — 首个正式版：40 队双级联赛、250 名真实球员池、V2 队标、首次启动教程、大图标侧栏与实心大箭头。

## 下载安装

### macOS（推荐，DMG）

从 GitHub Release 下载并安装：

```text
https://github.com/GabrielMu2006/Football-Simulator/releases/tag/v1.0.2
```

1. 下载 `Football.Simulator.UI.v2.dmg`；
2. 双击 DMG，把 `Football Simulator UI v2.app` 拖入「应用程序」；
3. 如果提示"无法验证"，右键应用 → 打开 → 再次确认；或执行：

```bash
xattr -dr com.apple.quarantine "/Applications/Football Simulator UI v2.app"
```

### Windows（便携压缩包）

从同一 Release 页面下载 `Football-Simulator-UI-v2-Windows.zip`，解压后双击
`Football Simulator UI v2.exe`。若安全提示来自未知发布者，选择"更多信息"→"仍要运行"。

> 大型二进制（DMG / zip）不进入 git 仓库，全部通过 GitHub Releases 提供。

## 从源码运行

```bash
python3 -m venv .venv-ui-v2
.venv-ui-v2/bin/pip install -r requirements-ui-v2.txt
.venv-ui-v2/bin/python ui_v2_main.py
```

## 核心内容

- 40 支虚拟球队：每个新存档随机排序，前 20 支进入一级联赛，后 20 支进入次级联赛；
- **250 名真实球员池**（200 基础 + 50 新增，见 `real_player_additions_50.md`）：
  每个新存档随机排序，前 50 名为初始真实球员，其余依次进入后续选秀池；
- 每队基础阵容：1 门将 + 4 后卫 + 3 中场 + 3 前锋；
- 一级联赛完整比赛模拟（进球 / 助攻 / 创造机会 / 成功防守 / 扑救 / 零封 / 评分 / 身价），
  次级联赛完整模拟但权重低于一级；
- 升降级、三杯赛（优胜者杯 / 挑战杯 / 超级杯）、转会（球员换球员）、选秀、赛季荣誉与历史归档；
- 存档管理：新建 / 选择 / 删除（回收站）/ 备份 / 导出 / 导入；存档为 SQLite 数据库；
- 首次启动自动弹出**游戏教程**，之后可从顶栏「教程」按钮随时打开。

## 界面导航（数据工作台）

- 侧栏：首页 / 赛季 / 比赛 / 赛事 / 球队 / 球员 / 转会 / 选秀 / 历史 / 存档（大图标 + 大文字）；
- 顶栏：实心大箭头 后退 / 前进、面包屑、全局搜索、存档切换、赛季与周次、处理待办、
  模拟下一周 / 推进 ▾（到下一待办 / 到赛季末）、本周战报、刷新、**教程**；
- 球员 / 球队 / 比赛 / 赛事全站互联；列表页为全高主表，支持筛选与列排序；
- 「赛事」杯赛页签：小组积分表 + 淘汰树；两回合淘汰赛首回合不写晋级方，次回合显示
  两回合总比分并标注 A（客场进球优势）/ P（点球大战）；
- 队徽：40 支球队使用 `team_badges_40_v2/PNG` 图片队标（V2：无外框、核心图案更大）。

### 快捷键

| 快捷键 | 功能 |
|---|---|
| Cmd/Ctrl+K | 全局搜索 |
| Cmd/Ctrl+Enter | 模拟下一周 |
| Cmd/Ctrl+Shift+Enter | 模拟到下一待办 |
| Cmd/Ctrl+Alt+Enter | 模拟到赛季末 |
| Cmd/Ctrl+Shift+W | 本周战报 |
| Cmd/Ctrl+R | 刷新 |

## 构建

### macOS .app 与 DMG

```bash
# 生成应用图标（需要时）
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python scripts/generate_app_icon.py

# 构建 .app
./build_macos_ui_v2_app.sh

# 生成 DMG
./scripts/create_macos_dmg.sh
```

产物：`dist-ui-v2/Football Simulator UI v2.app`、`release/macos/Football Simulator UI v2.dmg`。

### Windows

PyInstaller 不支持跨平台交叉编译，必须在 Windows 本机构建。在 Windows 仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

或双击 `build_windows_ui_v2.bat`。脚本自动创建 conda 构建环境、安装依赖、执行
PyInstaller，并组装 `release\windows\Football-Simulator-UI-v2-Windows\` 与 zip。
Mac 可通过 dsh-ssh 远程执行同一脚本，详见 `release/windows/README.md`。

## 测试

```bash
# 逻辑层（无 PySide6 也可运行部分）
python3 -m unittest discover -s tests

# 全量（含 UI）
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests
```

三赛季基线：`tests/baseline/three_season_fingerprint.json`，由
`python3 -m tests.generate_baseline` 生成；只有经批准的玩法变化才允许重新生成。

## 存档位置

- 源码运行时：项目目录 `saves/`；
- 打包后的 macOS 应用：`~/Library/Application Support/Football Simulator/saves`；
- 打包后的 Windows 应用：`%APPDATA%\Football Simulator\saves`；
- 教程已读状态（macOS）：`~/Library/Application Support/Football Simulator/tutorial_state.json`。

旧版 `state.json` 存档不兼容；请使用新版 SQLite 存档。

## 项目结构

```text
ui_v2_main.py                      唯一入口
football_simulator/                游戏逻辑、SQLite 持久化、查询层、UI
  ui_v2/components/tutorial.py     首次启动教程弹窗 + 顶栏「教程」按钮
assets/                            应用图标（app.icns / app.ico / PNG）
team_badges_40_v2/                 40 支球队队标 V2（PNG）
real_player_star_pool_200.md       真实球员池文档（200 基础）
real_player_additions_50.md        真实球员池新增 50 人文档（已合并进配置）
scripts/                           图标生成、DMG 打包、Windows 构建脚本
tests/                             冻结测试 / 页面验收测试 / 基线
build_macos_ui_v2_app.sh           macOS PyInstaller 构建
build_windows_ui_v2.bat            Windows 构建快捷入口（调用 scripts/build_windows.ps1）
"Football Simulator UI v2.spec"            macOS PyInstaller 配置
"Football Simulator UI v2 Windows.spec"    Windows PyInstaller 配置
release/macos/                     macOS 发布产物（本地）
release/windows/                   Windows 发布产物与说明（本地）
```

## GitHub 发布

- 源码、测试、图标源文件、打包脚本、README 提交到仓库；
- 大型二进制（DMG / zip）上传到 GitHub Releases，不进入 git。

```bash
git add football_simulator tests assets scripts README.md \
    "Football Simulator UI v2.spec" "Football Simulator UI v2 Windows.spec" \
    build_macos_ui_v2_app.sh build_windows_ui_v2.bat release/README.md
git commit -m "release: v1.0.2"
git push origin main

gh release create v1.0.2 \
    "release/macos/Football Simulator UI v2.dmg" \
    "release/windows/Football-Simulator-UI-v2-Windows.zip"
```
