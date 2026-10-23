# 先归档旧UI，再修队徽、Top20与全景连线

日期：2026-10-21。本文件为待实施规格，本轮未修改应用代码或发布包。

项目根目录：`/Users/gabrielmu/Documents/Football Simulator`。

## 1. 目标、依据与边界

按用户追加要求，先执行阶段0，将整个旧UI归档退出可运行源码，再修复独立复验发现的三项P2。保留已经通过的球员目录/历史荣誉分队徽、Top20历史表与荣誉馆、全景/轮次卡片样式、双败节点小字清理和路径菜单。项目根`AGENTS.md`中的启动对象核验约束必须同时执行。

依据：

- `Reviews/latest/identity-top20-bracket-polish-brief-review.md`。
- `artifacts/identity-brief-20261021-212235/probe-results.json`与`probe.py`。
- 同目录`player-top20-wrong-rank.png`、`panorama-canvas.png`、`round-node.png`和`targeted-tests.log`。

复验基线：macOS cocoa定向20项通过，但独立窗口抽查52处表格/683行发现20处155行缺队徽；球员详情把rank=1、score=1219.22显示为“年度第1219.22名”，当届还重复；全景14节点/20边数量正确，但6条边经过无关卡片区域。

新UI修复仅在macOS界面启用，只发布macOS应用和ZIP。Windows继续不连接、不构建、不实测、不发布，默认界面/查询行为不改。阶段0迁出共用队徽时，允许同步`Football Simulator UI v2 Windows.spec`中唯一必要的hidden-import模块路径，避免留下失效引用；这不是Windows移植或实测，不改变其他Windows打包逻辑或产物。

不改赛制、抽签、球队/赛事名称、模拟、赛历、荣誉分、Top20公式、颁奖、冻结事实、随机状态或存档格式。允许新增只读展示字段/适配，但不重新计算已保存名次。本轮不重新设计卡片或整张图，不生成图片。

## 阶段0：旧UI完整归档并移出运行和默认测试路径

选择“归档后移除”方案：保留可恢复源代码，不在普通源码树中留下另一套可运行UI。当前仅制定此方案，尚未移动/删除实际源码。

### 0-A：锁定范围、先形成可校验归档

1. 归档前检查脏改动和并行工作，记录当前源文件及测试清单；不reset/clean，不覆盖别人正在修改的文件。不要因为工作区有未提交文件就丢弃或只归档Git HEAD。
2. 归档整个`football_simulator/ui_v2/`的当前源码，包含旧app、main_window、pages、navigation、services、theme及components；排除可再生pycache/临时文件。旧队徽组件原样也进入归档供追溯。
3. ZIP放`archive/legacy-ui/legacy-ui-v2-<实际批次>.zip`，附manifest，记录原相对路径、逐文件SHA256、归档原因、时间、模块/测试去向及整个ZIP SHA256。ZIP CRC和逐文件字节校验通过后，才开始从工作源码移除旧UI。不要用最新应用ZIP代替源码归档。
4. 归档仅被动ZIP和清单，不能再留`legacy_ui/main_window.py`之类可直接导入/运行的第二份源码目录。不得把归档加入PYTHONPATH、正常测试发现或PyInstaller数据。

### 0-B：迁出当前UI实际复用的队徽

当前静态检查发现，现行UI在8个文件中引用旧目录，全部只涉及`components.team_crest`：

- `ui/components.py`、`competition_boards.py`、`transfer_history.py`。
- `ui/pages/core.py`、`competitions.py`、`entities.py`、`honors.py`、`tools.py`。

将该完整共用组件迁到`football_simulator/ui/team_crest.py`，更新8处导入和`tests/test_team_badges.py`；保留缓存、v3资源优先、v2备用图片和Retina逻辑。资产目录`team_badges_40_v2`是当前有效备用资源，不是旧UI，不能一起删除。

同步更新`build_macos_ui_v2_app.sh`、macOS.spec及Windows.spec中的队徽hidden-import为`football_simulator.ui.team_crest`。Windows只允许该静态引用调整，不构建/运行/发布；不得为规避必要迁移保留能启动旧窗口的兼容包。

迁移后扫描现行源码、测试、启动脚本及打包元数据，确认没有遗留旧模块依赖。声明旧入口的负例测试和文档中的历史路径可保留，不能把这些文字当实际导入。不要粗暴替换全部“ui_v2”：正式入口`ui_v2_main.py`、应用名、虚拟环境、现代`test_v2_*`与存档schema v2均保留。

