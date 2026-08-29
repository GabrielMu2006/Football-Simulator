# Football Simulator UI v2

一个本地运行的足球联赛模拟游戏。游戏以“旁观式管理后台”为主要体验：玩家创建存档、初始化球队与球员，然后按周推进赛季，在路由式数据工作台中查看联赛、杯赛、交易、选秀、荣誉和球员历史。

**唯一正式入口**：

```text
ui_v2_main.py
```

## 核心内容

- 40 支虚拟球队：每个新存档会随机排序，前 20 支进入一级联赛，后 20 支进入次级联赛。
- 200 名真实球员池：每个新存档会随机排序，前 50 名作为初始真实球员，剩余球员按顺序进入后续选秀池。
- 每队基础阵容：1 名门将、4 名后卫、3 名中场、3 名前锋。
- 一级联赛：完整比赛模拟，统计进球、助攻、创造机会、成功防守、扑救、零封、评分和身价。
- 次级联赛：独立简化模拟，根据真实球员数量提高进球概率。
- 升降级：一级联赛倒数三名降级；次级联赛前两名直接升级，3-6 名通过两回合附加赛争夺升级名额。
- 杯赛：优胜者杯、挑战杯、超级杯，包含小组赛、淘汰赛、双回合、单场决赛等不同赛制。
- 交易系统：冬窗每周 1-3 笔、夏窗每周 3-5 笔，只在一级联赛球队之间随机生成，可由玩家批准或拒绝。
- 选秀系统：赛季末生成 6-10 人选秀名单，从存档专属球员池顺序进入；不足时可由玩家手动补充。
- 历史与荣誉：记录球队荣誉、球员荣誉、赛事冠军、射手王、助攻王、赛事 MVP、年度 Top 20。
- 存档管理：支持新建、选择、删除存档。

## 界面导航（数据工作台）

- 侧栏：主页 / 比赛 / 赛事 / 球队 / 球员 / 转会 / 选秀 / 历史 / 存档。
- 顶部栏：后退、前进、面包屑、全局搜索（球员 / 球队）、存档选择、当前赛季与周次、处理待办、模拟下一周、本周战报。
- 球员、球队、比赛、赛事全部互联互通：球员 → 个人页（六页签：概览 / 各赛事数据 / 赛季历史 / 奖项 / 比赛记录 / 评分身价轨迹）；球队 → 球队页（概览 / 阵容 / 赛程 / 赛季历史 / 转会 / 奖项关联）；已赛比赛 → 完整赛后报告；未赛比赛 → 赛前页；赛事 → 积分榜 / 签表 / 榜单 / 奖项 / 历史。
- 列表页均为全高主表，支持筛选与列排序；中央区域每页只有一个纵向滚动面。
- 后退 / 前进会恢复列表的筛选、排序、页签与滚动位置。

## macOS 游玩方式

本地仓库中可以直接双击：

```text
release/macos/Open Football Simulator UI v2.command
```

它会自动打开：

```text
release/macos/Football-Simulator-UI-v2-macOS/Football Simulator UI v2.app
```

如果 `.app` 在当前 macOS 上打开后立刻关闭，可以先使用源码本地启动器：

```text
release/macos/Run Football Simulator UI v2 Local.command
```

这个启动器会在项目目录中创建 `.venv-ui-v2`，安装固定版本的 UI 依赖，然后直接启动图形版游戏。

如果从 GitHub 下载，建议下载 macOS 压缩包：

```text
release/macos/Football-Simulator-UI-v2-macOS.zip
```

如果 macOS 提示无法打开，可以尝试：

1. 右键点击应用。
2. 选择“打开”。
3. 在弹窗中再次选择“打开”。

这是因为本项目使用本地签名，不是通过 App Store 或 Apple Developer ID 发布。

## 配置文件

总配置文件位于项目根目录：

```text
足球模拟器总配置.json
```

发布包里也会额外提供一份英文文件名副本，避免部分系统或压缩工具处理中文文件名时出现乱码：

```text
football_simulator_config.json
```

这两份配置内容相同，修改其中任意一份并放在 `.app` 同级目录，游戏都可以读取。它包含：

- 40 支球队的英文名与中文名。
- 200 名真实球员的英文名与位置。
- 默认球员能力值。
- 真实球员随机能力范围。

