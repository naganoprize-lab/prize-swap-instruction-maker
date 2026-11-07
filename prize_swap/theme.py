"""Central application theme. Analysis colours are transformed only for screens."""
import json
import math
from pathlib import Path
from PySide6.QtCore import QObject,Signal,Qt,QPointF,QRectF
from PySide6.QtGui import QColor,QPalette,QPixmap,QPainter,QPen,QIcon,QPainterPath,QActionGroup
from PySide6.QtWidgets import QApplication,QMenu,QComboBox
from .core import AppError,atomic_json

MODES={'system':'システム','light':'ライト','dark':'ダーク'}
LIGHT=dict(background='#F5F7FA',card='#FFFFFF',sidebar='#EDF1F5',text='#1F2937',muted='#64748B',border='#DCE3EA',accent='#2563EB',selection='#DBEAFE',alternate='#F8FAFC',canvas='#F5F7FA',grid='#DCE3EA',button_text='#FFFFFF',danger='#B42318')
DARK=dict(background='#111827',card='#1F2937',sidebar='#17212F',text='#F1F5F9',muted='#A8B4C5',border='#334155',accent='#60A5FA',selection='#244568',alternate='#17212F',canvas='#111827',grid='#27374A',button_text='#111827',danger='#FCA5A5')
DARK_ANALYSIS={'#A9D7FF':'#244E78','#FFE16A':'#705B25','#94D8A0':'#1F5237','#FFFFFF':'#253244','#F4A6A6':'#763D43','#B8BEC6':'#485260','#C5A5ED':'#594375','#8FC5F4':'#245476','#C5E2FA':'#304E68','#F7D5A0':'#715330','#FCE4EC':'#533044','#EDF0EE':'#334155','#FFF4BF':'#615026'}
DARK_METRICS={'assigned':('#214B35','#B9F1CE'),'unassigned':('#634524','#FFE0AF'),'excess':('#6A3538','#FFD0CD')}


def stylesheet(t):
    from .booth_style import METRIC_COLORS
    metrics=DARK_METRICS if t is DARK else METRIC_COLORS
    css=f"""
QWidget {{ color:{t['text']}; font-family:'Yu Gothic UI','Noto Sans CJK JP','Meiryo'; font-size:10pt; }}
QMainWindow,QDialog,QWidget#workspace {{ background:{t['background']}; }}
QFrame#card,QWidget#boothGrid,QWidget#dashboardCard {{ background:{t['card']}; border:1px solid {t['border']}; border-radius:8px; }}
QWidget#sidebar {{ background:{t['sidebar']}; border-right:1px solid {t['border']}; }}
QLabel {{ background:transparent; border:none; }}
QLabel#title,QLabel#pageTitle {{ font-size:17pt; font-weight:700; }}
QLabel#section {{ font-size:11pt; font-weight:700; }}
QLabel#muted,QLabel[tone="muted"] {{ color:{t['muted']}; }}
QLabel#count,QLabel#statValue {{ color:{t['accent']}; font-size:12pt; font-weight:700; }}
QLabel[tone="danger"] {{ color:{t['danger']}; font-weight:600; }}
QPushButton,QToolButton {{ background:{t['card']}; border:1px solid {t['border']}; border-radius:5px; padding:4px 8px; min-height:18px; }}
QPushButton:hover,QToolButton:hover {{ background:{t['selection']}; border-color:{t['accent']}; }}
QPushButton:pressed,QToolButton:pressed,QToolButton:checked {{ background:{t['selection']}; }}
QPushButton#primary {{ background:{t['accent']}; color:{t['button_text']}; border-color:{t['accent']}; font-weight:600; }}
QToolButton[nav="true"] {{ background:transparent; border:none; padding:7px 10px; text-align:left; }}
QToolButton[nav="true"]:checked {{ background:{t['selection']}; color:{t['accent']}; font-weight:600; }}
QLineEdit,QComboBox,QDateEdit,QSpinBox,QDoubleSpinBox,QPlainTextEdit,QTextEdit {{ background:{t['card']}; color:{t['text']}; border:1px solid {t['border']}; border-radius:5px; padding:3px 5px; min-height:20px; selection-background-color:{t['selection']}; selection-color:{t['text']}; }}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QDateEdit:focus,QDoubleSpinBox:focus {{ border-color:{t['accent']}; }}
QComboBox QAbstractItemView,QListView {{ background:{t['card']}; color:{t['text']}; selection-background-color:{t['selection']}; selection-color:{t['text']}; }}
QTableView {{ background:{t['card']}; alternate-background-color:{t['alternate']}; color:{t['text']}; border:1px solid {t['border']}; gridline-color:{t['border']}; selection-background-color:{t['selection']}; selection-color:{t['text']}; }}
QTableView::item:hover {{ border-bottom:1px solid {t['accent']}; }}
QTableView QLineEdit,QTableView QComboBox,QTableView QPlainTextEdit {{ border:1px solid {t['accent']}; border-radius:2px; padding:0 3px; }}
QHeaderView::section {{ background:{t['sidebar']}; color:{t['text']}; border:none; border-right:1px solid {t['border']}; border-bottom:1px solid {t['border']}; padding:5px; font-weight:600; }}
QTabWidget::pane {{ border:1px solid {t['border']}; border-radius:5px; background:{t['card']}; }}
QTabBar::tab {{ background:{t['sidebar']}; color:{t['muted']}; padding:5px 12px; border:1px solid {t['border']}; }}
QTabBar::tab:selected {{ background:{t['card']}; color:{t['accent']}; border-bottom:2px solid {t['accent']}; }}
QMenu,QMenuBar {{ background:{t['card']}; color:{t['text']}; border:1px solid {t['border']}; }}
QMenu::item {{ padding:6px 24px 6px 10px; }}
QMenu::item:selected {{ background:{t['selection']}; }}
QMenu::separator {{ height:1px; background:{t['border']}; margin:4px; }}
QStatusBar {{ background:{t['background']}; color:{t['muted']}; }}
QScrollArea {{ border:none; background:{t['background']}; }}
QGraphicsView {{ border:1px solid {t['border']}; border-radius:5px; }}
QScrollBar:vertical {{ background:{t['background']}; width:11px; margin:0; }}
QScrollBar:horizontal {{ background:{t['background']}; height:11px; margin:0; }}
QScrollBar::handle {{ background:{t['border']}; border-radius:4px; min-height:20px; min-width:20px; }}
QScrollBar::handle:hover {{ background:{t['muted']}; }}
QScrollBar::add-line,QScrollBar::sub-line {{ width:0; height:0; }}
QToolTip {{ background:{t['card']}; color:{t['text']}; border:1px solid {t['border']}; padding:5px; }}
QWidget:disabled {{ color:{t['muted']}; }}
QWidget#floorTools QToolButton {{ padding:2px 5px; }}
QWidget#floorTools QComboBox,QWidget#floorTools QLineEdit,QWidget#floorTools QDateEdit,QWidget#floorTools QSpinBox {{ padding:2px 4px; min-height:18px; }}
"""
    for key,(bg,fg) in metrics.items():css+=f'QLabel[metricRole="{key}"] {{ background:{bg}; color:{fg}; padding:4px; border-radius:3px; }}\n'
    return css


