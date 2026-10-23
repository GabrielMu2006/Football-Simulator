"""新 UI 源码入口冒烟：ui_v2_main.py 在独立子进程启动并干净退出。

必须使用当前测试解释器（或显式 ``FS_TEST_PYTHON`` 覆盖），不能写死
``.venv-ui-v2/bin/python``：Windows 上没有那个目录，关键入口会因此被跳过。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests import support

PROJECT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("FS_TEST_PYTHON") or sys.executable

_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_HAS_PYSIDE, "当前测试解释器没有 PySide6")
class EntrySmokeTests(unittest.TestCase):
    def test_source_entry_launches_and_quits(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="fs_entry_"))
        self.addCleanup(self._cleanup, tmp)
        data_root = tmp / "data"
        data_root.mkdir()
        env = dict(os.environ)
        env.update(
            {
                "QT_QPA_PLATFORM": "offscreen",
                "FS_DATA_ROOT": str(data_root),
                "FS_SMOKE_EXIT_MS": "1200",
                "PYTHONFAULTHANDLER": "1",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.pop("PYTHONSTARTUP", None)
        process = subprocess.run(
            [PYTHON, str(PROJECT / "ui_v2_main.py")],
            cwd=str(tmp),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        self.assertEqual(process.returncode, 0, (process.stderr or "")[-3000:])
        self.assertNotIn("Traceback", process.stderr or "")
        # 入口启动本身不应创建存档或写 current_save。
        saves = data_root / "saves"
        self.assertEqual(sorted(saves.glob("*")) if saves.exists() else [], [])

    @staticmethod
    def _cleanup(path: Path) -> None:
        support.assert_cleanup_path_is_temporary(path)
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
