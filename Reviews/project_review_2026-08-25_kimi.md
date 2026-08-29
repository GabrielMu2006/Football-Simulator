# 足球模拟器项目整体审视报告

- **审视模型**：Kimi Code CLI（Moonshot AI / 月之暗面）。注：运行环境未向模型暴露具体模型版本号，故只能注明产品名。
- **审视日期**：2026-08-25
- **审视方式**：只读全量代码审查（核心引擎、UI v2、工程化配置三路并行），未改动任何已有文件。

---

## 一、项目概况

这是一个 Python 编写的足球经理式模拟器：40 支虚构球队分两级联赛（各 20 队、38 轮循环赛），叠加 200 人真实球员池、三种杯赛（优胜者杯/挑战杯/超级杯）、升降级附加赛、冬夏转会窗、选秀、能力审核、赛季结算与历史归档。52 周赛历驱动，状态持久化在 `saves/<存档名>/state.json`。

项目共有**四套入口并存**：

| 入口 | 界面 | 状态 |
|---|---|---|
| `ui_v2_main.py` → `football_simulator/ui_v2/` | PySide6 GUI（约 4900 行） | 主力，功能超集，有 macOS/Windows 打包脚本 |
| `main.py` | argparse CLI（404 行） | 可用，但有残留 bug（见下） |
| `terminal_main.py` → `interactive_cli.py` | rich 交互式终端（1554 行） | 可用 |
| `mac_app.py` → `gui_app.py` | tkinter 旧 GUI（503 行） | 历史遗留，功能子集，建议下线 |

核心引擎（`models.py` / `match_engine.py` / `schedule.py` / `data.py`）纯标准库、无第三方依赖；UI 层仅依赖 PySide6 + PyInstaller。

## 二、架构评价

**分层意图是好的**：引擎层小而清晰，`ui_v2/services.py` 的 `SimulatorUIService` 是 UI 与核心之间唯一的服务层，UI 页面通过构造注入回调 + `set_snapshot` 统一刷新，dataclass 与类型标注覆盖较好，配置校验（`data.py:236-329`）和阵容完整性校验（`state.py:1460-1485`）做得相当认真。README（222 行）质量很高，玩法、三平台构建/发布步骤、项目结构俱全——这是整个工程做得最好的一块。

**主要结构问题**：

1. **`state.py` 是 3941 行、约 100 个函数的"上帝模块"**——杯赛、附加赛、转会、选秀、奖项、归档、序列化全部淤积在一起，是本项目最大的结构债。建议按 cup / transfer / draft / awards / serialization 拆分。
2. **UI 层直接消费核心层的原始 dict**（`simulated_weeks`、`cup_state`、`pending_draft` 等，如 `pages/matches_page.py:202`、`pages/cups_page.py:277`），等于把 JSON 序列化格式当成了公开协议；`cups_page.py:21` 甚至导入了 `state.py` 的下划线私有函数，绕过服务层。
3. **三套前端（tkinter GUI / CLI / ui_v2）各写一遍展示逻辑**，核心层每加一个功能要重复接线。ui_v2 已是功能超集，旧 tkinter GUI（`gui_app.py` / `mac_app.py` / `Football Simulator.spec`）已具备下线条件。

## 三、关键 Bug 与隐患（按优先级）

**确定性 bug：**

