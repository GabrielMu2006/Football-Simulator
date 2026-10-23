# -*- coding: utf-8 -*-
"""阶段0：确认正式UI是唯一入口，旧 ui_v2 已归档退出运行/测试/打包路径。

这不是文本检查：子进程实际运行 ui_v2_main.py 并回执 MainWindow/page 模块；
另起子进程收集全部现行测试模块，任何旧模块或 ImportError 都会失败。

候选包存在时，可用 FS_CANDIDATE_APP 指向本轮 .app，本模块再用 PyInstaller
归档视图核对模块清单；候选构建脚本会单独调用该检查。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from tests import support

ROOT = support.PROJECT_ROOT
PYTHON = os.environ.get("FS_TEST_PYTHON") or sys.executable
ARCHIVE_DIR = ROOT / "archive" / "legacy-ui"
ARCHIVE_ZIP = ARCHIVE_DIR / "legacy-ui-v2-20261021-2351.zip"
ARCHIVE_MANIFEST = ARCHIVE_DIR / "legacy-ui-v2-20261021-2351.manifest.json"
SPECS = (
    ROOT / "build_macos_ui_v2_app.sh",
    ROOT / "Football Simulator UI v2.spec",
    ROOT / "Football Simulator UI v2 Windows.spec",
)


def _run(cmd, *, env_extra=None, timeout=240):
    env = dict(os.environ)
    env.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONFAULTHANDLER": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    env.update(env_extra or {})
    env.pop("PYTHONSTARTUP", None)
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


class CurrentUIOnlyTests(unittest.TestCase):
    def test_old_source_directory_is_gone(self) -> None:
        self.assertFalse((ROOT / "football_simulator" / "ui_v2").exists())
        self.assertTrue((ROOT / "football_simulator" / "ui" / "team_crest.py").is_file())
        self.assertTrue((ROOT / "ui_v2_main.py").is_file())

    def test_current_source_does_not_import_legacy_ui(self) -> None:
        offenders = []
        for path in (ROOT / "football_simulator").rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(name == "football_simulator.ui_v2" or name.startswith("football_simulator.ui_v2.") for name in names):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_archive_manifest_and_zip_are_intact(self) -> None:
        manifest = json.loads(ARCHIVE_MANIFEST.read_text(encoding="utf-8"))
        digest = hashlib.sha256(ARCHIVE_ZIP.read_bytes()).hexdigest()
        self.assertEqual(digest, manifest["archive_sha256"])
        self.assertEqual(manifest["legacy_test_module_count"], 10)
        self.assertEqual(manifest["legacy_test_case_count"], 248)
        with zipfile.ZipFile(ARCHIVE_ZIP) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(sorted(archive.namelist()), sorted(entry["archive_path"] for entry in manifest["entries"]))
            for entry in manifest["entries"]:
                payload = archive.read(entry["archive_path"])
                self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"], entry["archive_path"])
        # The archive is a passive ZIP: no unpacked second runnable tree beside it.
        self.assertEqual([p.name for p in ARCHIVE_DIR.iterdir() if p.suffix == ".py"], [])

    def test_packaging_references_new_crest_component_only(self) -> None:
        for path in SPECS:
            text = path.read_text(encoding="utf-8")
            self.assertIn("football_simulator.ui.team_crest", text, path.name)
            self.assertNotIn("football_simulator.ui_v2.components.team_crest", text, path.name)
            self.assertNotIn("football_simulator.ui_v2.pages", text, path.name)
            self.assertNotIn("archive/legacy-ui", text, path.name)

    def test_source_entry_receipt_names_current_main_window(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="fs_identity_"))
        self.addCleanup(self._cleanup, tmp)
        data_root = tmp / "data"
        data_root.mkdir()
        result_path = tmp / "entry.json"
        proc = _run(
            [PYTHON, str(ROOT / "ui_v2_main.py")],
            env_extra={
                "FS_DATA_ROOT": str(data_root),
                "FS_SMOKE_RESULT": str(result_path),
                "FS_SMOKE_EXIT_MS": "1500",
            },
        )
        self.assertEqual(proc.returncode, 0, (proc.stderr or "")[-4000:])
        self.assertNotIn("Traceback", proc.stderr or "")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertNotEqual(payload["pid"], os.getpid())
        self.assertEqual(payload["main_window_module"], "football_simulator.ui.main_window")
        self.assertTrue(payload["main_window_file"].endswith("/football_simulator/ui/main_window.py"), payload["main_window_file"])
        self.assertTrue(payload["page_module"].startswith("football_simulator.ui.pages."), payload["page_module"])
        self.assertFalse(payload["frozen"])
        self.assertEqual(Path(payload["data_root"]).resolve(), data_root.resolve())
        self.assertEqual(payload["error"], "")

    def test_old_package_and_entry_are_not_importable(self) -> None:
        self.assertIsNone(importlib.util.find_spec("football_simulator.ui_v2"))
        proc = _run([PYTHON, "-c", "import football_simulator.ui_v2"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ModuleNotFoundError", proc.stderr)
        proc = _run([PYTHON, "-c", "from football_simulator.ui_v2.app import main"])
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("ModuleNotFoundError", proc.stderr)

    def test_current_discovery_collects_without_legacy_or_import_errors(self) -> None:
        from tests.run_suite import discover_modules

        modules = discover_modules()
        manifest = json.loads(ARCHIVE_MANIFEST.read_text(encoding="utf-8"))
        archived = {Path(entry["original_path"]).stem for entry in manifest["entries"] if entry["kind"] == "legacy-test"}
        collected = {Path(name.split(".")[-1]).name for name in modules}
        self.assertEqual(archived & collected, set(), f"旧UI测试仍在发现列表：{archived & collected}")
        driver = tmp_driver = Path(tempfile.mkdtemp(prefix="fs_collect_"))
        self.addCleanup(lambda: shutil.rmtree(tmp_driver, ignore_errors=True))
        script = driver / "collect.py"
        script.write_text(
            "import importlib, json, sys, unittest\n"
            "root, names = sys.argv[1], json.loads(sys.argv[2])\n"
            "sys.path.insert(0, root)\n"
            "errors = []\n"
            "count = 0\n"
            "for name in names:\n"
            "    try:\n"
            "        module = importlib.import_module(name)\n"
            "        suite = unittest.defaultTestLoader.loadTestsFromModule(module)\n"
            "        count += suite.countTestCases()\n"
            "    except Exception as exc:\n"
            "        errors.append([name, repr(exc)])\n"
            "print(json.dumps({'count': count, 'errors': errors}))\n",
            encoding="utf-8",
        )
        proc = _run([PYTHON, str(script), str(ROOT), json.dumps(modules)], timeout=420)
        self.assertEqual(proc.returncode, 0, (proc.stderr or "")[-4000:])
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["errors"], [])
        self.assertGreater(payload["count"], 400)
        self.assertFalse(any(name.startswith("football_simulator.ui_v2") for name in modules))

    def test_forty_v3_crests_load_from_new_component(self) -> None:
        from football_simulator.ui.team_crest import _badge_path, _badge_root, badge_source_directory, crest_pixmap

        support.ensure_qapp()
        root = _badge_root()
        self.assertIsNotNone(root)
        self.assertEqual(badge_source_directory(), "team_badges_40_v3")
        config = json.loads((ROOT / "足球模拟器总配置.json").read_text(encoding="utf-8"))
        names = config["premier_teams"] + config["second_division_teams"]
        self.assertEqual(len(names), 40)
        missing = [name for name in names if _badge_path(name) is None]
        self.assertEqual(missing, [])
        for name in names:
            pixmap = crest_pixmap(name, 24)
            self.assertFalse(pixmap.isNull(), name)

    def test_candidate_bundle_has_no_legacy_modules(self) -> None:
        app = os.environ.get("FS_CANDIDATE_APP")
        if not app:
            self.skipTest("FS_CANDIDATE_APP 未设置；候选包检查在候选构建后单独执行")
        app_path = Path(app)
        executable = app_path / "Contents" / "MacOS" / app_path.stem
        self.assertTrue(executable.is_file(), executable)
        viewer = ROOT / ".venv-ui-v2" / "bin" / "pyi-archive_viewer"
        proc = _run([str(viewer), "-r", str(executable)], timeout=180)
        self.assertEqual(proc.returncode, 0, (proc.stderr or "")[-4000:])
        listing = proc.stdout
        self.assertNotIn("football_simulator.ui_v2", listing)
        # Data files must not include the passive source archive.
        entries = [str(path.relative_to(app_path)) for path in app_path.rglob("*") if path.is_file()]
        self.assertFalse(any("legacy-ui" in entry for entry in entries))

    @staticmethod
    def _cleanup(path: Path) -> None:
        support.assert_cleanup_path_is_temporary(path)
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
