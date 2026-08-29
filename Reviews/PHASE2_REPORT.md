# 阶段 2 报告：Query Service 与只读 DTO（并行 Agent 协作）

> 日期：2026-08-28
> 执行：ZCode（GLM）主 Agent 集成；C1/C2/D 三个子 Agent 并行实施
> 前置：阶段 0（规则冻结）、阶段 1（SQLite 持久化）
> 边界遵守：玩法规则零改动；查询层不导入 state.py/persistence；Windows 零 diff；无 git 提交。

## 1. 架构与分工

```text
ui_v2 页面（阶段 4/5 迁移）
        ↓ 只读 DTO（frozen dataclass）
football_simulator/queries/          ← 阶段 2 新增
  base.py            连接/稳定 ID 原语/赛季解析（主 Agent 契约层）
  player_queries.py  球员目录/赛季档案/生涯/趋势/奖项（Agent C1）
  match_queries.py   比赛列表/统一详情(未赛+已赛)/相邻比赛（Agent C1）
  team_queries.py    球队目录/球队赛季档案（Agent C2）
  competition_queries.py  六赛事 overview/profile/积分榜/签表/榜单（Agent C2）
  history_queries.py 赛季归档/跨赛季历史（Agent C2）
  dashboard_queries.py    首页快照（Agent C2）
        ↓ 只读 SQL（PRAGMA query_only=ON）
persistence（SQLite，阶段 1）
```

**主 Agent 先行冻结的共享地基**（避免并行冲突）：
- `football_simulator/domain/`：从 state.py **原样抽出**评分/身价公式
  （`formulas.py`）与排名链（`standings.py`：积分→净胜球→进球→相互战绩→裁决），
  state.py 保留 re-import shim，行为零变化（63 项冻结测试含指纹全绿验证）；
- `queries/base.py` 契约层：只读连接、`PlayerRef/TeamRef/SeasonRef/CompetitionRef/
  MatchRef` 稳定 ID 原语、赛季解析、默认球员 ID 合成规则；
- Route/Router 参数 schema（§7.1 的 14 个路由）作为 Agent D 的接口冻结。

**稳定 ID 收敛（已知缺陷 #3 的落地）**：查询层统一以 `real::<姓名slug>` 为真实球员
规范 ID；归档/奖项的 legacy 键 `real::<显示名>` 与结算表的姓名键在 DTO 边界
转换为规范 ID；默认球员以 `default:<team_id>:<slot_number>` 合成（无跨赛季身份）。

## 2. 数据口径决定（有意与旧快照不同，已在契约中注明）

| 项 | 旧快照 | 查询层（阶段 2 起） |
|---|---|---|
| 统计来源 | 重放 premier+cup 比赛日 | `player_match_stats`（全部比赛，含次级/附加赛） |
| 转会归属 | 当前球队（缺陷 #7） | 比赛当时球队（§12.1 要求，tenure 正确） |
| 出场 | 球队出场数 | appeared 行计数（每场在册 22 人显式化） |
| 赛事合计 | 不支持 | **各赛事分段之和 == 赛季总计（测试锁定）** |

评分/身价仍用冻结公式：结算点值取自 `player_settlements`；赛事评分按现有公式
从该赛事数据推导（展示时标注"按现有公式推导"）。

## 3. 验收结果

```text
系统 Python： python3 -m unittest discover -s tests
  → Ran 175 tests — OK (skipped=26 Qt 用例无 PySide6 时跳过)
UI venv：     QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests
  → Ran 175 tests — OK（含 54 项 Qt 组件 offscreen 用例）
```

- 175 = 63 冻结（阶段 0+1，含三赛季基线指纹）+ 18（C1）+ 30（C2）+ 10（跨域一致性，主 Agent）+ 64（D，含路由纯 Python 用例与 Qt 组件用例）；
- **跨域一致性测试**（`tests/test_query_consistency.py`，主 Agent 编写）：分段合计==赛季总计、
  生涯==赛季和、转会球员多队分段、比赛详情↔球员比赛日志互证、球队↔赛事积分榜一致、
  首页榜单↔赛事榜单一致、历史冠军==积分榜第 1 —— 全部通过；
- 一致性测试当场抓到并修复一个真实缺陷：首页与赛事详情的榜单同分裁决链不一致
  （dashboard 缺少能力值步骤），已对齐为 统计↓→评分↓→能力↓→名称↑ 并用测试锁定。

## 4. 三个 Agent 的关键交付与偏差（均已评审接受）

**C1（球员/比赛）**：
- 目录行按"出场最多球队"为主队 + `additional_teams`；已完结赛季只收录该赛季出场过的球员（active 赛季收录全部 440 人以覆盖第 0 周）；
- 发现并如实呈现引擎事实：`_record_stat` 跳过默认球员 → "球员进球之和==比分"不成立（测试按 `≤` 断言），附加赛无事件行；
- 被 Select秀/交易替换的默认球员不可寻址（KeyError），其统计仍按 ID 合并进口径。

**C2（球队/赛事/历史/首页）**：
- `division` 过滤按该赛季实际参赛分区（升降级历史正确）；played=0 时 rank=None；
- 杯赛签表按 `(round_number, match_id)` 确定性排序（满足 §8.4"不得依赖无序集合"）；
- 附加赛冠军无归档时按引擎两回合规则（总比分→客场进球→高种子）确定性推算。

**D（导航外壳/组件）**：
- `navigation.py` 纯 Python（0 Qt 导入）：14 路由 schema 校验、`to_path/parse` 互逆（URL 转义含 `real::` 与中文）、Router back/forward/截断/200 上限/页面状态缓存；
- `EntityLink`：单击+Enter 导航、hover/focus 态、青色 token（取自现有 widgets 青色）；
- `EntityTable`：model/view + 代理排序（数值列按原始值比较）、双击+Enter 双路径激活、无嵌套滚动断言（真实纵向滚动面恰为表格自身一个）；
- 实测结论记录在测试注释：Qt 6.10 的 QTableView Return 不再自动发 `activated`，已在内部 keyPressEvent 显式补发。

## 5. 边界确认

- 查询模块导入审计：仅 标准库 + queries.base + domain + models；**零 state/persistence/UI 导入**；
- `navigation.py` 零 Qt 导入；组件零 state/queries 导入（占位 ViewModel 由测试提供）；
- Windows 冻结清单 `git status` 零输出；无 git 提交操作；用户工作树原样保留；
- 玩法行为零变化：63 项冻结测试（含三赛季指纹）全绿。

## 6. 下一阶段衔接（阶段 3/4）

- 阶段 3 余项：把 `MainWindow` 接到 Router（导航/侧栏/顶部上下文栏/全局搜索/待办入口）——组件与路由已就绪，页面按阶段 4/5 逐个迁移；
- 阶段 4 顺序：比赛详情 → 球员 profile → 球队 profile → 对应目录页，全部直接消费本阶段 DTO；
- 性能：查询均为索引 SQL，单次 profile 查询毫秒级（快照重放 O(n²) 仅存于旧快照路径，UI 迁移完成后即可绕开）。
