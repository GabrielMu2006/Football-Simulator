# Football Simulator 项目整体审视与评价

> 审视方式：对项目源码、配置、构建脚本、说明文档做静态阅读与交叉核对，未实际运行游戏，也未对任何已有文件做修改。
> 本报告仅新增于 `Reviews/` 目录。
> 生成模型：**DeepSeek（通过 DeepSeek Harness Web GUI 会话执行）**。
> 补充说明：同目录另存在 `Reviews/project_review_2026-08-25_codex_gpt5.md`（OpenAI Codex / GPT-5 生成），本报告与其相互独立，未对该文件做任何修改；本报告以静态阅读为主，运行级验证请参考该报告。

---

## 1. 审视范围

- 入口与前端：`main.py`（旧命令行）、`mac_app.py` + `gui_app.py`（旧 tkinter GUI）、`terminal_main.py` + `interactive_cli.py`（Rich 交互式 CLI）、`ui_v2_main.py` + `football_simulator/ui_v2/`（当前推荐的 PySide6 图形版）。
- 核心域：`data.py`、`models.py`、`match_engine.py`、`schedule.py`、`season.py`、`state.py`、`runtime.py`。
- 工程与发布：PyInstaller spec、macOS/Windows 构建脚本、`release/` 目录、`README.md`、配置文件与两份数据草稿 MD。
- 规模：约 **31 个 Python 文件、约 13,100 行**；其中 `state.py` 单文件 3,941 行，是最大的复杂度集中点。

## 2. 项目概览

这是一个本地运行的“旁观式管理”足球联赛模拟游戏：玩家创建/选择存档，初始化 40 支虚拟球队与 200 名真实球员池，按 52 周日历推进赛季，体验双联赛、升降级附加赛、优胜者杯 / 挑战杯 / 超级杯、能力变动审核、交易审核、选秀、荣誉与历史存档。

数据通过 `足球模拟器总配置.json` 驱动，新存档会生成随机化的存档专属配置；打包版把配置放入应用资源/同级目录，存档则落在系统用户数据目录（macOS `~/Library/Application Support/Football Simulator/saves`，Windows `%APPDATA%\Football Simulator\saves`）。

## 3. 做得好的地方

1. **功能完整度较高**：联赛、杯赛、升降级、交易、选秀、能力成长、身价/评分、历史荣誉、多存档管理均有实现，且 README 对玩法与发布流程描述清晰。
2. **核心领域模型清晰**：`models.py` 用 dataclass 表达 `Player/Team/TableRow/PlayerStatDelta/SeasonReport` 等，`Team` 在初始化时强校验 1-4-3-3 阵型，避免非法阵容进入后续流程。
3. **状态重构思路可圈可点**：`state.py` 把赛季结果保存为 `simulated_weeks` 日志，`build_snapshot_from_state` 每次从日志重算积分、球员统计、评分与身价，天然支持旧存档归一化与“修复后重算”。
4. **运行时路径抽象**：`runtime.py` 区分源码运行与 PyInstaller 打包运行，处理外置配置优先级、用户数据目录、默认存档种子，跨 mac/Windows 方向正确。
5. **UI v2 完成度高**：配色/主题统一，导航与页面分层清晰，存在“待办自动跳转”（能力审核/转会/选秀），比赛中心、杯赛中心、球队/球员/历史页面覆盖较全，交互体验明显优于旧前端。
6. **数据防御**：配置校验覆盖球队数量、唯一性、位置容量、能力值范围；阵容完整性校验（40 队、11 人、真实球员位置上限、重复 ID）贯穿初始化/交易/选秀。
7. **发布文档完整**：本地签名提示、GitHub Releases 附件策略、源码运行方式、构建步骤都有说明。

## 4. 主要问题与风险

### 4.1 高风险（建议尽快处理）

