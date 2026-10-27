"""Newspaper shelf and a quiet, selectable long-form reader."""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from football_simulator.ui import tokens as t
from football_simulator.ui.components import PageHeader, PageScroll, card, hline, label
from football_simulator.ui.pages.base import BasePage
from football_simulator.press.feature_queue import KINDS


def button(text, callback, *, secondary=True):
    widget = QPushButton(text)
    if secondary:
        widget.setObjectName('Secondary')
    widget.setMinimumHeight(44)
    widget.setCursor(Qt.PointingHandCursor)
    widget.clicked.connect(callback)
    return widget


def plain(text, name='', *, wrap=True):
    widget = label(text, name, wrap=wrap, selectable=True)
    widget.setTextFormat(Qt.PlainText)
    return widget


class PressPage(BasePage):
    route_name = 'press'

    def __init__(self, app):
        super().__init__(app)
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = PageScroll(); layout.addWidget(self.scroll)
        self.season_filter = None
        self.page = 0
        self.selection = None
        self.kind_filter=None
        self.status_filter=None
        self.competition_filter=None
        self._route_filter=None

    def reset_filter_context(self):
        # An explicit related-page link reapplies its target even when the
        # reader previously changed controls under the same route parameters.
        self._route_filter=None

    def refresh(self, snapshot, params):
        self.snapshot, self.params = snapshot, params
        route_filter=(params.get('season'),params.get('kind'),params.get('competition'))
        if route_filter!=self._route_filter:
            self.season_filter,self.kind_filter,self.competition_filter=route_filter
            self.status_filter=None;self.page=0;self._route_filter=route_filter
        self.scroll.reset()
        controller = self.app.press
        data = controller.overview()
        header = QHBoxLayout()
        header.addWidget(PageHeader('THE PRESS', '报刊', '每周观察、赛事回顾与赛季人物。'), 1)
        header.addWidget(plain('本地模板 · 离线生成', 'PressIssue'), 0, Qt.AlignTop)
        self.scroll.layout.addLayout(header)
        if not data['enabled']:
            frame = card(); layout = QVBoxLayout(frame); layout.setContentsMargins(24, 24, 24, 24)
            layout.addWidget(plain('从下一场比赛开始记录', 'SectionTitle'))
            layout.addWidget(plain('每周推进完成后自动生成周报；阶段、赛事、转会窗、选秀和Top20结束后也会自动后台撰稿。已结束的历史事件不补报。'))
            layout.addWidget(button('启用本存档报刊', controller.enable, secondary=False), 0, Qt.AlignLeft)
            self.scroll.layout.addWidget(frame); self.scroll.layout.addStretch(1)
            return
        weeks = data['weeks']
        candidates = [row for row in weeks if row['ready']]
        toolbar = card(); tools = QVBoxLayout(toolbar); tools.setContentsMargins(24, 20, 24, 20)
        row = QHBoxLayout()
        row.addWidget(plain('周报编辑台', 'SectionTitle')); row.addStretch(1)
        self.week_box = QComboBox(); self.week_box.setObjectName('PressWeekChoice')
        self.week_box.setMinimumWidth(230)
        for item in candidates:
            caption = {'queued':'待刊', 'available': '可生成', 'generating': '生成中', 'ready': '已刊发', 'failed': '未完成', 'paused':'待续写'}[item['status']]
            self.week_box.addItem(f"S{item['season']} · 第 {item['week']} 周 · {caption}", (item['season'], item['week']))
        if self.selection:
            index = self.week_box.findData(self.selection)
            if index >= 0:
                self.week_box.setCurrentIndex(index)
        self.week_box.currentIndexChanged.connect(self._select_week)
        row.addWidget(self.week_box)
        self.generate_button = button('', self._generate, secondary=False)
        self.generate_button.setObjectName('PressGenerate')
        row.addWidget(self.generate_button)
        tools.addLayout(row)
        self.job_message = plain(''); tools.addWidget(self.job_message)
        self._weeks = candidates
        self._select_week()
        self.scroll.layout.addWidget(toolbar)
        features=data.get('features',[])
        queue=card();queue_layout=QVBoxLayout(queue);queue_layout.setContentsMargins(24,18,24,18)
        queued=sum(r['status'] in ('queued','paused') and r.get('engine_id')=='local-template' for r in features+weeks);running=sum(r['status']=='generating' and r.get('engine_id')=='local-template' for r in features+weeks)
        failed=sum(r['status']=='failed' and r.get('engine_id')=='local-template' for r in features+weeks)
        queue_text=f'本地报道队列 · 待刊 {queued} · 撰稿 {running} · 未完成 {failed}'
        legacy_pending=sum(r.get('engine_id')=='deepseek' and r['status']!='ready' for r in features+weeks)
        if legacy_pending:queue_text+=f'\n原 DeepSeek 未完成任务 {legacy_pending} 篇，保留原状态，离线版不执行。'
        if controller.auto_paused:queue_text+='\n本地生成已暂停，请检查未完成任务后继续。'
        message=plain(queue_text);message.setObjectName('PressAutoQueue');queue_layout.addWidget(message)
        if controller.auto_needs_key or controller.auto_paused:
            queue_layout.addWidget(button('继续后台队列',controller.resume_auto),0,Qt.AlignLeft)
        self.scroll.layout.addWidget(queue)
        filters = QHBoxLayout(); filters.addWidget(plain('往期阅读', 'SectionTitle'))
        if self.competition_filter:
            from football_simulator.game import rules
            filters.addWidget(button(rules.display_name(self.competition_filter)+' ×',self._clear_competition))
        filters.addStretch(1)
        kind_box=QComboBox();kind_box.setObjectName('PressTypeFilter');kind_box.setMinimumWidth(150)
        for key,caption in [(None,'全部类型'),('weekly','周报'),*KINDS.items()]:kind_box.addItem(caption,key)
        kind_box.setCurrentIndex(max(0,kind_box.findData(self.kind_filter)))
        kind_box.currentIndexChanged.connect(lambda:self._kind(kind_box.currentData()))
        filters.addWidget(kind_box)
        season_box = QComboBox(); season_box.setObjectName('PressSeasonFilter')
        season_box.addItem('全部赛季', None)
        for season in sorted({r['season'] for r in weeks+features}, reverse=True):
            season_box.addItem(f'第 {season} 赛季', season)
        season_box.setCurrentIndex(max(0, season_box.findData(self.season_filter)))
        season_box.currentIndexChanged.connect(lambda: self._filter(season_box.currentData()))
        filters.addWidget(season_box)
        status_box=QComboBox();status_box.setObjectName('PressStatusFilter')
        for key,caption in [(None,'全部状态'),('ready','已刊发'),('unfinished','未完成'),('unread','未读')]:status_box.addItem(caption,key)
        status_box.setCurrentIndex(max(0,status_box.findData(self.status_filter)))
        status_box.currentIndexChanged.connect(lambda:self._status(status_box.currentData()))
        filters.addWidget(status_box);self.scroll.layout.addLayout(filters)
        reports=[{**r,'kind':'weekly','label':'周报'} for r in weeks if r['status'] in ('ready','queued','generating','failed','paused')]+features
        reports.sort(key=lambda r:(r['season'],r['week'],r.get('created_at','')),reverse=True)
        published=[r for r in reports if (self.season_filter is None or r['season']==self.season_filter)
                   and (self.kind_filter is None or r['kind']==self.kind_filter)
                   and (self.competition_filter is None or r.get('competition')==self.competition_filter)
                   and (self.status_filter is None or self.status_filter=='ready' and r['status']=='ready'
                        or self.status_filter=='unfinished' and r['status']!='ready' or self.status_filter=='unread' and r['unread'])]
        total_pages = max(1, (len(published) + 5) // 6)
        self.page = min(self.page, total_pages - 1)
        if not published:
            self.scroll.layout.addWidget(plain('当前筛选下暂无报道。周报可在上方手动生成，专题将在事件结束后自动进入待刊队列。'))
        for item in published[self.page * 6:(self.page + 1) * 6]:
            frame = card(); layout = QVBoxLayout(frame); layout.setContentsMargins(24, 20, 24, 20)
            layout.setSpacing(12)
            state={'queued':'待刊','generating':'撰稿中','failed':'未完成','ready':'已刊发','paused':'待续写'}[item['status']]
            layout.addWidget(plain(f"S{item['season']} · 第 {item['week']} 周 · {KINDS.get(item['kind'],'周报')} · {state}" + (' · 本地模板' if item.get('engine_id')=='local-template' else ' · 原 DeepSeek') + (' · 未读' if item['unread'] else ''), 'PressIssue'))
            layout.addWidget(plain(item['title'] or item['label'], 'PressHeadline'))
            if item['summary']:layout.addWidget(plain(item['summary'], 'PressDeck'))
            if item['status']=='ready':
                open_button=button('阅读全文 →',lambda checked=False,r=item:self._read(r))
                open_button.setProperty('report_id',item.get('report_id'));layout.addWidget(open_button,0,Qt.AlignLeft)
            elif item['kind']!='weekly':
                detail=('原 DeepSeek 任务，离线版保留原状态，不自动执行或转换。' if item.get('engine_id')!='local-template' else item.get('error') or '将自动在后台撰写，你可以继续推进赛季。')
                if item['status'] in ('generating','failed','paused') and item.get('engine_id')!='local-template':detail+=f" 已保留 {item['sections_done']}/{item['section_total']} 部分。"
                layout.addWidget(plain(detail))
                if item['status']=='failed' and item.get('engine_id')=='local-template':
                    retry=button('重试未完成部分',lambda checked=False,rid=item['report_id']:controller.retry_feature(rid))
                    retry.setEnabled(not controller.running);layout.addWidget(retry,0,Qt.AlignLeft)
            self.scroll.layout.addWidget(frame)
        if total_pages > 1:
            pager = QHBoxLayout()
            previous = button('上一页', lambda: self._page(-1)); previous.setEnabled(self.page > 0)
            following = button('下一页', lambda: self._page(1)); following.setEnabled(self.page + 1 < total_pages)
            pager.addWidget(previous); pager.addStretch(1); pager.addWidget(plain(f'{self.page + 1} / {total_pages}')); pager.addStretch(1); pager.addWidget(following)
            self.scroll.layout.addLayout(pager)
        self.scroll.layout.addStretch(1)

    def _select_week(self, *_):
        selected = self.week_box.currentData()
        self.selection = selected
        item = next((r for r in self._weeks if (r['season'], r['week']) == selected), None)
        self.generate_button.setEnabled(bool(item and item['status'] != 'generating' and (item['status']=='ready' or item.get('engine_id')=='local-template' or item['status']=='available')))
        self.generate_button.setText({'ready': '阅读周报', 'failed': '重试生成', 'paused':'继续生成', 'generating': '正在撰稿…'}.get(item['status'] if item else '', '生成周报'))
        text = '暂无可生成周次，请先推进一周。'
        if item:
            done, total = item['sections_done'], item['section_total']
            if item['status'] == 'generating':
                text = '正在使用本地模板后台撰稿，你可以继续观察比赛。'
            elif item['status'] == 'failed':
                text = (item.get('error') or '生成未完成。') + ' 重试沿用冻结的本地模板。'
            elif item['status'] == 'ready':
                text = '这期周报已保存。'
            elif item['status'] == 'paused':
                text = '任务已暂停，继续时沿用冻结的本地模板。'
            else:
                text = '推进后自动在后台撰写周报；也可在此立即生成。'
        if item and item.get('engine_id')=='deepseek' and item['status']!='ready':
            text='原 DeepSeek 任务，离线版保留原状态，不自动执行或转换。'
        self.job_message.setText(text)

    def _generate(self):
        item = next((r for r in self._weeks if (r['season'], r['week']) == self.selection), None)
        if not item:
            return
        if item['status'] == 'ready':
            self.open_route('press_article', season=item['season'], week=item['week'])
        else:
            self.app.press.generate(item['season'], item['week'], retry=item['status'] == 'failed')

    def _filter(self, season):
        self.season_filter, self.page = season, 0
        self.app._refresh_current_page(restore_scroll=False)

    def _clear_competition(self):
        self.competition_filter=None;self.page=0
        self.app._refresh_current_page(restore_scroll=False)

    def _kind(self,value):
        self.kind_filter,self.page=value,0;self.app._refresh_current_page(restore_scroll=False)

    def _status(self,value):
        self.status_filter,self.page=value,0;self.app._refresh_current_page(restore_scroll=False)

    def _read(self,item):
        if item['kind']=='weekly':self.open_route('press_article',season=item['season'],week=item['week'])
        else:self.open_route('press_article',report_id=item['report_id'])

    def _page(self, change):
        self.page += change
        self.app._refresh_current_page(restore_scroll=False)
        self.scroll.verticalScrollBar().setValue(0)


def article_html(article, season, week, *, sample=False, kind='weekly', issue_label=None, source=None):
    esc = html.escape
    parts = [f'<p class="issue">S{season} / WEEK {week:02d}' + (' · 历史开发稿示例' if sample else ' · '+esc(issue_label or KINDS.get(kind,'周报'))) + '</p>',
             f'<h1>{esc(article["title"])}</h1>', f'<p class="deck">{esc(article["summary"])}</p><hr>']
    if source:
        origin='本地模板' if source['engine_id']=='local-template' else 'DeepSeek · 历史原稿'
        parts.insert(1,f'<p class="issue">{esc(origin)}</p>')
    for index, section in enumerate(article['sections']):
        parts.append(f'<h2 id="section-{index}">{esc(section["heading"])}</h2>')
        for block in section['blocks']:
            if block.get('label'):
                parts.append(f'<p class="issue">{esc(block["label"])}</p>')
            parts.extend(f'<p>{esc(paragraph)}</p>' for paragraph in block['text'].split('\n') if paragraph.strip())
    return f'''<html><head><style>
      body {{color:{t.TEXT};font-size:17px;line-height:160%;}}
      h1 {{font-size:32px;color:{t.TEXT_STRONG};margin-top:16px;margin-bottom:22px;}}
      h2 {{font-size:23px;color:{t.PRIMARY};margin-top:32px;margin-bottom:14px;}}
      p {{margin-top:12px;margin-bottom:20px;line-height:160%;}}
      .issue {{font-size:15px;color:{t.ACCENT};font-weight:600;}}
      .deck {{font-size:18px;color:{t.MUTED};}}
      hr {{color:{t.BORDER};}}
    </style></head><body>{''.join(parts)}</body></html>'''


class ArticlePage(BasePage):
    route_name = 'press_article'

    def __init__(self, app):
        super().__init__(app)
        outer = QVBoxLayout(self); outer.setContentsMargins(28, 16, 28, 16)
        bar = QHBoxLayout()
        bar.addWidget(button('← 报刊', lambda: self.open_route('press'))); bar.addStretch(1)
        outer.addLayout(bar)
        center = QHBoxLayout(); center.addStretch(1)
        self.reader = QTextBrowser(); self.reader.setObjectName('PressReader')
        self.reader.setOpenExternalLinks(False); self.reader.setOpenLinks(False)
        self.reader.setMaximumWidth(900)
        self.reader.document().setDocumentMargin(36)
        center.addWidget(self.reader, 100); center.addStretch(1)
        outer.addLayout(center, 1)

    def refresh(self, snapshot, params):
        self.snapshot, self.params = snapshot, params
        library=self.app.press.library()
        if params.get('report_id'):
            data=library.read_feature(params['report_id'])
            self.reader.setHtml(article_html(data['article'],data['season'],data['week'],kind=data['kind'],issue_label=data['label'],source=library.source(report_id=params['report_id'])))
        else:
            article = library.read(int(params['season']), int(params['week']))
            self.reader.setHtml(article_html(article, int(params['season']), int(params['week']),source=library.source(season=int(params['season']),week=int(params['week']))))
        self.reader.verticalScrollBar().setValue(0)
        self.app._refresh_chrome()
