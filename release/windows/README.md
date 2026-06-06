# Windows Release Notes

Windows 可运行版 zip 较大，超过 GitHub 普通仓库单文件限制，因此不直接提交到仓库目录，而是作为 GitHub Release 附件提供。

```text
https://github.com/GabrielMu2006/Football-Simulator/releases/download/v0.1.0-windows/Football-Simulator-UI-v2-Windows.zip
```

解压后打开 `Football Simulator UI v2.exe`。

如果 Windows 安全提示来自未知发布者，请选择“更多信息”，再选择“仍要运行”。

如需重新构建 Windows 版，请在 Windows 系统上运行：

```bat
build_windows_ui_v2.bat
```

构建完成后，可以把 `dist-windows-ui-v2\Football Simulator UI v2` 复制到本目录并改名为 `Football-Simulator-UI-v2-Windows`。
