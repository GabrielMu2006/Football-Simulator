# -*- coding: utf-8 -*-
"""W-090：把已验证的候选 ZIP 提升为正式发布产物。

只做四件事：
1. 校验候选 ZIP（完整性、单一根目录、EXE 与 _internal 存在）；
2. 复制为 ``release/windows/<name>.zip``（该目录只保留最新一个 ZIP）；
3. 写 ``SHA256SUMS.txt``、简短 ``README.txt`` 与精简 ``build-info.txt``；
4. 把同一个 ZIP 解压到 ``app/`` 作为当前可运行版本。

不生成安装器、不写注册表、不碰用户存档。所有路径必须在 --workspace 内。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

EXE_NAME = "Football Simulator.exe"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 512), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_inside(path: Path, workspace: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    if resolved != workspace and workspace not in resolved.parents:
        raise SystemExit(f"{label} 越界（必须在 {workspace} 内）：{resolved}")
    return resolved


def verify_candidate(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise SystemExit(f"ZIP 完整性失败：{bad}")
        names = archive.namelist()
        roots = {name.split("/", 1)[0] for name in names if name}
        if len(roots) != 1:
            raise SystemExit(f"ZIP 应只有一个根目录：{sorted(roots)}")
        root = roots.pop()
        exe = f"{root}/{EXE_NAME}"
        if exe not in names:
            raise SystemExit("ZIP 缺少 EXE")
        if not any(name.startswith(f"{root}/_internal/") for name in names):
            raise SystemExit("ZIP 缺少 _internal（不是完整 onedir 包）")
        readme = f"{root}/README.txt" in names
        build_info = f"{root}/build-info.txt"
        build_info_text = archive.read(build_info).decode("utf-8") if build_info in names else ""
        version_text = ""
        version_name = f"{root}/VERSION.txt"
        if version_name in names:
            version_text = archive.read(version_name).decode("utf-8").strip()
        return {
            "root": root,
            "entries": len(names),
            "has_readme": readme,
            "version": version_text,
            "build_info": build_info_text,
        }


def release_readme(name: str, sha: str, version: str, root: str) -> str:
    return f"""Football Simulator {version} — Windows x64 免安装版
================================================

交付文件
--------
- {name}（免安装 ZIP，{version}）
- SHA256SUMS.txt（校验和）
- build-info.txt（构建信息：版本、依赖、来源清单摘要）

校验
----
    certutil -hashfile "{name}" SHA256
输出应与 SHA256SUMS.txt 中的值一致。

使用
----
1. 解压整个 ZIP（根目录 {root}），不要只取出里面的 EXE。
2. 运行 {root}\\{EXE_NAME}。
3. 详见 ZIP 内 README.txt：存档位置（%APPDATA%\\Football Simulator）、
   新格式 .fsarchive 导入导出、运行要求与常见问题。

本目录只保留当前最新版本；程序文件夹与用户存档分离，删除或移动程序
不会删除存档。本版本不含安装器，不写注册表、服务或系统目录。
"""


def extract_app(zip_path: Path, app_dir: Path, expected_root: str) -> dict:
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(app_dir)
    root = app_dir / expected_root
    exe = root / EXE_NAME
    if not exe.is_file():
        raise SystemExit(f"解压后缺少 EXE：{exe}")
    if not (root / "_internal").is_dir():
        raise SystemExit("解压后缺少 _internal")
    files = sum(1 for path in root.rglob("*") if path.is_file())
    size = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
    return {"app_path": str(root), "app_exe": str(exe), "files": files, "bytes": size}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="提升候选 ZIP 为正式发布产物")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--candidate-zip", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--result", default="")
    args = parser.parse_args(argv)

    workspace = Path(args.workspace).resolve()
    candidate = assert_inside(Path(args.candidate_zip), workspace, "候选 ZIP")
    output_dir = assert_inside(Path(args.output_dir), workspace, "发布目录")
    app_dir = assert_inside(Path(args.app_dir), workspace, "app 目录")
    if not candidate.is_file():
        raise SystemExit(f"候选 ZIP 不存在：{candidate}")

    info = verify_candidate(candidate)
    sha = sha256_file(candidate)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 只保留最新一个 ZIP：清理发布目录里其它 ZIP（限本目录）。
    removed = []
    for existing in output_dir.glob("*.zip"):
        if existing.resolve() != (output_dir / candidate.name).resolve():
            existing.unlink()
            removed.append(existing.name)

    target_zip = output_dir / candidate.name
    shutil.copy2(candidate, target_zip)
    if sha256_file(target_zip) != sha:
        raise SystemExit("复制后校验和不一致")

    checksums = f"{sha}  {target_zip.name}\n"
    (output_dir / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    (output_dir / "README.txt").write_text(
        release_readme(target_zip.name, sha, info["version"], info["root"]), encoding="utf-8"
    )
    if info["build_info"]:
        (output_dir / "build-info.txt").write_text(info["build_info"], encoding="utf-8")

    app_info = extract_app(target_zip, app_dir, info["root"])

    result = {
        "zip": str(target_zip),
        "zip_bytes": target_zip.stat().st_size,
        "zip_sha256": sha,
        "version": info["version"],
        "root": info["root"],
        "entries": info["entries"],
        "removed_other_zips": removed,
        "release_dir": str(output_dir),
        **app_info,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.result:
        Path(args.result).parent.mkdir(parents=True, exist_ok=True)
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
