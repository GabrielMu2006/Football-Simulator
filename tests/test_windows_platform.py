"""Windows 移植相关的平台行为回归。

覆盖本轮修复的四类问题（在 macOS 上也必须成立，因此跨平台运行）：
- 字体族：Windows 用 Segoe UI + Microsoft YaHei UI，macOS 保持原有顺序；
- 初始窗口尺寸：按屏幕可用区域收敛，不把固定最小高度顶到屏幕外；
- SQLite 只读 URI：盘符/中文/空格/``#``/``%`` 路径都能读；
- 低高度下侧栏整体可滚动，导航仍然可达。
"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from tests import support

support.ensure_qapp()


class FontTokenTests(unittest.TestCase):
    def test_windows_font_stack(self) -> None:
        from football_simulator.ui import tokens

        self.assertEqual(tokens.FONT_FAMILIES_WINDOWS[0], "Segoe UI Variable Text")
        self.assertIn("Segoe UI", tokens.FONT_FAMILIES_WINDOWS)
        self.assertIn("Microsoft YaHei UI", tokens.FONT_FAMILIES_WINDOWS)
        self.assertEqual(tokens.FONT_NUMERIC_WINDOWS[0], "Consolas")

    def test_macos_font_order_unchanged(self) -> None:
        from football_simulator.ui import tokens

        self.assertEqual(tokens.FONT_FAMILIES_MACOS[:2], ("Avenir Next", "PingFang SC"))
        self.assertIn("Helvetica Neue", tokens.FONT_FAMILIES_MACOS)
        self.assertEqual(tokens.FONT_NUMERIC_MACOS[0], "SF Mono")

    def test_active_stack_matches_platform(self) -> None:
        from football_simulator.ui import tokens

        expected = tokens.FONT_FAMILIES_WINDOWS if tokens.IS_WINDOWS else tokens.FONT_FAMILIES_MACOS
        self.assertEqual(tokens.FONT_FAMILIES, expected)
        for family in expected:
            self.assertIn(f'"{family}"', tokens.FONT_FAMILY)
        self.assertTrue(tokens.FONT_FAMILY.endswith("sans-serif"))
        self.assertTrue(tokens.FONT_NUMERIC.endswith("monospace"))

    def test_stylesheet_uses_token_stack(self) -> None:
        from football_simulator.ui import tokens
        from football_simulator.ui.theme import APP_STYLE

        self.assertIn(tokens.FONT_FAMILY, APP_STYLE)
        self.assertIn(tokens.FONT_NUMERIC, APP_STYLE)


class InitialGeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        from football_simulator.ui.main_window import initial_window_geometry

        self.compute = initial_window_geometry

    def test_design_size_kept_on_normal_screen(self) -> None:
        self.assertEqual(self.compute(1920, 1040), (1200, 760, 1440, 900))

    def test_shrinks_to_available_area(self) -> None:
        minimum_w, minimum_h, width, height = self.compute(1280, 720)
        self.assertEqual((minimum_w, minimum_h), (1200, 704))
        self.assertEqual((width, height), (1264, 704))

    def test_1080p_at_125_and_150_percent(self) -> None:
        # 1920x1080 在 125% 缩放下 Qt 报告逻辑尺寸 1536x864（任务栏另占一部分）。
        for width, height in ((1536, 816), (1280, 672)):
            minimum_w, minimum_h, target_w, target_h = self.compute(width, height)
            self.assertLessEqual(minimum_h, height)
            self.assertLessEqual(target_h, height)
            self.assertLessEqual(minimum_w, width)
            self.assertLessEqual(target_w, width)

    def test_low_available_height_hits_floor(self) -> None:
        self.assertEqual(self.compute(960, 500), (944, 484, 944, 484))

    def test_floor_is_enforced_for_tiny_screens(self) -> None:
        self.assertEqual(self.compute(640, 360), (900, 480, 900, 480))

    def test_missing_screen_falls_back_to_design(self) -> None:
        self.assertEqual(self.compute(0, 0), (1200, 760, 1440, 900))

    def test_window_uses_screen_available_geometry(self) -> None:
        from PySide6.QtWidgets import QApplication

        from football_simulator.app.service import GameService
        from football_simulator.ui.main_window import MainWindow, initial_window_geometry

        tmp = Path(tempfile.mkdtemp(prefix="fs_geom_"))
        support.install_test_data_root(str(tmp))
        self.addCleanup(self._cleanup, tmp)
        try:
            window = MainWindow(GameService(rng_factory=lambda: random.Random(11)))
            try:
                screen = QApplication.primaryScreen()
                available = screen.availableGeometry()
                expected = initial_window_geometry(available.width(), available.height())
                self.assertEqual(window.minimumWidth(), expected[0])
                self.assertEqual(window.minimumHeight(), expected[1])
            finally:
                window.close()
                window.deleteLater()
        finally:
            support.reset_test_data_root()

    @staticmethod
    def _cleanup(path: Path) -> None:
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(path)
        shutil.rmtree(path, ignore_errors=True)


class ReadOnlyUriTests(unittest.TestCase):
    """Windows 路径（盘符、中文、空格、#、%）上的只读连接。

    新 UI 走向 persistence_v2.store（meta/world 规范化表），因此这里用
    GameService 建真实新档，而不是 tests.support 的 v1 状态层夹具。
    """

    def setUp(self) -> None:
        self._base = Path(tempfile.mkdtemp(prefix="fs_uri_"))
        self.addCleanup(self._cleanup_base)
        special = self._base / "中文 目录 #7 100%"
        special.mkdir()
        self._special = special
        support.install_test_data_root(str(special))

    def _cleanup_base(self) -> None:
        support.reset_test_data_root()
        for child in list(self._base.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        support.assert_cleanup_path_is_temporary(self._base)
        shutil.rmtree(self._base, ignore_errors=True)

    def _service(self):
        from football_simulator.app.service import GameService

        return GameService(rng_factory=lambda: random.Random(5))

    def test_uri_escapes_special_characters(self) -> None:
        from football_simulator.persistence_v2 import store

        sample = Path(tempfile.gettempdir()) / "中文 目录 #7 100%" / "save.sqlite3"
        uri = store._readonly_uri(sample)
        self.assertTrue(uri.startswith("file:///"))
        self.assertIn("%23", uri)
        self.assertIn("%25", uri)
        self.assertIn("%20", uri)
        self.assertTrue(uri.endswith("?mode=ro"))
        self.assertNotIn("#", uri.split("?")[0])

    def test_database_under_special_path_is_readable(self) -> None:
        from football_simulator.persistence_v2 import store

        service = self._service()
        service.create_save("路径 测试")
        service.initialize_save("路径 测试")
        meta = store.read_meta("路径 测试")
        self.assertTrue(meta, "只读连接应能读到 meta")
        writable = store.load_world("路径 测试")
        readonly = store.load_world("路径 测试", readonly=True)
        self.assertEqual(writable["world_id"], readonly["world_id"])
        snapshot = service.snapshot("路径 测试")
        self.assertEqual(snapshot["season"], 1)
        self.assertEqual(snapshot["week"], 1)
        self.assertEqual(len(service.load_world("路径 测试")["teams"]), 40)

    def test_export_import_roundtrip_under_special_path(self) -> None:
        from football_simulator.persistence_v2 import store

        service = self._service()
        service.create_save("往返 测试")
        service.initialize_save("往返 测试")
        package = self._base / "导出 包 #1 100%.fsarchive"
        store.export_package("往返 测试", package)
        self.assertTrue(package.is_file())
        store.import_package(package, "导入 备份")
        from football_simulator import runtime

        self.assertTrue(runtime.save_exists("导入 备份"))
        original = store.load_world("往返 测试")
        imported = store.load_world("导入 备份")
        self.assertEqual(original["world_id"], imported["world_id"])
        self.assertEqual(original["season_number"], imported["season_number"])

    def test_import_rejects_duplicate_name(self) -> None:
        from football_simulator.persistence_v2 import store

        service = self._service()
        service.create_save("重名 测试")
        service.initialize_save("重名 测试")
        package = self._base / "dup.fsarchive"
        store.export_package("重名 测试", package)
        with self.assertRaises(FileExistsError):
            store.import_package(package, "重名 测试")

    def test_import_rejects_corrupt_package(self) -> None:
        from football_simulator.persistence_v2 import store

        bad = self._base / "bad.fsarchive"
        bad.write_bytes(b"not a zip")
        with self.assertRaises(store.SaveFormatError):
            store.import_package(bad, "坏包 测试")

    def test_deleted_handle_releases_database(self) -> None:
        """读取后必须关闭连接：Windows 上句柄不释放会导致目录删不掉。"""
        from football_simulator.persistence_v2 import store

        service = self._service()
        service.create_save("句柄 测试")
        service.initialize_save("句柄 测试")
        for _ in range(3):
            store.load_world("句柄 测试", readonly=True)
            store.read_meta("句柄 测试")
        path = store.save_dir("句柄 测试")
        shutil.rmtree(path)
        self.assertFalse(path.exists())



class PageOverflowTests(unittest.TestCase):
    """正常窗口尺寸下页面主体不得出现水平溢出。

    Windows 的 Segoe UI 字宽比 macOS 字体宽，曾经在 1440×900 的 dashboard
    和 1280×800 的 honors 上把页面最小宽度顶出视口（内容被裁）。这里直接测
    页面主滚动面（``PageScroll``），表格自身的横向滚动不计入。
    """

    ROUTES = (
        ("dashboard", {}),
        ("competitions", {}),
        ("history", {}),
        ("records", {}),
        ("honors", {}),
        ("transfers", {}),
        ("draft", {}),
        ("saves", {}),
        ("season", {}),
        ("weekly", {"week": 1}),
    )

    @classmethod
    def setUpClass(cls) -> None:
        from football_simulator.app.service import GameService
        from football_simulator.ui.main_window import MainWindow

        cls._tmp = Path(tempfile.mkdtemp(prefix="fs_overflow_"))
        cls._root = support.install_test_data_root(str(cls._tmp))
        cls.service = GameService(rng_factory=lambda: random.Random(9))
        cls.service.create_save("overflow")
        cls.service.initialize_save("overflow")
        cls.service.set_current_save("overflow")
        cls.window = MainWindow(cls.service)
        cls.window.show()
        support.ensure_qapp().processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        shutil.rmtree(cls._tmp, ignore_errors=True)

    @staticmethod
    def _layout_report(scroll) -> str:
        """列出最宽的布局分支，便于定位溢出源（失败时写进断言消息）。"""
        from PySide6.QtWidgets import QWidget

        body = scroll.widget()
        items = []

        def walk(layout, depth, path):
            if depth > 4:
                return
            for index in range(layout.count()):
                item = layout.itemAt(index)
                widget = item.widget()
                child = item.layout()
                if widget is not None:
                    try:
                        minimum = widget.minimumSizeHint().width()
                    except RuntimeError:
                        continue
                    label = f"{path}.{index}:{type(widget).__name__}#{widget.objectName()}"
                    if minimum > 300:
                        items.append((minimum, label, widget.width()))
                    inner = widget.layout()
                    if inner is not None:
                        walk(inner, depth + 1, label)
                elif child is not None:
                    label = f"{path}.{index}:{type(child).__name__}"
                    if child.minimumSize().width() > 300:
                        items.append((child.minimumSize().width(), label, None))
                    walk(child, depth + 1, label)

        walk(body.layout(), 0, "body")
        items.sort(reverse=True)
        return "; ".join(f"{minimum}@{label}(w={width})" for minimum, label, width in items[:5])

    def test_no_page_level_horizontal_overflow(self) -> None:
        from PySide6.QtWidgets import QAbstractScrollArea
        from PySide6.QtTest import QTest

        # 用固定尺寸而不是 resize：无交互桌面/小分辨率屏幕上窗口管理器会把
        # resize 夹到屏幕尺寸，断言就不再是"这个窗口尺寸下的布局"。
        self.addCleanup(self.window.setMinimumSize, 1200, 760)
        for width, height in ((1280, 800), (1440, 900), (1680, 980), (1280, 800)):
            self.window.setFixedSize(width, height)
            QTest.qWait(40)
            for route, params in self.ROUTES:
                with self.subTest(size=(width, height), route=route):
                    self.window.navigate(route, **params)
                    QTest.qWait(40)
                    page = self.window.stack.currentWidget()
                    scroll = getattr(page, "scroll", None)
                    if not isinstance(scroll, QAbstractScrollArea):
                        continue
                    self.assertEqual(
                        scroll.horizontalScrollBar().maximum(),
                        0,
                        "{}@{}x{} 出现页面级水平溢出（body={} viewport={} min={}）\n宽布局分支: {}".format(
                            route, width, height,
                            scroll.widget().width(),
                            scroll.viewport().width(),
                            scroll.widget().minimumSizeHint().width(),
                            self._layout_report(scroll),
                        ),
                    )
                    self.assertEqual(self.window.error_label.text(), "", f"{route}: {self.window.error_label.text()}")

    def test_dashboard_story_cards_keep_readable_minimum(self) -> None:
        """卡片最小宽度是唯一被放宽的约束，不能缩到不可读。"""
        from PySide6.QtWidgets import QLabel

        self.window.resize(1440, 900)
        self.window.navigate("dashboard")
        support.ensure_qapp().processEvents()
        page = self.window.stack.currentWidget()
        minimums = [label.minimumWidth() for label in page.findChildren(QLabel) if label.wordWrap()]
        self.assertTrue(minimums)
        self.assertLessEqual(max(minimums), 180)
        self.assertGreaterEqual(min(minimums), 0)


class SmokeHelperTests(unittest.TestCase):
    """ui_v2_main 的冒烟辅助函数必须忽略 QWidget.scroll 内建方法。"""

    def test_page_scroll_area_detects_real_and_missing_scroll(self) -> None:
        import ui_v2_main
        from PySide6.QtWidgets import QWidget

        from football_simulator.app.service import GameService
        from football_simulator.ui.pages.core import MatchesPage
        from football_simulator.ui.pages.entities import TeamsPage
        from football_simulator.ui.pages.honors import HonorsHubPage
        from football_simulator.ui.main_window import MainWindow

        tmp = Path(tempfile.mkdtemp(prefix="fs_smoke_helper_"))
        support.install_test_data_root(str(tmp))
        self.addCleanup(support.reset_test_data_root)
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        service = GameService(rng_factory=lambda: random.Random(21))
        service.create_save("smoke")
        service.initialize_save("smoke")
        window = MainWindow(service)
        try:
            window.navigate("honors")
            support.ensure_qapp().processEvents()
            honors = window.stack.currentWidget()
            self.assertIsInstance(honors, HonorsHubPage)
            self.assertIsNotNone(ui_v2_main._page_scroll_area(honors))

            window.navigate("matches")
            support.ensure_qapp().processEvents()
            matches = window.stack.currentWidget()
            self.assertIsInstance(matches, MatchesPage)
            # QWidget.scroll 是内建方法，不能被当成滚动面。
            self.assertTrue(callable(matches.scroll))
            self.assertIsNone(ui_v2_main._page_scroll_area(matches))
        finally:
            window.close()
            window.deleteLater()
            support.cleanup_qt_widgets()


class SidebarScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from football_simulator.app.service import GameService
        from football_simulator.ui.main_window import MainWindow

        cls._tmp = Path(tempfile.mkdtemp(prefix="fs_sidebar_"))
        cls._root = support.install_test_data_root(str(cls._tmp))
        cls.service = GameService(rng_factory=lambda: random.Random(3))
        cls.service.create_save("sidebar")
        cls.service.initialize_save("sidebar")
        cls.window = MainWindow(cls.service)
        cls.window.show()
        support.ensure_qapp().processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.deleteLater()
        support.cleanup_qt_widgets()
        support.reset_test_data_root()
        support.assert_cleanup_path_is_temporary(cls._tmp)
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_no_sidebar_scroll_on_normal_sizes(self) -> None:
        from PySide6.QtTest import QTest

        for width, height in ((1280, 800), (1440, 900), (1680, 980)):
            self.window.resize(width, height)
            QTest.qWait(60)
            bar = self.window.sidebar_scroll.verticalScrollBar()
            self.assertEqual(bar.maximum(), 0, f"{width}x{height}: 侧栏不该需要滚动")
            for nav in (self.window.nav, self.window.tools):
                self.assertEqual(nav.verticalScrollBar().maximum(), 0)
                for index in range(nav.count()):
                    rect = nav.visualItemRect(nav.item(index))
                    self.assertLessEqual(rect.bottom(), nav.viewport().height())

    def test_low_height_keeps_navigation_reachable(self) -> None:
        from PySide6.QtTest import QTest

        self.window.setMinimumSize(900, 480)
        self.window.resize(1000, 520)
        QTest.qWait(80)
        bar = self.window.sidebar_scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0, "低高度下侧栏应可滚动")
        bar.setValue(bar.maximum())
        QTest.qWait(40)
        self.assertTrue(self.window.status_line.isVisible())
        self.assertTrue(self.window.mode_box.isVisible())
        # 回到正常尺寸后滚动条消失，布局未损坏。
        self.window.resize(1440, 900)
        QTest.qWait(60)
        self.assertEqual(self.window.sidebar_scroll.verticalScrollBar().maximum(), 0)


if __name__ == "__main__":
    unittest.main()
