"""源码入口冒烟：按生产路径构造新 UI 并能干净退出。

子进程里把数据根重定向到临时目录，然后
QApplication → apply_theme → MainWindow(GameService()) → show → exec，
由 QTimer 自动退出，断言进程退出码 0 且没有 traceback。

使用当前测试解释器（或 ``FS_TEST_PYTHON``）：旧版写死
``.venv-ui-v2/bin/python``，在 Windows 上会让本模块被整体跳过。
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = os.environ.get("FS_TEST_PYTHON") or sys.executable
_HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None

_DRIVER = '''
import sys
from pathlib import Path

project, data_root = sys.argv[1], sys.argv[2]
sys.path.insert(0, project)

from football_simulator import runtime

runtime.set_data_root_override(Path(data_root))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from football_simulator.app.service import GameService
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.theme import apply_theme

app = QApplication.instance() or QApplication(sys.argv)
apply_theme(app)
window = MainWindow(GameService())
window.show()


def _quit() -> None:
    worker = getattr(window, "_worker", None)
    if worker is not None and worker.isRunning():
        QTimer.singleShot(200, _quit)
        return
    app.quit()


QTimer.singleShot(800, _quit)
code = app.exec()
window.close()
raise SystemExit(0 if code == 0 else 1)
'''


@unittest.skipUnless(_HAS_PYSIDE, "当前测试解释器没有 PySide6")
class SourceEntrySmokeTests(unittest.TestCase):
    def test_new_entry_launches_and_quits_cleanly(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="smoke_entry_"))
        self.addCleanup(self._cleanup, tmp)
        driver = tmp / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        data_root = tmp / "data"
        data_root.mkdir()
        env = dict(os.environ)
        env.update(
            {
                "QT_QPA_PLATFORM": "offscreen",
                "PYTHONFAULTHANDLER": "1",
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        proc = subprocess.run(
            [PYTHON, str(driver), str(PROJECT_ROOT), str(data_root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            cwd=str(tmp),
            env=env,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"入口进程应干净退出。\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        self.assertNotIn("Traceback", proc.stderr or "")

    @staticmethod
    def _cleanup(path: Path) -> None:
        support.assert_cleanup_path_is_temporary(path)
        shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
