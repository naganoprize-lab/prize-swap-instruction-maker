"""Sortable analysis tables; activating a record locates its existing floor item."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QLineEdit,QPushButton,QDialogButtonBox,QInputDialog,QMessageBox,QWidget,QComboBox)
from .floor_status import ratio_text,days_text,effective_stat,money


class ValueItem(QTableWidgetItem):
    def __init__(self,text,value=None):
        super().__init__(str(text));self.sort_value=value if value is not None else str(text).casefold()
    def __lt__(self,other):
        a,b=self.sort_value,getattr(other,'sort_value',other.text())
        if type(a) in (float,int) and type(b) in (float,int):return a<b
        return str(a)<str(b)


def make_table(headers,rows):
    t=QTableWidget(len(rows),len(headers));t.setHorizontalHeaderLabels(headers);t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows);t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    t.setAlternatingRowColors(True)
    for i,row in enumerate(rows):
        for j,v in enumerate(row):
            item=ValueItem(v[0],v[1]) if isinstance(v,tuple) else ValueItem(v)
            t.setItem(i,j,item)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive);t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True);t.resizeColumnsToContents();return t


def percent_text(v):return '—' if v is None else f'{v:.1%}'


def show_candidates(owner):
    from .replacement import assess
    dialog=QDialog(owner);dialog.setWindowTitle('入替候補（ルールによる参考判定）');dialog.resize(1250,660)
    layout=QVBoxLayout(dialog);layout.addWidget(QLabel('自動判定で入替を確定しません。商品変更・期間・データ鮮度を確認し、追加操作の確認後に指示書へ反映します。'))
    search=QLineEdit();search.setPlaceholderText('番号・商品・判定理由を検索');layout.addWidget(search)
    rows=[]
    for m in owner.machines:
        a=assess(m,owner.reference_day(),owner.analysis_settings,getattr(owner,'performance_history',[]))
        rows.append([(f'{a["priority"]+1} {a["status"]}',a['priority']),m.number,m.product,(ratio_text(m),__import__('prize_swap.floor_status',fromlist=['ratio']).ratio(m)),
            (days_text(m,owner.reference_day()),__import__('prize_swap.floor_status',fromlist=['days']).days(m,owner.reference_day())),
            (money(effective_stat(m,'average')),effective_stat(m,'average')),
            (percent_text(effective_stat(m,'po_rate')),effective_stat(m,'po_rate')),
            (percent_text(effective_stat(m,'cost_rate')),effective_stat(m,'cost_rate')),
            (percent_text(a['trend']),a['trend']),' / '.join(a['reasons']),(m.analysis or {}).get('loaded_at','—')])
    table=make_table(['優先順位','機器番号','商品名','アベ比','投入日数','平均売上','PO率','原価率','売上前回比','判定理由','データ更新日時'],rows)
    table.setColumnWidth(9,430);table.sortItems(0);layout.addWidget(table,1)
    def filter_rows(text):
        for i in range(table.rowCount()):table.setRowHidden(i,bool(text) and not any(text.casefold() in table.item(i,j).text().casefold() for j in range(table.columnCount())))
    search.textChanged.connect(filter_rows)
    def locate(row,*_):
        number=table.item(row,1).text();ids=[m.uid for m in owner.machines if m.number==number]
        owner.highlight_group(ids)
        if ids:
            owner.scene.clearSelection();node=owner.nodes[ids[0]];node.setSelected(True);owner.view.ensureVisible(node)
    table.cellClicked.connect(locate)
    row=QHBoxLayout();layout.addLayout(row)
    add=QPushButton('選択機器を入替指示書への追加候補にする');row.addWidget(add)
    def propose():
        numbers=list(dict.fromkeys(table.item(index.row(),1).text() for index in table.selectionModel().selectedRows()))
        propose_assignments(owner,numbers,dialog)
    add.clicked.connect(propose)
    close=QPushButton('閉じる');close.clicked.connect(dialog.accept);row.addWidget(close);dialog.exec()


def propose_assignments(owner,numbers,parent=None):
    numbers=[n for n in numbers if n]
    products=[p for p in owner.products() if p.day==owner.selected_day()]
    if not numbers or not products:
        owner.message.emit('機器と選択日の入替先商品を用意してください。');return False
    labels=[f'{i+1}: {p.name}' for i,p in enumerate(products)]
    label,ok=QInputDialog.getItem(parent or owner,'入替先商品の選択','追加候補を割り当てる商品',labels,editable=False)
    if not ok:return False
    product=products[labels.index(label)]
    if QMessageBox.question(parent or owner,'入替指示書へ反映の確認',f'「{product.name}」の投入予定へ {"、".join(numbers)} を追加します。既存の割当は維持します。確定しますか？')!=QMessageBox.StandardButton.Yes:return False
    from .floor_details import DetailCommand
    from copy import deepcopy
    before=owner.state();old=deepcopy(product.machines);new=list(dict.fromkeys(old+numbers));product.machines=new
    owner.history.push(DetailCommand(owner,before,owner.state(),[(product,{'machines':(old,deepcopy(new))})]))
    owner.product_edited.emit();owner.sync();owner.schedule_save();return True
