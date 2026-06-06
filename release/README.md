# Release Builds

这里存放可直接运行的游戏版本。

## macOS

当前可运行版本：

```text
release/macos/Football-Simulator-UI-v2-macOS/Football Simulator UI v2.app
```

也提供压缩包：

```text
release/macos/Football-Simulator-UI-v2-macOS.zip
```

如果 macOS 提示无法打开，请右键点击 `.app`，选择“打开”，再在弹窗中确认。

## Windows

Windows 可运行版通过 GitHub Releases 提供 zip 下载：

```text
https://github.com/GabrielMu2006/Football-Simulator/releases/download/v0.1.0-windows/Football-Simulator-UI-v2-Windows.zip
```

解压后运行：

```text
Football-Simulator-UI-v2-Windows/Football Simulator UI v2.exe
```

如果 Windows 安全提示来自未知发布者，请选择“更多信息”，再选择“仍要运行”。

如果需要重新构建 Windows 版，请在 Windows 系统上运行根目录下的：

```bat
build_windows_ui_v2.bat
```

然后把构建产物复制到：

```text
release/windows/Football-Simulator-UI-v2-Windows/
```
