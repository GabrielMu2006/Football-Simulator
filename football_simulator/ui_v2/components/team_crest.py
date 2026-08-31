# TeamCrest：按队名确定性生成的虚拟队徽。
# 后续可通过 set_custom_crest_provider 接入自定义图片路径，页面无需改动。

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, Optional

from PySide6.QtCore import QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

#: 自定义队徽提供者：签名 (team_name) -> Optional[图片路径/URL]。
#: 返回路径时，TeamCrest 与 draw_team_crest 会优先加载该图片。
CUSTOM_CREST_PROVIDER: Optional[Callable[[str], Optional[str]]] = None

_CREST_PALETTE = (
    ("#1e3a8a", "#93c5fd"),
    ("#7f1d1d", "#fca5a5"),
    ("#14532d", "#86efac"),
    ("#713f12", "#fcd34d"),
    ("#0e7490", "#67e8f9"),
    ("#6b21a8", "#d8b4fe"),
    ("#9a3412", "#fdba74"),
    ("#0f766e", "#5eead4"),
    ("#334155", "#cbd5e1"),
    ("#86198f", "#f0abfc"),
    ("#1d4ed8", "#bfdbfe"),
    ("#581c87", "#c4b5fd"),
)


def set_custom_crest_provider(provider: Optional[Callable[[str], Optional[str]]]) -> None:
    """注册自定义队徽提供者；传 None 恢复程序化生成。"""
    global CUSTOM_CREST_PROVIDER
    CUSTOM_CREST_PROVIDER = provider


def crest_colors(team_name: str):
    """按队名稳定取 (主色, 次色)。"""
    digest = hashlib.sha1(team_name.encode("utf-8")).digest()
    return _CREST_PALETTE[digest[0] % len(_CREST_PALETTE)]


def crest_initials(team_name: str) -> str:
    """缩写：英文名取前两个有效词首字母；单词名取前两个字符。"""
    words = [word for word in re.split(r"[^A-Za-z0-9]+", team_name) if word]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    if len(team_name) >= 2:
        return team_name[:2].upper()
    return (team_name or "?").upper()


#: 默认队标目录（team_badges_40/PNG，源码运行与 PyInstaller 打包后都可用）。
_BADGE_ROOT: Optional[Path] = None
_BADGE_ROOT_LOADED = False
#: 文件名（去掉 NN_ 前缀后的标准化队名）→ 实际 PNG 路径。
_BADGE_INDEX: Optional[Dict[str, str]] = None


def _badge_root() -> Optional[Path]:
    global _BADGE_ROOT, _BADGE_ROOT_LOADED
    if not _BADGE_ROOT_LOADED:
        _BADGE_ROOT_LOADED = True
        from football_simulator.runtime import resource_root

        candidate = resource_root() / "team_badges_40" / "PNG"
        _BADGE_ROOT = candidate if candidate.is_dir() else None
    return _BADGE_ROOT


def _badge_path(team_name: str) -> Optional[str]:
    """返回 team_name 对应的队标 PNG 路径；未找到返回 None。"""
    global _BADGE_INDEX
    root = _badge_root()
    if root is None:
        return None
    if _BADGE_INDEX is None:
        index: Dict[str, str] = {}
        for file in root.glob("*.png"):
            stem = file.stem
            parts = stem.split("_", 1)
            key = parts[1] if len(parts) == 2 and parts[0].isdigit() else stem
            index[key] = str(file)
        _BADGE_INDEX = index
    key = re.sub(r"[^A-Za-z0-9]+", "_", team_name).strip("_")
    return _BADGE_INDEX.get(key)


def _device_pixel_ratio() -> float:
    """当前屏幕设备像素比（Retina/高 DPI 为 2.0，普通屏幕为 1.0）。"""
    screen = QGuiApplication.primaryScreen()
    return float(screen.devicePixelRatio()) if screen is not None else 1.0


@lru_cache(maxsize=256)
def _loaded_image_pixmap(path: str, size: int) -> Optional[QPixmap]:
    """带缓存的图片队徽：按设备像素比渲染，保证高分屏依然清晰。"""
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    dpr = _device_pixel_ratio()
    target = max(1, int(round(size * dpr)))
    scaled = pixmap.scaled(
        target,
        target,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    return scaled


def _crest_pixmap(team_name: str, size: int) -> QPixmap:
    if CUSTOM_CREST_PROVIDER is not None:
        path = CUSTOM_CREST_PROVIDER(team_name)
        if path:
            pixmap = _loaded_image_pixmap(str(path), size)
            if pixmap is not None:
                return pixmap

    builtin_path = _badge_path(team_name)
    if builtin_path:
        pixmap = _loaded_image_pixmap(builtin_path, size)
        if pixmap is not None:
            return pixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    primary, secondary = crest_colors(team_name)

    # 外圈（次色） + 主体圆（主色）。
    pen = QPen(QColor(secondary))
    pen.setWidthF(max(1.0, size / 10.0))
    painter.setPen(pen)
    painter.setBrush(QColor(primary))
    painter.drawEllipse(QRectF(size * 0.08, size * 0.08, size * 0.84, size * 0.84))

    # 顶部小弧形镶边，增加识别度。
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(secondary))
    painter.drawChord(
        QRectF(size * 0.14, size * 0.14, size * 0.72, size * 0.72),
        30 * 16,
        120 * 16,
    )

    painter.setPen(QColor("#ffffff"))
    font = QFont()
    font.setPixelSize(max(8, int(size * 0.36)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(
        QRectF(0.0, 0.0, float(size), float(size)),
        Qt.AlignmentFlag.AlignCenter,
        crest_initials(team_name),
    )
    painter.end()
    return pixmap


def crest_pixmap(team_name: str, size: int) -> QPixmap:
    """生成队徽 QPixmap；size 为像素边长。"""
    return _crest_pixmap(team_name, size)


def draw_team_crest(
    painter: QPainter,
    rect: QRect,
    team_name: str,
    size: Optional[int] = None,
) -> QRect:
    """在 rect 内绘制居中队徽，返回实际绘制区域。"""
    if size is None:
        size = min(rect.width(), rect.height())
    pixmap = _crest_pixmap(team_name, size)
    x = rect.x() + (rect.width() - size) // 2
    y = rect.y() + (rect.height() - size) // 2
    painter.drawPixmap(x, y, pixmap)
    return QRect(x, y, size, size)


class TeamCrest(QWidget):
    """固定尺寸的队徽控件；可用于页头、比分板等上下文。"""

    def __init__(self, team_name: str, size: int = 48, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._team_name = team_name
        self._size = size
        self.setFixedSize(size, size)
        self.setToolTip(team_name)
        self.setAccessibleName(f"{team_name} 队徽")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.drawPixmap(0, 0, _crest_pixmap(self._team_name, self._size))

    def sizeHint(self) -> QSize:
        return QSize(self._size, self._size)