### 0-C：旧UI专属测试一起归档，保留现行有效覆盖

发现以下10个候选旧UI测试模块直接导入旧页面/窗口：

```text
test_main_window_shell.py
test_navigation_shell.py
test_page_competition.py
test_page_dashboard_weekly_season.py
test_page_history_saves.py
test_page_matches.py
test_page_players.py
test_page_teams.py
test_page_transfer_draft.py
test_tutorial.py
```

逐模块审阅：只验证旧UI的文件原样纳入同一ZIP，随后移出`tests/`；混合用例里仍覆盖现行共用功能的部分，先拆到现代测试后再归档旧部分。保留`test_team_badges`、`test_source_entry_smoke`、全部现代`test_macos_*`、游戏/记录/存档/规则等有效测试。不得因为新实现失败而将现代用例冠名legacy移出。

上次全量记录中这10模块合计248项，占59模块/717项的一部分。若审阅确认10模块全为旧UI专属，未加入新测试前的参考集合是49模块/469项；这只是可解释的历史数量变化，不能继续声称“本轮717项全过”。实施报告须列每个归档/保留/迁移模块的用例数，新增测试和最终实际数量另计。

默认运行器应自然只发现现行测试，不通过大范围ignore/skip掩盖导入错误。`tests/README.md`与当前任务命令同步，旧报告中的旧模块命令只作历史记录，正常回归不得重新解压运行它们。

### 0-D：移除旧目录、增加防误启动检查

1. 迁移依赖和归档校验完成后，从工作源码移除整个`football_simulator/ui_v2/`，不留旧app/main_window或启动转发桩。
2. 新增并执行`tests/test_current_ui_only.py`：正式入口子进程实际回执加载`football_simulator.ui.main_window`；旧`football_simulator.ui_v2`不再可导入，旧入口不能启动；新队徽组件加载40枚v3资源；按归档清单核对现行测试发现无旧UI模块和ImportError；静态打包引用不含旧模块。测试子进程必须先设置独立数据根，不能只检查源码文本。
3. 复用现有验证环境变量回执，必要时给`FS_SMOKE_RESULT`补仅验证模式使用的实际PID、可执行路径、MainWindow/page模块、资源根/frozen状态。连同构建来源指纹和独立数据根记录，证明截图来自目标进程，不能只看应用名或2.0.0。
4. 构建候选后检查PyInstaller模块清单，不含旧UI模块前缀`football_simulator.ui_v2`；归档ZIP不进入包，备用图片路径包含v2不算旧UI模块。
5. 阶段0验收通过后才继续下面三项P2。更新模块迁移导致的旧导入引用，不在归档过程中改队徽样式、奖项、模拟或存档事实。

## 2. P2-01：补齐杯赛榜单的队徽数据

### 根因

`macos.team_column`指定`icon_key='club'`，部分杯赛DTO仅有team_id/team；`CompetitionDetailPage._table_card`仍直接`set_rows(rows)`，不走该函数的“历届冠军”分页路径也未补club。因此列虽然配置正确，实际图标仍为空。

### 最小修复

1. 在`ui/pages/competitions.py::_table_card`创建表格前，检查是否为macOS且存在明确使用club的球队列；对该显示列表调用现有`macos.with_team_clubs(self.app.world(), rows)`后再set_rows。没有球队身份的表不要套用球队适配。
2. 历届冠军表不走`_table_card`，在进入`paginate_table`前对完整rows做相同适配，确保每页都有数据，而非只补当前第一页。
3. 审查其余未接入路径，只补显式team_id→club。保持行顺序、rank、points、zones与原始DTO不变；适配应复制行，不原地修改world/归档/查询列表。
4. 已有club的榜单不重复添加图标；有效team_id必须对应正确的现有v3队徽。缺少有效关联时安全占位，不通过文字或行号猜球队，不引入新资产加载器。
5. 保持原单击导航、分页、分组背景和联赛分隔线；不为加队徽改变列宽分配或UI风格。

### 必须补齐的固定夹具清单

使用`s3-finalized.fsarchive`副本，明确进入目标分区，分别定位以下表，而不是依据“已有图标列”挑选要检查的表。

| 榜单 | 表数 | 此夹具球队行数 |
| --- | --- | --- |
| 君临杯A–D分组积分榜 | 4 | 16 |
| 凌越杯登峰/涅槃积分榜 | 2 | 12 |
| 极境杯瑞士积分表 | 1 | 16 |
| 永世杯种子表 | 1 | 8 |
| 六杯最终名次/荣誉分 | 6 | 92 |
| 六杯历届冠军 | 6 | 11 |
| 合计 | 20 | 155 |

