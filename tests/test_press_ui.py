"""Current V0.3.0 source Qt checks. Native Cocoa acceptance is separate."""
import os
import unittest
import sys
import threading
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QEventLoop,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from football_simulator.ui.main_window import MainWindow
from football_simulator.press.local_library import activate,LocalLibrary
from football_simulator.press import repository
from tests.test_press_export import PressSaveCase
from tests.test_press_local_templates import offline_guards

APP=QApplication.instance() or QApplication([])


@unittest.skipUnless(sys.platform=='darwin','macOS source integration, not Linux Cocoa proof')
class PressUITests(PressSaveCase):
    @staticmethod
    def wait_until(predicate,timeout=10000):
        if predicate():return True
        loop=QEventLoop();poll=QTimer();limit=QTimer()
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        limit.setSingleShot(True);limit.timeout.connect(loop.quit)
        poll.start(10);limit.start(timeout);loop.exec();poll.stop();limit.stop()
        return predicate()

    def window(self):
        with patch.dict(os.environ,{'FS_PRESS_DEV':'1'}),patch('football_simulator.ui.macos_presentation.enabled',return_value=True):
            result=MainWindow(self.service)
        result.resize(1280,800);result.show();result.navigate('press')
        self.addCleanup(lambda:(result.press.prepare_close(),self.wait_until(lambda:not result.press.running),result.close()))
        return result

    def test_manual_weekly_background_save_switch_read_f5_reopen_and_no_keys(self):
        lib=activate('Press');self.advance()
        entered=threading.Event();release=threading.Event()
        from football_simulator.press.local_templates.engine import render as real_render
        def slow(packet,plan):
            entered.set();release.wait(10);return real_render(packet,plan)
        with offline_guards(),patch('football_simulator.press.local_templates.engine.render',side_effect=slow):
            window=self.window();window.press.timer.stop()
            QTest.mouseClick(window.stack.currentWidget().generate_button,Qt.LeftButton)
            self.assertTrue(self.wait_until(entered.is_set))
            try:
                self.advance() # Renderer does not own a database transaction.
                self.service.create_save('Other');self.service.initialize_save('Other')
                window.open_save('Other');window.navigate('press')
            finally:release.set()
            self.assertTrue(self.wait_until(lambda:not window.press.running))
            self.assertEqual(window.current_save_name(),'Other')
            self.assertFalse(window.press.overview()['enabled'])
            window.open_save('Press');window.press.timer.stop();window.navigate('press_article',season=1,week=1)
            page=window.stack.currentWidget();before=page.reader.toPlainText()
            self.assertIn('本地模板',before);QTest.keyClick(window,Qt.Key_F5);QTest.qWait(30)
            self.assertEqual(window.stack.currentWidget().reader.toPlainText(),before)
            self.assertFalse(window.error_label.text(),window.error_label.text())
            self.assertEqual(page.reader.horizontalScrollBar().maximum(),0)
            window.close()
            reopened=self.window();reopened.press.timer.stop();reopened.navigate('press_article',season=1,week=1)
            self.assertEqual(reopened.stack.currentWidget().reader.toPlainText(),before)

    def test_automatic_weekly_uses_same_background_scheduler(self):
        lib=activate('Press');window=self.window()
        with offline_guards():
            self.advance();window.press.pump()
            self.assertTrue(self.wait_until(lambda:lib.overview()['weeks'][0]['status']=='ready'))
            self.assertTrue(self.wait_until(lambda:not window.press.running))
            article=lib.read(1,1,mark_read=False)
            window.press.pump();QTest.qWait(20)
            self.assertEqual(lib.read(1,1,mark_read=False),article)

    def test_explicit_disable_has_no_press_controller(self):
        with patch.dict(os.environ,{'FS_PRESS_DEV':'0'}):window=MainWindow(self.service)
        self.addCleanup(window.close);self.assertIsNone(window.press)

    def test_close_paused_local_job_resumes_frozen_version(self):
        lib=activate('Press');self.advance();weekly=LocalLibrary(lib.path)
        job=weekly.reserve(1,1);self.assertFalse(weekly.generate_local(job,should_stop=lambda:True))
        window=self.window()
        with offline_guards():
            window.press.pump()
            self.assertTrue(self.wait_until(lambda:lib.overview()['weeks'][0]['status']=='ready'))
            self.assertTrue(self.wait_until(lambda:not window.press.running))
            self.assertEqual(lib.source(season=1,week=1)['template_version'],'local-news/1')

    def test_related_links_preserve_selected_report_type_season_and_f5(self):
        from PySide6.QtWidgets import QPushButton
        self.service.set_mode('Press','auto');self.advance(48)
        lib=activate('Press');self.advance(4)
        with offline_guards():
            for row in lib.feature_rows():self.assertTrue(lib.generate_local(lib.reserve_feature(row['report_id'])))
            window=self.window();window.press.timer.stop()
            for route,kind in [('draft','draft'),('annual_top20','top20'),('transfers','transfer')]:
                window.navigate(route,**({'season':1} if route!='transfers' else {}))
                self.assertFalse(window.error_label.text(),window.error_label.text())
                link=window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports')
                self.assertIsNotNone(link,route);QTest.mouseClick(link,Qt.LeftButton);QTest.qWait(20)
                page=window.stack.currentWidget();self.assertEqual((page.season_filter,page.kind_filter),(1,kind))
                QTest.keyClick(window,Qt.Key_F5);QTest.qWait(20)
                self.assertEqual(window.stack.currentWidget().kind_filter,kind)
                page=window.stack.currentWidget();page.kind_filter='weekly'
                window.navigate(route,**({'season':1} if route!='transfers' else {}))
                QTest.mouseClick(window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports'),Qt.LeftButton);QTest.qWait(20)
                self.assertEqual(window.stack.currentWidget().kind_filter,kind)

    def test_automatic_features_read_filter_competition_link_and_f5(self):
        from PySide6.QtWidgets import QComboBox,QPushButton
        self.advance(18);lib=activate('Press');self.advance()
        with offline_guards():
            window=self.window();window.press.pump()
            self.assertTrue(self.wait_until(lambda:not window.press.running and lib.next_local() is None))
            self.assertEqual(len(lib.feature_rows()),2)
            window.navigate('press');types=window.stack.currentWidget().findChild(QComboBox,'PressTypeFilter')
            types.setCurrentIndex(types.findData('phase'));QTest.qWait(20)
            button=next(b for b in window.stack.currentWidget().findChildren(QPushButton) if b.isVisible() and b.property('report_id'))
            rid=button.property('report_id');QTest.mouseClick(button,Qt.LeftButton);QTest.qWait(20)
            self.assertEqual(window._current_route,('press_article',{'report_id':rid}))
            text=window.stack.currentWidget().reader.toPlainText();self.assertIn('本地模板',text)
            QTest.keyClick(window,Qt.Key_F5);QTest.qWait(20)
            self.assertEqual(window.stack.currentWidget().reader.toPlainText(),text)
            window.navigate('competition',competition_id='premier')
            link=window.stack.currentWidget().findChild(QPushButton,'PressRelatedReports');self.assertIsNotNone(link)
            QTest.mouseClick(link,Qt.LeftButton);QTest.qWait(20)
            self.assertEqual(window.stack.currentWidget().competition_filter,'premier')
            QTest.keyClick(window,Qt.Key_F5);QTest.qWait(20)
            self.assertEqual(window.stack.currentWidget().competition_filter,'premier')
            self.assertFalse(window.error_label.text(),window.error_label.text())
