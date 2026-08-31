# Release Builds

这里存放可直接运行的游戏版本。

## macOS

当前可运行版本：

```text
release/macos/Open Football Simulator UI v2.command
```

推荐使用可安装镜像：

```text
release/macos/Football Simulator UI v2.dmg
```

也提供压缩包：

```text
release/macos/Football-Simulator-UI-v2-macOS.zip
```

生成 DMG：在项目根目录执行 `scripts/create_macos_dmg.sh`。

如果 macOS 提示无法打开，请右键点击 `.app`，选择“打开”，再在弹窗中确认。

## Windows

Windows 可运行版通过 GitHub Releases 提供 zip 下载：

```text
https://github.com/GabrielMu2006/Football-Simulator/releases
```

解压后运行：

```text
Football-Simulator-UI-v2-Windows/Football Simulator UI v2.exe
```

如果 Windows 安全提示来自未知发布者，请选择“更多信息”，再选择“仍要运行”。

构建 Windows 版必须在 Windows 本机执行（PyInstaller 不支持跨平台交叉编译）。
在仓库根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

或直接双击根目录的 `build_windows_ui_v2.bat`。

构建产物：

```text
release/windows/Football-Simulator-UI-v2-Windows/
release/windows/Football-Simulator-UI-v2-Windows.zip
```

详细说明见 `release/windows/README.md`。
