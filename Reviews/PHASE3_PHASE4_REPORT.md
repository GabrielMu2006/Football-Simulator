# 阶段 3+4 报告：路由外壳与核心实体页

> 日期：2026-08-29
> 执行：ZCode（GLM）主 Agent 集成与缺陷修复；D2（外壳）、E1（比赛）、E2（球员）、E3（球队）四个子 Agent 实施
> （期间发生平台级中断，主 Agent 完成中断恢复与全部收尾——详见第 4 节）
> 前置：阶段 0-2（规则冻结 / SQLite 持久化 / 查询层与导航组件）
> 边界遵守：玩法规则零改动（63 项冻结测试含指纹全绿）；Windows 零 diff；无 git 提交。

## 1. 交付内容

### 1.1 路由外壳（阶段 3 收尾，main_window.py 重构 + 新组件）

- Router 成为页面切换唯一事实源：侧栏点击 = router.navigate()；Router 观察者
  回调切换 QStackedWidget 并同步侧栏高亮——页面切换不再遍历侧栏文本。
- 侧栏按 §8.1 收敛为 9 项（主页/比赛/赛事/球队/球员/转会/选秀/历史/存档）；
  "本周战报"移入顶部栏按钮，模拟下一周成功后自动导航到 weekly_report 路由。
- 顶部上下文栏：后退/前进按钮（禁用态跟随 Router）、面包屑（含页面 route_context()
  显示名）、存档选择器、赛季/周次状态、"处理待办(N)"徽标、模拟下一周、全局搜索。
- 全局搜索（components/global_search.py）：250ms 防抖，球员前 6 + 球队前 4，
  单击/Enter 直接进入实体页；Esc 关闭；存档未初始化一律空结果。
- legacy 页面全部保留：未迁移路由（dashboard/season_overview/competition/transfers/
  draft/history/saves）挂 legacy 页面 + 适配层；legacy 回调（按球队名/球员名跳转）
  由外壳解析为稳定 ID 路由（_open_team 名称→team_id → Route("team", ...)）。
  competition 路由经 competition_hub_legacy.py（一级/次级积分榜 + 杯赛中心页签）。
- 存档切换时 router.clear_page_states()（§7.3：禁止跨存档实体历史）。
- 模拟/初始化期间按钮防重复提交；错误 QMessageBox 呈现。

### 1.2 核心实体页（阶段 4，全部消费阶段 2 查询 DTO）

页面契约：pages/entity_page_base.py（主 Agent 维护：PageContext + EntityPageBase）。

| 页面 | 路由 | 要点 |
|---|---|---|
| 比赛中心（matches_page.py 重写） | matches?season&competition?&week? | 全高主表（766 场全量，不再"前 12 条"截断）；行激活→比赛详情 |
| 比赛详情（match_detail_page.py 新建） | match?match=<id> | 已赛：比分板+全部关键事件（不截断）+22 行球员数据完整展开（外层单滚动面）；未赛：赛前页（两队积分摘要，无预测内容）；上一场/下一场限定赛事上下文 |
| 球员目录（players_page.py 重写） | players | 全高主表替代左侧窄列表；搜索/位置/球队筛选 |
| 球员个人页（player_profile_page.py 新建，1228 行） | player?player&season&tab? | §8.5 A-F 六页签全部落地：概览/各赛事数据（转会多队分段+合计行）/赛季历史/奖项（个人与球队荣誉严格分区）/比赛记录（全量 appeared 行）/评分身价轨迹（默认球员解释性空状态）；赛季选择器入路由 |
| 球队目录（teams_page.py 重写） | teams | 40 队全高主表；分区/搜索筛选 |
| 球队详情（team_profile_page.py 新建，1032 行） | team?team&season | §8.6 六页签：概览/阵容/赛程与结果/赛季历史/转会/奖项关联（个人奖与球队荣誉分区） |

滚动硬规则（§8.2）逐页验收：每个页面/页签恰有一个纵向滚动面（表格页=EntityTable
自身；内容页=单外层 QScrollArea，内部元素完整展开）；下拉弹出框按方案不计入。
两种验收尺寸（1440×860、1680×980）测试程序化断言 + 截图人工复核。