class ThemeManager(QObject):
    about_to_change=Signal()
    changed=Signal(str)
    def __init__(self,app):
        super().__init__(app);self.app=app;self.mode='system';self.storage=None;self.preferences_error=''
        self.system_scheme=app.styleHints().colorScheme();self.native_dark=app.palette().color(QPalette.ColorRole.Window).lightnessF()<.5
        app.styleHints().colorSchemeChanged.connect(self.system_changed)
        self.apply()
    @property
    def dark(self):
        if self.mode!='system':return self.mode=='dark'
        if self.system_scheme!=Qt.ColorScheme.Unknown:return self.system_scheme==Qt.ColorScheme.Dark
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as key:
                return winreg.QueryValueEx(key,'AppsUseLightTheme')[0]==0
        except (ImportError,OSError):return self.native_dark
    @property
    def tokens(self):return DARK if self.dark else LIGHT
    def system_changed(self,scheme):
        self.system_scheme=scheme
        if self.mode=='system':self.apply()
    def load(self,path):
        self.storage=Path(path)
        if not self.storage.exists():return
        try:
            if self.storage.stat().st_size>65536:raise AppError('テーマ設定が大きすぎます。')
            data=json.loads(self.storage.read_text(encoding='utf-8'))
            if not isinstance(data,dict) or data.get('version')!=1 or data.get('theme') not in MODES:raise AppError('テーマ設定の形式が不正です。')
            self.set_mode(data['theme'],persist=False)
        except (ValueError,OSError,AppError) as exc:self.preferences_error=str(exc)
    def set_mode(self,mode,persist=True):
        if mode not in MODES:raise AppError('テーマはライト・ダーク・システムから選択してください。')
        if persist and self.storage:
            self.storage.parent.mkdir(parents=True,exist_ok=True);atomic_json(self.storage,dict(version=1,theme=mode))
        self.mode=mode;self.apply()
    def apply(self):
        self.about_to_change.emit();t=self.tokens;p=QPalette()
        roles={'Window':'background','WindowText':'text','Base':'card','AlternateBase':'alternate','Text':'text','Button':'card','ButtonText':'text','ToolTipBase':'card','ToolTipText':'text','Highlight':'selection','HighlightedText':'text','Link':'accent','PlaceholderText':'muted','Light':'border','Mid':'border','Dark':'sidebar'}
        for role,key in roles.items():p.setColor(getattr(QPalette.ColorRole,role),QColor(t[key]))
        for role in (QPalette.ColorRole.Text,QPalette.ColorRole.WindowText,QPalette.ColorRole.ButtonText):p.setColor(QPalette.ColorGroup.Disabled,role,QColor(t['muted']))
        self.app.setPalette(p);self.app.setStyleSheet(stylesheet(t));self.changed.emit('dark' if self.dark else 'light')


