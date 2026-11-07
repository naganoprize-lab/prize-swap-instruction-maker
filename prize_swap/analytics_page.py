"""Embedded sales workspace; all values come from the resolved floor service."""

from copy import deepcopy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QSplitter, QTabWidget, QScrollArea
from .analytics_data import FIELDS, AXES, AGGREGATIONS, formatted, filter_records
from .analysis_views import make_table, MethodAnalysisPanel, MethodBars
from .performance_validation import period_text


def fill_table(table, headers, rows):
    from .analysis_views import ValueItem
    table.setSortingEnabled(False)
    table.clearContents()
    table.setColumnCount(len(headers));table.setHorizontalHeaderLabels(headers)
    table.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for j,value in enumerate(row):
            item=ValueItem(value[0],value[1]) if isinstance(value,tuple) else ValueItem(value)
            table.setItem(i,j,item)


class SalesPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window=window;self.owner=window.floor
        self.focus_uids=None;self.conditions=deepcopy(self.owner.analytics_ui['conditions'])
        self.records=[];self.results=[];self.dirty=True;self._loading=False
        outer=QVBoxLayout(self);outer.setContentsMargins(6,6,6,6);outer.setSpacing(5)
        self.metadata=QLabel();self.metadata.setWordWrap(True);outer.addWidget(self.metadata)
        self.controls=QHBoxLayout();outer.addLayout(self.controls)
        self.axis=QComboBox();self.metric=QComboBox();self.operation=QComboBox();self.chart_type=QComboBox()
        for label,control,items in (("集計軸",self.axis,AXES),("指標",self.metric,FIELDS),("計算",self.operation,AGGREGATIONS),("グラフ",self.chart_type,{"horizontal":"横棒グラフ"})):
            self.controls.addWidget(QLabel(label));self.controls.addWidget(control)
            for key,text in items.items():control.addItem(text,key)
        self.controls.addStretch()
        row=QHBoxLayout();outer.addLayout(row)
        self.search=QLineEdit();self.search.setPlaceholderText('機械番号・管理ナンバー・機種・商品・方法を検索')
        row.addWidget(self.search,1)
        self.focus_note=QLabel();row.addWidget(self.focus_note)
        clear=QPushButton('対象マシンの絞り込み解除');clear.clicked.connect(self.clear_focus);row.addWidget(clear)
        self.notice=QLabel();self.notice.setWordWrap(True);outer.addWidget(self.notice)
        self.splitter=QSplitter(Qt.Orientation.Vertical);outer.addWidget(self.splitter,1)
        self.chart_scroll=QScrollArea();self.chart_scroll.setWidgetResizable(True)
        self.canvas=MethodBars();self.chart_scroll.setWidget(self.canvas);self.splitter.addWidget(self.chart_scroll)
        self.data_tabs=QTabWidget();self.splitter.addWidget(self.data_tabs)
        self.result_table=make_table([],[]);self.detail_table=make_table([],[])
        self.data_tabs.addTab(self.result_table,'集計結果');self.data_tabs.addTab(self.detail_table,'全項目・取得元')
        self.legacy=MethodAnalysisPanel(self.owner);self.data_tabs.addTab(self.legacy,'運営方法別（従来指標）')
        self.splitter.setSizes([380,260])
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(120);self.timer.timeout.connect(self.refresh)
        self.load_state()
        for control in (self.axis,self.metric,self.operation,self.chart_type):control.currentIndexChanged.connect(self.changed)
        self.search.textChanged.connect(self.changed)
        self.owner.changed.connect(self.invalidate)

    def load_state(self):
        self._loading=True;s=self.owner.analytics_ui
        for key,control in (("axis",self.axis),("metric",self.metric),("operation",self.operation),("chart",self.chart_type)):
            control.setCurrentIndex(max(0,control.findData(s[key])))
        self.search.setText(s['search']);self.conditions=deepcopy(s['conditions'])
        self._loaded_ui=deepcopy(s);self._loading=False

    def changed(self,*_):
        if self._loading:return
        s=self.owner.analytics_ui
        for key,control in (("axis",self.axis),("metric",self.metric),("operation",self.operation),("chart",self.chart_type)):
            s[key]=control.currentData()
        s['search']=self.search.text();s['conditions']=deepcopy(self.conditions)
        self._loaded_ui=deepcopy(s);self.owner.schedule_save();self.dirty=True
        self.timer.start()

    def invalidate(self,*_):
        self.dirty=True
        if self.isVisible():self.timer.start()

    def showEvent(self,event):
        super().showEvent(event)
        if self.dirty:self.refresh()

    def clear_focus(self):
        self.focus_uids=None;self.refresh()

    def focus_machine(self,uid,scope='machine'):
        rs=self.owner.analytics.records();r=next((r for r in rs if r['uid']==uid),None)
        if r is None:self.focus_uids=None
        elif scope=='machine':self.focus_uids={uid}
        elif scope in ('machine_type','method'):self.focus_uids={a['uid'] for a in rs if a[scope]==r[scope]}
        self.refresh()

    def refresh(self):
        if self._loading:return
        if self.owner.analytics_ui!=self._loaded_ui:self.load_state()
        self.timer.stop();self.dirty=False
        try:
            self.records,self.results=self.owner.analytics.query(self.conditions,self.search.text(),self.focus_uids,self.axis.currentData(),self.metric.currentData(),self.operation.currentData())
            self.notice.setText('欠損は0で補完しません。異なる期間は分離して表示します。比率の単純平均・加重平均・再計算を区別してください。')
        except Exception as exc:
            self.records=filter_records(self.owner.analytics.records(),self.conditions,self.search.text(),self.focus_uids);self.results=[];self.notice.setText(str(exc))
        a=self.owner.analysis_state
        self.metadata.setText(f'{period_text(a)} ｜ 更新 {a["loaded_at"] or "未読込"} ｜ 分析対象 {len(self.records)} / {len(self.owner.machines)}台')
        self.focus_note.setText('マシン指定あり' if self.focus_uids is not None else '')
        metric=self.metric.currentData();operation=self.operation.currentData()
        rows=[]
        for r in self.results:
            value=formatted(metric,r['value']) if operation not in ('count','share') else formatted('po_rate' if operation=='share' else 'dispensed',r['value'])
            rows.append([r['label'],r['period_text'],(value,r['value']),r['count'],r['valid_count'],r['mode'],'、'.join(r['products'])])
        fill_table(self.result_table,['区分','集計期間','集計値','対象台数','有効値件数','計算方式','商品構成'],rows)
        self.result_table.setSortingEnabled(True)
        keys=list(FIELDS)
        raw=[]
        for r in self.records:
            raw.append([formatted(k,r.get(k)) for k in keys]+[r['loaded_at'] or '—',r['source_file']])
        fill_table(self.detail_table,[FIELDS[k] for k in keys]+['更新日時','実績ファイル'],raw)
        self.detail_table.setSortingEnabled(False)
        for i,r in enumerate(self.records):
            for j,key in enumerate(keys):
                if key in r['sources']:
                    info=r['sources'][key];self.detail_table.item(i,j).setToolTip(info['source']+' / 更新 '+(info['updated_at'] or '不明'))
        self.detail_table.setSortingEnabled(True)
        self.canvas.records=[dict(method=r['label']+' '+r['period_text'],average_sales=r['value']) for r in self.results]
        self.canvas.setMinimumHeight(max(220,len(self.results)*34));self.canvas.update()
        self.legacy.refresh()
