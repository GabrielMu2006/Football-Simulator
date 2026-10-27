"""Linux container checks. macOS-style rendering is explicitly not Cocoa proof."""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def isolation():
    assert sys.platform == "linux"
    assert os.getuid() != 0
    assert os.environ["FS_DATA_ROOT"] == "/run/fs-data"
    assert not any((ROOT / name).exists() for name in ("saves", "release", ".git", "current_save.txt"))
    assert not Path("/var/run/docker.sock").exists()
    try:
        (ROOT / "should-never-write").write_text("failure")
    except OSError:
        pass
    else:
        raise AssertionError("source mount is writable")
    limits = {name: (Path("/sys/fs/cgroup") / name).read_text().strip() for name in ("cpu.max", "memory.max", "pids.max")}
    result = {"actual_platform": sys.platform, "uid": os.getuid(), "source": str(ROOT), "data_root": os.environ["FS_DATA_ROOT"],
              "limits": limits, "source_read_only": True, "user_data_and_release_not_mounted": True}
    Path("/results/isolation.json").write_text(json.dumps(result, indent=2))


def core():
    names = ["tests.test_v2_rules", "tests.test_v2_engine", "tests.test_v2_persistence", "tests.test_v2_service",
             "tests.test_vnext_calendar", "tests.test_vnext_records_honors",
             "tests.test_swiss_transfer_rules.TransferRulesTests", "tests.test_swiss_transfer_rules.TransferPersistenceTests",
             "tests.test_press_local_templates", "tests.test_team_honor_totals.HonorTotalDataTests", "tests.test_champions_board.ChampionDataTests"]
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    Path("/results/tests.json").write_text(json.dumps({"suite": "portable-core", "targets": names, "tests": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors), "skips": len(result.skipped), "passed": result.wasSuccessful()}, indent=2))
    return 0 if result.wasSuccessful() else 1


def press():
    names=['tests.test_press_local_templates','tests.test_press_planning','tests.test_press_features',
           'tests.test_press_library','tests.test_press_automatic','tests.test_press_export','tests.test_press_ui']
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromNames(names))
    Path('/results/tests.json').write_text(json.dumps({'suite':'press','targets':names,'tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'passed':result.wasSuccessful()},indent=2))
    return 0 if result.wasSuccessful() else 1


def ui():
    from unittest.mock import patch
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from football_simulator import runtime
    from football_simulator.app.service import GameService
    from football_simulator.persistence_v2 import store
    from football_simulator.ui.main_window import MainWindow
    from football_simulator.ui.theme import apply_theme
    runtime.set_data_root_override(Path("/run/fs-data"))
    store.import_package(ROOT / "tests/fixtures_vnext/s3-finalized.fsarchive", "Container")
    service = GameService(honor_profile="champion-first-2026-10-23", edition_archives=True)
    service.prepare_save("Container")
    app = QApplication([])
    apply_theme(app)
    with patch("football_simulator.ui.macos_presentation.enabled", return_value=True):
        window = MainWindow(service)
        window.resize(1440, 900)
        window.show()
        window.open_save("Container")
        cases = []
        for route, params, section in [("dashboard", {}, None), ("honors", {}, None),
            ("team", {"team_id": "team-glorious-vision"}, "overview"), ("team", {"team_id": "team-glorious-vision"}, "honors"),
            ("team", {"team_id": "team-glorious-vision"}, "history"),
            ("competition", {"competition_id": "premier"}, "champions"),
            ("competition", {"competition_id": "starpath"}, None), ("competition", {"competition_id": "ember"}, None)]:
            window.navigate(route, **params)
            page = window.stack.currentWidget()
            if section:
                key = params.get("team_id") or f"{params['competition_id']}:{window.world()['season_number']}"
                page.set_detail_section(key, section)
            QTest.qWait(80)
            assert not window.error_label.text(), window.error_label.text()
            assert page.scroll.horizontalScrollBar().maximum() == 0
            QTest.keyClick(window, Qt.Key_F5)
            QTest.qWait(50)
            assert not window.error_label.text(), window.error_label.text()
            window.grab().save(f"/results/ui-{len(cases)}.png")
            cases.append({"route": route, "params": params, "section": section, "passed": True})
        window.close()
    Path("/results/ui.json").write_text(json.dumps({"actual_platform": sys.platform, "qt_platform": app.platformName(),
        "macos_style_emulation": True, "native_macos_acceptance": False, "cases": cases, "passed": True}, indent=2))
    return 0


if __name__ == "__main__":
    isolation()
    raise SystemExit(ui() if sys.argv[1] == "ui" else press() if sys.argv[1] == "press" else core())
