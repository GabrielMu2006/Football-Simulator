# -*- coding: utf-8 -*-
"""校验 40 支球队都能映射到 team_badges_40_v3 的正式队标。"""

import json
import unittest
from pathlib import Path

from football_simulator.ui.team_crest import _badge_path, _badge_root, badge_source_directory

ROOT = Path(__file__).resolve().parents[1]


class TeamBadgesTest(unittest.TestCase):
    def test_badge_root_exists(self) -> None:
        root = _badge_root()
        self.assertIsNotNone(root, "team_badges_40_v3/PNG 目录未找到")
        pngs = list(root.glob("*.png"))  # type: ignore[union-attr]
        self.assertEqual(len(pngs), 40)
        self.assertEqual(badge_source_directory(), "team_badges_40_v3", "正常必须命中 v3，不能静默回退 v2")

    def test_all_config_team_names_have_badge(self) -> None:
        config_path = ROOT / "足球模拟器总配置.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        names = config["premier_teams"] + config["second_division_teams"]
        self.assertEqual(len(names), 40)
        missing = [name for name in names if _badge_path(name) is None]
        self.assertFalse(missing, f"缺少队标图片的球队: {missing}")

    def test_v3_manifest_maps_all_files_and_is_transparent(self) -> None:
        from PySide6.QtGui import QImage

        manifest = json.loads((ROOT / "team_badges_40_v3" / "manifest.json").read_text(encoding="utf-8"))
        assets = manifest["assets"]
        self.assertEqual(len(assets), 40)
        self.assertEqual(len({asset["club_en"] for asset in assets}), 40)
        for asset in assets:
            path = ROOT / asset["asset_path"]
            self.assertTrue(path.is_file(), asset["asset_path"])
            self.assertEqual(Path(_badge_path(asset["club_en"]) or "").name, path.name)
            image = QImage(str(path))
            self.assertFalse(image.isNull(), path.name)
            self.assertEqual((image.width(), image.height()), (1254, 1254), path.name)
            self.assertTrue(image.hasAlphaChannel(), path.name)

    def test_crest_sizes_32_36_42_48_render(self) -> None:
        from tests.support import ensure_qapp
        from football_simulator.ui.team_crest import crest_pixmap

        ensure_qapp()

        for size in (32, 36, 42, 48):
            pixmap = crest_pixmap("Kingsmeadow Rovers", size)
            self.assertFalse(pixmap.isNull(), size)
            self.assertLessEqual(pixmap.width(), size)
            self.assertGreater(pixmap.width(), 0)


if __name__ == "__main__":
    unittest.main()