现有测试的两个空洞必须修：

- `test_u02`用列key是否包含club判断双榜，但club实际是icon_key，该条件不会执行；改为明确定位球队列并断言应有图标配置。
- 从同杯球员榜再次navigate不保证返回概览；必须显式切到“阶段与签表”，重新取得当前页面，断言四组/两组确实存在、数量及行数正确，再逐行查图标。空列表循环不能算通过。

### 验收

同一夹具原20处155行的缺图标数从155降到0；原52处表格审查也应无新增缺失。每处先断言表存在、预期行数非零，再验证实际QTableWidgetItem图标非空及club与该行team_id一致。徽章路径/资源应命中正式v3，不能以任意程序化占位图标代替正确队徽。

补一个超过一页的历届冠军只读测试夹具，检查切页/F5后仍有正确图标，且原归档未变。原球员目录、历史荣誉分、联赛表、Top20、纪录榜和双前十仍正确。新增图标尺寸测试按逻辑像素/DPR处理，不照抄Retina物理像素断言。

## 3. P2-02：年度排名只读rank并按赛季去重

### 根因

`queries._player_awards`的Top20行只提供value=score；球员详情用`row.get('rank') or row.get('value')`生成排名，导致计算分数变成名次。同一赛季既在archives又在current awards时被重复拼接。

历史Top20表和球员荣誉馆当前显示正确。`records.player_honors(world, player_id)['top20']`已有基于赛季/球员的去重与真实rank，可优先复用。

### 推荐实施路径

1. macOS球员详情的年度排名区域改从上述现有只读年度事实取得数据，构建仅含season/rank/player_id/team_id等必要身份的展示行。继续把非Top20赛事奖项留在原区域。
2. 用稳定`competition=='top20'`或明确类型区分年度排名与赛事奖项，不靠“年度”中文前缀猜奖项类型。
3. 年度名次只读已保存rank，删除所有`value/score`兜底，也不从“年度Top20第N名”的文字解析排名。有效名次为保存的1–20；若确实没有合法rank，使用“名次未记录”等明确空态，不拿评分或当前排序编造。
4. 按年度事实身份去重：`(season_number, player_id, award_kind='top20')`。同季有正式归档时优先采用归档事实；未归档当前季只显示已产生的正式年度事实。不同季相同rank、同季不同球员不能误合并。
5. 复用已有荣誉馆去重逻辑，避免新建另一套不一致的事实合并。不要为了复用把完整score/components带入显示字段、tooltip或隐藏展开区。
6. 如果实施选择为`_player_awards`额外返回rank，只能做兼容性字段补充，仍保留旧字段和非Mac默认调用语义；macOS不得继续用value显示年度名次。无需改存档或排名核心。

### 验收

- 原始副本中Darwin Nunez年度排名应为：S1第1、S2第4、S3第1；S3只出现一次。仍保存986.88/1130.52/1219.22等原score，UI不将它们显示成名次或计算内容。
- 历史Top20、球员详情荣誉、球员荣誉馆的同球员同季rank一致，且保持当届球队身份。
- 增加“当前季已归档且current awards还在”“当前正式年度事实尚未归档”“无Top20”“缺失/无效rank”用例。不能只测本来已经正确的历史表。
- 归档rank/score/components、荣誉积分、MVP/射手/助攻值及共享规则不变；非Top20奖项数值仍显示。对比读取前后world/数据库，确认没有写入去重结果。
- 建议直接核验三入口的结构化展示值，不只搜索页面是否存在“分数解释”等字样。

## 4. P2-03：给双败跨组连线分配卡片外通道

### 根因

`GraphView.paintEvent`统一取源右侧和目标左侧的x中点作为折线竖段。源/目标同列时，中点位于卡片内部；WB→LB的部分边穿过同列其他卡片，被后绘制的白卡遮断。

### 最小修复

