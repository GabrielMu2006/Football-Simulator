# 保留的设计与最近验证

最新 macOS 逐周实玩复查：latest/macos-playthrough-review.json，包含空积分榜、换季崩溃、凌越杯胜场排序、季末周次和分页修复；必要截图位于 latest/screenshots/macos-playthrough-*.png。完整可再生成证据留在 artifacts/macos-playthrough-2026-10-21/。

历史审查报告、过期启动prompt、大批截图、临时测试脚本与旧应用产物已按用户要求清理。

- latest/：最近一次验证摘要、必要日志、实际游戏截图、应用图标预览与清理清单；app-icon-generation.json保留新图标的提示词和来源。
- latest/windows-port-plan.md 与 windows-port-prompt.md：已确认 E 盘目录和免安装 ZIP 形式的 Windows 移植方案与实施启动 prompt；当前尚未执行移植构建。
- names_career_honors_2026-10-20_162714/：当前功能方案、名称策略及冬窗后赛历修订来源。
- identity_redesign_2026-10-20/：已确认的40主场名单，包含自动化测试所需独立对照JSON。
- game_design_2026-10-20/：基础赛制设计与原始确认来源；如旧赛历与新版不同，以football_simulator/rules/和本次赛历修订为准。
- trophy_generation_2026-10-20_165645/generation-records.json：八座奖杯的生成提示词与来源记录；真实资源在assets/trophies/v1。

最新版应用统一放在release/macos/。可重新生成的测试产物写入artifacts/tests/；测试代码和必要夹具保留在tests/，不再依赖已删除的旧审查目录。

2026-10-21 macOS五处界面修复的交付记录在latest/macos-ui-fixes.json；实际打包截图在latest/screenshots/macos-ui-*.png。Windows构建文件与产物未由本轮修改。
