# 生成 Football Simulator UI v2 的应用图标（macOS .icns 所需 PNG 集）。
# 用法：QT_QPA_PLATFORM=offscreen .venv-ui-v2/bin/python scripts/generate_app_icon.py

from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)

SIZE = 1024
CENTER = SIZE / 2.0
BALL_RADIUS = 340.0


def _polygon_points(center: QPointF, radius: float, rotation_deg: float = -90.0) -> QPolygonF:
    points = []
    for i in range(5):
        angle = math.radians(rotation_deg + i * 72.0)
        points.append(QPointF(center.x() + radius * math.cos(angle), center.y() + radius * math.sin(angle)))
    return QPolygonF(points)


def _scale_points(points: QPolygonF, factor: float) -> list:
    return [QPointF(p.x() * factor, p.y() * factor) for p in points]


def draw_icon(painter: QPainter, size: int) -> None:
    scale = size / SIZE
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # 背景：深蓝渐变圆角方形。
    painter.setPen(Qt.PenStyle.NoPen)
    background = QLinearGradient(0, 0, size, size)
    background.setColorAt(0.0, QColor("#1d4f8f"))
    background.setColorAt(0.5, QColor("#0f2338"))
    background.setColorAt(1.0, QColor("#0b1220"))
    painter.setBrush(background)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    # 装饰性青色弧线（左上与右下呼应主界面强调色）。
    painter.setPen(QPen(QColor(125, 211, 252, 60), max(2.0, size * 0.01)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawArc(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88), 35 * 16, 45 * 16)
    painter.drawArc(QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88), 215 * 16, 45 * 16)

    # 足球主体：白色渐变球体。
    ball_center = QPointF(CENTER * scale, CENTER * scale)
    ball_rect = QRectF(
        CENTER * scale - BALL_RADIUS * scale,
        CENTER * scale - BALL_RADIUS * scale,
        BALL_RADIUS * 2 * scale,
        BALL_RADIUS * 2 * scale,
    )
    ball_gradient = QRadialGradient(
        ball_center.x() - ball_rect.width() * 0.25,
        ball_center.y() - ball_rect.height() * 0.25,
        ball_rect.width() * 0.8,
    )
    ball_gradient.setColorAt(0.0, QColor("#ffffff"))
    ball_gradient.setColorAt(0.65, QColor("#eef3fa"))
    ball_gradient.setColorAt(1.0, QColor("#aab8cc"))
    painter.setPen(QPen(QColor("#7dd3fc"), max(2.0, size * 0.012)))
    painter.setBrush(ball_gradient)
    painter.drawEllipse(ball_rect)

    # 黑色/深蓝五边形纹理（中心一个 + 周围五个）。
    patch_color = QColor("#0d2136")
    painter.setBrush(patch_color)
    painter.setPen(QPen(QColor("#f8fbff"), max(1.0, size * 0.004)))

    center_radius = 118.0 * scale
    ring_radius = 285.0 * scale
    small_radius = 68.0 * scale

    painter.drawPolygon(_polygon_points(ball_center, center_radius))
    for k in range(5):
        angle = math.radians(-90.0 + k * 72.0)
        p = QPointF(
            ball_center.x() + ring_radius * math.cos(angle),
            ball_center.y() + ring_radius * math.sin(angle),
        )
        painter.drawPolygon(_polygon_points(p, small_radius, (-90.0 + k * 72.0 + 36.0)))

    # 球体高光。
    painter.setPen(Qt.PenStyle.NoPen)
    highlight = QRadialGradient(
        ball_center.x() - ball_rect.width() * 0.3,
        ball_center.y() - ball_rect.height() * 0.35,
        ball_rect.width() * 0.35,
    )
    highlight.setColorAt(0.0, QColor(255, 255, 255, 180))
    highlight.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(highlight)
    painter.drawEllipse(ball_rect)

    # 底部小文字标（产品名缩写，仅在 512+ 时清晰）。
    if size >= 256:
        painter.setPen(QColor("#e8eef7"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.055))
        painter.setFont(font)
        painter.drawText(
            QRectF(0, size * 0.855, size, size * 0.12),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "FOOTBALL SIMULATOR",
        )


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])

    from PySide6.QtGui import QPixmap

    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for size in sizes:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        draw_icon(painter, size)
        painter.end()
        pixmap.save(str(ASSETS / f"app_icon_{size}.png"))

    # macOS iconutil 需要的 Icons.icns 目录结构。
    iconset = ASSETS / "AppIcon.iconset"
    if iconset.exists():
        for child in iconset.iterdir():
            if child.is_file():
                child.unlink()
    else:
        iconset.mkdir(parents=True, exist_ok=True)
    icon_files = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    for name, source_size in icon_files.items():
        source = ASSETS / f"app_icon_{source_size}.png"
        shutil.copyfile(source, iconset / name)

    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "app.icns")],
        check=True,
    )
    print("generated:", ASSETS / "app.icns")
    print("missing sizes:", [s for s in sizes if not (ASSETS / f"app_icon_{s}.png").exists()])


if __name__ == "__main__":
    main()
