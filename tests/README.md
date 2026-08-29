# 规则冻结测试套件（阶段 0 交付物）

本目录是 PROJECT_OVERHAUL_IMPLEMENTATION_PLAN.md 阶段 0 的交付物：在大改开始前，
用自动化测试刻画并冻结当前工作树的玩法行为。**这些测试是后续所有阶段的合并门槛：
任何未走提案流程的玩法变化（概率、公式、赛制、周次、结算口径）都会让本套件失败。**

## 运行方式

```bash
# 全量（系统 Python，约 170 秒；Qt 组件测试在无 PySide6 时自动跳过）
python3 -m unittest discover -s tests

# 全量（含全部 Qt 页面测试，使用 UI venv 的 offscreen 模式——最终验收口径）
QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests

# 单个模块
python3 -m unittest tests.test_schedule_calendar_freeze

# 重新生成三赛季基线指纹（仅在批准的玩法变化或基线格式升级后运行）
python3 -m tests.generate_baseline
```

测试全部在独立临时目录中运行（`runtime.set_save_root_override`），随机源替换为
固定种子（`state.set_rng_provider`），**不会读写项目 `saves/` 目录**。若批量写流程
在未隔离状态下运行，`tests.support.assert_save_root_isolated` 会直接拒绝执行。

## 测试模块与冻结范围

| 模块 | 冻结内容 |
|---|---|
| `test_schedule_calendar_freeze.py` | 38 轮×380 场、每对两次、每队 19 主；52 周日历的 kind/周次/杯赛事件映射；冬窗 25–27、夏窗 50–52、附加赛 46–49；结算周 24/49；`WEEK_CUP_EVENTS` 等常量表 |
| `test_match_engine_freeze.py` | 同种子完全可复现；六项统计语义；真实球员统计过滤；零封/扑救归属；主场加成 0.030 与压力分截断；`ROLE_WEIGHTS`、`EVENT_MINUTES` |
| `test_three_season_regression.py` | 三赛季端到端：赛程完整性、积分约束（含平局计分等式）、升降级/附加赛衔接、历史归档、杯赛第 2/3 赛季按时序激活、选秀池单调增长、阵容完整性、待办阻塞与解除、**赛事合计==赛季总计**、**快照统计聚合范围=premier+cup 比赛日** |
| `test_settlement_awards_freeze.py` | 冬窗/赛季末结算缓存写入时机与内容；评分/身价公式（受控输入直接锁定系数）；身价下限 8.0；结算口径（次级联赛球员在无杯赛赛季无结算）；Top20 结构 |
| `test_transfer_draft_freeze.py` | 转会窗口触发与批准/拒绝/系统重算状态语义；转会历史留痕字段；选秀候选池、初始身价 30.0、池游标；能力审核条目形状与 60–88 范围 |
| `test_persistence_paths.py` | 状态写读回环；缺失存档报错；存档目录创建/删除；**以“记录当前实际行为”方式固化两个已知安全缺陷**（见下） |
| `test_persistence_transactions.py` | 事务回滚（持久化失败/提交失败）、未提交即关连接（进程中断模拟）、并发写锁、schema 版本守卫、稳定 match_id、赛季中途重初始化丢弃语义 |
| `test_baseline_fingerprint.py` | 固定随机源下三个完整赛季的逐周比分、积分榜、杯赛冠军、升降级过渡、Top20 与赛事奖项得主与 `tests/baseline/three_season_fingerprint.json` 完全一致 |
| `test_query_player.py` / `test_query_match.py` | 阶段 2：球员赛季档案/生涯/目录查询与比赛列表/详情/相邻比赛查询（C1） |
| `test_query_team.py` / `test_query_competition_history_dashboard.py` | 阶段 2：球队/赛事/历史/首页查询（C2），含积分榜约束与榜单确定性 |
| `test_query_consistency.py` | 阶段 2 集成：分段合计==赛季总计、生涯==赛季和、转会多队分段、比赛详情↔比赛日志互证、球队↔赛事积分榜一致、首页↔各域一致、历史冠军==积分榜 |
| `test_navigation_shell.py` | 阶段 3 前期：Route/Router/面包屑/页面状态缓存（纯 Python）+ EntityLink/EntityTable/PageHeader/FilterBar/EmptyState（PySide6 offscreen；无 PySide6 时自动跳过） |
| `test_page_matches.py` | 阶段 4：比赛中心全量主表/筛选持久化/行激活；比赛详情（已赛完整事件+22 行球员数据、未赛赛前页）、上一场/下一场、零嵌套滚动、双尺寸截图 |
| `test_page_players.py` | 阶段 4：球员目录；球员个人页六页签（概览/各赛事/赛季历史/奖项（个人与球队荣誉分区）/比赛记录/评分身价轨迹）、赛季选择器、转会分段、默认球员空状态、链接合同、零嵌套滚动 |
| `test_page_teams.py` | 阶段 4：球队目录（40 队/分区过滤）；球队详情六页签（概览/阵容/赛程/赛季历史/转会/奖项关联）、升降级分区、链接合同、零嵌套滚动 |
| `test_main_window_shell.py` | 阶段 3 收尾：路由外壳（侧栏=路由镜像、后退/前进、面包屑）、全局搜索、待办徽标、模拟后导航、存档切换清空页面状态、防重复提交 |