| 编号 | 问题 | 位置 | 影响 |
|---|---|---|---|
| H1 | **旧前端仍在使用旧字段/旧语义**：`main.py` 与 `gui_app.py` 读取 `week_data.get("matchdays")`，而 `state.py` 早已改为 `premier_matchdays / second_matchdays / cup_matchdays / playoff_matchdays`，因此“已模拟联赛轮次”永远显示 0；同时它们用 `result.matchdays`（只含一级联赛）渲染每周赛果，杯赛周、次级联赛、升级附加赛会被判定为“本周没有比赛”（如 W28 超级杯决赛、W46-49 附加赛）。 | `main.py:159/193`、`gui_app.py:220/252` | 旧入口功能失真，若用户仍运行会得到错误信息。 |
| H2 | **“初始化赛季”缺少防误操作保护**：`initialize_save_state` 允许在赛季进行中重新初始化，且 UI 的“初始化赛季”按钮始终可用；一旦误点，当前未完成赛季会被覆盖并直接进入“下一赛季”，不会归档、无确认提示。 | `state.py:147-220`、`ui_v2/main_window.py:239-249` | 可能直接丢失玩家进度。 |
| H3 | **Windows 打包引用不存在的文件**：`Football Simulator UI v2 Windows.spec` 的 `datas` 包含 `("football_simulator_config.json", ".")`，但仓库根目录没有该文件（只有 `dist-ui-v2` 与 `release/macos/...` 内有英文名副本），`build_windows_ui_v2.bat` 直接使用该 spec，构建很可能在打包阶段报“文件不存在”。 | `Football Simulator UI v2 Windows.spec:13-16`、`build_windows_ui_v2.bat:20-25` | Windows 发布流程不可复现。 |
| H4 | **存档名路径校验不完整**：`normalize_save_name` 只拒绝 `.`、`..` 与 `/`，未拒绝反斜杠 `\`、系统路径分隔符、保留名等；在 Windows 上 `create_save_directory` / `delete_save_directory` 可能创建嵌套目录或越权路径。 | `runtime.py:165-171` | 本地单机应用风险有限，但属于输入校验缺口；删除存档时风险放大。 |
| H5 | **存档写入非原子、无自动备份**：`_write_state_json` 直接 `path.write_text(...)`，进程崩溃/断电可能写坏 `state.json`；工作区已出现 `saves/main/state.before_roster_fix.json`，说明历史上有过手工抢救，但机制没有产品化。 | `state.py:3702-3706`、`saves/main/` | 存档损坏后无自动回滚，玩家损失不可恢复。 |

### 4.2 中风险（影响长期维护）

| 编号 | 问题 | 位置 | 影响 |
|---|---|---|---|
| M1 | **无自动化测试、无 CI、无 lint/类型检查配置**：全仓库未发现测试文件；对 13k 行、尤其 `state.py` 这种高状态逻辑，回归风险很高。 | 仓库全局 | 后续改动（尤其杯赛/交易/选秀/存档迁移）缺少安全网。 |
| M2 | **前端版本过多且重复**：`main.py`、`gui_app.py`、`interactive_cli.py`、`ui_v2` 四套入口并行，指标字典、格式化逻辑、页面结构高度重复；`season.py`（旧联赛模拟器）已无任何模块引用，属于死代码。 | `season.py`、四套入口 | 改动容易只更新 UI v2，旧入口继续运行旧行为。 |
| M3 | **UI 依赖核心层私有函数**：`cups_page.py` 直接 import `_winners_cup_group_standings_from_snapshot`（私有/下划线开头）。 | `ui_v2/pages/cups_page.py:21` | 核心函数改名/重构会破坏 UI；应提供公开只读接口。 |
| M4 | **交易后球队统计归属可能失真**：`build_snapshot_from_state` 从当前阵容 `player_registry` 建立 `player_stats_map`，再按 `player_stats.team_name`（当前队伍）累加球队赛季数据；冬窗/夏窗转会的球员，其整个赛季进球会记到最终球队头上（界面用 `_player_season_team_display` 做了展示层修正，但团队统计底座仍是这个口径）。 | `state.py:657-665` 附近 | 球队历史数据可能不准确，影响荣誉/排行公平性。 |
| M5 | **配置/数据校验不完整**：未校验真实球员姓名唯一性；只要求 `real_players >= 50`，未要求总数为 200；未校验草稿池（剩余 150 人）数量与位置；能力值等默认参数没有显式写在配置里，而是散落在代码默认值中，用户不易发现。 | `data.py:236-329` | 非法或“瘦身”配置可能导致选秀池提前耗尽、ID 冲突。 |
| M6 | **构建脚本可移植性差**：macOS 构建与本地启动脚本硬编码 `arch -arm64`（仅 Apple Silicon）；旧 `Football Simulator.spec` / `Football Simulator CLI.spec` 的 `pathex` 硬编码本机绝对路径；UI v2 构建未指定图标/版本号（版本信息停留在默认 0.0.0）。 | `build_macos_ui_v2_app.sh:19-24`、`Football Simulator.spec:6`、`Football Simulator CLI.spec:6` | 跨机器/跨架构复现构建困难。 |
| M7 | **文档与实际结构有小偏差**：README 写“推荐发布 `dist-ui-v2/Football-Simulator-UI-v2-macOS` 文件夹”，但实际 `dist-ui-v2/` 是 app + 外置配置平铺，发布文件夹是在 `release/macos/` 手工组装；README 对旧入口未标注“已废弃”。 | `README.md:160-175`、`dist-ui-v2/*` | 新手照文档操作可能找不到目标文件夹/接口行为不符。 |
| M8 | **工程卫生**：工作区混有 `.venv-ui-v2`、`dist-ui-v2`、`build-ui-v2`、`.pyinstaller-config-ui-v2`、`release/...app`、`.DS_Store`、`saves/` 状态与备份文件，且未发现 `.gitignore`（也未发现 `.git`）。 | 仓库根目录 | 仓库体积大、容易把运行产物误提交；建议建立 git + .gitignore + CI。 |

### 4.3 低风险/观察项

1. `WeekSimulationResult.matchdays` 属性只返回一级联赛，命名上容易误导调用方（旧前端正是踩了这个坑）；建议删除或改名，并让旧前端显式遍历四个赛事列表。
2. `main.py` / `gui_app.py` 判断休赛期用的是 `{"long_break","short_break"}`，与全新赛历的 `winter_break/summer_break/open_week` 不一致。
3. 全程使用 `random.SystemRandom()`，只依赖系统熵，没有可复现种子；`season.py` 提供了 `seed` 参数但已是死代码，调平衡性/回归测试困难。
4. UI 层大量使用 `except Exception`（约 10 处），错误提示粒度粗，定位问题时难以区分“存档不存在/数据非法/系统异常”。
5. `runtime.user_data_root()` 对非 Windows、非 macOS 平台统一返回 `~/Library/Application Support/...`，Linux 用户路径不符合习惯（当前目标平台可接受）。
6. 配置文件中文文件名可能造成部分系统/压缩工具乱码；项目已提供英文副本缓解，但仍建议把英文名副本作为仓库根目录的正式默认文件之一（可选）。

## 5. 量化评价

| 维度 | 评分 | 简评 |
|---|---|---|
| 功能完整度 | 9.0 / 10 | 玩法覆盖广，联赛/杯赛/交易/选秀/荣誉/存档都已落地。 |
| 架构分层 | 7.5 / 10 | 核心域分层清楚；但 state.py 单文件 3,941 行，承担了归档、杯赛、交易、选秀、结算、序列化等过多职责。 |
| 代码可维护性 | 6.5 / 10 | 命名与注释总体规范，防御性校验多；重复前端与巨型状态文件拖累维护。 |
| 健壮性 | 7.0 / 10 | 阵容/配置校验扎实；但旧接口字段漂移、无原子写、无路径转义补齐。 |
| 测试与工程化 | 3.5 / 10 | 无自动化测试、无 CI、无 lint/类型检查配置、无 .gitignore。 |
| 文档 | 8.5 / 10 | README 覆盖玩法、配置、存档、构建、发布；仅少量路径/接口描述与现状不一致。 |
| 发布与跨平台 | 6.0 / 10 | macOS 产物已就位；Windows spec 引用了缺失文件，macOS 构建 arm64-only。 |
| 用户体验（UI v2） | 8.5 / 10 | 主题美观、页面完整、待办聚焦好；数据可信度受交易归属/旧接口问题影响。 |
| 数据安全 | 5.5 / 10 | 有存档目录隔离，但缺少原子写、自动备份、赛季初始化误操作保护。 |

**综合评价：约 7.2 / 10（B+ 档）**。

项目当前状态：**功能与打磨超出“玩具原型”，接近“可长期游玩的个人作品”；但距离“可放心交付/持续迭代的工程”还差一层工程化投入**。

## 6. 改进优先级建议

### P0（不改功能也可能出事）

1. 修复旧前端字段语义（或明确标记废弃并只保留 UI v2 作为唯一入口）。
2. 为“初始化赛季”增加确认弹窗 + 仅在赛季结束/无状态时允许，或先自动归档。
3. 修复 Windows spec 缺失的 `football_simulator_config.json`（生成副本或从 datas 移除）。
4. `normalize_save_name` 使用 `os.sep`/`Path` 校验，拒绝 `\`、`..\`、保留名，并做路径规范化。
5. `_write_state_json` 改为“临时文件 + os.replace 原子替换”，并为每次写入保留 `state.json.bak`。

### P1（提升可维护性与可信度）

6. 从 `state.py` 按职责拆分：`archive.py`、`cup.py`、`trade.py`、`draft.py`、`settlement.py`、`serialization.py`；引入状态 `schema_version` 与迁移表。
7. 引入 pytest + 少量黄金用例（配置校验、赛程生成、联赛结算、交易后阵容完整性、杯赛冠军推进、赛季归档回放），并将 `random.SystemRandom` 改为可注入 `rng`（支持 seed）。
8. 建立 `.gitignore`（`.venv*`、`build*`、`dist*`、`.pyinstaller-*`、`saves/`、`__pycache__/`、`.DS_Store`）并初始化 Git + GitHub Actions 基本 CI（compile + pytest + 可选 mac/win 打包冒烟）。
9. 统一前端策略：保留 UI v2 为唯一正式入口，删除或归档 `main.py`/`gui_app.py`，避免旧接口长期漂移。
10. 把 `_winners_cup_group_standings_from_snapshot` 等私有读取转为 state 公开函数（`get_*` 系列），UI 不再依赖核心私有 API。

### P2（体验与长期演进）

11. 将转会后的赛季数据按“球员在某队时段的统计”拆分，提升球队/球员历史可信度。
12. 配置补充显式默认字段（能力值、范围、市场价值参数）并校验 200 人唯一性与草稿池完整性。
13. macOS 构建改为检测 CPU 架构/可配置 `TARGET_ARCH`，加图标与版本号；README 同步实际发布布局。
14. 增加存档自动备份/导出/导入功能，把现有手工 `state.before_roster_fix.json` 的做法产品化。

## 7. 结论

这是一个**完成度高、题材有趣、分层思路不错**的足球模拟器个人项目：核心玩法闭环完整，UI v2 视觉与交互成熟，配置/数据校验也做了不少工作。真正拉低评价的并不是“功能少”，而是**工程化与数据安全**：旧前端仍在按过期字段运行、赛季初始化可无提示覆盖进度、Windows 打包引用缺失文件、`state.py` 单文件过重、没有测试与 CI。

如果按“个人自玩 + 持续迭代”标准，项目已达到 **B+ / 7.2 分**；如果按“对外发布 + 长期多人维护”标准，建议先完成 P0 与 P1（约 2-3 天工作量），评分可提升至 8.5 分以上。

---

*本审视基于当前工作区静态内容，未修改任何已有文件；如后续代码发生变化，结论需重新评估。*

**生成者/模型：DeepSeek（通过 DeepSeek Harness Web GUI 会话执行）**
