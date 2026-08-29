# 阶段 0 基线报告：工作树保护与玩法冻结

> 日期：2026-08-28
> 执行：ZCode（GLM），Football Simulator 主实施与集成 Agent
> 性质：PROJECT_OVERHAUL_IMPLEMENTATION_PLAN.md 阶段 0 交付物（规则冻结测试集、
> 当前行为说明、已知缺陷列表）。本阶段不做视觉重构，不改任何玩法规则。

## 1. 工作树保护方式

- **未执行任何 commit / push / reset / checkout**。全部工作以增量方式叠加在
  用户当前工作树之上；用户的 24 个已修改文件原样保留。
- 测试全部通过 `runtime.set_save_root_override` 写入独立临时目录，项目
  `saves/`（用户存档 `default`、`main`）零接触。
- 一次测试基建缺陷曾把生成器存档写入 `saves/freeze/`（非用户数据），已当场
  删除，并加入 `assert_save_root_isolated` 防呆闸防止复发。

### 1.1 未跟踪文件依赖风险（确认存在，提交时必须整体处理）

| 未跟踪文件 | 被谁依赖 |
|---|---|
| `football_simulator/ui_v2/pages/large_table_page.py` | `main_window.py:24` 硬导入（`git show HEAD` 中无此导入） |
| `release/macos/Run Football Simulator UI v2 Local.command` | 已修改的 `README.md` 与已修改的 `Open Football Simulator UI v2.command`（改为 exec 该脚本） |
| `Reviews/` 六份文档与 `Reviews/ui_audit/` | 文档性内容 |

结论：**任何未来提交若包含 `main_window.py` 或 `README.md` 的改动而遗漏上述
未跟踪文件，会直接产出不可导入/文档断链的仓库状态。** 在获得单独授权提交时，
必须将工作树作为整体提交，并用干净目录 checkout 验证可导入。

### 1.2 本阶段新增/修改的文件

新增：

```text
tests/__init__.py
tests/support.py                          # 测试基建（临时存档根、种子随机源、赛季推进、指纹采集）
tests/test_schedule_calendar_freeze.py    # 赛程与赛历冻结
tests/test_match_engine_freeze.py         # 比赛引擎冻结
tests/test_three_season_regression.py     # 三赛季状态机回归
tests/test_settlement_awards_freeze.py    # 结算/评分/身价/奖项冻结
tests/test_transfer_draft_freeze.py       # 转会/选秀/能力审核冻结
tests/test_persistence_paths.py           # 持久化与路径（含缺陷记录断言）
tests/test_baseline_fingerprint.py        # 三赛季种子基线指纹对照
tests/generate_baseline.py                # 基线生成器（python3 -m tests.generate_baseline）
tests/baseline/three_season_fingerprint.json  # 188KB 基线（seed=20260828）
tests/README.md                           # 测试说明与缺陷记录
```

修改（仅最小测试注入接口，无任何公式/权重/周次/分支变化）：

```text
football_simulator/runtime.py   # save_root() 增加 _SAVE_ROOT_OVERRIDE 测试重定向钩子
football_simulator/data.py      # load_save_config/ensure_save_config 增加可选 rng 参数（默认 SystemRandom）
football_simulator/state.py     # 8 处 random.SystemRandom() 收敛为 _rng()；新增 set_rng_provider()；
                                # initialize/draft/week-49 的 load_save_config 传入同一 rng（调用顺序不变）
```

生产默认行为不变：`_rng()` 默认返回 `random.SystemRandom()`，与改造前一致。

## 2. 规则冻结测试结果

```text
python3 -m unittest discover -s tests
Ran 50 tests in 88.9s — OK
```

- 50 个用例覆盖：赛程/日历常量、引擎可复现性与统计语义、三赛季端到端结构
  不变量、结算与奖项结构、转会/选秀/能力审核流程、路径边界、三赛季指纹。
- 基线指纹由两个独立进程分别生成并逐字节一致（验证了无哈希序依赖）。
- 基线内容抽检：S1 无杯赛冠军；S2 优胜者杯+挑战杯冠军、超级杯空缺；S3 三杯
  齐全；每季 Top20 与四项赛事奖项得主齐全 —— 与设计时序一致。

## 3. 当前行为说明（刻画结论，供后续阶段遵循）

以下行为在固定随机源下被测试锁定，重构不得无声改变：

1. **出场口径**：球员 `appearances` = 其当前注册球队在 premier+cup 比赛日的
   出场次数（团队口径），不区分首发/分钟。
