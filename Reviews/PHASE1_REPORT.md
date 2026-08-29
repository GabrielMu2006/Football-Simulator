# 阶段 1 报告：SQLite 持久化与应用事务

> 日期：2026-08-28
> 执行：ZCode（GLM），Football Simulator 主实施与集成 Agent
> 前置：阶段 0 规则冻结测试集（`Reviews/PHASE0_BASELINE.md`）
> 边界遵守：玩法公式/权重/赛制零修改；未编写旧档迁移器；Windows 专属文件零 diff。

## 1. 交付内容

### 1.1 新增持久化层 `football_simulator/persistence/`

| 模块 | 职责 |
|---|---|
| `connection.py` | 连接管理：`foreign_keys=ON`、`journal_mode=WAL`、`synchronous=FULL`、`busy_timeout=5000`；`user_version` 校验（空库/未来版本拒绝）；`BEGIN IMMEDIATE` 事务原语 |
| `schema.py` | 全部 DDL：`save_meta`、`seasons`、`teams`、`players`、`real_player_pool`、`season_runtime`、`season_archives`、`matches`、`match_events`、`player_match_stats`、`player_settlements`、`transfers`、`drafts`、`pending_actions`、`awards` + `player_team_tenures` 派生视图；`schema_version=1` |
| `save_repository.py` | 工作字典（与旧 state.json 同形）↔ 规范化表双向转换；赛季赛程预建与稳定 `match_id`；归档按内容变化 upsert |

### 1.2 状态机接线（`state.py`）

- `_load_state_json` / `_write_state_json` / `_state_path` 替换为 repository 版本；
- 5 个公开 API（初始化、模拟下一周、能力审核、转会审核、选秀）全部包在
  `_state_transaction` 中：**BEGIN IMMEDIATE → 物化 → 冻结的模拟逻辑 → 持久化 → COMMIT**，
  任何异常（含 COMMIT 失败）整体回滚并关闭连接；
- 生产随机默认不变（`random.SystemRandom`），rng 消费顺序与阶段 0 完全一致。

### 1.3 关键设计决定

1. **稳定 `match_id`**：建档时即为两级联赛全部 760 场生成 `m-<赛季>-<类别>-w<周>-r<轮>-o<序>`
   的 scheduled 行；杯赛/附加赛在产生时按同一规则获得 ID。已完成的比赛行**不可变**。
2. **`player_match_stats` 按比赛当时注册阵容写入**（每场 22 行 `appeared=1`，六项可为 0），
   `team_id` 为比赛当时所属球队 —— 满足方案“以比赛当周 tenure 归属计数”的数据要求；
   `player_team_tenures` 用视图从该表派生。**聚合/奖项公式未改动**（现行为把统计按
   当前球队归属；改动该口径属玩法变化，留待提案）。
3. **旧档零兼容**：只认 `saves/<存档名>/save.sqlite3`；目录中只有旧 `state.json` 时
   视为未初始化（有测试证明不探测、不迁移）。
4. **历史不再每周重写**：`season_archives` 按赛季号 upsert 且内容不变时不写；
   归档时派生规范化 `awards` 行供阶段 2 查询。
5. **路径安全收口**：`normalize_save_name` 白名单（中文/字母/数字/空格/下划线/连字符，
   1–64 字符，拒绝点号、分隔符、Windows 保留设备名）；`delete_save_directory`
   拒绝符号链接、越出根目录、根目录本身（先查 symlink 再 resolve，防借链接越界删除）。

## 2. 验收结果

### 2.1 冻结测试全绿（含跨持久化时代指纹对照）

```text
python3 -m unittest discover -s tests
Ran 62 tests — OK（约 108 秒）
```

- **62 用例 = 阶段 0 的 50 个规则冻结用例 + 12 个阶段 1 新增事务/路径用例**；
- **三赛季基线指纹（JSON 时代生成）在 SQLite 持久化下逐字节复现** —— 这是
  “用 SQLite 替代整份 JSON 覆盖写，不改变游戏结果”的最强验收；
- 事务/故障/并发用例全部通过：注入持久化失败、注入提交失败、未提交即关连接
  （模拟进程中断）、第二写者被拒（busy 后清晰报错）、串行成功、schema 版本守卫、
  空库/损坏元数据处理。

### 2.2 过程中发现并修复的真实缺陷

