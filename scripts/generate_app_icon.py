"""Export the approved icon artwork into PNG, ICO and macOS ICNS assets.

Artwork: assets/app_icon_master.png (generated with imagegen; do not redraw here).
Usage: .venv-ui-v2/bin/python scripts/generate_app_icon.py
Requires PySide6 and macOS iconutil. Only resizing/format conversion is performed.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

ROOT = Path(__file__).resolve().parent.parent
SIZES = (16, 32, 64, 128, 256, 512, 1024)
ICONSET_FILES = {
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


def resized(source: QImage, size: int) -> QImage:
    return source.scaled(
        size, size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def write_ico(source: QImage, target: Path) -> None:
    """Write the standard ICO directory with alpha-preserving PNG entries."""
    entries = []
    for size in (16, 32, 48, 64, 128, 256):
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not resized(source, size).save(buffer, "PNG"):
            raise RuntimeError(f"Failed to encode {size}px ICO entry")
        buffer.close()
        entries.append((size, bytes(data)))

    offset = 6 + 16 * len(entries)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    for size, data in entries:
        dimension = size if size < 256 else 0
        directory.extend(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    target.write_bytes(bytes(directory) + b"".join(data for _, data in entries))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "assets/app_icon_master.png")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets")
    args = parser.parse_args()
    source = QImage(str(args.source))
    if source.isNull() or source.width() != source.height() or source.width() < 1024:
        raise SystemExit("Icon master must be a readable square image of at least 1024px")
    if not source.hasAlphaChannel():
        raise SystemExit("Icon master must preserve transparency outside the rounded tile")
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        raise SystemExit("macOS iconutil is required to export app.icns")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        if not resized(source, size).save(str(output / f"app_icon_{size}.png"), "PNG"):
            raise SystemExit(f"Failed to write {size}px PNG")
    write_ico(source, output / "app.ico")

    iconset = output / "AppIcon.iconset"
    iconset.mkdir(exist_ok=True)
    for name, size in ICONSET_FILES.items():
        shutil.copyfile(output / f"app_icon_{size}.png", iconset / name)
    subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(output / "app.icns")], check=True)
    print(f"Exported {len(SIZES)} PNG sizes, 6 ICO sizes and app.icns to {output}")


if __name__ == "__main__":
    main()
