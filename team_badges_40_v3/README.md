# 40队队标重绘 v3 与主场候选交接

日期：2026-10-20。此文件交付资源和命名候选；游戏代码接入由实施任务完成。

## 已交付

- PNG/：40枚独立队标，均为1254×1254透明RGBA PNG；文件名与v2一一对应。
- Previews/40_team_badges_v3_overview.png：全部40队总览。
- Previews/preview_1_01-10.png 至 preview_4_31-40.png：每10队一张，同时显示32/48px效果。
- gallery.html：无需外部依赖的本地图册，可搜索、切换背景、展开每队3个球场候选、勾选后导出JSON。勾选仅在当前页面整理意见，不修改游戏。
- manifest.json：球队映射、设计主题、调色、完整生成prompt、原始生成路径与最终文件校验信息。
- STADIUM_CANDIDATES.md / stadium-candidates.json：主场名候选的便携副本；工作区权威来源是 Reviews/identity_redesign_2026-10-20/ 下同名文件。

## 视觉决定与生成记录

采用现代纹章＋克制奇幻，保留球队象征与主要色系，用墨色轮廓和浅色分隔边统一整套视觉。队名和年份没有画进队标，便于小尺寸显示及后续改名。球队身份按club_en映射，编号只是本套资源索引，不代表排名或所属联赛。

使用内置image_gen工具，按logo-brand类别逐队生成40次，再对猩红之月与铁谷做2次定向修整，清理大图检查发现的内部透明杂斑。完整prompt与修整说明记录在manifest。所有最终文件均由工具生成后原样复制，没有用Python重绘、抠图或修改像素。Variants/仅保存这两枚的初稿作过程记录，不是正式资源。

图册和预览是浏览器排版、截图；PNG/中的40枚队标都是独立生成的完整文件，没有从总览里切图。PNG不是可编辑的原生SVG。外部照片、现实俱乐部队徽、网络字体均未引入。

## 主场选择状态（已更新）

用户已完成全部40队选择，原始导出和最终名单已收入 `Reviews/identity_redesign_2026-10-20/`：`stadium-name-selections.json`、`STADIUM_SELECTIONS.md`、`stadiums.selected.json`。本资源目录内的120组候选及空selected字段是选择前的历史副本，不能据此判定用户未选择。

实施以最终名单为准，按俱乐部稳定身份更新显示名，不改变场地ID、升降级归属、中立抽签、主场加成或历史事实。生产stadiums的接入仍需运行与构建验收。

## 给实施者

1. 采用本套队标时读取manifest的club_en→asset_path映射，资源根解析使用项目现有runtime机制。不要按本季联赛序号绑定队标。
2. 现有ui_v2/components/team_crest.py仍指向team_badges_40_v2；本轮没有直接改动运行代码。T-100负责接入v3、保持缓存和缺图回退，并按实际构建配置收录资源。
3. 头像、队名、比分、年份及统计仍用原生UI文字；不把总览截图用于比赛页面。
4. 表格建议实际检查32/36/42/48px与Retina；大图用于详情。字母和文本继续由游戏界面绘制。
5. 球场名使用 `Reviews/identity_redesign_2026-10-20/stadiums.selected.json` 中40个已选结果，不重新选项；原始导出已在同目录保存供核对。
6. 旧team_badges_40_v2目录保留，便于回看。不要覆盖既有审查截图或其他agent正在修改的代码。
7. 这里只完成资源交付，不据此把工程/UI任务标为Done；Qt接入和构建验收仍需各自证据。

## 验证证据

文件校验在Reviews/identity_redesign_2026-10-20/verification-results.json；浏览器图册检查在browser-review.json。覆盖40队完整映射、透明PNG、源文件复制一致性、120组唯一双语候选、四组预览全部加载、深色背景、32/48px实际尺寸、搜索与候选展开。

这不是生产游戏全量测试或打包完成证据。查看本地图册可直接用浏览器打开gallery.html；本次也开启了仅限127.0.0.1的本地预览。
