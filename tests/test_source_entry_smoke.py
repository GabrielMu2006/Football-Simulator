"""源码入口冒烟（阶段 7，§12.6）：ui_v2_main.py 可从源码启动。

在 offscreen 子进程中把存档根重定向到临时目录，按生产路径构造
（QApplication + 主题 + MainWindow(SimulatorUIService()) + show + exec），
由 QTimer 自动退出，断言进程干净退出且无 traceback。
无 UI venv（PySide6）时整模块跳过。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv-ui-v2" / "bin" / "python"

_DRIVER = '''
import sys
from pathlib import Path

project, save_root = sys.argv[1], sys.argv[2]
sys.path.insert(0, project)

from football_simulator import runtime, state

runtime.set_save_root_override(Path(save_root))
state.set_rng_provider(lambda: __import__("random").Random(20260828))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from football_simulator.ui_v2.services import SimulatorUIService
from football_simulator.ui_v2.main_window import MainWindow
from football_simulator.ui_v2.theme import APP_STYLE

app = QApplication(sys.argv)
app.setStyleSheet(APP_STYLE)
window = MainWindow(SimulatorUIService())
window.show()


def _quit() -> None:
    worker = getattr(window, "_simulate_worker", None)
    if worker is not None and worker.isRunning():
        QTimer.singleShot(200, _quit)
        return
    app.quit()


QTimer.singleShot(800, _quit)
code = app.exec()
window.close()
raise SystemExit(0 if code == 0 else 1)
'''


def _venv_available() -> bool:
    return VENV_PYTHON.exists()


@unittest.skipUnless(_venv_available(), "需要 .venv-ui-v2（PySide6）")
class SourceEntrySmokeTests(unittest.TestCase):
    def test_ui_v2_main_launches_and_quits_cleanly(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="smoke_entry_"))
        driver = tmp / "driver.py"
        driver.write_text(_DRIVER, encoding="utf-8")
        try:
            proc = subprocess.run(
                [str(VENV_PYTHON), str(driver), str(PROJECT_ROOT), str(tmp / "saves")],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=str(tmp),
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"入口进程应干净退出。\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
            )
            self.assertNotIn("Traceback", proc.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