新建存档时，游戏会从总配置生成一份存档专属配置：

```text
saves/<存档名>/config.json
```

因此，修改总配置只会影响之后新建的存档，不会自动覆盖已经存在的存档。

## 存档格式与位置（重要：与旧版不兼容）

从本次大改起，每个存档是一个 SQLite 数据库：

```text
saves/<存档名>/save.sqlite3   # 唯一状态存储
saves/<存档名>/config.json    # 存档专属配置
```

- **旧版 `state.json` 存档不兼容**：新版不读取、不迁移旧存档；旧存档目录在新版中视为未初始化。
- 每周推进、转会审核、选秀、赛季结算都在单个数据库事务内完成：中断或失败会整体回滚，不会产生半写状态。
- 同一存档的并发写入要么串行成功、要么明确报错，不会静默覆盖。

源码运行时，存档在项目目录 `saves/`。打包后的 macOS `.app` 运行时，存档在：

```text
~/Library/Application Support/Football Simulator/saves
```

## Windows 版（暂时冻结）

Windows 图形版短期冻结：不修复、不重新打包、不随本次大改验证。历史发布的 Windows 压缩包仍可从 GitHub Releases 下载：

```text
https://github.com/GabrielMu2006/Football-Simulator/releases/download/v0.1.0-windows/Football-Simulator-UI-v2-Windows.zip
```

注意：该历史包基于旧存档格式（`state.json`），与当前版本的 SQLite 存档互不兼容；`build_windows_ui_v2.bat` 与 `Football Simulator UI v2 Windows.spec` 在仓库中原样保留但暂不维护。

## 从源码运行

需要 Python 3.9+。

```bash
python3 -m venv .venv-ui-v2
.venv-ui-v2/bin/pip install -r requirements-ui-v2.txt
.venv-ui-v2/bin/python ui_v2_main.py
```

## 构建与测试

构建 macOS `.app`：

```bash
chmod +x build_macos_ui_v2_app.sh
./build_macos_ui_v2_app.sh
# 产物：dist-ui-v2/Football Simulator UI v2.app
```

运行测试（无需第三方依赖即可跑逻辑层；完整 UI 测试用 `.venv-ui-v2`）：

```bash
python3 -m unittest discover -s tests                                   # 逻辑层
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests   # 全量
```

测试覆盖：玩法规则冻结（三赛季回归 + 固定随机源逐周基线指纹）、SQLite 事务与并发、查询一致性、全部页面的导航 / 链接 / 滚动验收。详见 `tests/README.md`。

## 项目结构

```text
ui_v2_main.py                唯一入口
football_simulator/
  data.py                    配置读取与球队/球员创建
  models.py                  领域模型（球队/球员/比赛）
  match_engine.py            比赛模拟（玩法冻结）
  schedule.py                赛程生成（玩法冻结）
  state.py                   赛季状态机（写命令；玩法冻结）
  domain/                    冻结的纯公式（评分/身价）与排名链
  persistence/               SQLite 连接 / schema / 存档仓库
  queries/                   只读查询服务（稳定 DTO，供 UI 使用）
  runtime.py                 运行时路径与存档目录
  ui_v2/
    navigation.py            Route / Router / 面包屑 / 页面状态缓存
    components/              EntityLink / EntityTable / PageHeader / FilterBar / EmptyState / 全局搜索
    pages/                   主页 / 比赛 / 赛事 / 球队 / 球员 / 转会 / 选秀 / 历史 / 存档
    main_window.py           路由式应用外壳
build_macos_ui_v2_app.sh     macOS 构建脚本
build_windows_ui_v2.bat      Windows 构建脚本（冻结）
足球模拟器总配置.json          总配置
release/macos/               macOS 可运行版与启动器
tests/                       规则冻结 / 事务 / 查询 / 页面验收测试
Reviews/                     项目审视与分阶段实施报告
```

## GitHub 发布建议

- 仓库中提交源码、构建脚本、README、`足球模拟器总配置.json` 和 `release/` 目录。
- 不建议把 `.venv*/`、`build*/`、`dist*/`、`saves/`、`__pycache__/` 提交进源码仓库。
- Windows zip 超过 GitHub 普通仓库单文件限制，因此放在 GitHub Releases 中作为下载附件。
