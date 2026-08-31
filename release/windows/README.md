# Windows 构建与运行

## 运行可执行版

1. 从 GitHub Releases 下载最新的 Windows 压缩包：

   https://github.com/GabrielMu2006/Football-Simulator/releases

2. 解压 `Football-Simulator-UI-v2-Windows.zip`；
3. 双击 `Football Simulator UI v2.exe`。

如果 Windows 安全提示来自未知发布者，请选择“更多信息”，再选择“仍要运行”。

## 从源码构建（Windows 本机）

前置条件：

- Windows 10/11（64 位）；
- conda（Miniconda / Anaconda）已安装并在 PATH 中可用；
- git（如需从 GitHub 拉取最新代码）；
- 不需要预装 Python：构建脚本会用 conda 创建独立的构建环境。

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

或直接双击根目录的 `build_windows_ui_v2.bat`。

脚本会自动完成：

1. 创建/复用 conda 环境 `football-sim-build`（Python 3.11）；
2. 安装 `requirements-ui-v2.txt` 中的依赖（PySide6 + PyInstaller）；
3. 生成 `football_simulator_config.json` 英文配置副本；
4. 运行 PyInstaller（使用“Football Simulator UI v2 Windows.spec”）；
5. 组装 `release\windows\Football-Simulator-UI-v2-Windows\` 并压缩为
   `release\windows\Football-Simulator-UI-v2-Windows.zip`。

常用参数：

```powershell
# 换一个 conda 环境名
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -EnvName football-sim-build-2
# 跳过依赖安装（环境已准备好时）
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -SkipEnvSetup
```

## 从 Mac 通过 SSH 远程构建（可选）

PyInstaller **不支持**在 macOS 上交叉编译 Windows 程序，所以必须在 Windows
机器上执行构建。Mac 只需要负责代码和下载产物：

```bash
# 1. Mac 上推送代码
git push origin main

# 2. SSH 到 Windows 拉取并构建（Windows 需启用 OpenSSH Server）
ssh user@windows-host "cd C:\path\to\Football-Simulator && git pull && powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1"

# 3. 把 zip 拉回 Mac
scp user@windows-host:'C:/path/to/Football-Simulator/release/windows/Football-Simulator-UI-v2-Windows.zip' release/windows/
```

## 注意

- Windows 版 exe 使用用户数据目录 `%APPDATA%\Football Simulator\saves` 存放存档，
  与源码运行时的 `saves/` 不共用；
- 大型 zip 不提交到 git 仓库，以 GitHub Releases 附件形式发布。