| 缺陷 | 后果 | 修复 |
|---|---|---|
| `_state_transaction` 的 COMMIT 在 try/except/else 的 else 子句中执行 | else 中抛错不被捕获 → 连接泄漏、事务句柄残留、后续事务全部“不可嵌套” | 重构为内层 try/except + finally 清理 |
| `delete_save_directory` 先 resolve 后查 symlink | 符号链接检查恒为假，可借链接越界删除（测试复现了该攻击路径） | 先查 symlink/存在性，再 resolve 做包含性判断 |
| persist 每周用当前阵容重写全部已完成比赛的 `player_match_stats` | 转会后历史比赛归属被腐蚀 → 转会估值变化 → 随机流分歧（被基线指纹捕获，**共 2555 处差异**） | 已完成比赛行不可变，详情只在首次完成时写入 |
| `_load_result_player_stats` 编辑残留产生两个同名方法，旧版按当前 team_id JOIN | 转会后物化丢失球员历史统计行（同上被指纹捕获） | 删除旧定义，仅按 player_id 关联 |
| 物化只含“有已完成比赛”的周 | 休赛周/空杯赛周条目缺失（52 → 42 周） | 按日历补齐第 1..current_week 全部周条目 |
| players 主键误用 slot_number | `slot_number` 是位置内编号，非全队唯一 | 新增 `roster_index` 保存阵容顺序（顺序影响引擎随机选择，必须精确） |
| teams UPSERT 在升降级互换时 ordinal 冲突 | 赛季 2 初始化失败 | 两阶段更新：先临时置负再写入最终序（team_id 稳定不变） |
| 赛季中途重新初始化的孤儿数据 | legacy 语义是“丢弃未完成赛季、递增赛季号”（非同赛季重开）；SQLite 下若不清空被放弃赛季的比赛行，其 completed 行会残留并污染后续查询 | 初始化时清空被放弃的不完整赛季数据（归档不受影响） |

### 2.3 性能实测（开发机：Apple Silicon / Python 3.9.6）

| 指标 | 数值 |
|---|---|
| 每周推进耗时 | S1 105ms/周 → S3 196ms/周（增长来自已知的快照全量重放 O(n²)，阶段 2 查询投影与阶段 7 增量化处理） |
| 3 赛季数据库体积 | 18.6 MB（2487 场比赛、54,714 行 player_match_stats、3 份归档） |
| 快照加载 | 约 67ms/次（第 3 赛季） |
| 赛程预建 | 每赛季一次（save_meta 标记防重复） |

对比基线：旧 JSON 每“存档整个历史”重写一次，两赛季即 9.26MB 单文件；
新架构归档只在赛季结束时写一次。

### 2.4 UI 兼容冒烟

`SimulatorUIService` 依赖的 state/runtime/data API 签名未变，实测通过：
建档（含中文存档名）、初始化、推周、读快照、选秀日志、球队中文名、删除存档。
UI 页面本身的重构属阶段 3–5。

## 3. 存档目录新布局

```text
saves/<存档名>/
  config.json      # 存档专属配置（与旧版相同，继续由 data.py 管理）
  save.sqlite3     # 唯一状态存储（schema_version=1）
```

工作区现有 `saves/default`、`saves/main` 为旧格式：新版本不会读取，也不会破坏
其内容；删除动作按计划留到阶段 6，且仅删除项目工作区内明确确认的存档。

## 4. 边界确认

- 玩法规则：零修改（指纹逐字节一致即证明）；
- 旧档迁移/兼容层：未编写；
- Windows 冻结清单：`build_windows_ui_v2.bat`、`Football Simulator UI v2 Windows.spec`、
  `release/windows/` —— `git status` 零输出；
- 工作树：未执行任何 git 提交类操作；用户已有修改原样保留；
  未跟踪依赖文件（`large_table_page.py`、`Run Football Simulator UI v2 Local.command`）
  与本次新增文件同在工作树中，任何未来提交需整体处理。

## 5. 遗留与后续阶段衔接

- **阶段 2（查询投影）**：`matches`/`player_match_stats`/`awards`/`transfers`/
  `player_settlements` 表与 `player_team_tenures` 视图已就绪；球员赛季总计与
  赛事合计的查询一致性测试将直接建在其上。注意已知缺陷 #3（双 ID 体系）需在
  DTO 设计中收敛。
- **阶段 7（性能）**：快照重放 O(n²) 与“每 persist 重物化整档”是下一个性能
  目标；本阶段已把写放大从“全历史 JSON”降到“当前赛季表”。
- 未完成项：无（阶段 1 任务清单 1–8 全部完成）。
