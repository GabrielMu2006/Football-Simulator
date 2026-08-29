# Football Simulator 项目整体审视报告

## 审视信息

- 审视日期：2026-08-28（Asia/Shanghai）
- 审视模型：**ZCode（智谱 GLM）**
- 审视对象：`main` 分支当前工作树，基准提交 `b5051b4`，叠加 24 个已跟踪文件的未提交修改与 2 个未跟踪文件（`large_table_page.py`、`Run Football Simulator UI v2 Local.command`）
- 审视方式：全量源码静态阅读；项目副本复制到系统临时目录做端到端两赛季冒烟；只读加载现有存档；offscreen 模式构建 GUI 主窗口；存档名路径边界行为实测；构建脚本、spec、依赖与仓库统计核对
- 约束遵守：本次只新增本报告；未修改任何既有项目文件。所有模拟与路径实验均在 `/tmp` 副本中进行，项目 `saves/` 未被触碰
- 与既有报告的关系：`Reviews/` 内已有四份同类报告，本报告为独立完成，主要结论与其交叉印证；工作树提交一致性风险与杯赛激活时序验证为本报告新增内容

## 总结结论

这是一个玩法闭环完整、内容广度可观的本地足球经理式模拟器。两级联赛、38 轮赛程、三类杯赛、升降级附加赛、冬夏转会窗、选秀、能力审核、荣誉与历史归档全部真实连通。本次审视在临时目录副本中连续跑通两个完整赛季（共 104 周），杯赛从第 2 赛季起完整进行并正确归档冠军，阵容与球员 ID 完整性全程无恙，说明核心状态机的实际完成度是可靠的。

主要短板不在玩法而在工程保障：存档写盘非原子、路径校验存在缺口、Windows 构建配置引用了不存在的文件、零自动化测试，以及 `state.py` 在本次工作树中又增长了约 700 行、持续向"上帝模块"淤积。此外，当前工作树存在一个时点性风险：未跟踪的新文件已被已修改的跟踪文件硬依赖，若按现状部分提交会直接产出无法导入的仓库状态。

### 综合评价

| 维度 | 评分 | 评价 |
| --- | ---: | --- |
| 功能完整度 | 8.5/10 | 玩法系统丰富，两赛季冒烟验证杯赛/选秀/审核/归档全链路真实可用 |
| 核心逻辑正确性 | 7.5/10 | 多赛季冒烟通过、完整性约束贯穿始终，但缺自动化回归且旧入口残留 bug |
| 架构与可维护性 | 5.5/10 | 引擎/UI 分层清楚，但 `state.py` 已达 3941 行且仍在增长 |
| 数据安全 | 4.5/10 | 非原子写盘 + 路径边界缺口，唯一用户数据（存档）缺乏保护 |
| 构建与发布 | 5.5/10 | macOS 本地产物可用，Windows 配置有阻断项，版本管理缺失 |
| 文档与可上手性 | 7/10 | README 玩法与构建说明扎实，杯赛激活时序与架构要求未写明 |
| **整体** | **6.7/10** | **产品完成度上乘，工程保障需要一次集中加固** |

## 已验证内容