1. 在双败全景只读布局层生成每条边实际使用的折线点列，例如`polyline`；渲染层直接绘制这组点。保留边的source/target/outcome/team_id/target_slot语义，不能靠删除或隐藏边解决穿越。
2. 对不同列的普通正向边，在相邻列的空隙中折转；对同列、回向或跨WB/LB边，分配卡片外的出线/入线通道，并使用分区间空隙横向转接。
3. 同列WB→LB的可实施走法：源行右边界→源列右侧通道→WB/LB间空隙→目标列左侧通道→目标slot左边界。第一列左侧需留足画布内边距，不能把通道画到负坐标后裁掉。
4. 若必要，只轻微增加局部画布边距/分区间隙，为多个通道分配确定性间距。不得换卡片配色/字号/队徽大小、将节点挤小或整体大改布局。
5. 把节点矩形及标题区域作为障碍；除合法源/目标连接端点外，路径不进入节点内部。线条与非端点卡片边界应有约4–6px净距，避免擦边造成视觉连接误读。
6. 按稳定边身份分配通道，输出确定、无随机。尽量减少不必要交叉和共线重叠；无实际关系的交叉不画连接圆点，不把两条独立边误表现为合流。
7. 终点仍落到真实target_slot对应球队行，起点仍对应winner/loser身份。保留当前20条流向、WB次轮败者交叉落位、GF双来源和无重置赛。
8. `GraphView`供瑞士图共用：双败边可携带自有点列，瑞士现有图保持原拓扑与行为。不要把双败避障规则直接强加到所有图导致瑞士回归；如公共绘制代码变动，补瑞士烟测。
9. 缩放只做坐标变换，实际画布边界必须包含路线和节点；适应宽度、横移、下部节点可达及现有阶段/路径跳转都保留。

### 验收

- 对完成态14节点/20条实际绘制路径做独立几何检查：与非端点卡片内部相交为0；原6条失败边逐条成为通过，不能只测某一条同列边。
- 点列必须正是paintEvent绘制的点列，不接受“测试里生成一套避障路径，绘图仍走旧中点”的结果。检查完整画布截图，确认线确实沿外部通道连续可见。
- 检查source/target/slot和14/20数量不变，没有丢边、反边或重置决赛；不同层面的边交叉不能误合流。
- 覆盖初始胜者组首轮4场、中途WB/LB混合、完成态，及点球节点高度变化；未产生后轮不画假比赛。
- 1280/1440/1680三档及约50%/100%/150%缩放下，图无裁死路线、卡片/标题不被连线穿越，节点仍可达。窄窗口允许图内横移，不允许页面整体横向溢出。

## 5. 必要回归与执行顺序

完成阶段0依赖迁移与旧UI归档后，先补当前实现会失败的用例，再按P2-01→P2-02→P2-03修复。复用现有现代测试类或新增小模块，例如`tests.test_macos_identity_p2`；迁移验证可用独立`tests.test_current_ui_only`。不要把独立复验probe作为唯一测试，也不要为了运行新测试重写旧断言。

| 回归组 | 必须验证 |
| --- | --- |
| T00 旧UI退役 | ZIP逐文件及CRC校验；共享队徽迁移；旧包不可导入；现代入口身份；现行测试发现正常；包内无旧UI模块；归档用例数可核对 |
| T01 图标覆盖 | 原20处表实际存在且预期行数正确；全部club/图标匹配team_id；歷届冠军后续分页/F5；原通过榜单不回归 |
| T02 年度名次 | 三入口同季同rank；原Darwin示例；归档/current去重；无数据/缺rank；score/components与非Top20数值保持 |
| T03 路线避障 | 实际绘制点列对非端点卡片相交0；原6条边；20条身份/槽位；初始/中途/完成态；缩放/画布边界 |
| T04 已通过交互 | GF初始标签与内容一致；第一次点WB生效；路径菜单真实点击后跨组定位；F5新控件仍为目标轮次；返回/宽窄切换不丢mode/group/round |
| T05 只读与平台 | world/DB/归档/随机状态不变；Mac开关关闭不改变默认行为；Windows仅必要静态队徽导入路径调整，产物不改、不实测 |

不能用空循环、过滤掉没有icon_key的表、只触发信号代替真实点击、只看目标仍在宽布局中等方式降低测试强度。图标尺寸按DPR正确处理；如果另外复跑既有`test_team_badges`的cocoa用例遇到已记录的Retina逻辑/物理像素差异，单列说明，不以跳过或删除失败断言掩盖。

先跑新增迁移验证及P2用例，再运行以下仍保留的现代原生回归：

```bash
QT_QPA_PLATFORM=cocoa PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python -m unittest \
  tests.test_current_ui_only \
  tests.test_macos_identity_bracket_polish tests.test_macos_competition_refinement \
  tests.test_game_feel_followup tests.test_macos_detail_navigation \
  tests.test_macos_live_competitions tests.test_macos_observer_views \
  tests.test_vnext_records_honors -v
```

