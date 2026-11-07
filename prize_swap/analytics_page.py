"""Embedded sales workspace; all values come from the resolved floor service."""

from copy import deepcopy
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton, QSplitter, QTabWidget, QScrollArea, QStackedWidget, QCheckBox, QMenu, QToolButton, QWidgetAction, QFormLayout, QFileDialog, QMessageBox
from .analytics_data import FIELDS, NUMERIC, AXES, RATIOS, AGGREGATIONS, formatted, filter_records, history_series, validate_ui_state
from .analysis_views import make_table, MethodAnalysisPanel
from .analytics_charts import ChartCanvas, CHARTS, chart_icon, chart_constraints, histogram, scatter
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
        tools=QWidget();self.controls=QHBoxLayout(tools);self.controls.setContentsMargins(0,0,0,0)
        tool_scroll=QScrollArea();tool_scroll.setWidgetResizable(True);tool_scroll.setWidget(tools);tool_scroll.setFixedHeight(42);tool_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(tool_scroll)
        self.axis=QComboBox();self.metric_choice=QComboBox();self.operation=QComboBox();self.chart_type=QComboBox()
        for label,control,items in (("集計軸",self.axis,AXES),("指標",self.metric_choice,FIELDS),("計算",self.operation,AGGREGATIONS),("グラフ",self.chart_type,CHARTS)):
            self.controls.addWidget(QLabel(label));self.controls.addWidget(control)
            for key,text in items.items():control.addItem(text,key)
        self.controls.addStretch()
        row=QHBoxLayout();outer.addLayout(row)
        self.search=QLineEdit();self.search.setPlaceholderText('機械番号・管理ナンバー・機種・商品・方法を検索')
        row.addWidget(self.search,1)
        self.focus_note=QLabel();row.addWidget(self.focus_note)
        clear=QPushButton('対象マシンの絞り込み解除');clear.clicked.connect(self.clear_focus);row.addWidget(clear)
        actions=QHBoxLayout();outer.addLayout(actions)
        self.legend=QCheckBox('凡例');self.legend.setChecked(self.owner.analytics_ui['legend']);actions.addWidget(self.legend)
        self.order=QComboBox();self.order.addItems(['値・降順','値・昇順','名前順']);actions.addWidget(self.order)
        graph_settings=QToolButton();graph_settings.setText('グラフ設定');menu=QMenu(graph_settings);options=QWidget();form=QFormLayout(options)
        self.stack_axis=QComboBox();self.x_metric=QComboBox();self.y_metric=QComboBox()
        for c,values in ((self.stack_axis,AXES),(self.x_metric,{k:v[0] for k,v in NUMERIC.items()}),(self.y_metric,{k:v[0] for k,v in NUMERIC.items()})):
            for key,text in values.items():c.addItem(text,key)
        for label,c in [('積上・クロスの第2軸',self.stack_axis),('散布図・横軸',self.x_metric),('散布図・縦軸',self.y_metric)]:form.addRow(label,c)
        action=QWidgetAction(menu);action.setDefaultWidget(options);menu.addAction(action);graph_settings.setMenu(menu);graph_settings.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);actions.addWidget(graph_settings)
        self.expand_button=QPushButton('グラフを拡大');self.expand_button.setCheckable(True);self.expand_button.toggled.connect(self.expand_chart);actions.addWidget(self.expand_button)
        save=QPushButton('グラフ画像保存');save.clicked.connect(lambda:self.save_image());actions.addWidget(save)
        actions.addStretch()
        self.export_button=QToolButton();self.export_button.setText('CSV・Excel出力');export_menu=QMenu(self.export_button)
        for name in ('集計結果','詳細','グラフデータ','クロス集計'):
            export_menu.addAction('CSV：'+name,lambda checked=False,n=name:self.save_csv(table=n))
        export_menu.addAction('Excel：全データ',lambda:self.save_excel())
        self.export_button.setMenu(export_menu);self.export_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);actions.addWidget(self.export_button)
        self.filter_row=QHBoxLayout();outer.addLayout(self.filter_row)
        self.filter_field=QComboBox();self.filter_operator=QComboBox();self.filter_value=QLineEdit();self.filter_upper=QLineEdit()
        self.filter_row.addWidget(QLabel('条件'))
        for key,text in FIELDS.items():self.filter_field.addItem(text,key)
        for text,key in [('含む','contains'),('一致','eq'),('以上','ge'),('以下','le'),('範囲','range'),('未取得','missing')]:self.filter_operator.addItem(text,key)
        for c in (self.filter_field,self.filter_operator,self.filter_value,self.filter_upper):self.filter_row.addWidget(c)
        add=QPushButton('条件追加');add.clicked.connect(self.add_condition);self.filter_row.addWidget(add)
        self.condition_button=QToolButton();self.condition_button.setText('適用条件 0');self.condition_menu=QMenu(self.condition_button);self.condition_button.setMenu(self.condition_menu);self.condition_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);self.filter_row.addWidget(self.condition_button)
        self.filter_field.currentIndexChanged.connect(self.filter_editor_changed);self.filter_operator.currentIndexChanged.connect(self.filter_editor_changed)
        self.filter_value.returnPressed.connect(self.add_condition);self.filter_upper.returnPressed.connect(self.add_condition)
        self.filter_editor_changed()
        self.notice=QLabel();self.notice.setWordWrap(True);outer.addWidget(self.notice)
        self.splitter=QSplitter(Qt.Orientation.Vertical);outer.addWidget(self.splitter,1)
        self.chart_stack=QStackedWidget();self.splitter.addWidget(self.chart_stack)
        self.chart_scroll=QScrollArea();self.chart_scroll.setWidgetResizable(True)
        self.canvas=ChartCanvas();self.chart_scroll.setWidget(self.canvas);self.chart_stack.addWidget(self.chart_scroll)
        from .radar import RadarChart
        self.radar=RadarChart();self.radar_scroll=QScrollArea();self.radar_scroll.setWidgetResizable(True);self.radar_scroll.setWidget(self.radar);self.chart_stack.addWidget(self.radar_scroll)
        self.cross_table=make_table([],[]);self.chart_stack.addWidget(self.cross_table)
        self.data_tabs=QTabWidget();self.splitter.addWidget(self.data_tabs)
        self.result_table=make_table([],[]);self.detail_table=make_table([],[])
        self.data_tabs.addTab(self.result_table,'集計結果');self.data_tabs.addTab(self.detail_table,'全項目・取得元')
        self.legacy=MethodAnalysisPanel(self.owner);self.legacy_scroll=QScrollArea();self.legacy_scroll.setWidgetResizable(True);self.legacy_scroll.setWidget(self.legacy);self.data_tabs.addTab(self.legacy_scroll,'運営方法別（従来指標）')
        self.splitter.setSizes([380,260])
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(120);self.timer.timeout.connect(self.refresh)
        self.load_state()
        for control in (self.axis,self.metric_choice,self.operation,self.chart_type):control.currentIndexChanged.connect(self.changed)
        self.search.textChanged.connect(self.changed)
        self.owner.changed.connect(self.invalidate)
        self.canvas.activated.connect(self.window.highlight_analysis_machines)
        self.radar.activated.connect(lambda uids:self.window.highlight_analysis_machines(uids,True))
        self.cross_table.cellClicked.connect(self.cross_clicked)
        self.result_table.cellClicked.connect(self.result_clicked)
        self.detail_table.cellClicked.connect(self.detail_clicked)
        from .theme import manager
        manager().changed.connect(self.theme_changed)
        for c in (self.stack_axis,self.x_metric,self.y_metric,self.order):c.currentIndexChanged.connect(self.changed)
        self.legend.toggled.connect(self.changed)
        self.theme_changed()

    def load_state(self):
        self._loading=True;s=self.owner.analytics_ui
        for key,control in (("axis",self.axis),("metric",self.metric_choice),("operation",self.operation),("chart",self.chart_type),("stack",self.stack_axis),("x_metric",self.x_metric),("y_metric",self.y_metric)):
            control.setCurrentIndex(max(0,control.findData(s[key])))
        self.search.setText(s['search']);self.conditions=deepcopy(s['conditions'])
        self.legend.setChecked(s['legend']);self.order.setCurrentIndex(['descending','ascending','label'].index(s['sort']))
        self.focus_uids=set(s['focus_uids']) if s['focus_uids'] is not None else None
        self._loaded_ui=deepcopy(s);self._loading=False

    def changed(self,*_):
        if self._loading:return
        metric=self.metric_choice.currentData();op=self.operation.currentData()
        if metric not in NUMERIC and op not in ('count','share') or metric in RATIOS and op=='sum' or op=='recalculate' and metric!='cost_rate':
            self.operation.blockSignals(True);self.operation.setCurrentIndex(self.operation.findData('count' if metric not in NUMERIC else 'mean'));self.operation.blockSignals(False)
        s=self.owner.analytics_ui
        for key,control in (("axis",self.axis),("metric",self.metric_choice),("operation",self.operation),("chart",self.chart_type),("stack",self.stack_axis),("x_metric",self.x_metric),("y_metric",self.y_metric)):
            s[key]=control.currentData()
        s['search']=self.search.text();s['conditions']=deepcopy(self.conditions)
        s['legend']=self.legend.isChecked();s['descending']=self.order.currentIndex()==0
        s['sort']=['descending','ascending','label'][self.order.currentIndex()]
        s['focus_uids']=sorted(self.focus_uids) if self.focus_uids is not None else None
        self._loaded_ui=deepcopy(s);self.owner.schedule_save();self.dirty=True
        self.timer.start()

    def invalidate(self,*_):
        self.dirty=True
        if self.isVisible():self.timer.start()

    def showEvent(self,event):
        super().showEvent(event)
        if self.dirty:self.refresh()

    def clear_focus(self):
        self.focus_uids=None;self.changed();self.refresh()

    def focus_machine(self,uid,scope='machine'):
        rs=self.owner.analytics.records();r=next((r for r in rs if r['uid']==uid),None)
        if r is None:self.focus_uids=None
        elif scope=='machine':self.focus_uids={uid}
        elif scope in ('machine_type','method'):self.focus_uids={a['uid'] for a in rs if a[scope]==r[scope]}
        self.changed();self.refresh()

    def refresh(self):
        if self._loading:return
        if self.owner.analytics_ui!=self._loaded_ui:self.load_state()
        self.timer.stop();self.dirty=False
        self.update_condition_menu()
        try:
            self.records,self.results=self.owner.analytics.query(self.conditions,self.search.text(),self.focus_uids,self.axis.currentData(),self.metric_choice.currentData(),self.operation.currentData(),self.stack_axis.currentData() if self.chart_type.currentData() in ('stacked','cross') else None)
            self.results=sorted(self.results,key=lambda r:(r['value'] is None,-r['value'] if self.order.currentIndex()==0 and r['value'] is not None else r['value'] if r['value'] is not None else 0) if self.order.currentIndex()!=2 else (r['label'],r['period_text']))
            self.notice.setText('欠損は0で補完しません。異なる期間は分離して表示します。比率の単純平均・加重平均・再計算を区別してください。')
        except Exception as exc:
            self.records=filter_records(self.owner.analytics.records(),self.conditions,self.search.text(),self.focus_uids);self.results=[];self.notice.setText(str(exc))
        a=self.owner.analysis_state
        self.metadata.setText(f'{period_text(a)} ｜ 更新 {a["loaded_at"] or "未読込"} ｜ 分析対象 {len(self.records)} / {len(self.owner.machines)}台')
        self.focus_note.setText('マシン指定あり' if self.focus_uids is not None else '')
        metric=self.metric_choice.currentData();operation=self.operation.currentData()
        rows=[]
        for r in self.results:
            value=formatted(metric,r['value']) if operation not in ('count','share') else formatted('po_rate' if operation=='share' else 'dispensed',r['value'])
            rows.append([r['label'],r['period_text'],(value,r['value']),r['count'],r['valid_count'],r['mode'],'、'.join(r['products'])])
        fill_table(self.result_table,['区分','集計期間','集計値','対象台数','有効値件数','計算方式','商品構成'],rows)
        for i,r in enumerate(self.results):
            self.result_table.item(i,0).setData(Qt.ItemDataRole.UserRole,r['uids'])
        self.result_table.setSortingEnabled(True)
        keys=list(FIELDS)
        raw=[]
        for r in self.records:
            raw.append([formatted(k,r.get(k)) for k in keys]+[r['loaded_at'] or '—',r['source_file']])
        fill_table(self.detail_table,[FIELDS[k] for k in keys]+['更新日時','実績ファイル'],raw)
        self.detail_table.setSortingEnabled(False)
        for i,r in enumerate(self.records):
            self.detail_table.item(i,0).setData(Qt.ItemDataRole.UserRole,[r['uid']])
            for j,key in enumerate(keys):
                if key in r['sources']:
                    info=r['sources'][key];self.detail_table.item(i,j).setToolTip(info['source']+' / 更新 '+(info['updated_at'] or '不明'))
        self.detail_table.setSortingEnabled(True)
        self.update_chart()
        self.legacy.refresh()

    def cross_clicked(self,row,column):
        item=self.cross_table.item(row,column)
        if item and item.data(Qt.ItemDataRole.UserRole):self.window.highlight_analysis_machines(item.data(Qt.ItemDataRole.UserRole))

    def result_clicked(self,row,column):
        item=self.result_table.item(row,0)
        if item:self.window.highlight_analysis_machines(item.data(Qt.ItemDataRole.UserRole) or [])

    def detail_clicked(self,row,column):
        item=self.detail_table.item(row,0)
        if item:self.window.highlight_analysis_machines(item.data(Qt.ItemDataRole.UserRole) or [],True)

    def filter_editor_changed(self,*_):
        numeric=self.filter_field.currentData() in NUMERIC
        for i in range(self.filter_operator.count()):
            key=self.filter_operator.itemData(i);self.filter_operator.model().item(i).setEnabled(key in ('eq','missing') or key=='contains' and not numeric or key in ('ge','le','range') and numeric)
        current=self.filter_operator.currentData()
        if current=='contains' and numeric:self.filter_operator.setCurrentIndex(self.filter_operator.findData('ge'))
        elif current in ('ge','le','range') and not numeric:self.filter_operator.setCurrentIndex(0)
        self.filter_upper.setVisible(self.filter_operator.currentData()=='range')
        self.filter_value.setVisible(self.filter_operator.currentData()!='missing')
        percent=self.filter_field.currentData() in ('po_rate','cost_rate')
        self.filter_value.setPlaceholderText('割合(%)：5 → 5%' if percent else '数値または文字')
        self.filter_upper.setPlaceholderText('上限(%)' if percent else '上限')

    def add_condition(self):
        from .core import AppError
        import math,unicodedata
        try:
            key=self.filter_field.currentData();op=self.filter_operator.currentData();c=dict(field=key,op=op)
            def value(edit):
                text=unicodedata.normalize('NFKC',edit.text()).strip()
                if key in NUMERIC:
                    number=float(text.replace(',','').rstrip('%'))
                    if not math.isfinite(number):raise ValueError('有限の数値を入力してください。')
                    return number/100 if key in ('po_rate','cost_rate') else number
                if key=='planned' and op=='eq':
                    if text not in ('投入予定','予定なし','あり','なし'):raise ValueError('投入予定は「投入予定」または「予定なし」と入力してください。')
                    return text in ('投入予定','あり')
                return text
            if op!='missing':c['value']=value(self.filter_value)
            if op=='range':c['upper']=value(self.filter_upper)
            candidate=self.owner.analytics_ui|dict(conditions=self.conditions+[c]);validate_ui_state(candidate)
            self.conditions.append(c);self.changed();self.refresh()
        except (ValueError,AppError) as exc:self.notice.setText('条件を追加できません：'+str(exc))

    def update_condition_menu(self):
        self.condition_menu.clear();self.condition_button.setText(f'適用条件 {len(self.conditions)}')
        for i,c in enumerate(self.conditions):
            text=FIELDS[c['field']]+' '+c['op']+' '+formatted(c['field'],c.get('value'))
            if c['op']=='range':text+=' 〜 '+formatted(c['field'],c['upper'])
            self.condition_menu.addAction('解除：'+text,lambda checked=False,index=i:self.remove_condition(index))
        self.condition_menu.addAction('すべての条件を解除',self.clear_conditions)

    def remove_condition(self,index):
        if index<len(self.conditions):self.conditions.pop(index)
        self.changed();self.refresh()

    def clear_conditions(self):
        self.conditions=[];self.changed();self.refresh()

    def protected_paths(self):
        return [self.owner.source,self.window.source,self.owner.analysis_state.get('source')]+[e['snapshot'].get('source') for e in self.owner.performance_history]

    def save_csv(self,path=None,table='集計結果',overwrite=False):
        from .analytics_export import write_csv,export_tables
        chosen=path is None
        if chosen:path,_=QFileDialog.getSaveFileName(self,'CSV出力：'+table,'売上分析_'+table+'.csv','CSV (*.csv)')
        if not path:return False
        try:
            self.refresh();tables=export_tables(self)
            if table not in tables:raise ValueError('クロス集計グラフを選択してから出力してください。')
            write_csv(path,*tables[table],protected=self.protected_paths(),overwrite=overwrite or chosen);return True
        except Exception as exc:
            self.notice.setText('CSV保存エラー：'+str(exc));return False

    def save_excel(self,path=None,overwrite=False):
        from .analytics_export import write_workbook,export_tables
        chosen=path is None
        if chosen:path,_=QFileDialog.getSaveFileName(self,'分析データをExcel出力','売上分析.xlsx','Excel (*.xlsx)')
        if not path:return False
        try:
            self.refresh();state=self.owner.analysis_state
            metadata=[('対象期間',period_text(state)),('抽出日時',state.get('extracted_at') or '不明'),('読込日時',state.get('loaded_at') or '不明'),('ファイル名',state.get('file_name') or '未読込'),('グラフ',self.chart_type.currentText()),('集計軸',self.axis.currentText()),('計算方式',self.operation.currentText()),('条件',str(self.conditions)),('注意',self.notice.text())]
            write_workbook(path,export_tables(self),metadata,self.protected_paths(),overwrite or chosen);return True
        except Exception as exc:self.notice.setText('Excel保存エラー：'+str(exc));return False

    def theme_changed(self,*_):
        for i in range(self.chart_type.count()):self.chart_type.setItemIcon(i,chart_icon(self.chart_type.itemData(i)))

    def update_chart(self):
        kind=self.chart_type.currentData();metric=self.metric_choice.currentData();operation=self.operation.currentData()
        self.timeline,self.history_omitted=history_series(self.owner,self.records,metric) if metric in NUMERIC and metric not in ('ratio','days') else ([],0)
        for i in range(self.chart_type.count()):
            key=self.chart_type.itemData(i);reason=chart_constraints(key,self.records,self.results,metric,operation,self.timeline)
            item=self.chart_type.model().item(i);item.setEnabled(not reason);item.setToolTip(reason or CHARTS[key])
        reason=chart_constraints(kind,self.records,self.results,metric,operation,self.timeline)
        self.chart_stack.setCurrentWidget(self.chart_scroll)
        self.chart_data=[]
        if reason:
            self.notice.setText(self.notice.text()+'\n'+reason);self.canvas.set_data(kind,[],reason);return
        title=f'{AXES[self.axis.currentData()]} ／ {FIELDS[metric]} ／ {AGGREGATIONS[operation]}'
        data=self.results;value_key='dispensed' if operation=='count' else 'po_rate' if operation=='share' else metric
        if kind in ('line','area'):
            data=self.timeline;title=f'{FIELDS[metric]}：同じ商品・運営方法の期間別実測値';value_key=metric
            self.notice.setText(self.notice.text()+f'\n履歴：重複期間・文脈不明 {self.history_omitted}件を比較から除外。集計設定ではなく各マシンの実測値を表示します。')
        elif kind=='histogram':
            data=histogram(self.records,metric);value_key='dispensed';title=FIELDS[metric]+'の分布（有効値のみ）'
        elif kind=='scatter':
            data=scatter(self.records,self.x_metric.currentData(),self.y_metric.currentData());value_key=self.y_metric.currentData();title=f'{FIELDS[self.x_metric.currentData()]} × {FIELDS[value_key]}'
        elif kind=='radar':
            from .radar import evaluate,score_record
            machine=next(m for m in self.owner.machines if m.uid==self.records[0]['uid'])
            peers=[score_record(m,self.owner.reference_day(),self.owner.analysis_settings) for m in self.owner.machines]
            self.radar.set_axes(evaluate(machine,self.owner.reference_day(),self.owner.analysis_settings,peers));self.chart_stack.setCurrentWidget(self.radar_scroll)
        elif kind=='cross':
            self.render_cross();self.chart_stack.setCurrentWidget(self.cross_table)
        self.chart_data=data
        self.canvas.set_data(kind,data,title,value_key,FIELDS[self.x_metric.currentData()],legend=self.legend.isChecked())

    def render_cross(self):
        from PySide6.QtWidgets import QTableWidgetItem
        self.cross_table.setSortingEnabled(False)
        rows=list(dict.fromkeys((r['period'],r['label']) for r in self.results));columns=sorted({r['series'] for r in self.results})
        fill_table(self.cross_table,['区分・期間']+columns,[[label+' / '+(period[0] or '期間不明')]+['—']*len(columns) for period,label in rows])
        for r in self.results:
            i=rows.index((r['period'],r['label']));j=columns.index(r['series'])+1
            key='dispensed' if self.operation.currentData()=='count' else 'po_rate' if self.operation.currentData()=='share' else self.metric_choice.currentData()
            item=self.cross_table.item(i,j);item.setText(formatted(key,r['value']));item.setData(Qt.ItemDataRole.UserRole,r['uids']);item.setToolTip(r['period_text']+' / '+r['mode'])

    def expand_chart(self,expanded):
        self.data_tabs.setVisible(not expanded);self.expand_button.setText('通常表示へ戻す' if expanded else 'グラフを拡大')

    def save_image(self,path=None):
        if path is None:
            path,_=QFileDialog.getSaveFileName(self,'グラフ画像保存','売上分析.png','PNG (*.png)')
        if not path:return False
        widget=self.radar if self.chart_type.currentData()=='radar' else self.cross_table if self.chart_type.currentData()=='cross' else self.canvas
        ok=widget.grab().save(str(path),'PNG')
        if not ok:QMessageBox.warning(self,'画像保存','グラフ画像を保存できません。')
        return ok
