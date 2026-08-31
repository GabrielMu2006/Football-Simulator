# -*- coding: utf-8 -*-
"""调试日志（仅 FOOTBALL_SIMULATOR_DEBUG=1 时写入用户数据目录 debug.log）。

用于排查 macOS 全屏下存档操作“滑动屏幕退回桌面”的问题：
记录窗口状态变化、存档操作、Qt 弹窗调用等关键事件。
"""

from __future__ import annotations

import os
from datetime import datetime

from football_simulator import runtime as sim_runtime

# 仅当 FOOTBALL_SIMULATOR_DEBUG=1 时记录（诊断用）。
_ENABLED = os.environ.get("FOOTBALL_SIMULATOR_DEBUG") == "1"


def log(message: str) -> None:
    if not _ENABLED:
        return
    try:
        path = sim_runtime.user_data_root() / "debug.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"[{datetime.now().isoformat(timespec='milliseconds')}] {message}\n")
    except Exception:
        pass


def hook_qmessagebox() -> None:
    """给 QMessageBox 静态方法加日志包装（调用前/后各记一行）。"""
    if not _ENABLED:
        return
    try:
        from PySide6.QtWidgets import QMessageBox

        for name in ("information", "warning", "critical", "question"):
            original = getattr(QMessageBox, name)

            def make_wrapper(orig, method_name):
                def wrapper(*args, **kwargs):
                    log(f"QMessageBox.{method_name} called")
                    try:
                        result = orig(*args, **kwargs)
                    except Exception:
                        log(f"QMessageBox.{method_name} raised")
                        raise
                    log(f"QMessageBox.{method_name} returned {result!r}")
                    return result

                return wrapper

            setattr(QMessageBox, name, make_wrapper(original, name))
    except Exception:
        pass
