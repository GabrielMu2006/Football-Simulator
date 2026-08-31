# -*- coding: utf-8 -*-
"""游戏教程弹窗：首次启动自动展示一次，顶栏「教程」按钮可随时重新打开。

- 首次展示状态保存在用户数据目录（`user_data_root()/tutorial_state.json`），
  只有版本号低于当前 `TUTORIAL_VERSION` 时才自动弹出；
- 测试（offscreen）与 `FOOTBALL_SIMULATOR_DISABLE_TUTORIAL=1` 时自动跳过，
  不会阻塞自动化测试或调试。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from football_simulator import runtime as sim_runtime

TUTORIAL_VERSION = 1
_STATE_FILE_NAME = "tutorial_state.json"


def _state_path() -> Path:
    return sim_runtime.user_data_root() / _STATE_FILE_NAME


def tutorial_seen_version() -> int:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return int(data.get("version", 0))
    except Exception:
        return 0


def mark_tutorial_seen(version: int = TUTORIAL_VERSION) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": version}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def should_show_tutorial() -> bool:
    if os.environ.get("FOOTBALL_SIMULATOR_DISABLE_TUTORIAL") == "1":
        return False
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    return tutorial_seen_version() < TUTORIAL_VERSION


_SECTIONS = [
    (
        "1. 快速开始",
        "在「存档」页新建存档 → 点击「初始化赛季」→ 之后用「模拟下一周」推进比赛。"
        "存档保存在本机，支持备份 / 导出 / 导入 / 回收站。",
    ),
    (
        "2. 推进赛季",
        "顶部「模拟下一周」推进一周；「推进 ▾」可以一次推进到下一个待办或赛季末；"
        "「本周战报」查看最近一周的比赛与积分。遇到转会审核 / 能力审核 / 选秀等待办时，"
        "模拟会暂停，处理完待办后继续。",
    ),
    (
        "3. 数据工作台",
        "侧栏共 10 个页面：首页 / 赛季 / 比赛 / 赛事 / 球队 / 球员 / 转会 / 选秀 / 历史 / 存档。"
        "球员、球队、比赛、赛事全部可点击互跳；顶部可切换存档与赛季，全局搜索（Cmd/Ctrl+K）快速定位。",
    ),
    (
        "4. 核心系统",
        "40 支虚拟球队分成一级联赛与次级联赛；优胜者杯 / 挑战杯 / 超级杯三线杯赛；"
        "赛季末升降级换位。转会（球员换球员）、选秀（真实球员进入联赛）、赛季荣誉与历史归档都在这里查看。",
    ),
    (
        "5. 快捷键",
        "Cmd/Ctrl+K 全局搜索 · Cmd/Ctrl+Enter 模拟下一周 · Cmd/Ctrl+Shift+Enter 到下一待办 · "
        "Cmd/Ctrl+Alt+Enter 到赛季末 · Cmd/Ctrl+Shift+W 本周战报 · Cmd/Ctrl+R 刷新。",
    ),
]


class TutorialDialog(QDialog):
    """带滚动的教程弹窗；模态展示，关闭后记录已读版本。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("游戏教程")
        self.setModal(True)
        self.resize(760, 600)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        title = QLabel("<h2>欢迎来到 Football Simulator</h2>")
        title.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 6, 8, 6)
        content_layout.setSpacing(12)
        for heading, body in _SECTIONS:
            block = QLabel(
                f"<h3 style='color:#f8fbff;'>{heading}</h3>"
                f"<p style='color:#cbd7e6;'>{body}</p>"
            )
            block.setWordWrap(True)
            block.setTextFormat(Qt.TextFormat.RichText)
            content_layout.addWidget(block)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton("开始游戏")
        close_button.setObjectName("tutorialCloseButton")
        close_button.setAutoDefault(True)
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        root.addLayout(button_row)

    @classmethod
    def show_first_run_if_needed(cls, parent: Optional[QWidget] = None) -> None:
        """首次启动（版本低于当前）时模态展示并记录已读。"""
        if not should_show_tutorial():
            return
        dialog = cls(parent)
        try:
            dialog.exec()
        finally:
            mark_tutorial_seen()

    @classmethod
    def show_tutorial(cls, parent: Optional[QWidget] = None) -> None:
        """手动打开教程（顶栏按钮）；同样记录已读。"""
        dialog = cls(parent)
        try:
            dialog.exec()
        finally:
            mark_tutorial_seen()
