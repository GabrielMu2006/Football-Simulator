# Football Simulator UI v2

一个本地运行的足球联赛模拟游戏：玩家创建存档、初始化球队与球员，按周推进赛季，
在路由式数据工作台中查看联赛、杯赛、交易、选秀、荣誉和球员历史。

## 安装（macOS 推荐）

使用可安装镜像：

```text
release/macos/Football Simulator UI v2.dmg
```

1. 双击 DMG；
2. 把 `Football Simulator UI v2.app` 拖入「应用程序」；
3. 如果提示“无法验证”，右键应用 → 打开 → 再次确认；或执行：

```bash
xattr -dr com.apple.quarantine "/Applications/Football Simulator UI v2.app"
```

也可以使用便携压缩包：

```text
release/macos/Football-Simulator-UI-v2-macOS.zip
```

## 从源码运行

```bash
python3 -m venv .venv-ui-v2
.venv-ui-v2/bin/pip install -r requirements-ui-v2.txt
.venv-ui-v2/bin/python ui_v2_main.py
```

## 核心内容

- 40 支虚拟球队：每个新存档随机排序，前 20 支进入一级联赛，后 20 支进入次级联赛。
- 200 名真实球员池：每个新存档随机排序，前 50 名作为初始真实球员，其余依次进入后续选秀池。
- 每队基础阵容：1 名门将、4 名后卫、3 名中场、3 名前锋。
- 一级联赛：完整比赛模拟，统计进球、助攻、创造机会、成功防守、扑救、零封、评分和身价。
- 次级联赛：完整比赛模拟，统计权重低于一级；独立升降级与杯赛。
- 升降级、杯赛（优胜者杯 / 挑战杯 / 超级杯）、转会系统、选秀系统、历史与荣誉。
- 存档管理：新建、选择、删除存档；存档为 SQLite 数据库。

## 界面导航（数据工作台）

- 侧栏：首页 / 赛季 / 比赛 / 赛事 / 球队 / 球员 / 转会 / 选秀 / 历史 / 存档。
- 顶部栏：后退、前进、面包屑、全局搜索（Cmd/Ctrl+K 聚焦）、存档选择、当前赛季与周次、
  处理待办、模拟下一周、本周战报、刷新。
- 球员、球队、比赛、赛事全部互联互通；列表页为全高主表，支持筛选与列排序。

## 应用图标与队徽

- 应用图标：`assets/app.icns`（macOS）与 `assets/app.ico`（Windows），
  均由 `scripts/generate_app_icon.py` 生成。
- 虚拟队徽：默认加载 `team_badges_40/PNG` 的 40 支球队队标图片；
  缺失时回退为按队名确定性生成的文字队徽，也可通过 `set_custom_crest_provider()` 覆盖。

## 构建 macOS .app 与 DMG

```bash
# 生成应用图标（需要时）
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python scripts/generate_app_icon.py

# 构建 .app
./build_macos_ui_v2_app.sh

# 生成 DMG
./scripts/create_macos_dmg.sh
```

产物：

- `dist-ui-v2/Football Simulator UI v2.app`
- `release/macos/Football Simulator UI v2.dmg`

## 构建 Windows 版

PyInstaller 不支持跨平台交叉编译，Windows 版必须在 Windows 本机构建。
在 Windows 的仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

或双击根目录 `build_windows_ui_v2.bat`。脚本会用 conda 创建独立构建环境、
安装依赖、执行 PyInstaller，并组装：

- `release/windows/Football-Simulator-UI-v2-Windows/`
- `release/windows/Football-Simulator-UI-v2-Windows.zip`

Mac 可通过 SSH（Windows 开启 OpenSSH Server）远程执行同一脚本，再把 zip 拉回，
详见 `release/windows/README.md`。

## 测试

```bash
# 逻辑层（无 PySide6 也可运行部分）
python3 -m unittest discover -s tests

# 全量（含 UI）
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests
```

## GitHub 发布建议

- 源码、测试、图标源文件、打包脚本、README 正常提交到仓库。
- 大型二进制（DMG / zip / .app）不要放进 git，建议上传到 GitHub Releases。

```bash
git add football_simulator tests assets scripts README.md \
    "Football Simulator UI v2.spec" "Football Simulator UI v2 Windows.spec" \
    build_macos_ui_v2_app.sh build_windows_ui_v2.bat release/README.md
git commit -m "feat: UI overhaul, team crests, app icon, DMG packaging"
git push origin main

gh release create v0.2.0 \
    "release/macos/Football Simulator UI v2.dmg" \
    "release/macos/Football-Simulator-UI-v2-macOS.zip"
```

## 存档位置

- 源码运行时：项目目录 `saves/`。
- 打包后的 macOS 应用：`~/Library/Application Support/Football Simulator/saves`。
- 打包后的 Windows 应用：`%APPDATA%\Football Simulator\saves`。

旧版 `state.json` 存档不兼容；请使用新版 SQLite 存档。

## 项目结构

```text
ui_v2_main.py                唯一入口
football_simulator/          游戏逻辑、SQLite 持久化、查询层、UI
assets/                      应用图标（app.icns / app.ico / PNG）
team_badges_40/              40 支球队队标（PNG / SVG）
scripts/                     图标生成、DMG 打包、Windows 构建脚本
tests/                       冻结测试 / 页面验收测试
build_macos_ui_v2_app.sh     macOS PyInstaller 构建
build_windows_ui_v2.bat      Windows 构建快捷入口（调用 scripts/build_windows.ps1）
"Football Simulator UI v2.spec"/"...Windows.spec"  macOS / Windows PyInstaller 配置
scripts/create_macos_dmg.sh  DMG 打包
release/macos/               发布产物（DMG / zip）
release/windows/             Windows 发布产物与说明
```