## 基线指纹

`tests/baseline/three_season_fingerprint.json`（seed=20260828，含主配置 sha256）
是“同随机源对照”验收工具。生成器与对照测试分属不同进程，已验证跨进程
（含 PYTHONHASHSEED 随机化）完全一致。后续阶段 1/2 的结构性重写必须保持
该指纹通过，或附提案后有意重新生成并说明差异原因。

## 已知缺陷记录（冻结为当前行为，修复时必须有意识翻转断言）

1. ~~**非原子写盘**~~ **已修复（阶段 1）**：状态写入改为 SQLite 事务（`BEGIN
   IMMEDIATE` + WAL + `synchronous=FULL`），见 `test_persistence_transactions.py`。
2. ~~**存档路径边界缺口**~~ **已修复（阶段 1）**：`normalize_save_name` 白名单
   （中文/字母/数字/空格/下划线/连字符，1–64 字符）+ Windows 保留名拒绝 +
   删除的符号链接/包含性校验。原 `KNOWN_DEFECT` 断言已翻转为拒绝语义。
3. **球员双 ID 体系**：注册表/比赛统计 ID 为 `real::<姓名slug>`，而历史键
   `_player_history_key_for_player` 为 `real::<显示名>`。阶段 2 稳定 ID 设计必须收敛。
4. **结算口径**：球员统计与结算只累计 premier+cup 比赛日；次级联赛球员在无杯赛
   赛季没有评分/身价；夏窗转入一级联赛的球员按 ≤49 周口径现算、不进第 49 周缓存。
5. **能力审核按姓名做决策键**：同名球员会互相覆盖（当前池内无重名，故未触发）。
6. **杯赛小组同分裁决**：`_winners_cup_group_standings*` 每次查看现场创建随机源
   做同分裁决，展示层非确定性（模拟结果本身在固定随机源下可复现）。
7. **赛季结束残留待办**：第 52 周模拟后立即生成新转会待办，需处理完才能初始化
   新赛季（`tests.support.run_season` 已按此语义封装）。
8. **快照全量重放 O(n²)**：每赛季耗时随周数增长（实测 S1 105ms/周 → S3 196ms/周，
   开发机 Apple Silicon / Python 3.9）。阶段 2 查询投影与阶段 7 增量化处理。

## 依赖关系提示

`tests/support.py` 依赖阶段 0 加入的两个最小注入接口：`runtime.set_save_root_override`
与 `state.set_rng_provider`（含 `data.load_save_config` 的可选 rng 参数）。生产默认
行为不变：随机源仍是 `random.SystemRandom`，存档根目录解析逻辑未变。
