"""Source Qt checks only; no paid API, production save or packaged-app claim."""
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from football_simulator.ui.main_window import MainWindow
from football_simulator.ui.pages.press import article_html
from football_simulator.press.demo import simulated_transport
from football_simulator.press.library import activate
from football_simulator.press.feature_library import FeatureLibrary
from tests.test_press_export import PressSaveCase

APP = QApplication.instance() or QApplication([])


class MemoryCredentials:
    def get(self): return 'offline-ui-test-credential-only'


@unittest.skipUnless(sys.platform == 'darwin', 'macOS source integration')
class PressUITests(PressSaveCase):
    @staticmethod
    def wait_until(predicate, timeout=10000):
        if predicate():
            return True
        loop = QEventLoop()
        poll, limit = QTimer(), QTimer()
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        limit.setSingleShot(True); limit.timeout.connect(loop.quit)
        poll.start(10); limit.start(timeout)
        loop.exec()
        poll.stop(); limit.stop()
        return predicate()

    def test_background_completion_switch_read_refresh_and_existing_article(self):
        library = activate('Press'); self.advance()
        # Reopen a pre-existing development library from before WAL setup.
        from football_simulator.persistence_v2 import store
        connection = store._connect(library.path)
        try:
            self.assertEqual(connection.execute('PRAGMA journal_mode=DELETE').fetchone()[0], 'delete')
        finally:
            connection.close()
        with patch.dict(os.environ, {'FS_PRESS_DEV': '1'}):
            window = MainWindow(self.service)
        window.resize(1280, 800); window.show()
        release = threading.Event(); entered = threading.Event()
        def transport(request, key):
            entered.set()
            release.wait(10)
            return simulated_transport(request, key)
        window.press.credentials = MemoryCredentials()
        window.press.transport = transport
        def cleanup():
            release.set()
            self.wait_until(lambda: not window.press.running)
            window.close()
        self.addCleanup(cleanup)
        window.navigate('press'); QTest.qWait(50)
        self.assertFalse(window.error_label.text(), window.error_label.text())
        with library.connection() as connection:
            self.assertEqual(connection.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        # Page refresh disposes old controls with deleteLater. Target the visible
        # current control, rather than findChild returning a retired instance.
        generate = window.stack.currentWidget().generate_button
        self.assertTrue(generate.isVisible() and generate.isEnabled())
        QTest.mouseClick(generate, Qt.LeftButton)
        # Use the actual Qt event loop for queued QThread startup/signals.
        self.assertTrue(self.wait_until(entered.is_set), str(library.overview()))
        # Main UI remains live and the game can commit another completed week.
        self.service.advance_week('Press')
        window.refresh_all()
        self.service.create_save('Other'); self.service.initialize_save('Other')
        window.open_save('Other'); window.navigate('press')
        release.set()
        self.assertTrue(self.wait_until(lambda: not window.press.running))
        self.assertEqual(window.current_save_name(), 'Other')
        self.assertFalse(window.press.overview()['enabled'])
        window.open_save('Press'); window.navigate('press')
        self.assertEqual(library.overview()['unread'], 1)
        window.navigate('press_article', season=1, week=1); QTest.qWait(30)
        page = window.stack.currentWidget()
        self.assertIn('模拟周报', page.reader.toPlainText())
        QTest.keyClick(window, Qt.Key_F5); QTest.qWait(30)
        self.assertEqual(window._current_route, ('press_article', {'season': 1, 'week': 1}))
        self.assertFalse(window.error_label.text(), window.error_label.text())
        self.assertEqual(library.overview()['unread'], 0)
        # Existing real API return: reader/layout input only, no imported save facts.
        response = json.loads((Path(__file__).parent / 'fixtures_press/second_weekly/response.json').read_text())
        article = response
        page.reader.setHtml(article_html(article, 1, 9, sample=True)); QTest.qWait(30)
        self.assertIn(article['title'], page.reader.toPlainText())
        self.assertIn('历史开发稿示例', page.reader.toPlainText())
        self.assertEqual(page.reader.horizontalScrollBar().maximum(), 0)
        window.close()

    def test_explicit_disable_has_no_press_feature_or_tables(self):
        with patch.dict(os.environ, {'FS_PRESS_DEV': '0'}):
            window = MainWindow(self.service)
        self.addCleanup(window.close)
        self.assertIsNone(window.press)
        self.assertNotIn('press', [window.nav.item(i).data(Qt.UserRole) for i in range(window.nav.count())])
        window.close()

    def test_close_saves_current_request_and_manual_weekly_resume_uses_cache(self):
        library=activate('Press');self.advance()
        entered,release=threading.Event(),threading.Event();calls=[]
        def transport(request,key):
            calls.append(request);entered.set();release.wait(10)
            return simulated_transport(request,key)
        with patch('football_simulator.ui.press_controller.Credentials',MemoryCredentials):
            window=MainWindow(self.service)
        window.press.transport=transport;window.show();window.navigate('press')
        self.addCleanup(lambda:(release.set(),self.wait_until(lambda:not window.press.running),window.close()))
        window.press.generate(1,1)
        self.assertTrue(self.wait_until(entered.is_set))
        window.close()
        self.assertTrue(window.press.close_when_idle)
        release.set()
        self.assertTrue(self.wait_until(lambda:not window.press.running and not window.isVisible()))
        row=library.overview()['weeks'][0]
        self.assertEqual((row['status'],row['sections_done']),('paused',1))
        with patch('football_simulator.ui.press_controller.Credentials',MemoryCredentials):
            reopened=MainWindow(self.service)
        self.addCleanup(reopened.close)
        reopened.press.transport=transport;reopened.show();reopened.navigate('press')
        self.assertEqual(reopened.stack.currentWidget().generate_button.text(),'继续生成')
        QTest.mouseClick(reopened.stack.currentWidget().generate_button,Qt.LeftButton)
        self.assertTrue(self.wait_until(lambda:not reopened.press.running))
        self.assertEqual(library.overview()['weeks'][0]['status'],'ready')
        self.assertEqual(len(calls),4)
        reopened.close()

    def test_frozen_macos_availability_and_missing_key_do_not_dispatch(self):
        self.advance(18);activate('Press',features=True);self.advance()
        from unittest.mock import Mock
        credentials=Mock();credentials.get.return_value=None
        root=Path(__file__).resolve().parents[1]
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(root),create=True), \
             patch('football_simulator.ui.press_controller.Credentials',return_value=credentials):
            window=MainWindow(self.service)
        self.addCleanup(window.close)
        transport=Mock(side_effect=AssertionError('no key must never dispatch'))
        window.press.transport=transport;window.show();window.navigate('press');window.press.pump()
        self.assertTrue(self.wait_until(lambda:window.press.auto_needs_key and not window.press.running))
        transport.assert_not_called()
        self.assertEqual(len(window.press.library().feature_rows()),2)
        self.assertTrue(all(r['status']=='queued' for r in window.press.library().feature_rows()))
        self.assertFalse(window.error_label.text(),window.error_label.text())
        window.close()

    def test_draft_top20_and_transfer_related_links_keep_the_selected_season(self):
        from football_simulator.persistence_v2 import store
        self.service.set_mode('Press','auto');self.advance(48)
        activate('Press',features=True);self.advance(4)
        library=FeatureLibrary(store.database_path('Press'))
        for row in library.feature_rows():
            job=library.reserve_feature(row['report_id'])
            self.assertTrue(library.generate(job,'offline-ui-test',transport=simulated_transport))
        with patch('football_simulator.ui.press_controller.Credentials',MemoryCredentials):
            window=MainWindow(self.service)
        self.addCleanup(window.close);window.resize(1280,800);window.show()
        for route,kind in [('draft','draft'),('annual_top20','top20'),('transfers','transfer')]:
            window.navigate(route,**({'season':1} if route!='transfers' else {}))
            self.assertFalse(window.error_label.text(),window.error_label.text())
            link=window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports')
            self.assertIsNotNone(link,route)
            QTest.mouseClick(link,Qt.LeftButton);QTest.qWait(20)
            page=window.stack.currentWidget()
            self.assertEqual((page.season_filter,page.kind_filter),(1,kind))
            QTest.keyClick(window,Qt.Key_F5);QTest.qWait(20)
            self.assertEqual(window.stack.currentWidget().kind_filter,kind)
            self.assertFalse(window.error_label.text(),window.error_label.text())
            window.stack.currentWidget().kind_filter='weekly'
            window.navigate(route,**({'season':1} if route!='transfers' else {}))
            link=window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports')
            QTest.mouseClick(link,Qt.LeftButton);QTest.qWait(20)
            self.assertEqual(window.stack.currentWidget().kind_filter,kind)
        window.close()

    def test_automatic_features_keep_ui_live_and_route_to_the_saved_article(self):
        from football_simulator.persistence_v2 import store
        self.advance(18); activate('Press', features=True); self.advance()
        library = FeatureLibrary(store.database_path('Press'))
        entered, release, calls = threading.Event(), threading.Event(), []
        def transport(request, key):
            calls.append(request); entered.set(); release.wait(10)
            return simulated_transport(request, key)
        with patch.dict(os.environ, {'FS_PRESS_DEV': '1'}), \
             patch('football_simulator.ui.press_controller.Credentials', MemoryCredentials), \
             patch('football_simulator.ui.press_controller.provider.send_once', transport):
            window = MainWindow(self.service)
        window.resize(1280, 800); window.show()
        def cleanup():
            release.set(); window.press.timer.stop()
            self.wait_until(lambda: not window.press.running)
            window.close()
        self.addCleanup(cleanup)
        window.navigate('press'); window.press.pump()
        self.assertTrue(self.wait_until(entered.is_set))
        self.service.advance_week('Press'); window.refresh_all()
        self.assertEqual(self.world()['season']['week'], 21)
        self.service.create_save('Other'); self.service.initialize_save('Other')
        window.open_save('Other'); window.navigate('players')
        route = window._current_route
        release.set()
        self.assertTrue(self.wait_until(lambda: not window.press.running))
        self.assertEqual(window.current_save_name(), 'Other')
        self.assertEqual(window._current_route, route)
        self.assertEqual(len(calls), 1)
        window.open_save('Press'); window.navigate('press'); window.press.pump()
        self.assertTrue(self.wait_until(lambda: not window.press.running and library.next_queued() is None))
        self.assertEqual(len(calls), 2)
        window.navigate('press'); QTest.qWait(30)
        page = window.stack.currentWidget()
        from PySide6.QtWidgets import QComboBox
        types = page.findChild(QComboBox, 'PressTypeFilter')
        types.setCurrentIndex(types.findData('phase')); QTest.qWait(20)
        page = window.stack.currentWidget()
        button = next(b for b in page.findChildren(QPushButton) if b.isVisible() and b.property('report_id'))
        report_id = button.property('report_id')
        QTest.mouseClick(button, Qt.LeftButton); QTest.qWait(30)
        self.assertEqual(window._current_route, ('press_article', {'report_id': report_id}))
        self.assertIn('模拟专题', window.stack.currentWidget().reader.toPlainText())
        QTest.keyClick(window, Qt.Key_F5); QTest.qWait(30)
        self.assertEqual(window._current_route, ('press_article', {'report_id': report_id}))
        self.assertEqual(library.unread_count(), 1)
        self.assertEqual(len(calls), 2)
        self.assertFalse(window.error_label.text(), window.error_label.text())
        window.navigate('competition',competition_id='premier')
        report_link=window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports')
        self.assertIsNotNone(report_link)
        QTest.mouseClick(report_link,Qt.LeftButton);QTest.qWait(20)
        page=window.stack.currentWidget()
        self.assertEqual((page.season_filter,page.competition_filter),(1,'premier'))
        QTest.keyClick(window,Qt.Key_F5);QTest.qWait(20)
        self.assertEqual(window.stack.currentWidget().competition_filter,'premier')
        self.assertFalse(window.error_label.text(),window.error_label.text())