2. **统计聚合范围**：快照 `player_stats` 只累计 `premier_matchdays` 与
   `cup_matchdays` 的球员增量；`second_matchdays`、`playoff_matchdays` 的球员
   统计保留在 `simulated_weeks` 原始数据中但不聚合。
3. **统计行范围**：`player_stats` 只含真实球员（来自 `player_registry`，赛季 1
   为 50 人）；默认球员无统计行。`_record_stat` 跳过非真实球员（工作树用户新行为）。
4. **结算口径**：冬窗（24 周）/赛季末（49 周）结算只统计 premier+cup 比赛日；
   无杯赛赛季中次级联赛球员无评分/身价；夏窗转入一级联赛的球员按 ≤49 周口径
   现算评分、不进第 49 周缓存；身价下限 8.0；默认球员身价恒为空。
5. **待办时序**：冬窗三周与夏窗三周各生成转会待办；第 49 周生成能力审核
   （40% 池）+ 选秀待办；待办存在时 `simulate_next_week` 阻塞；第 52 周（夏窗）
   结束后仍会残留一批转会待办，需处理完才能初始化新赛季。
6. **杯赛时序**：第 1 赛季无杯赛；第 2 赛季优胜者杯+挑战杯；第 3 赛季起超级杯
   （依赖往届冠军）。
7. **积分等式**：`Σpoints = 3×380 − 平局场数`（表格平局行数为双倍计）。
8. **选秀**：目标人数 6–10（`candidate_count` 预设，内联随机仅为兜底）；
   新秀 `initial_market_value=30.0`；池按赛季单调增长且 ID 唯一。

## 4. 已知缺陷列表（本阶段只记录不修复）

> 阶段 1 进展：缺陷 1、2 已修复（SQLite 事务 + 路径白名单/包含性校验），
> 详见 `Reviews/PHASE1_REPORT.md`；其余缺陷保持记录状态。

| # | 缺陷 | 位置 | 处理归属 |
|---|---|---|---|
| 1 | ~~非原子写盘，无备份/校验~~ 已修复（阶段 1 SQLite 事务） | `state.py::_write_state_json` | ✅ 阶段 1 |
| 2 | ~~路径边界：接受 `..\evil`/`con` 等；无包含性校验~~ 已修复（白名单 + resolve 包含性 + 删除保护） | `runtime.py::normalize_save_name`、`delete_save_directory` | ✅ 阶段 1 |
| 3 | 球员双 ID 体系：`real::<slug>`（注册/统计）与 `real::<显示名>`（历史键）并存 | `data.py::real_player_id` vs `state.py::_player_history_key_for_player` | 阶段 2：稳定 ID 收敛 |
| 4 | 能力审核按姓名做决策键，同名球员互相覆盖 | `state.py::apply_ability_review_decisions` | 阶段 2/3：改按稳定 ID（需与 UI 决策载荷一起改，先提案） |
| 5 | 杯赛小组同分裁决每次查看现场随机（展示层非确定性） | `state.py::_winners_cup_group_standings*` | 阶段 2：裁决结果落盘复用（行为变化需提案确认） |
| 6 | 快照全量重放 O(n²)、UI 每次刷新重建全部页面 | `state.py::build_snapshot_from_state`、`main_window.py` | 阶段 2/7：查询投影 + 增量化 |
| 7 | 转会后球队统计按当前球队归属（历史失真，展示层有 `_player_season_team_display` 部分修正） | `state.py` | 阶段 1 已存比赛当时归属数据（`player_match_stats.team_id` + tenures 视图）；聚合口径变化需提案 |
| 8 | 当前 UI 仍存在“前 N 条 + 查看完整”与小框滚动模式 | `ui_v2/pages/*` | 阶段 3–5 重构 |

## 5. Windows 冻结确认

本阶段未触碰任何 Windows 专属文件。零 diff 验证见第 6 节。

## 6. 阶段 0 验收清单对照

- [x] 记录 git status、未跟踪文件和依赖关系（第 1 节）
- [x] 保护 `large_table_page.py` 与本地启动器不被遗漏（第 1.1 节，未做任何提交）
- [x] 赛程、比赛、积分、结算、转会、选秀、奖项行为刻画测试（50 用例）
- [x] 测试专用可复现随机源，生产默认不变
- [x] 三赛季结构不变量固化（不写死随机比分；逐周比分进基线指纹文件）
- [x] 测试能在干净临时目录创建存档、推进三赛季、重载并验证实体数量、赛程
      完整性、积分约束、奖项结构和待办状态
