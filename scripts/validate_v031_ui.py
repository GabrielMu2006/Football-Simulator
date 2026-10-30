"""Exercise V0.3.1 source UI against an explicitly chosen isolated test save."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTabBar
from football_simulator import runtime
from football_simulator.app.service import GameService
from football_simulator.game import rules
from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.components import EntityLink
from football_simulator.ui.theme import apply_theme


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Supply the isolated data root")
    root = Path(sys.argv[1]).resolve()
    if not (root / "v031-acceptance.json").is_file():
        raise SystemExit("Not a V0.3.1 acceptance directory")
    runtime.set_data_root_override(root)
    runtime.set_config_path_override(root / runtime.SHARED_CONFIG_FILE_NAME)
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    window = MainWindow(GameService(honor_profile=rules.HONOR_PROFILE, edition_archives=True))
    window.resize(1440, 900)
    window.show()
    QTest.qWait(100)
    assert window.current_save_name() == "V031Acceptance"
    window.navigate("competition", competition_id=rules.PREMIER)
    page = window.stack.currentWidget()
    tabs = page.findChild(QTabBar, "DetailSections")
    index = next(i for i in range(tabs.count()) if tabs.tabData(i) == "mvp_history")
    QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
    QTest.qWait(100)
    assert window.stack.currentWidget().detail_state("premier:3")["section"] == "mvp_history"
    page = window.stack.currentWidget()
    button = next(b for b in page.findChildren(QPushButton) if b.text() == "查看历届 EVP")
    QTest.mouseClick(button, Qt.LeftButton)
    QTest.qWait(100)
    assert window._current_route[0] == "competition_evp"
    page = window.stack.currentWidget()
    selector = page.findChild(QComboBox, "EVPSeason")
    assert selector.currentData() == 3
    selector.setFocus()
    QTest.keyClick(selector, Qt.Key_Down)
    QTest.qWait(100)
    assert window._current_route[1]["season"] == 2
    page = window.stack.currentWidget()
    links = page.findChildren(EntityLink)
    assert links
    QTest.mouseClick(links[0], Qt.LeftButton)
    QTest.qWait(100)
    assert window._current_route[0] == "player_honors"
    assert not window.error_label.text(), window.error_label.text()
    window.navigate("history")
    QTest.qWait(100)
    for key in ("mvp_counts", "evp_counts"):
        page = window.stack.currentWidget()
        tabs = page.findChild(QTabBar, "DetailSections")
        index = next(i for i in range(tabs.count()) if tabs.tabData(i) == key)
        QTest.mouseClick(tabs, Qt.LeftButton, pos=tabs.tabRect(index).center())
        QTest.qWait(100)
        assert window.stack.currentWidget().history_section["section"] == key
        assert window.stack.currentWidget().findChildren(EntityLink)
        window.grab().save(str(root / f"history-{key}.png"))
    assert not window.error_label.text(), window.error_label.text()
    page = window.stack.currentWidget()
    receipt = {"type": "source QTest", "pid": os.getpid(), "executable": sys.executable,
               "entry": str(Path(__file__).resolve()), "root": str(ROOT), "data_root": str(root),
               "save": window.current_save_name(), "main_window_module": type(window).__module__,
               "main_window_file": sys.modules[type(window).__module__].__file__,
               "page_module": type(page).__module__, "page_file": sys.modules[type(page).__module__].__file__,
               "checks": ["MVP tab click", "EVP page button", "EVP season keyboard selection", "EVP player link", "history award boards"],
               "error": window.error_label.text()}
    (root / "source-qtest.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))
    window.close()


if __name__ == "__main__":
    main()
