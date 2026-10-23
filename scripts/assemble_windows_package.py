# -*- coding: utf-8 -*-
"""把 PyInstaller onedir 产物组装为免安装 ZIP（Windows 构建使用）。

职责：
- 复制 ``dist/Football Simulator`` 到 staging 的一个版本化目录；
- 写入用户说明（README.txt）、版本信息（VERSION.txt）与精简 build-info；
- 以 UTF-8 文件名标志压缩为 ZIP，输出 SHA-256；
- 候选包与正式包分离：``--candidate-only`` 只写 staging 区域。

不做任何安装器、注册表、服务或系统目录写入。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

APP_DIR_NAME = "Football Simulator"
EXE_NAME = "Football Simulator.exe"
ZIP_PREFIX = "Football-Simulator"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 512), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_version(source_root: Path) -> str:
    sys.path.insert(0, str(source_root / "scripts"))
    try:
        import write_windows_version_info as version_info  # noqa: PLC0415

        return version_info.read_app_version(source_root)
    finally:
        sys.path.pop(0)


def release_name(version: str) -> str:
    return f"{ZIP_PREFIX}-{version}-Windows-x64"


def render_readme(template: str, version: str, folder_name: str) -> str:
    replacements = {
        "{{version}}": version,
        "{{folder}}": folder_name,
        "{{exe}}": EXE_NAME,
    }
    text = template
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def render_build_info(facts: dict) -> str:
    lines = [
        "Football Simulator — Windows build information",
        "=" * 52,
        f"version: {facts['version']}",
        f"built_at: {facts['built_at']}",
        f"package: {facts['package']}",
        f"layout: onedir (portable ZIP, no installer)",
        "",
        "runtime",
        f"  python: {facts['python']}",
        f"  python_executable: {facts['python_executable']}",
        f"  pyside6: {facts['pyside6']}",
        f"  qt: {facts['qt']}",
        f"  pyinstaller: {facts['pyinstaller']}",
        "",
        "host",
        f"  platform: {facts['platform']}",
        f"  machine: {facts['machine']}",
        f"  windows: {facts['windows']}",
        "",
        "source snapshot",
        f"  manifest_files: {facts['manifest_files']}",
        f"  manifest_sha256: {facts['manifest_sha256']}",
        f"  app_version_source: football_simulator/version.py",
        "",
    ]
    return "\n".join(lines)


def write_zip(staging_root: Path, zip_path: Path) -> None:
    """压缩 staging 根下的单一应用目录（zip 内以版本目录为根）。"""
    if zip_path.exists():
        zip_path.unlink()
    entries = []
    for path in sorted(staging_root.rglob("*")):
        if path.is_file():
            entries.append(path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in entries:
            arcname = path.relative_to(staging_root).as_posix()
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(path, "rb") as handle:
                archive.writestr(info, handle.read())


def directory_stats(root: Path) -> dict:
    files = 0
    size = 0
    for path in root.rglob("*"):
        if path.is_file():
            files += 1
            size += path.stat().st_size
    return {"files": files, "bytes": size}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="组装 Windows 免安装 ZIP")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dist", required=True)
    parser.add_argument("--staging", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--candidate-only", action="store_true")
    parser.add_argument("--manifest", default="")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    source_root = Path(args.source).resolve()
    dist_dir = Path(args.dist).resolve()
    staging_dir = Path(args.staging).resolve()
    output_dir = Path(args.output).resolve()

    for path in (source_root, dist_dir, staging_dir, output_dir):
        if workspace not in path.parents and path != workspace:
            raise SystemExit(f"路径越界（必须在 {workspace} 内）：{path}")

    version = read_version(source_root)
    folder = release_name(version)
    app_source = dist_dir / APP_DIR_NAME
    exe_path = app_source / EXE_NAME
    if not exe_path.is_file():
        raise SystemExit(f"未找到打包产物：{exe_path}")

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    package_root = staging_dir / folder
    package_root.mkdir(parents=True)

    # 1) 应用本体
    shutil.copytree(app_source, package_root, dirs_exist_ok=True)

    # 2) 说明与版本
    template_path = source_root / "packaging" / "windows" / "README.txt"
    template = template_path.read_text(encoding="utf-8")
    (package_root / "README.txt").write_text(render_readme(template, version, folder), encoding="utf-8")
    (package_root / "VERSION.txt").write_text(f"{version}\n", encoding="utf-8")

    manifest_path = Path(args.manifest) if args.manifest else None
    manifest_files = 0
    manifest_sha = ""
    if manifest_path and manifest_path.is_file():
        manifest_sha = sha256_file(manifest_path)
        try:
            manifest_files = int(json.loads(manifest_path.read_text(encoding="utf-8"))["file_count"])
        except (ValueError, KeyError, json.JSONDecodeError):
            manifest_files = 0

    import PySide6
    from PySide6 import QtCore

    import PyInstaller

    facts = {
        "version": version,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "package": folder,
        "python": platform.python_version() + " " + platform.machine(),
        "python_executable": sys.executable,
        "pyside6": PySide6.__version__,
        "qt": QtCore.qVersion(),
        "pyinstaller": PyInstaller.__version__,
        "platform": sys.platform,
        "machine": platform.machine(),
        "windows": platform.platform(),
        "manifest_files": manifest_files,
        "manifest_sha256": manifest_sha or "(未提供清单)",
    }
    (package_root / "build-info.txt").write_text(render_build_info(facts), encoding="utf-8")

    zip_name = f"{folder}.zip"
    if args.candidate_only:
        target_zip = staging_dir.parent / zip_name if staging_dir.name == "staging" else staging_dir / zip_name
    else:
        target_zip = output_dir / zip_name
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    write_zip(staging_dir, target_zip)

    zip_sha = sha256_file(target_zip)
    stats = directory_stats(package_root)
    summary = {
        "version": version,
        "folder": folder,
        "zip": str(target_zip),
        "zip_bytes": target_zip.stat().st_size,
        "zip_sha256": zip_sha,
        "candidate_only": bool(args.candidate_only),
        "staging": str(package_root),
        "package_files": stats["files"],
        "package_bytes": stats["bytes"],
        "facts": facts,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