1. 全部 33 个 Python 源文件在 Python 3.9.6 下编译通过，与 README "需要 Python 3.9+" 的声明一致。
2. 只读加载现有 `default`（第 1 赛季第 2 周）与 `main`（第 3 赛季第 28 周、2 个归档赛季）存档均成功；两队 40 支球队阵容均为 11 人，真实球员 ID 无重复。
3. 临时副本连续两个完整赛季端到端冒烟通过（总耗时约 10 秒）：第 1 赛季 52 周纯联赛（冠军产生、历史归档）；第 2 赛季 52 周联赛 + 优胜者杯（小组赛 6 轮 → 双回合 1/4 决赛、半决赛、决赛）+ 挑战杯（32 强 → 决赛）全部进行，冠军正确写入 `cup_state` 与赛季归档；期间转会全批准、能力审核全通过、选秀完成，结束后全部阵容仍为 11 人、63 个真实球员 ID 全部唯一。
4. 杯赛从第 2 赛季（优胜者杯/挑战杯）与第 3 赛季（超级杯）才激活是设计使然——`_initialize_cup_state`（`state.py:1812-1865`）需要上赛季排名做参赛与种子依据；第 1 赛季三个杯赛周为空周。
5. offscreen 模式（`QT_QPA_PLATFORM=offscreen`，PySide6 6.10.3）构建 `MainWindow` 成功：13 个页面、13 个导航项，默认存档快照正常载入。
6. 存档名校验实测：`normalize_save_name` 接受 `..\evil`、`con`、`nul.txt`；`_state_path('../outside')` 解析到 `saves/../outside/state.json`，即直接 API/CLI 输入可越出 `saves/`（UI 服务层有 `normalize_save_name` 防护，CLI `main.py` 无）。
7. `Football Simulator UI v2 Windows.spec` 的 `datas` 同时引用根目录 `football_simulator_config.json`，该文件在根目录不存在，Windows 构建在数据收集阶段即会失败。
8. 仓库统计：Git 跟踪 372 个文件，其中 322 个位于 `release/`（含约 31 MB 的 macOS zip 与完整解压 `.app` 二进制树），仓库对象约 100 MiB。
9. 当前工作树中，未跟踪的 `football_simulator/ui_v2/pages/large_table_page.py` 被已修改的跟踪文件 `main_window.py` 无条件导入（`git show HEAD` 中无此导入）；已修改的 `README.md` 引用了未跟踪的 `release/macos/Run Football Simulator UI v2 Local.command`。

未执行的部分：没有在真实显示器上操作 GUI（offscreen 构建不等于交互验证）；没有在 Windows 主机上执行打包；没有验证 README 中的远程下载链接；没有审计 macOS 发布物签名（前序报告已做，本次未重复）。

## 做得好的地方

### 1. 多赛季状态机是真实可靠的

本次独立复现了两赛季全流程：联赛 38 轮 × 2 级、杯赛小组赛到决赛、附加赛、三窗口审核、选秀、归档、次赛季重建（含升降级后的阵容迁移），全程无异常、无脏数据。对一个约 1.3 万行的个人项目而言，这是最核心的资产。

### 2. 杯赛设计有依据且分层清晰

优胜者杯用上赛季一级联赛前 16 名分 4 组循环，挑战杯覆盖一级全部 + 次级前 12 名并按排名设种子，超级杯第 3 赛季才引入且依赖往届冠军。参赛资格、种子排序、双回合/单场规则都有明确的数据来源，不是随手拼凑。

### 3. 完整性约束贯穿所有写路径

`Team.__post_init__` 强制 11 人与 1/4/3/3 位置约束；交易应用前后各有 `_validate_roster_integrity`；本次工作树新增的 `_normalize_rosters_and_registry` 在每次加载/写盘时自愈修复阵容与注册表，实测两个旧存档加载正常。防线层层叠加，脏数据很难存活。

### 4. 转会审核的重算机制考虑了真实边界

提案基于生成时的阵容，玩家分批批准后可能失效。`_regenerate_trade_for_current_teams` 会在原交易不可用时基于当前阵容重算并标注"系统重算通过"，无法重算时明确记录"系统拒绝"及原因，而不是静默丢弃或崩档。

### 5. UI v2 的服务层与页面拆分是正确的方向

`SimulatorUIService` 是 UI 与核心状态之间的唯一通道；13 个页面各司其职；`widgets.py` 收敛了表格/卡片/徽章样板。本次工作树新增的 `LargeTablePage` 把原先散在五六个页面的"截断前 N 条 + 查看全部"模式统一成一个带"§ "分组行样式的大表页面，是明确的质量改进。

### 6. 引擎层零依赖且随机源已可注入

`models/match_engine/schedule/data` 纯标准库；`simulate_match(fixture, rng)` 接受外部 rng——这为将来写确定性测试留好了口子（缺口在 `state.py` 直接使用 `SystemRandom`，见下文）。

## 主要问题与建议

### P1：状态文件写入不是原子的，异常中断可能损坏唯一存档

证据：`state.py:3702-3706` 直接 `Path.write_text` 覆写 `state.json`，无临时文件、无 `os.replace`、无备份、无 `fsync`。

影响：每周模拟、三类审核、选秀都整文件覆写。写盘中途崩溃/断电/磁盘写满会留下半截 JSON，加载端只会报"存档文件无效"，没有任何恢复路径。存档是本游戏唯一的用户数据，这是最高优先级风险。

