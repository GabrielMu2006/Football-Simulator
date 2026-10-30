# Football Simulator · V0.3.1

本地运行的足球联赛观察模拟器。推进一个52周赛季，追踪40支球队、两级联赛、六项杯赛、球员数据与赛季故事。当前开发分支为 V0.3.1，使用本地模板离线生成报刊，并提供赛事 MVP、EVP 和年度 Top20。

<p align="center">
  <img src="assets/app_icon_256.png" width="128" alt="V0.1.0 绿金足球图标">
  <img src="assets/press-v0.2.0/app_icon_256.png" width="128" alt="V0.2.0 蓝鲸足球图标">
  <img src="assets/v0.3.1/app_icon_256.png" width="128" alt="V0.3.1 足球盾徽图标">
</p>

| 版本 | 内容 | 下载 |
|---|---|---|
| **V0.3.1** | 足球盾徽图标；离线报刊、MVP 与 EVP | 当前为源码及本机安装版，尚未发布 GitHub Release |
| **V0.2.0** | 蓝鲸足球图标；含报刊中心 | [macOS Apple Silicon ZIP](https://github.com/GabrielMu2006/Football-Simulator/releases/download/V0.2.0/Football-Simulator-V0.2.0-macOS-arm64.zip) · [Release](https://github.com/GabrielMu2006/Football-Simulator/releases/tag/V0.2.0) |
| **V0.1.0** | 绿金足球图标；原联赛观察版 | [macOS Apple Silicon ZIP](https://github.com/GabrielMu2006/Football-Simulator/releases/download/V0.1.0/Football-Simulator-V0.1.0-macOS-arm64.zip) · [Release](https://github.com/GabrielMu2006/Football-Simulator/releases/tag/V0.1.0) |

下载后解压，将应用放到任意可访问目录运行，无需安装Python。当前发布仅覆盖macOS Apple Silicon；Windows移植和实测继续延期。应用采用本机临时签名，尚未进行Apple Developer ID公证。

## V0.3.1 报刊与个人奖项

- 每个存档可单独启用本地报刊。周报随比赛推进自动生成；阶段报、赛事总报、转会窗报、选秀报和 Top20 专题在相应事件后生成。
- 文章依据游戏记录撰写，不需要 API 密钥；已生成的旧版 DeepSeek 文章仍可阅读。周报默认已读，其他已刊发报道在阅读前显示未读。
- 赛事 MVP 从符合条件的冠亚军球员中选出，EVP 面向赛事参赛球员；次级联赛与升降级附加赛不颁发这两项奖。球员荣誉和赛事历史页面可查看奖项。
- 年度 Top20 综合赛季表现、赛事个人奖项及球队成绩，具体规则见[奖项方案](docs/v0.3.1-awards-proposal.md)。

## V0.2.0 报刊中心（历史版本）

每篇报道是一篇带小标题的完整长文，以球队故事、走势、冷门和人物表现为主。

- **周报**：手动选择已完成周次生成。
- **阶段报**：两级联赛半程、Reign Cup小组赛、Apex Cup瑞士轮、Ascendance Cup组外循环和突围赛结束后自动生成。
- **赛事总报、转会窗报、选秀报、Top20专题**：在相应结算或决定完成后自动进入后台队列，不阻塞推进。
- 赛事、转会、选秀与年度Top20页面提供对应报道入口；筛选、返回和F5保留上下文。
- 每个存档单独启用，不补过去的报道；此前的历史事实仍可用于新文章的背景。
- 成功文章只生成一次。阅读、刷新、重开不再次调用API；正常关闭会保存当前请求，随后暂停，重开复用已完成部分。
- 失败时允许手动重试缺失部分；未知请求结果不会自动重新付费。Top20按概览与20位球员点评分段生成，本地合成。

使用说明见[报刊与恢复](docs/press/newspaper.md)。文章基于游戏冻结事实，媒体评论由模型撰写；比分、榜单和正式荣誉以游戏记录为准。

### API密钥

在“报刊 → API设置”中使用自己的DeepSeek API密钥，可选择仅本次使用或保存在macOS钥匙串。已经保存的密钥继续沿用；新应用首次读取时，macOS可能要求本人授权，无需重新填写密钥。

**源码、GitHub Release和安装包不含开发者密钥、游玩存档或个人配置。** 密钥不写入文章、存档或日志。API调用费用由密钥所属账户承担。本项目不是DeepSeek官方应用。

## 版本与存档目录

各版本使用不同的默认数据目录：

| 应用 | 默认数据目录 |
|---|---|
| Football Simulator V0.1.0 | `~/Library/Application Support/Football Simulator/` |
| Football Simulator V0.2.0 | `~/Library/Application Support/Football Simulator V0.2.0/` |
| Football Simulator V0.3.1 | `~/Library/Application Support/Football Simulator V0.3.1/` |

V0.2.0不会自动改写V0.1.0的存档。需要继续原有进度时，先在V0.1.0导出`.fsarchive`，再在V0.2.0导入副本；两个版本之后分别推进。完整导出包包含数据库、配置和manifest，旧裸SQLite/JSON格式仍不迁移。

各版本不会自动共享写入存档。V0.3.1 的本机应用位于个人应用程序目录；本机安装包保存在 `release/macos/`。源码修改不会自动更新已安装的应用；后续开发使用[隔离开发流程](docs/isolated-development.md)。

## 游戏内容

- 40支球队、两级联赛各20队、38轮主客场循环。
- 六项杯赛：Reign Cup、Destiny Cup、Apex Cup、Resurgence Cup、Ascendance Cup、Aeon Cup；包含分组、瑞士轮、双败、突围和两回合淘汰。
- S1无杯；S2开放五杯；S3起六杯均开放。所有冠军均在冬窗后产生。
- 八座赛事奖杯、40队队徽和双语球场；中立比赛不获得主场加成。
- 真实球员目录、历届冠军、历史数据、跨赛季荣誉分总榜、奖杯数和年度Top20。
- 不同位置、每方最多三人的球员交换；总身价差上限20，交换后位置不超限，同一球员同窗仅转会一次。
- 自动处理、关键事项暂停、全部手动三种观察模式；转会审核、选秀、升降级和升级附加赛。

## 从源码运行

```bash
python3 -m venv .venv-ui-v2
.venv-ui-v2/bin/pip install -r requirements-ui-v2.txt
FS_DATA_ROOT="$PWD/.local/source-development" .venv-ui-v2/bin/python ui_v2_main.py
```

唯一现行入口是`ui_v2_main.py`，窗口为`football_simulator.ui.main_window.MainWindow`。旧`football_simulator/ui_v2/`已退役并被动归档，不属于运行入口。开始工作前请阅读[AGENTS.md](AGENTS.md)。

快捷键：Ctrl/Cmd+K搜索；Alt+左右箭头前进/后退；F5刷新当前页面。

## 开发与构建

```bash
python3 scripts/test_in_docker.py --suite core
python3 scripts/test_in_docker.py --suite ui
./build_macos_ui_v2_app.sh
```

Docker只挂载白名单源码快照，不挂载用户存档、正式应用、主目录或Docker socket。Linux离屏Qt测试不作为macOS Cocoa验收。构建输出使用独立临时目录；验证候选后才晋升正式包。源码和发布清单需经过密钥检查，应用ZIP通过GitHub Release分发，不提交到Git。

项目主体：`football_simulator/game/`（核心）、`persistence_v2/`（存档）、`app/`（服务查询）、`ui/`（界面）、`press/`（报刊材料、队列与生成）。规则位于`football_simulator/rules/`，测试位于`tests/`。

[完整更新记录](CHANGELOG.md)