1. **休赛期判断永远为假**：`main.py:160` 与 `gui_app.py:221` 用 `{"long_break","short_break"}` 判断休赛周，但 `schedule.py` 实际产出的是 `winter_break/summer_break/open_week`——赛历改版后的残留 bug。
2. **Windows 构建大概率失败**：`Football Simulator UI v2 Windows.spec` 的 datas 引用根目录的 `football_simulator_config.json`，该文件在根目录**并不存在**，PyInstaller 遇到缺失 data 源会直接报错。
3. **能力审核按球员姓名做决策键**：`pages/season_overview_page.py:206` 用 `item["name"]` 作 `decisions` 键，同名球员会互相覆盖、名字对不上则静默拒绝；转会审核用 `trade_id` 才是正确做法。
4. **`runtime.py:43-47` 死代码**：`shared_config_path()` 已提前 return，后面"复制配置到用户目录"的分支永远不会执行。
5. **跨平台**：`runtime.py:30-32` Linux 分支落到了 macOS 路径 `~/Library/Application Support/`；`build_macos_ui_v2_app.sh` 硬编码 `arch -arm64`，Intel Mac 无法构建；`runtime.py:169` 存档名只拒绝 `/`，未拒绝 `\`、`:`，Windows 上可造成路径逃逸。
6. **CLI 显示缺漏**：`main.py:172` 只遍历一级联赛 matchdays，次级/杯赛/附加赛结果完全不显示；`interactive_cli.py:963-965` 球队选择表的"名次"列填的是跨联赛全局序号（1-40），不是联赛内排名。

**高风险隐患：**

7. **零测试**。全仓库没有任何测试文件。一个 4000 行状态机 + 概率引擎 + 多套杯赛赛制却零回归保障，是本项目最大的工程风险。且随机源全部是 `random.SystemRandom()`（`state.py:239`），不可注入种子、对局不可复现——补测试前需要先重构随机源注入。
8. **存档写入非原子**：`_write_state_json`（`state.py:3702-3706`）直接整文件覆写，进程中断即毁档，无备份/回滚（`state.before_roster_fix.json` 这个手工备份文件恰好是教训的痕迹）。`delete_save_directory` 直接 `shutil.rmtree` 无回收站。
9. **UI 线程阻塞**：全仓库没有任何 `QThread`/`threading`。`_simulate_week`（`ui_v2/main_window.py:251-260`）在 GUI 线程同步执行整周模拟 + JSON 读写，期间 UI 完全冻结、macOS 会报"应用无响应"。
10. **性能**：`build_snapshot_from_state()`（`state.py:565-749`）每次从全部 `simulated_weeks` **重放所有比赛**重建快照，且单周内最多被调用 4 次，整赛季是 O(n²) 重放开销；它还原地 mutate 入参 dict（`state.py:566`），存在双重规范化。UI 侧 `MainWindow._refresh_views` 每次刷新无条件重建全部 12 个页面。当前规模尚可接受，但属于明确的扩展性天花板。

## 四、工程化与仓库卫生

- **严重：约 119MB 的 `.app` 二进制发布包被提交进 git**（`release/` 下 325 个跟踪文件，含 Qt framework 二进制和 31MB zip；全仓库跟踪内容仅约 122MB）。二进制无法 diff、每次重签名全量变更。zip 已是最优分发形式，解压目录树应移出 git（或全部转 GitHub Releases）。
- 4 个 spec 文件中 3 个是 PyInstaller 残留物且无人使用，`pathex` 硬编码了开发者本机绝对路径；macOS 走命令行调 PyInstaller、Windows 走 spec，风格不统一。
- 同一份总配置 json 在仓库跟踪内容里出现 4 次（根目录 / release / .app 内 Frameworks 与 Resources）。
- `build_macos_app.sh` 的 `--add-data "saves:saves"` 依赖一个已被 gitignore 的目录，干净 checkout 上构建会失败；且 README 完全没记录这个脚本。
- 无分支、无 tag：README 链接的 GitHub Release `v0.1.0-windows` 无法对应到源码版本；无 CHANGELOG、无版本号管理（`CFBundleShortVersionString` 写死 `0.0.0`）。
- 依赖管理：`PySide6>=6.10,<6.11` 区间过窄而 `pyinstaller>=6.0` 无上限，策略矛盾；无锁文件、无 CI，构建不可严格复现。
- `.gitignore` 本身完善（.DS_Store 未入库，好）；根目录的 `build-ui-v2/`、`dist-ui-v2/`、`.venv-ui-v2/`、`.tools/`（一整包 gh CLI）等杂物均已 ignore，只是本地工作区脏，另有 26 个未提交修改。
- 草稿文档 `team_name_draft_40.md` 里残留 AI 对话口吻文字（"我可以再帮你补……"），内容已进总配置 json，建议删除或移出仓库。

## 五、代码质量细节

- **优点**：命名清晰、领域词统一；新代码全部 `from __future__ import annotations` + 较完整类型标注；`ui_v2/widgets.py` 收敛了大量表格样板；Qt 对象树资源管理正确，无泄漏迹象。
- **重复**：`_slugify_*` 有三份近似实现；`PLAYER_METRICS`/`TEAM_METRICS`/`SCOPE_LABELS` 在 `main.py` 与 `interactive_cli.py` 整段重复；"截断前 N 条 + 查看全部跳大表"模式在五六页面各抄一遍；`main_window.py:337-366` 四个 `_open_*` 方法是同一循环的四份拷贝。
- **死代码**：`season.py` 整文件无调用方；`pages/placeholder_page.py` 无引用；`state.py:620-625` 的冬季结算统计算完从未使用；`_merge_player_registry`、`_player_market_value` 等无活调用。
- **注释稀缺**：引擎全部调参系数（`match_engine.py:86-104`、结算周 24/49、交易阈值 10.0、新秀身价 30.0 等魔法数字）无任何设计意图说明。
- **类型标注被动态属性破坏**：`build_metric_card` 往 `CardFrame` 实例上挂属性（`widgets.py:131-132`），导致几十处 `# type: ignore[attr-defined]`——定义一个 `MetricCard` 子类即可全部消除。
- 错误处理粒度不统一：宽 `except Exception`（`main_window.py:232`）、静默吞错（`draft_page.py:196`）与精确捕获混用，无"哪些弹窗、哪些降级"的统一约定。

## 六、总体评价

对于一个个人项目，本工程的完成度相当高：领域模型完整（两级联赛 + 三杯赛 + 附加赛 + 转会 + 选秀 + 审核 + 历史归档），配置校验和边界检查认真，UI v2 结构规整、功能丰富，README 是亮点中的亮点。引擎层（models/match_engine/schedule/data）小而干净，问题几乎全部集中在两处：**复杂度的无节制淤积**（`state.py` 3941 行、四套入口并存）和**工程保障的缺失**（零测试、二进制入库、非原子存档写、Windows 构建损坏）。

**优先行动清单**（按投入产出比排序）：

1. 修复 Windows 构建（补 `football_simulator_config.json` 或改 spec）。
2. 修复休赛期 kind 残留 bug（`main.py:160`、`gui_app.py:221`）与能力审核按名字匹配问题。
3. `state.json` 改原子写（写临时文件 + rename）并保留一份滚动备份。
4. 把 `release/macos/` 的解压 `.app` 目录树移出 git，给发布打 tag。
5. 随机源改为可注入种子，然后为 `match_engine` 和 `simulate_next_week` 补第一批测试。
6. 周模拟移入 `QThread`，消除 UI 冻结。
7. 中期：`state.py` 按领域拆分；退役 tkinter 旧 GUI 与冗余 spec/入口；删除死代码与草稿残留。

**一句话总结**：玩法与完成度是个人项目里的上乘水平，但"能跑"与"可维护"之间的差距主要体现在测试为零、状态模块过大和发布流程的手工化上——先修确定性 bug、保住存档安全，再谈拆分与清理。