建议：同目录写 `state.json.tmp` → `fsync` → `os.replace` 原子替换；替换前滚动保留一份 `state.json.bak`；为状态增加 `schema_version` 并在加载时校验。

### P1：存档路径没有形成可靠的目录边界

证据（实测）：`runtime.py:165-171` 的 `normalize_save_name` 只拒绝 `/` 与 `.`/`..`，接受 `..\evil` 与 Windows 保留设备名 `con`/`nul.txt`；`state.py:3679-3680` 的 `_state_path` 完全不做规范化，`../outside` 可解析到 `saves/` 之外。

影响：Windows 上 `..\` 是真实分隔符，通过 CLI（`main.py` 把 `--save` 原样传入）或直接 API 可越界读写甚至配合 `delete_save_directory` 之外的行为造成破坏；UI 服务层有防护，但防线只在一层。

建议：存档名收敛为有限字符集白名单（拒绝 `\`、`:`、控制字符与 Windows 保留名）；所有路径入口统一走一个校验函数；`resolve()` 后做"必须位于 `save_root().resolve()` 之下"的最终包含性检查。

### P1：Windows 构建配置引用了仓库中不存在的文件

证据：`Football Simulator UI v2 Windows.spec` 的 `datas` 引用根目录 `football_simulator_config.json`（不存在）；`build_windows_ui_v2.bat` 直接使用该 spec，无预复制步骤；英文副本目前只在 macOS 构建脚本中生成。

影响：在 Windows 上按当前脚本重新构建，PyInstaller 分析阶段预期直接失败。README 描述的 Windows 发布流程当前不可复现。

建议：bat 脚本在调用 PyInstaller 前复制中文配置为英文副本，或从 spec 中移除该输入；补一次 Windows 上的最小构建冒烟并纳入发布清单。

### P1：当前工作树存在"部分提交即断链"的时点风险

证据：未跟踪的 `large_table_page.py` 被已修改的 `main_window.py` 硬导入；已修改的 `README.md` 引用未跟踪的 `Run Football Simulator UI v2 Local.command`。

影响：若按常规"提交已修改文件"操作而遗漏 `git add` 两个新文件，提交后的仓库状态在导入 `main_window` 时即崩溃，README 指向不存在的脚本。本次审视时点下这是发布一致性的直接风险。

建议：尽快把工作树作为一个整体提交（显式 `git add football_simulator/ui_v2/pages/large_table_page.py "release/macos/Run Football Simulator UI v2 Local.command"`），并在提交前用干净目录 checkout 验证可导入。

### P2：零自动化测试，且核心层随机源不可注入

全仓库无测试文件与 CI。更关键的是 `state.py` 全部使用 `random.SystemRandom()`（初始化、周模拟、审核、选秀多处），不可注入种子，状态机测试只能做不变量与冒烟断言，无法做确定性回归。`match_engine` 已经接受外部 rng，缺口只在上层。

建议：把 `state.py` 的随机源收敛到单点（如模块级 `_rng()` 或向公开函数增加可选 `rng` 参数），即可为赛程不变量（每队 38 场、对阵两次）、两赛季状态机、交易/选秀后阵容完整性补上第一批测试。

### P2：`state.py` 持续向"上帝模块"淤积

本次工作树又为 `state.py` 增加 707 行（交易重算、转会历史、阵容自愈、归档查询），总量达 3941 行、约 130 个函数，同时承担存储、迁移、联赛、杯赛、附加赛、转会、选秀、评分、荣誉与查询投影。每项新功能都在加剧维护风险。

建议：以当前两赛季冒烟为行为基线，按 `storage` / `competitions` / `transfers` / `draft` / `settlement` / `queries` 分域拆分；新功能先进独立模块，存量代码渐进迁移。

### P2：快照全量重放是明确的性能天花板

`build_snapshot_from_state`（`state.py:565-749`）每次从全部 `simulated_weeks` 重放所有比赛重建表格与统计；`simulate_next_week` 一次普通周至少调用 2 次（第 234、302 行），结算周/休赛周 3-4 次，单赛季整体是 O(n²) 重放。当前规模实测 10 秒跑完两赛季尚无感知，但 GUI 每次刷新还无条件重建全部 12 个页面，历史存档到 10+ 赛季时会明显变慢。

建议：为表格与统计维护增量缓存（本周只加新 matchday），快照重放仅在加载时执行一次；UI 刷新只更新当前可见页面。

### P2：GUI 线程阻塞与能力审核按姓名做决策键

- 全 UI 无任何 `QThread`/`threading`，`_simulate_week`（`main_window.py:251-260`）在 GUI 线程同步执行整周模拟与 JSON 写盘，期间界面完全冻结。
- 能力审核以球员姓名为决策键（`state.py:332` `decisions.get(item["name"])`、`season_overview_page.py:206`），且配置校验不检查真实球员姓名唯一性——同名球员的审核会互相覆盖，`player_pool` 同样按姓名索引。转会审核用 `trade_id` 才是正确做法，能力审核应改用 `player_id`。

### P2：仓库把约 100 MiB 二进制混入源码历史

`release/` 下 322 个跟踪文件包含完整 `.app` 二进制树与约 31 MB 的 zip，同一内容双份存储；`.app` 内还重复打包了配置 JSON。二进制不可 diff，每次重签名全量变更，克隆与历史持续膨胀。建议发布物迁至 GitHub Releases（README 已有此实践），仓库只保留构建脚本与校验和；应用版本号目前是 `0.0.0` 且无 tag，无法对应源码版本。

### P3：旧入口的确定性残留 bug

- `main.py:160` 与 `gui_app.py:221` 用 `{"long_break", "short_break"}` 判断休赛周，而 `schedule.py` 实际产出 `winter_break/summer_break/open_week`——休赛周永远落到"本周没有比赛"通用分支，文案错误但无害。
- `interactive_cli.py:955-968` 球队选择表"名次"列填的是一级+次级拼接后的全局序号（1-40），不是联赛内排名。
- `Football Simulator.spec`、`Football Simulator CLI.spec` 的 `pathex` 硬编码开发者本机绝对路径；`build_macos_app.sh` 的 `--add-data "saves:saves"` 依赖被 gitignore 的目录，干净 checkout 上必然失败；`season.py` 整文件与 `placeholder_page.py` 无任何调用方；`runtime.py:39-47` 冻结配置回退分支不可达；`runtime.py:30` Linux 落到 macOS 路径。tkinter 旧 GUI（`gui_app.py`/`mac_app.py`）与旧 CLI 建议整体退役，至少在 README 标注"仅 UI v2 受支持"。

### P3：文档缺口与魔法数字

杯赛从第 2/3 赛季才激活是核心玩法规则，README 未提；`requirements-ui-v2.txt` 的 `PySide6>=6.10,<6.11` 与 `pyinstaller>=6.0` 策略矛盾且无锁文件；macOS 构建脚本与本地启动器均硬编码 `arch -arm64`（README 未标注 Apple Silicon 限定）；`match_engine.py` 的全部调参系数、结算周 24/49、荣誉积分表等魔法数字没有任何设计意图注释。

## 建议实施顺序

1. **先恢复一致性**：整体提交当前工作树（务必包含两个未跟踪文件），让仓库回到可构建状态。
2. **保护存档**：原子写盘 + 滚动备份 + `schema_version`；存档名白名单与包含性校验统一收口。
3. **修复发布阻断**：补 Windows spec 的英文配置输入；版本号与 tag 建立；README 标注架构限定。
4. **建立最低测试网**：先收敛 `state.py` 随机源为可注入，再补赛程不变量、两赛季状态机、交易/选秀完整性三组测试。
5. **结构治理**：`state.py` 分域拆分、退役旧入口与死代码、快照增量缓存、周模拟移入 QThread。
6. **分发治理**：发布物迁 GitHub Releases，仓库保留构建材料与校验和。

## 最终评价

这个项目最可贵的地方是"模拟世界真的连起来了"：第 2 赛季的优胜者杯小组赛积分、双回合淘汰的种子保护、转会重算的边界处理、跨赛季的阵容迁移，全部是可实测验证的行为，而不是界面上的承诺。以个人项目衡量，玩法完成度处于上乘水平；`LargeTablePage` 一类的近期改动也说明作者正在主动偿还 UI 层的重复债。

当前不需要推倒重来，需要的是把"能玩"托举到"敢发布"：原子存档与路径校验保护用户数据，一次整体提交消除断链风险，第一批自动化测试锁定已验证的行为，然后逐步给 3941 行的 `state.py` 减负。完成 P1 项与最低测试网后，项目整体质量有望从 6.7/10 进入可持续公开发布的区间。
