"""Opt-in, per-save background reporting for macOS."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, QTimer
import uuid
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout,
)

from football_simulator.persistence_v2 import store
from football_simulator.press import PressError, provider, repository
from football_simulator.press.credentials import Credentials
from football_simulator.press.library import Library, activate
from football_simulator.press.feature_library import FeatureLibrary


class ReportWorker(QThread):
    progress = Signal()

    def __init__(self, library, job, key, transport, parent):
        super().__init__(parent)
        self.library, self.job, self._key, self.transport = library, job, key, transport

    def run(self):
        try:
            self.library.generate(self.job, self._key, transport=self.transport, progress=self.progress.emit,
                                  should_stop=self.isInterruptionRequested)
        finally:
            self._key = None


class FeatureWorker(QThread):
    progress=Signal()
    needs_key=Signal()
    paused=Signal()

    def __init__(self,library,report_id,credentials,transport,parent,*,retry=False):
        super().__init__(parent)
        self.library,self.report_id,self.credentials,self.transport=library,report_id,credentials,transport
        self.retry=retry;self.job=None

    def run(self):
        key=None
        try:
            key=self.credentials.get()
            if not key:self.needs_key.emit();return
            self.job=self.library.reserve_feature(self.report_id,retry=self.retry)
            if self.job:
                self.progress.emit()
                ok=self.library.generate(self.job,key,transport=self.transport,progress=self.progress.emit,
                                         should_stop=self.isInterruptionRequested)
                if not ok:
                    with self.library.connection() as conn:
                        row=conn.execute('SELECT error FROM press_features WHERE report_id=?',(self.report_id,)).fetchone()
                    if row and row[0] in ('密钥未通过认证，请重新设置。','API 余额不足。','请求过于频繁，请稍后重试。'):self.paused.emit()
        except Exception:
            # No exception/provider bodies or credentials enter the interface.
            try:
                with self.library.connection(write=True) as conn:
                    conn.execute("UPDATE press_features SET status='failed',error='专题任务未启动，请检查材料后重试。' WHERE report_id=? AND status='queued'",(self.report_id,))
            except Exception:
                pass
            self.paused.emit()
        finally:key=None


class PressController(QObject):
    changed = Signal()

    def __init__(self, window, *, credentials=None, transport=None):
        super().__init__(window)
        self.window = window
        self.credentials = credentials if credentials is not None else Credentials()
        self.transport = transport or provider.send_once
        self.workers = {}
        self._recovered = set()
        self.close_when_idle = False
        self.simulation = False
        self.auto_needs_key=False
        self.auto_paused=False
        self.changed.connect(self._changed)
        self.timer=QTimer(self);self.timer.setInterval(1000)
        self.timer.timeout.connect(self.pump);self.timer.start()

    @property
    def running(self):
        return bool(self.workers)

    def prepare_close(self):
        self.close_when_idle = True
        self.timer.stop()
        for worker in self.workers.values():worker.requestInterruption()

    def related_button(self, *, season, kind=None, competition=None):
        """Link to frozen reports without enabling or backfilling a save."""
        try:
            rows=[r for r in self.library().feature_rows() if r['season']==season
                  and (kind is None or r['kind']==kind)
                  and (competition is None or r['competition']==competition)]
        except (PressError,OSError):return None
        if not rows:return None
        result=QPushButton('本赛事报道' if competition else {'draft':'本届选秀报道','top20':'本届 Top20 专题','transfer':'本季转会报道'}.get(kind,'相关报道'))
        result.setObjectName('PressRelatedReports')
        result.setMinimumHeight(40)
        def open_related():
            page=self.window._pages.get('press')
            if page is not None:page.reset_filter_context()
            self.window.navigate('press',season=season,kind=kind,competition=competition)
        result.clicked.connect(open_related)
        return result

    def library(self):
        name = self.window.current_save_name()
        if not name:
            raise PressError("请先建立存档。")
        library = FeatureLibrary(store.database_path(name))
        # The development launcher holds one process lock per isolated root.
        # After restart, interrupted requests are displayed, never auto-retried.
        with library.connection() as conn:
            active=repository.enabled(conn)
            activation=conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone() if active else None
        identity=(library.path,activation[0] if activation else None)
        if identity not in self._recovered:
            # Existing development libraries also need the concurrent journal;
            # creating new tables is not the only one-time setup operation.
            if active:
                library.install()
            library.recover(tuple(worker.job['token'] for worker in self.workers.values() if worker.library.path==library.path and worker.job))
            self._recovered.add(identity)
        return library

    def overview(self):
        return self.library().overview()

    def enable(self):
        activate(self.window.current_save_name(),features=True)
        self.changed.emit()

    def pump(self):
        if self.running or self.close_when_idle or self.auto_needs_key or self.auto_paused or not self.window.has_save:return
        try:
            library=self.library();report_id=library.next_queued()
            if report_id:self._start_feature(library,report_id)
        except (PressError,OSError):
            self.auto_paused=True

    def resume_auto(self):
        self.auto_needs_key=False;self.auto_paused=False
        self.changed.emit();QTimer.singleShot(0,self.pump)

    def _start_feature(self,library,report_id,*,retry=False):
        token=str(uuid.uuid4())
        worker=FeatureWorker(library,report_id,self.credentials,self.transport,self,retry=retry)
        self.workers[token]=worker
        worker.progress.connect(self.changed.emit)
        worker.needs_key.connect(self._needs_key)
        worker.paused.connect(self._pause_auto)
        worker.finished.connect(lambda token=token:self._finished(token))
        worker.start();self.changed.emit()

    def _needs_key(self):
        self.auto_needs_key=True;self.changed.emit()

    def _pause_auto(self):
        self.auto_paused=True;self.changed.emit()

    def retry_feature(self,report_id):
        if self.running:return
        self._start_feature(self.library(),report_id,retry=True)

    def generate(self, season, week, *, retry=False):
        try:
            key = self.credentials.get()
            if not key:
                if not self.settings():
                    return
                key = self.credentials.get()
            library = Library(self.library().path)
            job = library.reserve(int(season), int(week), retry=retry)
            if job is None:
                self.window.navigate('press_article', season=season, week=week)
                return
            worker = ReportWorker(library, job, key, self.transport, self)
            self.workers[job['token']] = worker
            worker.progress.connect(self.changed.emit)
            worker.finished.connect(lambda token=job['token']: self._finished(token))
            worker.start()
            self.changed.emit()
        except PressError as exc:
            QMessageBox.information(self.window, '周报', str(exc))

    def _finished(self, token):
        worker = self.workers.pop(token)
        worker.deleteLater()
        self.changed.emit()
        if self.close_when_idle and not self.running:
            self.window.close()
        elif not self.close_when_idle:
            QTimer.singleShot(0,self.pump)

    def _changed(self):
        # Never navigate on completion: the user may now be reading another save.
        if self.window._current_route and self.window._current_route[0] in ('press', 'dashboard'):
            self.window._refresh_current_page(restore_scroll=True)
        else:
            self.window._refresh_chrome()

    def settings(self):
        if self.simulation:
            QMessageBox.information(self.window, '模拟体验', '模拟版不使用密钥，也不会调用网络。\n要设置真实密钥，请打开“启动报刊开发版”。')
            return False
        dialog = QDialog(self.window)
        dialog.setWindowTitle('DeepSeek · 报刊设置')
        dialog.setMinimumWidth(460)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(16)
        title = QLabel('让比赛成为故事')
        title.setObjectName('SectionTitle'); layout.addWidget(title)
        note = QLabel('周报由你手动生成；阶段、赛事结束、转会窗关闭、选秀和Top20结算后，专题自动在后台撰写。阅读已保存文章不会调用API。')
        note.setWordWrap(True); layout.addWidget(note)
        layout.addWidget(QLabel('DeepSeek API 密钥'))
        field = QLineEdit(); field.setObjectName('PressApiKey')
        field.setEchoMode(QLineEdit.Password); field.setPlaceholderText('输入新密钥以设置或替换')
        layout.addWidget(field)
        remember = QCheckBox('保存在本机 macOS 钥匙串')
        remember.setChecked(True); layout.addWidget(remember)
        error = QLabel(''); error.setWordWrap(True); error.setObjectName('ErrorText'); layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('保存密钥')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        remove = QPushButton('移除已保存密钥')
        buttons.addButton(remove, QDialogButtonBox.DestructiveRole)
        layout.addWidget(buttons)

        def save():
            try:
                self.credentials.set(field.text().strip(), remember=remember.isChecked())
                field.clear(); dialog.accept()
            except PressError as exc:
                error.setText(str(exc))

        def clear():
            try:
                self.credentials.clear(); field.clear(); dialog.reject()
            except PressError as exc:
                error.setText(str(exc))

        buttons.accepted.connect(save); buttons.rejected.connect(dialog.reject)
        remove.clicked.connect(clear)
        accepted = dialog.exec() == QDialog.Accepted
        field.clear()
        if accepted:self.resume_auto()
        return accepted
