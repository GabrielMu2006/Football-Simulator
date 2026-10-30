"""Offline, per-save background reporting for the development UI."""
from __future__ import annotations

import uuid
from PySide6.QtCore import QObject,QThread,Signal,QTimer
from PySide6.QtWidgets import QPushButton,QMessageBox
from football_simulator.persistence_v2 import store
from football_simulator.press import PressError,repository
from football_simulator.press.local_library import LocalLibrary,LocalFeatureLibrary,activate


class LocalWorker(QThread):
    progress=Signal()
    failed=Signal()

    def __init__(self,library,item,parent,*,retry=False):
        super().__init__(parent)
        self.library,self.item,self.retry=library,item,retry
        self.job=None

    def run(self):
        try:
            if self.item['kind']=='weekly':
                self.job=self.library.reserve(self.item['season'],self.item['week'],retry=self.retry)
            else:self.job=self.library.reserve_feature(self.item['report_id'],retry=self.retry)
            if self.job:
                self.progress.emit()
                ok=self.library.generate_local(self.job,progress=self.progress.emit,should_stop=self.isInterruptionRequested)
                if not ok and not self.isInterruptionRequested():self.failed.emit()
        except Exception:
            self.failed.emit()


class PressController(QObject):
    changed=Signal()

    def __init__(self,window,*,credentials=None,transport=None):
        super().__init__(window)
        self.window=window
        # Injection slots remain for offline assertions. Neither is called or
        # instantiated by this engine, including settings and queue recovery.
        self.credentials,self.transport=credentials,transport
        self.workers={};self._recovered=set()
        self.close_when_idle=False;self.auto_paused=False;self.auto_needs_key=False;self.simulation=False
        self._after_simulation=None
        self.changed.connect(self._changed)
        self.timer=QTimer(self);self.timer.setInterval(1000)
        self.timer.timeout.connect(self.pump);self.timer.start()

    @property
    def running(self):return bool(self.workers)

    def prepare_close(self):
        self.close_when_idle=True;self.timer.stop()
        for worker in self.workers.values():worker.requestInterruption()

    def related_button(self,*,season,kind=None,competition=None):
        try:
            rows=[r for r in self.library().feature_rows() if r['season']==season
                  and (kind is None or r['kind']==kind) and (competition is None or r['competition']==competition)]
        except (PressError,OSError):return None
        if not rows:return None
        result=QPushButton('本赛事报道' if competition else {'draft':'本届选秀报道','top20':'本届 Top20 专题','transfer':'本季转会报道'}.get(kind,'相关报道'))
        result.setObjectName('PressRelatedReports');result.setMinimumHeight(40)
        def open_related():
            page=self.window._pages.get('press')
            if page is not None:page.reset_filter_context()
            self.window.navigate('press',season=season,kind=kind,competition=competition)
        result.clicked.connect(open_related)
        return result

    def library(self):
        name=self.window.current_save_name()
        if not name:raise PressError('请先建立存档。')
        library=LocalFeatureLibrary(store.database_path(name))
        with library.connection() as conn:
            active=repository.enabled(conn)
            activation=conn.execute('SELECT instance_id FROM press_activation WHERE singleton=1').fetchone() if active else None
        identity=(library.path,activation[0] if activation else None)
        if identity not in self._recovered:
            if active:
                library.install()
                library.recover(tuple(w.job['token'] for w in self.workers.values() if w.library.path==library.path and w.job))
            self._recovered.add(identity)
        return library

    def overview(self):return self.library().overview()

    def enable(self):
        activate(self.window.current_save_name());self.changed.emit();QTimer.singleShot(0,self.pump)

    def pump(self):
        if self.running or self.close_when_idle or self.simulation:return
        if self.auto_paused or not self.window.has_save:
            self._finish_simulation();return
        try:
            library=self.library()
            with library.connection() as conn:
                if not repository.enabled(conn):
                    self._finish_simulation();return
            item=library.next_local()
            if item:
                target=LocalLibrary(library.path) if item['kind']=='weekly' else library
                self._start(target,item)
            else:self._finish_simulation()
        except (PressError,OSError):
            self._pause_auto();self._finish_simulation()

    def begin_simulation(self):
        self.simulation=True

    def finish_simulation(self, callback):
        """Drain the local queue before showing the newly simulated results."""
        self.simulation=False
        self._after_simulation=callback
        self.pump()

    def _finish_simulation(self):
        callback,self._after_simulation=self._after_simulation,None
        if callback:callback()

    def resume_auto(self):
        self.auto_paused=False;self.changed.emit();QTimer.singleShot(0,self.pump)

    def _start(self,library,item,*,retry=False):
        token=str(uuid.uuid4());worker=LocalWorker(library,item,self,retry=retry)
        self.workers[token]=worker
        worker.progress.connect(self.changed.emit);worker.failed.connect(self._pause_auto)
        worker.finished.connect(lambda token=token:self._finished(token))
        worker.start();self.changed.emit()

    def _pause_auto(self):self.auto_paused=True;self.changed.emit()

    def retry_feature(self,report_id):
        if not self.running:self._start(self.library(),{'kind':'feature','report_id':report_id},retry=True)

    def generate(self,season,week,*,retry=False):
        if self.running:return
        try:
            library=LocalLibrary(self.library().path)
            source=library.source(season=season,week=week)
            if source and source['engine_id']!='local-template':
                raise PressError('这是原 DeepSeek 任务；离线版保留原状态，不自动转换。')
            self._start(library,{'kind':'weekly','season':int(season),'week':int(week)},retry=retry)
        except PressError as exc:QMessageBox.information(self.window,'周报',str(exc))

    def _finished(self,token):
        worker=self.workers.pop(token);worker.deleteLater();self.changed.emit()
        if self.close_when_idle and not self.running:self.window.close()
        elif not self.close_when_idle:QTimer.singleShot(0,self.pump)

    def _changed(self):
        if self.simulation or self._after_simulation is not None:return
        if self.window._current_route and self.window._current_route[0] in ('press','dashboard'):
            self.window._refresh_current_page(restore_scroll=True)
        else:self.window._refresh_chrome()

    def settings(self):
        QMessageBox.information(self.window,'本地模板','V0.3.1 使用本地模板离线撰稿，无需 API 密钥。旧 DeepSeek 文章保留原文，未完成的原引擎任务保留状态。')
        return False
