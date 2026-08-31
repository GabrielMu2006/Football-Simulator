# -*- coding: utf-8 -*-
"""校验 40 支球队都能映射到 team_badges_40_v2 的新队标。"""

import json
import unittest
from pathlib import Path

from football_simulator.ui_v2.components.team_crest import _badge_path, _badge_root

ROOT = Path(__file__).resolve().parents[1]


class TeamBadgesTest(unittest.TestCase):
    def test_badge_root_exists(self) -> None:
        root = _badge_root()
        self.assertIsNotNone(root, "team_badges_40/PNG 目录未找到")
        pngs = list(root.glob("*.png"))  # type: ignore[union-attr]
        self.assertEqual(len(pngs), 40)

    def test_all_config_team_names_have_badge(self) -> None:
        config_path = ROOT / "足球模拟器总配置.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        names = config["premier_teams"] + config["second_division_teams"]
        self.assertEqual(len(names), 40)
        missing = [name for name in names if _badge_path(name) is None]
        self.assertFalse(missing, f"缺少队标图片的球队: {missing}")


if __name__ == "__main__":
    unittest.main()
