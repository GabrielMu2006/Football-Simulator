# -*- coding: utf-8 -*-
"""教程弹窗状态逻辑：首次展示标记、版本持久化、测试环境抑制。"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from football_simulator.ui_v2.components import tutorial


class TutorialStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="fs_tutorial_")).resolve()
        self._state = self._tmp / "tutorial_state.json"
        self._patcher = mock.patch.object(tutorial, "_state_path", return_value=self._state)
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def test_default_not_seen(self) -> None:
        self.assertEqual(tutorial.tutorial_seen_version(), 0)

    def test_mark_seen_roundtrip(self) -> None:
        tutorial.mark_tutorial_seen()
        self.assertEqual(tutorial.tutorial_seen_version(), tutorial.TUTORIAL_VERSION)

    def test_should_show_toggle_and_offscreen_suppression(self) -> None:
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "cocoa"}, clear=False):
            os.environ.pop("FOOTBALL_SIMULATOR_DISABLE_TUTORIAL", None)
            self.assertTrue(tutorial.should_show_tutorial())
            tutorial.mark_tutorial_seen()
            self.assertFalse(tutorial.should_show_tutorial())
        with mock.patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}, clear=False):
            self.assertFalse(tutorial.should_show_tutorial())

    def test_disable_env_var(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"QT_QPA_PLATFORM": "cocoa", "FOOTBALL_SIMULATOR_DISABLE_TUTORIAL": "1"},
            clear=False,
        ):
            self.assertFalse(tutorial.should_show_tutorial())


if __name__ == "__main__":
    unittest.main()