公共图形绘制和查询展示均涉及本轮，最后代码执行一次逐模块全量：

```bash
QT_QPA_PLATFORM=offscreen PYTHONFAULTHANDLER=1 .venv-ui-v2/bin/python \
  tests/run_suite.py --json artifacts/<本轮唯一目录>/module-results.json
```

先创建真实目录替换占位符，报告当次实际module/test/skip数量以及阶段0退出默认发现的旧UI用例数。原生专属项用cocoa补验，不引用上轮717项或独立20项当本轮结果；不要求重新模拟三个完整赛季。用有效归档副本及必要的独立真实中途状态完成。

独立复验脚本可作对照：`artifacts/identity-brief-20261021-212235/probe.py`。它的旧路径几何检查重现的是旧中点折线；新实现要改为检查实际绘制点列，保留独立的线段/矩形相交判断和对所有20条边的遍历，不能让旧脚本公式成为新实现的假失败或假通过。

## 6. 原生及包内验收

保留以下实际截图/操作证据：

- 君临/凌越分组、瑞士表、永世种子、杯赛最终名次及历届冠军队徽，至少覆盖原缺失六类。
- Darwin球员详情年度名次三行，不出现1219.22等计算分数或重复S3；同季历史表和荣誉馆对照。
- 三档双败全景完整画布、原6条边的路由细节、当前轮次节点。卡片外观与已通过版本一致，常态节点无说明小字，必要点球/加时保留。
- 路径菜单打开→选择跨组目标→实际F5→仍为目标轮次，确认刷新发生；图模式、阶段和页面缓存一致。

按根AGENTS.md先记录启动对象：正确绝对路径、新PID、MainWindow/page实际模块、构建指纹、独立数据根和测试存档。源码已改但未重建的正式包只可作修前基线。禁止用open -a、同名旧进程或旧源码快照猜测当前版本；发现对象不符，标记证据无效并重验。

在本轮候选macOS.app中真实点击验证上述关键链路，包含榜单切页/刷新、球员年度排名、全景两视图/缩放。只能用真实包内操作证明包内通过；源码QTest、启动截图或直接refresh_all不等于实际包内点击/F5。

原包内交互存在权限未验证限制；优先使用当前可用原生UI工具补验，不能直接沿用旧权限结论跳过。若仍受阻，完成其余工作并交付候选，明确未验证项，保持整体验收未完成；不以未验收候选替换正式包，也不请求无依据的额外批准。

## 7. 保护、发布与交付

在构造service/窗口前隔离完整数据根和配置。保护项目及`~/Library/Application Support/Football Simulator`的用户存档、指针、配置、备份/回收站，记录生产数据与Windows产物边界指纹。Windows.spec的唯一必要静态模块路径替换单列为预期差异，不将Windows产物加入可修改范围。只读去重/装饰/图布局不写事实，不消费随机数。

不reset/clean、不覆盖其他工作、不关闭用户游戏、不主动启动其他agent。保留已修Qt生命周期、草稿、换季、新闻及本轮已通过的UI；不得重引入macOS页面销毁时setParent(None)旧崩溃。

候选从自有源码快照构建，BUILD_DIR/DIST_DIR仅本轮自有临时目录。通过测试、原生视觉、签名、ZIP完整性、40队徽/8奖杯/规则资源、隔离启动和真实包内操作后，替换：

- `release/macos/Football Simulator UI v2.app`
- `release/macos/Football-Simulator-UI-v2-macOS.zip`

规范路径启动新实例复验，候选与正式包内容指纹一致，记录实际PID和ZIP SHA256；Windows产物不变，仅说明必要的静态导入调整。当前正式ZIP基线为`34220750fdbeeffbc575c993cde32abccc7a566b4770d31d5de03a2315249430`，实施时核查是否已有新的合法发布，不能无条件拿旧基线覆盖新工作。

用户要求仅保留最新版应用：成功发布后清理本轮自有重复.app/ZIP和临时构建缓存，保留报告、测试日志、截图和必要源码；不删除其他agent目录或恢复已经清理的旧应用。

最终交付阶段0＋三项P2状态、归档清单与SHA256、旧测试去向/数量、20表覆盖结果、启动身份回执和代表性修前修后图。报告分别给出代码、测试、视觉、包内操作证据；归档及三项修复全部达标才标Done。任一未通过/未验证如实列出，不用测试数字或边数量代替界面验收。
