"""Run a disposable, resource-limited test against a source-only snapshot."""
import argparse
from datetime import datetime
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "football-simulator-tests:0.1.0"


def source_files():
    """Explicit source roots: never include working saves, release or home."""
    for name in ("football_simulator", "tests", "scripts", "assets", "team_badges_40_v2", "team_badges_40_v3", "archive/legacy-ui"):
        for path in (ROOT / name).rglob("*"):
            if path.is_file() and not any(part in ("__pycache__", ".DS_Store", "Variants", "gallery") for part in path.parts):
                yield path
    for name in ("ui_v2_main.py", "requirements-ui-v2.txt", "AGENTS.md", "build_macos_ui_v2_app.sh",
                 "Football Simulator UI v2.spec", "Football Simulator UI v2 Windows.spec", "足球模拟器总配置.json"):
        yield ROOT / name
    yield ROOT / "Reviews/identity_redesign_2026-10-20/stadiums.selected.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("core", "ui"), default="core")
    parser.add_argument("--build", action="store_true", help="rebuild the dependency image")
    args = parser.parse_args()
    subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=True, timeout=20)
    if args.build or subprocess.run(["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        if platform.machine().lower() not in ("arm64", "aarch64"):
            parser.error("This pinned test image targets Apple Silicon / Linux arm64.")
        # Download via the host's configured network, then install offline in
        # the image. Container proxy differences cannot stall a large Qt wheel.
        with tempfile.TemporaryDirectory(prefix="fs_docker_dependencies_") as build_temp:
            context = Path(build_temp)
            for name in ("Dockerfile", "requirements.txt", ".dockerignore"):
                shutil.copy2(ROOT / "docker/testing" / name, context / name)
            subprocess.run([sys.executable, "-m", "pip", "download", "--only-binary=:all:",
                            "--platform", "manylinux_2_39_aarch64", "--python-version", "312",
                            "--implementation", "cp", "--abi", "abi3", "--dest", str(context / "wheels"),
                            "-r", str(context / "requirements.txt")], check=True)
            subprocess.run(["docker", "build", "-t", IMAGE, str(context)], check=True)
    output = ROOT / "artifacts/docker" / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    uid, gid = os.getuid() or 10001, os.getgid() or 10001
    with tempfile.TemporaryDirectory(prefix="fs_docker_source_") as temp:
        source = Path(temp)
        manifest = []
        for path in sorted(set(source_files())):
            rel = path.relative_to(ROOT)
            target = source / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            manifest.append({"path": str(rel), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        (output / "source-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        command = ["docker", "run", "--rm", "--init", "--read-only", "--network=none", "--cpus=2", "--memory=2g",
                   "--pids-limit=256", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--user", f"{uid}:{gid}",
                   "--tmpfs", "/tmp:rw,size=536870912,mode=1777",
                   "--tmpfs", f"/run/fs-data:rw,size=805306368,mode=700,uid={uid},gid={gid}",
                   "--mount", f"type=bind,source={source},target=/workspace,readonly",
                   "--mount", f"type=bind,source={output},target=/results",
                   "-e", "FS_DATA_ROOT=/run/fs-data", IMAGE, "python", "scripts/docker_check.py", args.suite]
        (output / "invocation.json").write_text(json.dumps({"command": command, "suite": args.suite}, indent=2))
        with (output / "console.log").open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        print((output / "console.log").read_text())
        print(f"Evidence: {output}")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
