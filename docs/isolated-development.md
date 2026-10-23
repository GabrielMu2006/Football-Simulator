# 稳定版与开发测试隔离

V0.1.0 正式应用与 ZIP 位于 `release/macos/`。用户明确要求发布之前，开发任务不得覆盖此目录。已安装的应用不随源码修改而变化，正在游玩的进程不用于测试。

## Docker 日常测试

安装并启动 Docker Desktop，然后在项目根目录执行：

```bash
python3 scripts/test_in_docker.py --suite core --build
python3 scripts/test_in_docker.py --suite ui
```

首次构建安装 Python 3.12、PySide6 Essentials 6.10.3（本项目用到的 QtCore/Gui/Widgets/Test）与 Linux Qt 依赖。之后通常不需 `--build`；修改依赖时重新构建。
当前镜像固定为 arm64。包装脚本先用本机 pip 下载 Linux wheel，再在临时 Docker 构建目录中离线安装，避免容器代理与本机网络不同导致大文件下载卡住；依赖不会安装进本机 Python 环境。

- `core`：明确列出的跨平台规则、引擎、存档、服务、赛历、转会和历史荣誉用例。不是全部平台测试的替代品，具体用例及跳过情况见结果 JSON。
- `ui`：Linux offscreen Qt 渲染，显式启用 macOS 展示样式，检查主要页面、无错误、横向布局与 F5。回执始终注明真实平台 Linux；不伪装成 Cocoa 或 macOS 包验收。
- 每次先生成独立源码快照，只读挂载进容器。白名单中没有 `saves/`、`release/`、`.git/`、用户主目录或 Docker socket。测试夹具只来自 `tests/fixtures_vnext/`。
- 容器非 root、无网络、只读根文件系统，数据根在临时内存目录；默认最多 2 CPU / 2 GiB，容器退出后删除。唯一可写主机目录是该次 `artifacts/docker/` 证据目录。
- 输出记录源码指纹、实际平台、挂载隔离、CPU/内存限制、测试结果和截图。Docker 构建首次拉取镜像需要网络；运行测试时禁用网络。

这能隔离存档和正式应用，但 Docker 仍共享本机硬件；游玩时如感到卡顿，可停止本次测试或降低脚本里的 CPU 限额。不要为测试修改 Docker Desktop 的全局资源设置。

## 本机 macOS 补验与发版

原生 Cocoa、菜单/文件选择器、字体、快捷键、签名和 `.app` 打包仍在 macOS 检查。使用自有临时构建目录、绝对入口和独立 `FS_DATA_ROOT`；优先离屏验证，前台验证只操作自己启动的测试进程。不得关闭、推进或借用用户正在玩的实例。

测试通过只产生候选包。只有用户明确要求发布/更新稳定版后，才按 AGENTS.md 的“候选验证→晋升→正式包复验”流程更新 `release/macos/`。V0.1.0 的 GitHub tag 和发布资产作为稳定基线保留，不随日常开发重打包。

Windows 构建、远程连接和原生实测继续搁置。

参考：[Docker 只读挂载](https://docs.docker.com/engine/storage/bind-mounts/)、[资源限制](https://docs.docker.com/engine/containers/resource_constraints/)、[Qt 平台部署](https://doc.qt.io/qtforpython-6/deployment/index.html)。