链接合同（§7.2）：球员/球队/比赛/赛事全部可点（单击 + 聚焦后 Enter），
hover 青色下划线 + 手型光标 + 焦点框；无双击依赖。

### 1.3 审计截图

Reviews/ui_audit/phase4/ 共 39 张（外壳 2 + 比赛页 6 + 球员页 12 + 球队页 12 +
目录页 4 + 轨迹/空状态等），覆盖每个页面/页签 × 两种尺寸。主 Agent 人工抽查
球员概览、比赛中心、球队概览与外壳 4 张：信息密度、单滚动面、口径标注
（"按现有公式推导"/"最近结算"）均符合规格。

## 2. 验收结果

最终验收（venv offscreen）：
  QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python -m unittest discover -s tests
  → Ran 272 tests — OK（223 秒）
系统 Python（Qt 用例自动跳过）：
  python3 -m unittest discover -s tests
  → Ran 211 tests — OK (skipped=62)
python3 -m compileall → 通过；Windows 冻结清单 git status 零输出

272 = 63 冻结（阶段 0+1）+ 102 查询/导航（阶段 2）+ 107 外壳与页面（阶段 3+4）。

## 3. 中断恢复过程（本轮的实际工作）

四个并行 Agent 派发时遭遇平台故障（验证码失败/并发超限），但 D2/E2/E3 在中断前
已落码、E1 未开始。恢复工作：

1. 盘点：确认外壳/球员页/球队页及其测试已落地，缺 E1 全部、外壳截图、若干修复。
2. 补齐 E1：单 Agent 后台重发（吸取并发超限教训），完成比赛两页 + 27 用例 + 6 截图。
3. 修复 7 个遗留缺陷/测试问题：
   - 段崩溃根因（最重要）：team_profile_page 的 4 个列 delegate 创建时既无 parent
     也无 Python 引用，而 setItemDelegateForColumn 不取得所有权——PySide6 GC 删除
     C++ 对象后视图持有悬空指针，导致 SIGSEGV（野指针、崩溃点漂移，与 macOS 崩溃
     报告完全吻合）。修复：delegate 挂 parent + 页面持有引用；修复后连续多次
     全模块运行稳定。
   - 两个 Qt 测试模块补 PySide6 缺失跳过守卫（系统 Python 发现流程不再报错）。
   - teams 测试三处：附加赛仅在次级球队间进行（取样范围修正）、导入私有委托名
     对齐、closed-connection 与防抖信号路径修正。
   - players 测试三处：页头标题按 objectName 查找、球队荣誉"· "装饰前缀归一化、
     概览中赛事分段球队链接与最近比赛对手链接并存（断言改为"对手链接存在且可点"）。
   - shell 测试一处：Route 参数统一为字符串，'1' == 1 类型不匹配修正。

## 4. 边界确认

- 玩法行为零变化（阶段 0 冻结套件 + 三赛季基线指纹在 272 用例中全绿）；
- 页面只消费查询 DTO，零 state.py/persistence 直接依赖（导入审计通过）；
- 用户工作树原样保留；未执行任何 git 提交；Windows 冻结清单零 diff；
- saves/ 用户存档未触碰（全部测试走临时目录）。

## 5. 已知事项与下一阶段（阶段 5）

- 中央区 legacy 页面（首页/赛季/赛事/转会/选秀/历史/存档）仍为旧卡片布局，
  其中旧页自身的小卡内滚动将在阶段 5 按同一套查询+组件迁移时消除；
- LargeTablePage 旁路保留为 legacy 页面的过渡跳转，阶段 5 迁移完成后删除；
- 阶段 5 顺序：赛事/积分/杯赛 → 首页/周报/赛季 → 转会 → 选秀 → 历史与荣誉 →
  存档管理，并做全局实体链接扫尾审计；随后阶段 6（入口与旧存档清理）、
  阶段 7（性能/macOS）。
