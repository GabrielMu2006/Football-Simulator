# Football Simulator UI v2

一个本地运行的足球联赛模拟游戏。游戏以“旁观式管理后台”为主要体验：玩家创建存档、初始化球队与球员，然后按周推进赛季，查看联赛、杯赛、交易、选秀、荣誉和球员历史。

当前推荐游玩版本是图形版：

```text
Football Simulator UI v2.app
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

## macOS 游玩方式

下载或构建完成后，打开：

```text
dist-ui-v2/Football Simulator UI v2.app
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

## 存档位置

源码运行时，存档在项目目录：

```text
saves/
```

打包后的 macOS `.app` 运行时，存档在：

```text
~/Library/Application Support/Football Simulator/saves
```

Windows 版运行时，存档在：

```text
%APPDATA%\Football Simulator\saves
```

## 从源码运行

需要 Python 3.9+。

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-ui-v2.txt
.venv/bin/python ui_v2_main.py
```

## 构建 macOS `.app`

```bash
chmod +x build_macos_ui_v2_app.sh
./build_macos_ui_v2_app.sh
```

构建产物：

```text
dist-ui-v2/Football Simulator UI v2.app
```

## 制作 macOS 发布压缩包

推荐发布整个 `dist-ui-v2/Football-Simulator-UI-v2-macOS` 文件夹，其中包含 `.app`、README 和外置配置文件：

```bash
cd dist-ui-v2
/usr/bin/zip -qry --symlinks ../Football-Simulator-UI-v2-macOS.zip Football-Simulator-UI-v2-macOS
```

## 构建 Windows 版

Windows `.exe` 需要在 Windows 系统上构建。把整个项目复制到 Windows 后运行：

```bat
build_windows_ui_v2.bat
```

构建产物：

```text
dist-windows-ui-v2\Football Simulator UI v2\Football Simulator UI v2.exe
```

## 项目结构

```text
football_simulator/
  data.py              配置读取与球队/球员创建
  match_engine.py      比赛模拟
  schedule.py          赛程生成
  state.py             存档、赛季推进、杯赛、交易、选秀、荣誉
  runtime.py           运行时路径与存档目录
  ui_v2/               图形界面

ui_v2_main.py          UI v2 入口
build_macos_ui_v2_app.sh
build_windows_ui_v2.bat
足球模拟器总配置.json
```

## GitHub 发布建议

- 仓库中提交源码、构建脚本、README 和 `足球模拟器总配置.json`。
- 不建议把 `.venv/`、`build*/`、`dist*/`、`saves/`、`__pycache__/` 提交进源码仓库。
- `.app` 或 `.zip` 更适合放在 GitHub Releases 中作为下载附件。
