"""八项赛事的真实奖杯资源及共享展示控件。

manifest 路径固定从 resource_root()/assets/trophies/v1 派生。缺 manifest、
缺图、坏图、SHA/尺寸不合法都只影响图片，不影响冠军文本。缓存键包含实际
路径、manifest 签名、ID、目标尺寸与 DPR；F5 时文件变更会被重新读取。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from football_simulator import runtime
from football_simulator.game import rules
from football_simulator.ui import tokens as t

TROPHY_IDS = ("premier", "second", "aureole", "confluence", "starpath", "ember", "ascendant", "zenith")
_CACHE: Dict[tuple, QPixmap] = {}
_SOURCE_CACHE: Dict[tuple, dict] = {}
_MANIFEST_CACHE: Dict[tuple, dict] = {}


def _remember(cache: dict, key: tuple, value, limit: int = 64):
    if len(cache) >= limit:
        cache.pop(next(iter(cache)))
    cache[key] = value
    return value


def trophy_dir() -> Path:
    return runtime.resource_root() / "assets" / "trophies" / "v1"


def manifest_path() -> Path:
    return trophy_dir() / "manifest.json"


def _file_signature(path: Path) -> tuple:
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    except OSError:
        return (str(path.resolve()), 0, 0)


def load_manifest(path: Optional[Path] = None) -> dict:
    target = Path(path) if path is not None else manifest_path()
    sig = _file_signature(target)
    cached = _MANIFEST_CACHE.get(sig)
    if cached is not None:
        return cached
    result = {"version": 0, "assets": {}, "valid": False, "error": "manifest 尚待交付"}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的奖杯资源版本")
        assets = data.get("assets")
        if not isinstance(assets, dict):
            raise ValueError("assets 必须是 ID 字典")
        result = {"version": int(data.get("version", 0)), "assets": assets, "valid": True, "error": ""}
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - 资源错误必须克制回退
        result = {"version": 0, "assets": {}, "valid": False, "error": f"manifest 无效：{exc}"}
    return _remember(_MANIFEST_CACHE, sig, result)


def asset_status(competition_id: str, path: Optional[Path] = None) -> dict:
    target = Path(path) if path is not None else manifest_path()
    manifest = load_manifest(target)
    if competition_id not in TROPHY_IDS:
        return {"ready": False, "reason": "该赛事不设置冠军奖杯", "entry": None}
    entry = (manifest.get("assets") or {}).get(competition_id)
    if not isinstance(entry, dict) or entry.get("status") != "ready":
        return {"ready": False, "reason": "奖杯图片待交付", "entry": entry}
    image_path = (target.parent / str(entry.get("path") or f"{competition_id}.png")).resolve()
    if not image_path.is_relative_to(target.parent.resolve()):
        return {"ready": False, "reason": "奖杯图片路径无效", "entry": entry}
    if not image_path.is_file():
        return {"ready": False, "reason": "奖杯图片文件缺失", "entry": entry, "path": image_path}
    signature = (_file_signature(target), competition_id, _file_signature(image_path))
    cached = _SOURCE_CACHE.get(signature)
    if cached is not None:
        return cached
    result = {"ready": False, "reason": "", "entry": entry, "path": image_path, "signature": signature}
    try:
        payload = image_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if entry.get("sha256") and digest.lower() != str(entry["sha256"]).lower():
            result["reason"] = "奖杯图片校验未通过"
        else:
            pixmap = QPixmap()
            if not pixmap.loadFromData(payload):
                result["reason"] = "奖杯图片无法解码"
            elif ((entry.get("width") is not None and entry["width"] != pixmap.width()) or
                  (entry.get("height") is not None and entry["height"] != pixmap.height())):
                result["reason"] = "奖杯图片尺寸不一致"
            else:
                result.update(ready=True, pixmap=pixmap)
    except OSError:
        result["reason"] = "奖杯图片无法读取"
    return _remember(_SOURCE_CACHE, signature, result, 32)


def trophy_pixmap(competition_id: str, target: int = 128, dpr: float = 1.0, path: Optional[Path] = None) -> Optional[QPixmap]:
    if competition_id not in TROPHY_IDS:
        return None
    target_path = Path(path) if path is not None else manifest_path()
    status = asset_status(competition_id, target_path)
    if not status.get("ready"):
        return None
    key = (status["signature"], max(1, int(target)), round(float(dpr), 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    source = status["pixmap"]
    physical = max(1, int(round(int(target) * max(1.0, float(dpr)))))
    scaled = source.scaled(physical, physical, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(max(1.0, float(dpr)))
    return _remember(_CACHE, key, scaled)


def clear_cache() -> None:
    _CACHE.clear()
    _SOURCE_CACHE.clear()
    _MANIFEST_CACHE.clear()


class TrophyImage(QWidget):
    """等比展示透明原图；淡化态只影响图片，不表示失去冠军事实。"""

    def __init__(self, competition_id: str, size: int = 160, *, muted: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.competition_id = competition_id
        self.muted = muted
        self.setObjectName("TrophyImage")
        self.setFixedSize(size, size)
        self.setAccessibleName(f"{rules.display_name(competition_id, 'en')} 冠军奖杯")
        self.setProperty("competitionId", competition_id)
        self.setProperty("muted", muted)
        self.reload()

    def sizeHint(self) -> QSize:
        return self.size()

    def reload(self) -> None:
        self.status = asset_status(self.competition_id)
        self.setProperty("assetReady", self.status["ready"])
        self.setToolTip(self.accessibleName() if self.status["ready"] else self.status["reason"])
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        # 原图上下留白较紧，插槽本身保留10%边距，不裁切/重写原图。
        edge = max(1, round(min(self.width(), self.height()) * 0.8))
        pixmap = trophy_pixmap(self.competition_id, edge, self.devicePixelRatioF())
        if pixmap is None:
            painter.setPen(QColor(t.MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "奖杯")
            return
        painter.setOpacity(0.22 if self.muted else 1.0)
        logical = pixmap.deviceIndependentSize()
        rect = QRectF((self.width() - logical.width()) / 2, (self.height() - logical.height()) / 2,
                      logical.width(), logical.height())
        painter.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