def manager():
    app=QApplication.instance()
    if app is None:return None
    if not hasattr(app,'_prize_theme'):app._prize_theme=ThemeManager(app)
    return app._prize_theme


def tokens():
    m=manager();return m.tokens if m else LIGHT


def analysis_color(value):
    m=manager()
    if not m or not m.dark:return value
    value=QColor(value).name().upper()
    if value in DARK_ANALYSIS:return DARK_ANALYSIS[value]
    c=QColor(value);c.setHslF(max(0,c.hslHueF()),min(.8,c.hslSaturationF()),.26);return c.name()


def contrast_text(background):
    c=QColor(background)
    def linear(v):return v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4
    luminance=sum(w*linear(v) for w,v in zip((.2126,.7152,.0722),(c.redF(),c.greenF(),c.blueF())))
    return '#F1F5F9' if luminance<.25 else '#20242A'


def metric_colors(role):
    from .booth_style import METRIC_COLORS
    m=manager();return (DARK_METRICS if m and m.dark else METRIC_COLORS)[role]


def tone(widget,value):
    if widget.property('tone')!=value:
        widget.setProperty('tone',value);widget.style().unpolish(widget);widget.style().polish(widget);widget.update()


def line_icon(name):
    pix=QPixmap(48,48);pix.fill(Qt.GlobalColor.transparent);p=QPainter(pix);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.scale(2,2)
    pen=QPen(QColor(tokens()['text']),1.6);pen.setCapStyle(Qt.PenCapStyle.RoundCap);pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin);p.setPen(pen)
    if name=='sun':
        p.drawEllipse(QPointF(12,12),4,4)
        for i in range(8):
            a=math.pi*i/4;p.drawLine(QPointF(12+7*math.cos(a),12+7*math.sin(a)),QPointF(12+9*math.cos(a),12+9*math.sin(a)))
    elif name=='moon':
        path=QPainterPath(QPointF(16,3));path.cubicTo(4,-1,0,16,12,20);path.cubicTo(18,22,22,16,21,13);path.cubicTo(13,18,7,8,16,3);p.drawPath(path)
    elif name=='monitor':p.drawRoundedRect(QRectF(3,3,18,13),2,2);p.drawLine(12,16,12,21);p.drawLine(7,21,17,21)
    elif name in ('grid','dashboard','floor'):
        for x,y in ((3,3),(14,3),(3,14),(14,14)):p.drawRoundedRect(QRectF(x,y,7,7),1,1)
    elif name=='chart':
        p.drawLine(3,3,3,21);p.drawLine(3,21,22,21)
        for x,h in ((7,7),(12,13),(17,17)):p.drawRect(x,21-h,3,h)
    elif name=='settings':
        p.drawEllipse(QPointF(12,12),6,6);p.drawEllipse(QPointF(12,12),2,2)
        for i in range(8):
            a=math.pi*i/4;p.drawLine(QPointF(12+6*math.cos(a),12+6*math.sin(a)),QPointF(12+9*math.cos(a),12+9*math.sin(a)))
    else:
        p.drawRoundedRect(QRectF(4,3,16,18),2,2)
        for y in (8,12,16):p.drawLine(8,y,17,y)
    p.end();return QIcon(pix)


class ThemeMenu(QMenu):
    def __init__(self,parent=None):
        super().__init__('テーマ',parent);self.actions_by_mode={};self.group=QActionGroup(self);self.group.setExclusive(True)
        for key,label in MODES.items():
            action=self.addAction(label);action.setCheckable(True);action.triggered.connect(lambda checked=False,k=key:manager().set_mode(k));self.group.addAction(action);self.actions_by_mode[key]=action
        manager().changed.connect(self.refresh);self.refresh()
    def refresh(self,*_):
        for key,action in self.actions_by_mode.items():action.setChecked(key==manager().mode);action.setIcon(line_icon({'light':'sun','dark':'moon','system':'monitor'}[key]))


class ThemeCombo(QComboBox):
    def __init__(self,parent=None):
        super().__init__(parent)
        for key,label in MODES.items():self.addItem(label,key)
        self.currentIndexChanged.connect(lambda *_:manager().set_mode(self.currentData()))
        manager().changed.connect(self.refresh);self.refresh()
    def refresh(self,*_):
        self.blockSignals(True);self.setCurrentIndex(self.findData(manager().mode));self.blockSignals(False)


def appearance_dialog(parent):
    from PySide6.QtWidgets import QDialog,QFormLayout,QLabel,QDialogButtonBox
    dialog=QDialog(parent);dialog.setWindowTitle("テーマ・表示設定")
    form=QFormLayout(dialog);form.addRow("アプリのテーマ",ThemeCombo(dialog))
    note=QLabel("システムはWindowsのアプリ設定に連動します。手動選択はOSより優先します。\n画面のテーマは印刷・PDFの白背景や分析結果を変更しません。")
    note.setWordWrap(True);form.addRow(note)
    close=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);close.rejected.connect(dialog.reject);form.addRow(close)
    dialog.resize(520,220);dialog.exec()
