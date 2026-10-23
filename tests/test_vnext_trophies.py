"""NH-070: 奖杯加载、缺图回退与缓存；不写正式 trophy 目录。"""
from __future__ import annotations
import hashlib, json, shutil, tempfile, unittest
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from football_simulator import runtime
from football_simulator.ui.trophies import TROPHY_IDS, asset_status, clear_cache, load_manifest, trophy_pixmap
from tests import support

ROOT = Path(__file__).resolve().parent.parent


class TrophyLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = support.ensure_qapp()
        cls.tmp = Path(tempfile.mkdtemp(prefix="fs_vnext_trophy_"))

    @classmethod
    def tearDownClass(cls) -> None:
        clear_cache()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        clear_cache()
        self.dir = Path(tempfile.mkdtemp(dir=self.tmp))
        self.image = self.dir / "premier.png"
        shutil.copy2(ROOT / "assets" / "atmosphere" / "stadium_night.png", self.image)
        self.manifest = self.dir / "manifest.json"

    def _write(self, **overrides) -> None:
        entry = {
            "path": "premier.png",
            "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
            "width": QImage(str(self.image)).width(),
            "height": QImage(str(self.image)).height(),
            "status": "ready",
        }
        entry.update(overrides)
        self.manifest.write_text(json.dumps({"version": 1, "assets": {"premier": entry}}), encoding="utf-8")

    def test_eight_ids_contract_and_missing_formal_manifest(self) -> None:
        self.assertEqual(TROPHY_IDS, ("premier", "second", "aureole", "confluence", "starpath", "ember", "ascendant", "zenith"))
        missing = self.dir / "missing-manifest.json"
        self.assertFalse(asset_status("premier", missing)["ready"])
        self.assertIsNone(trophy_pixmap("premier", path=missing))
        formal = ROOT / "assets" / "trophies" / "v1" / "manifest.json"
        if formal.exists():
            manifest = load_manifest(formal)
            self.assertEqual(set(manifest["assets"]), set(TROPHY_IDS))

    def test_valid_manifest_scaling_dpr_and_cache(self) -> None:
        self._write()
        status = asset_status("premier", self.manifest)
        self.assertTrue(status["ready"], status)
        self.assertFalse(load_manifest(self.manifest)["error"])
        one = trophy_pixmap("premier", 64, 1.0, self.manifest)
        two = trophy_pixmap("premier", 64, 1.0, self.manifest)
        self.assertIsNotNone(one)
        self.assertFalse(one.isNull())
        self.assertEqual(one.cacheKey(), two.cacheKey())
        self.assertLessEqual(one.width(), 64)
        self.assertLessEqual(one.height(), 64)
        hi = trophy_pixmap("premier", 64, 2.0, self.manifest)
        self.assertEqual(hi.devicePixelRatio(), 2.0)
        self.assertLessEqual(hi.width(), 128)

    def test_missing_file_bad_hash_and_bad_json_fallback(self) -> None:
        self._write(path="missing.png")
        self.assertFalse(asset_status("premier", self.manifest)["ready"])
        self._write(sha256="0" * 64)
        status = asset_status("premier", self.manifest)
        self.assertFalse(status["ready"])
        self.assertIn("校验", status["reason"])
        self.manifest.write_text("{not json", encoding="utf-8")
        self.assertFalse(load_manifest(self.manifest)["valid"])
        self.assertIsNone(trophy_pixmap("premier", 64, 1.0, self.manifest))
        self._write(status="planned", sha256=None)
        self.assertFalse(asset_status("premier", self.manifest)["ready"])

    def test_f5_reload_after_manifest_change(self) -> None:
        self._write()
        self.assertIsNotNone(trophy_pixmap("premier", 64, 1.0, self.manifest))
        self._write(status="planned")
        self.assertIsNone(trophy_pixmap("premier", 64, 1.0, self.manifest))
        self._write()
        self.assertIsNotNone(trophy_pixmap("premier", 64, 1.0, self.manifest))


def tearDownModule() -> None:
    support.cleanup_qt_widgets()
