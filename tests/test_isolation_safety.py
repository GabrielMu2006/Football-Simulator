"""T-000：测试隔离与退出清理的安全基线。

这些断言直接保护用户真实文件：任何测试夹具的建立/清理都不得改变项目根下
``saves/``、``backups/``、``trash/``、``current_save.txt`` 与总配置的内容或
存在性。清理只允许使用夹具保存的绝对临时路径。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from football_simulator import runtime as sim_runtime
from football_simulator import state as sim_state

from tests import support
from tests.support import FreezeTestCase, PROJECT_ROOT, create_save, seeded_provider


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(block)
    return digest.hexdigest()


def production_fingerprint() -> dict:
    """项目根可写数据的指纹（路径 -> 大小/摘要），用于前后对照。"""
    snapshot = {}
    for name in ("saves", "backups", "trash"):
        root = PROJECT_ROOT / name
        if not root.exists():
            snapshot[name] = None
            continue
        entries = []
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                entries.append((str(path.relative_to(root)), path.stat().st_size, _file_digest(path)))
            elif path.is_symlink():
                entries.append((str(path.relative_to(root)), "symlink", os.readlink(path)))
        snapshot[name] = entries
    for name in (sim_runtime.CURRENT_SAVE_FILE_NAME, sim_runtime.SHARED_CONFIG_FILE_NAME):
        path = PROJECT_ROOT / name
        snapshot[name] = (
            (path.stat().st_size, _file_digest(path)) if path.exists() else None
        )
    return snapshot


class IsolationPathTests(FreezeTestCase):
    def test_data_root_paths_all_inside_fixture(self) -> None:
        root = sim_runtime.data_root()
        self.assertEqual(root, self._tmp_path)
        self.assertEqual(sim_runtime.save_root(), root / "saves")
        self.assertEqual(sim_runtime.backups_root(), root / "backups")
        self.assertEqual(sim_runtime.trash_root(), root / "trash")
        self.assertEqual(sim_runtime.current_save_path(), root / "current_save.txt")
        self.assertTrue(support._is_within(sim_runtime.current_save_path(), root))
        self.assertTrue(support._is_within(sim_runtime.shared_config_path(), root))
        # 总配置是可写的临时副本，不是项目配置。
        self.assertNotEqual(sim_runtime.shared_config_path().resolve(), (PROJECT_ROOT / sim_runtime.SHARED_CONFIG_FILE_NAME).resolve())

    def test_current_save_is_written_into_fixture_not_project(self) -> None:
        project_pointer = PROJECT_ROOT / sim_runtime.CURRENT_SAVE_FILE_NAME
        # 干净快照可能根本没有指针文件：记录存在性与内容，而不是假定它存在。
        existed_before = project_pointer.exists()
        content_before = project_pointer.read_text(encoding="utf-8") if existed_before else None
        sim_runtime.create_save_directory("isolation_probe")
        sim_runtime.store_current_save_name("isolation_probe")
        self.assertTrue((self._tmp_path / "current_save.txt").exists())
        self.assertEqual(sim_runtime.load_current_save_name(), "isolation_probe")
        self.assertEqual(project_pointer.exists(), existed_before, "夹具改动了项目根的指针文件存在性")
        if existed_before:
            self.assertEqual(
                project_pointer.read_text(encoding="utf-8"),
                content_before,
                "夹具改写了项目根的真实指针文件",
            )

    def test_backups_and_trash_stay_inside_fixture(self) -> None:
        create_save("isolation_save")
        backup = sim_runtime.backup_save("isolation_save")
        self.assertTrue(support._is_within(backup, self._tmp_path))
        self.assertIn("backups", backup.parts)
        trashed = sim_runtime.move_save_to_trash("isolation_save")
        self.assertTrue(support._is_within(trashed, self._tmp_path))
        self.assertIn("trash", trashed.parts)
        restored = sim_runtime.restore_trash_save(trashed, "isolation_save")
        self.assertTrue(restored.exists())

    def test_cleanup_guard_rejects_project_paths(self) -> None:
        with self.assertRaises(RuntimeError):
            support.assert_cleanup_path_is_temporary(PROJECT_ROOT)
        with self.assertRaises(RuntimeError):
            support.assert_cleanup_path_is_temporary(PROJECT_ROOT / "saves")
        with self.assertRaises(RuntimeError):
            support.assert_cleanup_path_is_temporary(Path(tempfile.gettempdir()))


class ProductionFilesUntouchedTests(unittest.TestCase):
    def test_fixture_lifecycle_does_not_change_project_files(self) -> None:
        before = production_fingerprint()
        with support.isolate_save_root() as root:
            create_save("safety_probe")
            sim_runtime.store_current_save_name("safety_probe")
            self.assertTrue((root / "saves" / "safety_probe").is_dir())
        after = production_fingerprint()
        self.assertEqual(before, after, "测试夹具改动了项目真实文件")

    def test_atexit_style_cleanup_uses_saved_temp_path(self) -> None:
        """模拟“override 撤销后清理”：只允许作用于保存的临时路径。"""
        before = production_fingerprint()
        tmp_dir = tempfile.mkdtemp(prefix="fs_atexit_sim_")
        root = support.install_test_data_root(tmp_dir)
        sim_state.set_rng_provider(seeded_provider())
        try:
            create_save("atexit_probe")
            sim_runtime.store_current_save_name("atexit_probe")
        finally:
            # 与历史 atexit 事故相同的顺序：先撤销注入……
            support.reset_test_data_root()
            # ……再清理；此处只能用保存下来的 root。
            support.assert_cleanup_path_is_temporary(root)
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        self.assertFalse(Path(tmp_dir).exists())
        self.assertEqual(before, production_fingerprint())


class QApplicationLifecycleTests(unittest.TestCase):
    def test_ensure_qapp_returns_single_instance(self) -> None:
        app = support.ensure_qapp()
        if app is None:
            self.skipTest("当前解释器没有 PySide6")
        self.assertIs(support.ensure_qapp(), app)
        from PySide6.QtWidgets import QApplication

        self.assertIs(QApplication.instance(), app)
