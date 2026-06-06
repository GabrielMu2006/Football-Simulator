# Windows Release Folder

把后续在 Windows 系统上构建出的可运行版本放到这里。

推荐结构：

```text
release/windows/Football-Simulator-UI-v2-Windows/
  Football Simulator UI v2.exe
  _internal/
  football_simulator_config.json
  README.md
```

Windows 版需要在 Windows 系统上构建：

```bat
build_windows_ui_v2.bat
```

构建完成后，可以把 `dist-windows-ui-v2\Football Simulator UI v2` 复制到本目录并改名为 `Football-Simulator-UI-v2-Windows`。
