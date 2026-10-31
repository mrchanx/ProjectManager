# -*- coding: utf-8 -*-
"""主窗口：菜单栏+工具栏+左侧项目/任务树+三视图(流程/甘特/表格)+详情面板。"""
import math
import os
import traceback

from PySide6.QtCore import (QEvent, Qt, QDate, QItemSelection, QItemSelectionModel,
                            QObject, QPointF, QRectF, QSettings, QSize, QTimer)
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QFontMetrics,
                           QPainter, QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
                               QDialog, QFileDialog, QFormLayout, QFrame,
                               QGraphicsEllipseItem, QGraphicsLineItem,
                               QGraphicsPolygonItem,
                               QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton,
                               QSpinBox, QSplitter, QStatusBar,
                               QStyledItemDelegate, QStyle, QStyleOptionViewItem,
                               QTabWidget,
                               QTableView, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QTextEdit,
                               QToolBar, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget, QInputDialog)

from . import db, help_text, paths, report
from .templates import TEMPLATES, template_names

try:                                    # 版本号统一来自 version.py（唯一版本源）
    from version import APP_VERSION
except Exception:                       # noqa: BLE001 - 缺文件时不影响启动
    APP_VERSION = ""

STATUS_NAMES = db.STATUS_NAMES

# 状态 → (填充色, 边框色, 文字色)
STATUS_STYLE = {
    "pending": ("#EFF0F2", "#B7BCC2", "#5F6368"),
    "doing":   ("#DCEBFD", "#2F80ED", "#185FA5"),
    "done":    ("#D9F2E1", "#27AE60", "#1E7E45"),
    "blocked": ("#FADCDC", "#EB5757", "#B03A3A"),
    "rework":  ("#FDEBD8", "#F2994A", "#B06015"),
}

# 任务树顶层项（阶段抬头）上挂的阶段名。
# 刻意避开 Qt.UserRole —— 那个角色存的是任务 id，on_tree_selected 靠它区分任务行。
STAGE_ROLE = Qt.UserRole + 1


def fmt_date(s):
    return s if s else "—"


def describe_link(path: str) -> str:
    """关联项在列表里的显示名：文件夹加前缀，文件只显示文件名。"""
    p = str(path or "").strip()
    if not p:
        return ""
    name = os.path.basename(p.rstrip("\\/")) or p
    if os.path.isdir(p):
        return "[文件夹] %s" % name
    return name


# 本次构建的完整版本标识（含调试尾缀，如 1.3.5 / 1.3.5-debug.2）。
# 打包版由 resources/build_version.txt 提供（app/paths.py 读取），开发模式回退 version.py。
APP_VERSION_LABEL = getattr(paths, "BUILD_VERSION", "") or APP_VERSION

APP_TITLE = "产研项目进度管理"
if APP_VERSION_LABEL:
    APP_TITLE += " v%s" % APP_VERSION_LABEL
if getattr(paths, "IS_DEBUG_BUILD", False) and "debug" not in APP_VERSION_LABEL:
    # 调试版身份通常已写在版本标识里（1.3.5-debug.2），此处只兜底老产物
    APP_TITLE += "（调试版）"


def get_settings():
    """窗口尺寸/上次项目等本地配置，随 data 目录一起备份。

    路径由 app/paths.py 决定：开发=项目根 data\\settings.ini，
    安装版=%LOCALAPPDATA%\\ProjectManager\\data\\settings.ini（升级不丢）。
    """
    os.makedirs(os.path.dirname(paths.SETTINGS_PATH), exist_ok=True)
    return QSettings(paths.SETTINGS_PATH, QSettings.IniFormat)


# ---------------- 显示设置（流程图绘制方式，可在「设置 → 显示设置」里改）----------------

VIEW_DEFAULTS = {
    # 选中任务后，把无关任务与连线淡化，突出当前这条链路
    "dim_unrelated": True,
    # 连线高亮范围：
    #   none     不高亮（只做淡化，不画彩色线）
    #   adjacent 只显示直接上游 / 直接下游（默认）
    #   closure  显示全部上下游（整条传递链）
    "arrow_scope": "adjacent",
    # 个体矩阵冻结列数：横向滚动时把最左侧 N 列钉住（0 = 不冻结，默认 5）
    # ⚠ 只能固定"最前面连续 N 列"（N ∈ 0..5），不允许 1/3/5 这种挑列 ——
    #   矩阵本身就是靠列号偏移（前 5 列汇总 + 之后每列一个任务）定位的。
    "matrix_freeze_cols": 5,
}

ARROW_SCOPE_LABELS = [
    ("adjacent", "只显示直接上游 / 下游（紧邻一环）"),
    ("closure", "显示全部上下游（整条传递链）"),
    ("none", "不高亮连线（只做淡化）"),
]
ARROW_SCOPE_ALL = [k for k, _ in ARROW_SCOPE_LABELS]

# 个体矩阵最左侧这几列是"汇总信息"（个体 SN / 名称 / 进度 / 当前停在 / 受阻返工），
# 也是唯一允许冻结的列范围：冻结必须取**连续前缀**（前 1~5 列），
# 不允许 1、3、5 这种跳列 —— 矩阵的列号本身就有语义（≥5 才是任务状态列）。
MATRIX_SUMMARY_HEADERS = ["个体SN", "名称", "进度", "当前停在", "受阻/返工"]
FROZEN_COL_MAX = len(MATRIX_SUMMARY_HEADERS)


def load_view_settings() -> dict:
    s = get_settings()
    opts = dict(VIEW_DEFAULTS)
    opts["dim_unrelated"] = s.value("view/dim_unrelated", VIEW_DEFAULTS["dim_unrelated"],
                                    type=bool)
    scope = s.value("view/arrow_scope", VIEW_DEFAULTS["arrow_scope"], type=str)
    opts["arrow_scope"] = scope if scope in ARROW_SCOPE_ALL else VIEW_DEFAULTS["arrow_scope"]
    try:
        cols = int(s.value("view/matrix_freeze_cols",
                           VIEW_DEFAULTS["matrix_freeze_cols"]))
    except (TypeError, ValueError):                  # 配置被手改坏 → 回默认
        cols = VIEW_DEFAULTS["matrix_freeze_cols"]
    # 夹到 0..5，且**只能取连续前缀**（负数/超范围一律当不冻结）
    opts["matrix_freeze_cols"] = max(0, min(int(cols), FROZEN_COL_MAX))
    return opts


def save_view_settings(opts: dict) -> None:
    s = get_settings()
    s.setValue("view/dim_unrelated", bool(opts.get("dim_unrelated", True)))
    s.setValue("view/arrow_scope", str(opts.get("arrow_scope", "adjacent")))
    s.setValue("view/matrix_freeze_cols",
               max(0, min(int(opts.get("matrix_freeze_cols",
                                       VIEW_DEFAULTS["matrix_freeze_cols"])),
                          FROZEN_COL_MAX)))
    s.sync()


class ClickRectItem(QGraphicsRectItem):
    """可点击矩形图元（甘特条等）：点中后延迟一拍回调。

    延迟到事件循环下一拍再回调是必需的：回调里通常会重建整个场景
    （scene.clear()），在 mousePressEvent 里立即销毁自己属于典型的
    "事件处理中删自己"，会踩到 Qt 内部状态。
    """

    def __init__(self, payload, callback, rect):
        super().__init__(rect)
        self.payload = payload
        self.callback = callback
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, e):
        super().mousePressEvent(e)
        payload, cb = self.payload, self.callback
        QTimer.singleShot(0, lambda: cb(payload))


class FlowNode(ClickRectItem):
    """流程图节点卡片，点击回调选中任务。

    卡片里的文字用 paint() 直接绘制，**不挂 QGraphicsTextItem 子项**：
    子项会吃掉压在文字上的鼠标按下事件，导致卡片中间（正好是文字区）点不动、
    只有卡片边缘能选中。单 item + 自绘即可保证整张卡片都能点。
    """

    def __init__(self, tid, callback, rect, title, subtitle, text_color):
        super().__init__(tid, callback, rect)
        self.tid = tid                  # 兼容既有引用（高亮/测试）
        self.title = title
        self.subtitle = subtitle
        self.text_color = QColor(text_color)
        self.setAcceptHoverEvents(True)

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        r = self.rect()
        painter.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
        painter.setPen(QPen(self.text_color))
        painter.drawText(QRectF(r.x() + 6, r.y() + 3, r.width() - 12, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, self.title)
        painter.setFont(QFont("Microsoft YaHei", 9))
        painter.setPen(QPen(self.text_color.lighter(120)))
        # 宽度留出右下角卡点红点的位置（-26），字幕不会压到红点上
        painter.drawText(QRectF(r.x() + 6, r.y() + 23, r.width() - 26, 16),
                         Qt.AlignLeft | Qt.AlignVCenter, self.subtitle)


class MatrixCellDelegate(QStyledItemDelegate):
    """个体矩阵单元格：选中态只描框，不用底色盖状态色。

    默认的选中高亮是一层**不透明**的蓝（实测 #3089D3），盖上去之后
    "进/完/阻/返"四种状态色全被糊成一个颜色 —— 框选一片时反而看不出
    各自是什么状态。这里把 `State_Selected` 抹掉再交给基类绘制
    （状态底色/文字照常画），选中框自己画。
    """

    BORDER = QColor("#1F5FA8")

    def paint(self, painter, option, index):        # noqa: N802 - Qt 接口
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.State_Selected
        super().paint(painter, opt, index)
        if option.state & QStyle.State_Selected:
            painter.save()
            painter.setPen(QPen(self.BORDER, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(1, 1, -1, -1), 3, 3)
            painter.restore()


class FrozenColumnsView(QTableView):
    """个体矩阵的"冻结列"层：只显示最左侧 N 列，钉在视口左上角。

    Qt 没有原生冻结列，标准做法是**再叠一个视图**共用同一份 model 与
    selectionModel —— 于是选中状态、右键、键盘行为与主表天然一致；
    它自己不滚动（两个滚动条都关掉），只负责把前 N 列画在最上面。

    与主表"像素级对齐"靠四件事：
      1. 列宽逐列抄主表（拖主表表头改列宽 → sectionResized → 重新同步）；
      2. 表头高度设成主表的表头高度，于是两侧表体起点 y 一致；
      3. 位置取主表 viewport.x() 与表头 y，宽度 = 前 N 列宽之和，
         高度 = 一直到主表 viewport 底部（正好不含主表的水平滚动条）；
      4. 无边框（NoFrame）是刻意的 —— 这样它的列 0 和主表的列 0
         落在同一个像素上，不会差 1px。
    第 N 列右边画一条竖线，提示"冻结区到此为止"。
    """

    def __init__(self, master, parent=None):
        super().__init__(parent or master)
        self.master = master
        self.cols = 0
        self._model = master.model()                      # 同一份数据（留引用防 GC）
        self.setModel(self._model)
        self.setSelectionModel(master.selectionModel())   # 选区也共享
        self.setItemDelegate(master.itemDelegate())       # 选中描框跟着一起用
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.NoFocus)                   # 不抢主表焦点
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 与主表同为"按行"滚动，这样滚动条数值可以直接对拷
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerItem)
        self.setWordWrap(False)
        self.viewport().setAutoFillBackground(True)       # 盖住主表左侧内容
        self.verticalHeader().setVisible(False)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionsClickable(False)
        hh.setSectionResizeMode(QHeaderView.Fixed)        # 列宽只从主表同步
        # 冻结区右侧的分隔线：用一个 1px 子控件，别在 paintEvent 里
        # QPainter(self) 画 —— QAbstractScrollArea 上那样画会报
        # "QPainter::setPen: Painter not active"（绘制发生在 viewport 里）。
        self._edge = QFrame(self)
        self._edge.setStyleSheet("background:#C8CDD3;")
        self._edge.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def sync(self, cols):
        """按主表当前几何重新摆放自己；cols = 要冻结的列数（取连续前缀）。"""
        m = self.master
        total = m.columnCount()
        self.cols = max(0, min(int(cols), total))
        for c in range(total):
            hidden = c >= self.cols
            self.setColumnHidden(c, hidden)
            if not hidden:
                self.setColumnWidth(c, m.columnWidth(c))
        self.verticalHeader().setDefaultSectionSize(
            m.verticalHeader().defaultSectionSize())
        hh, mh = self.horizontalHeader(), m.horizontalHeader()
        hh.setFixedHeight(mh.height())                    # 表体起点对齐
        vp = m.viewport()
        width = sum(m.columnWidth(c) for c in range(self.cols))
        self.setGeometry(vp.x(), mh.y(), width,
                         max(0, vp.y() + vp.height() - mh.y()))
        self._edge.setGeometry(width - 1, 0, 1, self.height())


class NullableDateEdit(QWidget):
    """可为空的日期编辑：QDateEdit + 清除按钮。"""

    # 哨兵日期 = 下界 = "未设置"（靠 setSpecialValueText 显示成中文）
    MIN_DATE = QDate(2000, 1, 1)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("yyyy-MM-dd")
        self.edit.setSpecialValueText("未设置")
        self.edit.setDateRange(self.MIN_DATE, QDate(2099, 12, 31))
        self.edit.setDate(self.MIN_DATE)
        self._cal = self.edit.calendarWidget()
        if self._cal is not None:
            self._cal.installEventFilter(self)
        btn = QPushButton("清除")
        btn.setFixedWidth(44)
        btn.clicked.connect(self.clear_value)
        lay.addWidget(self.edit)
        lay.addWidget(btn)

    def eventFilter(self, obj, ev):
        # 日历弹出且当前仍是"未设置"时，把日期跳到**今天**（免得从 2000 年翻页）。
        # 当年这里写死过 2026-01-01，跨年之后点开日历就落在过去某天，很别扭；
        # 取 QDate.currentDate() 才是"软件使用当天"。
        if (obj is getattr(self, "_cal", None) and ev.type() == QEvent.Show
                and self.edit.date() == self.MIN_DATE):
            self.edit.setDate(QDate.currentDate())
        return super().eventFilter(obj, ev)

    def set_value(self, s):
        """装载任务日期值：空值必须**显式复位**成"未设置"。

        ⚠ 这里曾经写成 `if s:` 才设日期 —— 空值时控件保持上一个任务的日期不动。
        后果：切到没填日期的任务时面板里躺着旧日期，之后任何一次写库
        （改名字/改进度/切任务前的 flush）都会把这个日期写到新任务上，
        表现为"给一个任务填了 2026-09-01，之后点谁谁都是 2026-09-01"。
        日期控件是有状态的（不会像 QLineEdit 那样被 setText("") 清掉），
        所以"空值"必须走 else 分支回到哨兵日期。
        """
        d = QDate.fromString(s, "yyyy-MM-dd") if s else QDate()
        if d.isValid():
            self.edit.setDate(d)
        else:
            self.edit.setDate(self.MIN_DATE)

    def clear_value(self):
        """复位为"未设置"（未选中任何任务时用）。"""
        self.edit.setDate(self.MIN_DATE)

    def value(self):
        d = self.edit.date()
        if d.year() <= 2000:
            return ""
        return d.toString("yyyy-MM-dd")


class PlanDurationLink(QObject):
    """把「计划开始 / 计划结束 / 计划天数」三个控件绑成一件事。

    规则（与用户口径一致：**结束 = 开始 + 天数**，天数就是两个日期的日历日差）：

        · 改「计划天数」(>0) 且开始有值 → 结束 = 开始 + 天数
        · 改「计划结束」且开始有值      → 天数 = 结束 - 开始
        · 改「计划开始」 → 天数有值就按天数推算结束，否则按结束反推天数
        · 「计划开始」没填 → 天数先记着不动结束（没有基准算不出来），
          等开始日期填上那一刻自动补算出结束日期

    「计划天数」是**算出来的显示值，不落库**：库里的真值始终只有
    plan_start / plan_end 两个日期，天数额外用 QDate.daysTo 反推。
    这样导出 JSON、报告、甘特图都不用为它改结构，也不可能出现
    "天数与日期对不上"的脏数据。

    ⚠ 装载（切换任务）期间必须 set_active(False)：日期控件是有状态的，
    _load_detail 逐个 setDate 会一路发 dateChanged，此时若参与换算，
    就会把上一个任务的天数算到新任务头上（数据串写的经典成因）。
    """

    def __init__(self, dt_start, dt_end, spin_days, on_change=None, parent=None):
        super().__init__(parent)
        self.dt_start = dt_start
        self.dt_end = dt_end
        self.spin = spin_days
        self._on_change = on_change
        self._active = True
        self._guard = False        # 换算过程中自己改了控件，别再听自己的信号
        spin_days.setRange(0, 3650)
        spin_days.setSpecialValueText("未设置")
        spin_days.setToolTip("填「计划天数」→ 自动推算计划结束（结束 = 开始 + 天数，"
                             "需先填计划开始）；反过来改了结束日期，天数会自动回算。")
        dt_start.edit.dateChanged.connect(self._start_changed)
        dt_end.edit.dateChanged.connect(self._end_changed)
        spin_days.valueChanged.connect(self._days_changed)

    # ---------------- 对外 ----------------

    def set_active(self, flag):
        """装载数据期间关掉联动（见类注释）。"""
        self._active = bool(flag)

    def sync_from_dates(self):
        """按当前两个日期刷新天数显示，不触发 on_change —— 装载完成后调用。"""
        self._guard = True
        try:
            self.spin.setValue(self._days_between())
        finally:
            self._guard = False

    def clear(self):
        """没选中任务、面板清空时把天数一起复位。"""
        self._guard = True
        try:
            self.spin.setValue(0)
        finally:
            self._guard = False

    # ---------------- 内部 ----------------

    def _has_start(self):
        return self.dt_start.edit.date() != NullableDateEdit.MIN_DATE

    def _days_between(self):
        s = self.dt_start.edit.date()
        e = self.dt_end.edit.date()
        if s == NullableDateEdit.MIN_DATE or e == NullableDateEdit.MIN_DATE:
            return 0
        return max(0, s.daysTo(e))

    def _recalc(self):
        """一次完整的联动计算（在 _guard 里跑，避免信号互相触发）。"""
        d = self.spin.value()
        if d > 0 and self._has_start():
            self.dt_end.edit.setDate(self.dt_start.edit.date().addDays(d))
        else:
            self.spin.setValue(self._days_between())

    def _start_changed(self, *_):
        if not self._active or self._guard:
            return
        self._guard = True
        try:
            self._recalc()
        finally:
            self._guard = False
        self._notify()

    def _end_changed(self, *_):
        if not self._active or self._guard:
            return
        self._guard = True
        try:
            self.spin.setValue(self._days_between())
        finally:
            self._guard = False
        self._notify()

    def _days_changed(self, *_):
        if not self._active or self._guard:
            return
        d = self.spin.value()
        if d > 0 and not self._has_start():
            # 计划开始还没填：天数先记在控件里，但**不触发保存/刷新** ——
            # 没有基准算不出结束日期，而刷新会把面板重新装载一遍，
            # 刚填的天数当场就被抹掉了。等开始日期填上那一刻，
            # _start_changed 会把结束日期补出来并正常落库。
            return
        self._guard = True
        try:
            if d > 0:
                self.dt_end.edit.setDate(self.dt_start.edit.date().addDays(d))
            else:
                # 天数被清成"未设置"：两个日期若都在，按日差还原（天数是算出来的，
                # 不是独立数据 —— 库里的真值只有开始/结束两个日期）
                self.spin.setValue(self._days_between())
        finally:
            self._guard = False
        self._notify()

    def _notify(self):
        if self._on_change is not None:
            self._on_change()


class ZoomableView(QGraphicsView):
    """支持 Ctrl+滚轮缩放的视图（以鼠标位置为中心），Ctrl+0 复位缩放。

    普通滚轮仍是上下滚动；视图重建（切换任务刷新）时缩放保持不丢。
    另支持"点击空白处"回调：blank_click_callback 非空时，鼠标左键点在
    非流程节点（泳道背景/连线/空白）上会触发它，用于取消选中。
    """

    MIN_ZOOM, MAX_ZOOM = 0.2, 5.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._zoom = 1.0
        self._blank_cb = None
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)

    def set_blank_click_callback(self, cb):
        self._blank_cb = cb

    def mousePressEvent(self, e):
        cb = self._blank_cb
        if cb is not None and e.button() == Qt.LeftButton:
            # 命中节点则不处理（节点自己会选中）；点泳道底色/连线/空场景
            # 都算"空白"，延迟一拍回调，避免在事件分发中重建场景。
            hit = False
            it = self.itemAt(e.pos())
            while it is not None and not hit:
                hit = isinstance(it, FlowNode)
                it = it.parentItem()
            if not hit:
                QTimer.singleShot(0, cb)
        super().mousePressEvent(e)

    def _set_zoom(self, z):
        self._zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, z))
        self.setTransform(QTransform.fromScale(self._zoom, self._zoom))

    def zoom_level(self):
        return round(self._zoom, 3)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self._set_zoom(self._zoom * (1.15 if e.angleDelta().y() > 0
                                        else 1 / 1.15))
            e.accept()
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_0 and (e.modifiers() & Qt.ControlModifier):
            self._set_zoom(1.0)
            e.accept()
            return
        super().keyPressEvent(e)


class NewProjectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新建项目")
        self.setMinimumWidth(380)
        form = QFormLayout(self)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("例如：XX型号六维力传感器 2026-01批")
        self.ed_code = QLineEdit()
        self.ed_code.setPlaceholderText("项目编号（可选）")
        self.cmb_tpl = QComboBox()
        for key, label in template_names():
            self.cmb_tpl.addItem(label, key)
        for t in db.list_templates():          # 自定义模板（模板管理里保存的）
            self.cmb_tpl.addItem("%s（自定义）" % t["name"], ("custom", t["id"]))
        self.cmb_tpl.setCurrentIndex(1)
        self.cmb_tpl.currentIndexChanged.connect(self._tpl_changed)
        self.spin_units = QSpinBox()
        self.spin_units.setRange(0, 999)
        self.spin_units.setValue(10)
        self.spin_units.setToolTip("本批有几个弹性体/传感器就填几个，0 = 不启用个体跟踪")
        self.ed_sn_prefix = QLineEdit("SN-")
        form.addRow("项目名称*", self.ed_name)
        form.addRow("项目编号", self.ed_code)
        form.addRow("流程模板", self.cmb_tpl)
        form.addRow("批次个体数量", self.spin_units)
        form.addRow("SN 前缀", self.ed_sn_prefix)
        btn = QPushButton("创建")
        btn.clicked.connect(self.accept)
        form.addRow(btn)

    def _tpl_changed(self, idx):
        # 换成空白模板时清零个体；六维力模板默认 10 个
        if self.cmb_tpl.itemData(idx) != "six_axis":
            self.spin_units.setValue(0)

    def values(self):
        return (self.ed_name.text().strip(), self.ed_code.text().strip(),
                self.cmb_tpl.currentData(), self.spin_units.value(),
                self.ed_sn_prefix.text().strip() or "SN-")

    def accept(self):
        """创建前先校验：名称必填、且不能与已有项目重名。

        校验放在对话框里而不是创建之后——否则名字重了要重新填一遍整个表单。
        """
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写项目名称")
            self.ed_name.setFocus()
            return
        if db.project_name_exists(name):
            QMessageBox.warning(
                self, "项目已存在",
                "已存在同名项目「%s」，不能重复创建。\n\n"
                "项目名称是区分项目/批次的唯一凭据（报告、看板、导出数据都按它命名），"
                "建议在名称里带上型号或批次，例如：\n"
                "    XX型六维力传感器 2026-02批" % name)
            self.ed_name.setFocus()
            self.ed_name.selectAll()
            return
        super().accept()


class NewTaskDialog(QDialog):
    """添加任务：支持选择前置任务（本任务依赖谁）与后续任务（谁依赖本任务）。"""

    def __init__(self, parent, tasks, default_stage="", stages=None):
        super().__init__(parent)
        self.setWindowTitle("添加任务")
        self.setMinimumWidth(480)
        form = QFormLayout(self)
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("任务名称（必填）")
        self.ed_owner = QLineEdit()
        self.ed_owner.setPlaceholderText("负责人，如：机械 / 电路 / 生产")
        self.cmb_stage = QComboBox()
        self.cmb_stage.setEditable(True)
        self.cmb_stage.setMinimumWidth(160)
        # 阶段下拉就按项目的实际阶段列出来；空白项目还没有阶段 → 空列表，
        # 用户直接输入新名字或点「新建…」（不要硬塞内置的研发设计等）
        stage_items = list(stages or [])
        for t in tasks:
            s = t["stage"] or "未分组"
            if s not in stage_items:
                stage_items.append(s)
        self.cmb_stage.addItems(stage_items)
        if default_stage:
            self.cmb_stage.setCurrentText(default_stage)
        btn_stage = QPushButton("新建…")
        btn_stage.setToolTip("新建一个阶段并选用")
        btn_stage.clicked.connect(self._new_stage)
        srow = QHBoxLayout()
        srow.addWidget(self.cmb_stage, 1)
        srow.addWidget(btn_stage)
        swrap = QWidget()
        swrap.setLayout(srow)
        self.dt_ps = NullableDateEdit()
        self.dt_pe = NullableDateEdit()
        # 计划天数：填了天数就自动算结束日期（结束 = 开始 + 天数）
        self.spin_days = QSpinBox()
        self.plan_link = PlanDurationLink(self.dt_ps, self.dt_pe, self.spin_days)
        self.list_preds = QListWidget()
        self.list_preds.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list_preds.setMinimumHeight(110)
        self.list_succs = QListWidget()
        self.list_succs.setSelectionMode(QAbstractItemView.MultiSelection)
        self.list_succs.setMinimumHeight(110)
        for t in tasks:
            label = "[%s] %s" % (t["stage"] or "未分组", t["name"])
            for lst in (self.list_preds, self.list_succs):
                it = QListWidgetItem(label)
                it.setData(Qt.UserRole, t["id"])
                lst.addItem(it)
        btn = QPushButton("创建")
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        form.addRow("任务名称*", self.ed_name)
        form.addRow("阶段", swrap)
        form.addRow("负责人", self.ed_owner)
        form.addRow("计划开始", self.dt_ps)
        form.addRow("计划结束", self.dt_pe)
        form.addRow("计划天数", self.spin_days)
        form.addRow("前置任务\n(它们完成后本任务才能开始)", self.list_preds)
        form.addRow("后续任务\n(它们将依赖本任务)", self.list_succs)
        form.addRow(btn)

    def _new_stage(self):
        name, ok = QInputDialog.getText(self, "新建阶段", "阶段名称：")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if self.cmb_stage.findText(name) < 0:
            self.cmb_stage.addItem(name)
        self.cmb_stage.setCurrentText(name)
        self.cmb_stage.setFocus()

    def values(self):
        def sel(lst):
            return [str(lst.item(i).data(Qt.UserRole))
                    for i in range(lst.count()) if lst.item(i).isSelected()]
        return {"name": self.ed_name.text().strip(),
                # 没选阶段就落到「未分组」，绝不自作主张塞成某个内置阶段
                "stage": self.cmb_stage.currentText().strip() or "未分组",
                "owner": self.ed_owner.text().strip(),
                "plan_start": self.dt_ps.value(), "plan_end": self.dt_pe.value(),
                "pred_ids": sel(self.list_preds), "succ_ids": sel(self.list_succs)}


class UnitManageDialog(QDialog):
    """批次个体管理：批量创建 / 单个增删 / 改 SN 名称备注。"""

    def __init__(self, parent, pid, project_name):
        super().__init__(parent)
        self.pid = pid
        self.setWindowTitle("批次个体管理 — %s" % project_name)
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        # 批量创建
        box = QGroupBox("批量创建（本批有多少个弹性体就建多少个）")
        r = QHBoxLayout(box)
        r.addWidget(QLabel("数量"))
        self.spin_count = QSpinBox()
        self.spin_count.setRange(1, 999)
        self.spin_count.setValue(10)
        r.addWidget(self.spin_count)
        r.addWidget(QLabel("SN前缀"))
        self.ed_prefix = QLineEdit("SN-")
        r.addWidget(self.ed_prefix)
        r.addWidget(QLabel("起始编号"))
        self.spin_start = QSpinBox()
        self.spin_start.setRange(0, 999)
        r.addWidget(self.spin_start)
        btn_batch = QPushButton("批量创建")
        btn_batch.clicked.connect(self.batch_create)
        r.addWidget(btn_batch)
        lay.addWidget(box)
        # 单个添加
        box2 = QGroupBox("添加单个（补编号 / 特殊件）")
        r2 = QHBoxLayout(box2)
        self.ed_sn = QLineEdit()
        self.ed_sn.setPlaceholderText("SN 编号，如 SN-11")
        self.ed_uname = QLineEdit()
        self.ed_uname.setPlaceholderText("名称（可选）")
        btn_one = QPushButton("添加")
        btn_one.clicked.connect(self.add_one)
        r2.addWidget(self.ed_sn, 1)
        r2.addWidget(self.ed_uname, 1)
        r2.addWidget(btn_one)
        lay.addWidget(box2)
        # 列表
        self.tbl_units = QTableWidget()
        self.tbl_units.setColumnCount(4)
        self.tbl_units.setHorizontalHeaderLabels(["SN", "名称", "备注", "进度"])
        self.tbl_units.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl_units.itemChanged.connect(self._item_changed)
        lay.addWidget(self.tbl_units, 1)
        # 底部按钮
        r3 = QHBoxLayout()
        btn_del = QPushButton("删除选中个体")
        btn_del.clicked.connect(self.delete_selected)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.accept)
        r3.addWidget(btn_del)
        r3.addStretch(1)
        r3.addWidget(btn_close)
        wrap = QWidget()
        wrap.setLayout(r3)
        lay.addWidget(wrap)
        self.reload()

    def reload(self):
        stats = db.unit_stats(self.pid)
        self.tbl_units.setRowCount(len(stats))
        for r, s in enumerate(stats):
            u = s["unit"]
            vals = [u["sn"], u["name"], u["note"],
                    "%d%%（%s）" % (s["progress"], s["current"])]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                it.setData(Qt.UserRole, (u["id"], c))
                if c < 3:
                    it.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled
                                | Qt.ItemIsSelectable)
                else:
                    it.setFlags(Qt.ItemIsEnabled)  # 进度列只读
                self.tbl_units.setItem(r, c, it)
        self.tbl_units.resizeColumnsToContents()
        self.tbl_units.horizontalHeader().setStretchLastSection(True)

    def _item_changed(self, item):
        uid, col = item.data(Qt.UserRole) or (None, None)
        if uid is None or col is None or col > 2:
            return
        db.update_unit(uid, ["sn", "name", "note"][col], item.text().strip())

    def batch_create(self):
        ids = db.create_units(self.pid, self.spin_count.value(),
                              self.ed_prefix.text().strip() or "SN-",
                              self.spin_start.value())
        db.sync_task_progress(self.pid)
        self.reload()
        QMessageBox.information(
            self, "完成", "已创建 %d 个个体。\n到「个体矩阵」视图给每个弹性体打点，"
            "任务进度与状态将自动计算。" % len(ids))

    def add_one(self):
        sn = self.ed_sn.text().strip()
        if not sn:
            QMessageBox.warning(self, "提示", "请填写 SN 编号")
            return
        db.add_unit(self.pid, sn, self.ed_uname.text().strip())
        db.sync_task_progress(self.pid)
        self.ed_sn.clear()
        self.ed_uname.clear()
        self.reload()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.tbl_units.selectedIndexes()},
                      reverse=True)
        if not rows:
            QMessageBox.information(self, "提示", "请先选中要删除的行")
            return
        uids = [self.tbl_units.item(r, 0).data(Qt.UserRole)[0] for r in rows]
        ret = QMessageBox.question(
            self, "确认删除", "删除选中的 %d 个个体及其全部矩阵状态？" % len(uids))
        if ret == QMessageBox.Yes:
            for uid in uids:
                db.delete_unit(uid)
            db.sync_task_progress(self.pid)
            self.reload()


class StageManageDialog(QDialog):
    """项目阶段管理：新增 / 重命名（同步任务） / 删除（须为空） / 上下移排序。

    顺序 = 任务树、流程图、报告里的分组顺序。新阶段一律加在**末尾**
    （先建的在前），再靠「上移 / 下移」——按钮和右键菜单都能调。
    """

    def __init__(self, parent, pid, project_name):
        super().__init__(parent)
        self.pid = pid
        self.setWindowTitle("阶段管理 — %s" % project_name)
        self.resize(380, 440)
        lay = QVBoxLayout(self)
        tip = QLabel("阶段决定任务树 / 流程图的分组与顺序；重命名会同步该阶段下所有任务。\n"
                     "新阶段加在末尾，选中后点「上移 / 下移」或直接右键调整顺序。")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.list = QListWidget()
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list, 1)
        r = QHBoxLayout()
        for text, slot in (("新增", self.add_stage), ("重命名", self.rename_stage),
                           ("删除", self.delete_stage)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            r.addWidget(b)
        b_up = QPushButton("上移")
        b_up.clicked.connect(lambda: self.move_stage(-1))
        b_down = QPushButton("下移")
        b_down.clicked.connect(lambda: self.move_stage(1))
        r.addWidget(b_up)
        r.addWidget(b_down)
        wrap = QWidget()
        wrap.setLayout(r)
        lay.addWidget(wrap)
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
        self.reload()

    def _menu(self, pos):
        """右键菜单：与下面那排按钮等价，顺手就能调顺序。"""
        it = self.list.itemAt(pos)
        if it is not None:
            self.list.setCurrentItem(it)
        has = it is not None
        m = QMenu(self)
        a_up = m.addAction("上移")
        a_up.triggered.connect(lambda: self.move_stage(-1))
        a_down = m.addAction("下移")
        a_down.triggered.connect(lambda: self.move_stage(1))
        m.addSeparator()
        a_ren = m.addAction("重命名…")
        a_ren.triggered.connect(self.rename_stage)
        a_del = m.addAction("删除")
        a_del.triggered.connect(self.delete_stage)
        m.addSeparator()
        a_add = m.addAction("新增阶段…")
        a_add.triggered.connect(self.add_stage)
        for a in (a_up, a_down, a_ren, a_del):
            a.setEnabled(has)
        m.exec(self.list.viewport().mapToGlobal(pos))

    def stages(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def reload(self):
        self.list.clear()
        self.list.addItems(db.list_stages(self.pid))

    def add_stage(self):
        name, ok = QInputDialog.getText(self, "新增阶段", "阶段名称：")
        if not (ok and name.strip()):
            return
        name = name.strip()
        if name in self.stages():
            QMessageBox.information(self, "提示", "该阶段已存在")
            return
        db.add_stage(self.pid, name)
        self.reload()

    def rename_stage(self):
        cur = self.list.currentItem()
        if not cur:
            QMessageBox.information(self, "提示", "请先选中一个阶段")
            return
        old = cur.text()
        new, ok = QInputDialog.getText(self, "重命名阶段", "新名称：", text=old)
        if not (ok and new.strip()) or new.strip() == old:
            return
        new = new.strip()
        if new in self.stages():
            QMessageBox.warning(self, "提示", "已存在同名阶段")
            return
        db.rename_stage(self.pid, old, new)
        self.reload()

    def delete_stage(self):
        cur = self.list.currentItem()
        if not cur:
            QMessageBox.information(self, "提示", "请先选中一个阶段")
            return
        name = cur.text()
        n = sum(1 for t in db.list_tasks(self.pid)
                if (t["stage"] or "未分组") == name)
        if n:
            QMessageBox.warning(
                self, "不能删除",
                "阶段「%s」下还有 %d 个任务。\n请先把任务移到其他阶段（任务详情里改）。"
                % (name, n))
            return
        if QMessageBox.question(self, "确认删除",
                                "删除阶段「%s」？" % name) == QMessageBox.Yes:
            db.delete_stage(self.pid, name)
            self.reload()

    def move_stage(self, delta):
        row = self.list.currentRow()
        stages = self.stages()
        if row < 0 or not (0 <= row + delta < len(stages)):
            return
        stages[row], stages[row + delta] = stages[row + delta], stages[row]
        db.set_stage_order(self.pid, stages)
        self.reload()
        self.list.setCurrentRow(row + delta)


class TemplateManageDialog(QDialog):
    """自定义模板管理：当前项目存为模板 / 新建空模板 / 重命名 / 删除。

    模板保存在本机数据库，「新建项目」时可直接选用；
    文件 → 导出全部数据 会把模板一并带走。
    """

    def __init__(self, parent, project=None, project_tasks=None):
        super().__init__(parent)
        self.project = project
        self.project_tasks = project_tasks or []
        self.setWindowTitle("模板管理")
        self.resize(500, 480)
        lay = QVBoxLayout(self)
        tip = QLabel("保存后，在「新建项目」的流程模板里即可选用。")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        self.list = QListWidget()
        self.list.itemSelectionChanged.connect(self._preview)
        lay.addWidget(self.list, 1)
        lay.addWidget(QLabel("预览："))
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(160)
        lay.addWidget(self.preview)
        r = QHBoxLayout()
        b_save = QPushButton("当前项目存为模板…")
        b_save.clicked.connect(self.save_from_project)
        b_new = QPushButton("新建空模板…")
        b_new.clicked.connect(self.new_blank)
        b_ren = QPushButton("重命名…")
        b_ren.clicked.connect(self.rename_template)
        b_del = QPushButton("删除")
        b_del.clicked.connect(self.delete_template)
        for b in (b_save, b_new, b_ren, b_del):
            r.addWidget(b)
        wrap = QWidget()
        wrap.setLayout(r)
        lay.addWidget(wrap)
        btn = QPushButton("关闭")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)
        self._by_id = {}
        self.reload()

    def reload(self):
        self._by_id = {}
        self.list.clear()
        for t in db.list_templates():
            self._by_id[t["id"]] = t
            self.list.addItem("%s（%d 项任务）" % (t["name"], len(t["tasks"])))

    def _current(self):
        it = self.list.currentItem()
        return self._by_id.get(it.data(Qt.UserRole)) if it else None

    def _preview(self):
        t = self._current()
        if not t:
            self.preview.clear()
            return
        lines = []
        cur_stage = None
        for k in t["tasks"]:
            s = k.get("stage") or "未分组"
            if s != cur_stage:
                lines.append("【%s】" % s)
                cur_stage = s
            dep = ("  ← " + "、".join(k["deps"])) if k.get("deps") else ""
            owner = (" — " + k["owner"]) if k.get("owner") else ""
            lines.append("  · %s%s%s" % (k.get("name", ""), owner, dep))
        self.preview.setPlainText("\n".join(lines) or "（空模板，无任务）")

    def save_from_project(self):
        if not self.project:
            QMessageBox.information(self, "提示", "当前没有打开项目")
            return
        if not self.project_tasks:
            QMessageBox.information(
                self, "提示", "当前项目没有任务，可以直接「新建空模板」。")
            return
        name, ok = QInputDialog.getText(self, "保存为模板", "模板名称：",
                                        text=self.project["name"])
        if not (ok and name.strip()):
            return
        by_id = {t["id"]: t for t in self.project_tasks}
        tasks = []
        for t in self.project_tasks:
            deps = [by_id[d]["name"] for d in db.task_dep_ids(t)
                    if d in by_id and d != t["id"]]
            tasks.append({"stage": t["stage"], "name": t["name"],
                         "owner": t["owner"] or "", "note": t["note"] or "",
                         "deps": deps})
        db.save_template(name.strip(), tasks)
        self.reload()

    def new_blank(self):
        name, ok = QInputDialog.getText(self, "新建空模板", "模板名称：")
        if not (ok and name.strip()):
            return
        db.save_template(name.strip(), [])
        self.reload()

    def rename_template(self):
        t = self._current()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一个模板")
            return
        name, ok = QInputDialog.getText(self, "重命名模板", "新名称：",
                                        text=t["name"])
        if not (ok and name.strip()) or name.strip() == t["name"]:
            return
        db.rename_template(t["id"], name.strip())
        self.reload()

    def delete_template(self):
        t = self._current()
        if not t:
            QMessageBox.information(self, "提示", "请先选中一个模板")
            return
        if QMessageBox.question(
                self, "确认删除", "删除模板「%s」？\n（不影响已用它创建的项目）"
                % t["name"]) == QMessageBox.Yes:
            db.delete_template(t["id"])
            self.reload()


class HelpDialog(QDialog):
    """使用说明（帮助 → 使用说明，F1）。

    界面上不再挂常驻操作提示文字，所有"怎么用"的说明集中到这里：
    左侧选分类，右侧逐条列该分类的说明；顶部可按关键字过滤分类。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        self.resize(900, 640)
        self.setSizeGripEnabled(True)
        box = QVBoxLayout(self)

        top = QHBoxLayout()
        title = QLabel("使用说明 —— 左侧选分类，右侧逐条看该分类的用法")
        title.setStyleSheet("font-size:13px; font-weight:bold;")
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(QLabel("筛选"))
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("关键字，如：缩放 / 双击 / 备份")
        self.ed_find.setFixedWidth(220)
        self.ed_find.setClearButtonEnabled(True)
        self.ed_find.textChanged.connect(self._filter)
        top.addWidget(self.ed_find)
        box.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.lst = QListWidget()
        self.lst.setMinimumWidth(170)
        self.lst.setMaximumWidth(280)
        self.lst.currentRowChanged.connect(self._show_section)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        split.addWidget(self.lst)
        split.addWidget(self.browser)
        split.setSizes([190, 700])
        box.addWidget(split, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("关闭")
        btn.setDefault(True)
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        box.addLayout(row)

        self._sections = list(help_text.HELP_SECTIONS)
        self._reload_list()

    # ---------- 分类列表 ----------

    def _reload_list(self, keep_title: str | None = None):
        self.lst.blockSignals(True)
        self.lst.clear()
        for t, _items in self._sections:
            self.lst.addItem(t)
        self.lst.blockSignals(False)
        row = 0
        if keep_title:
            for i, (t, _items) in enumerate(self._sections):
                if t == keep_title:
                    row = i
                    break
        if self._sections:
            self.lst.setCurrentRow(row)
            self._show_section(row)
        else:
            self.browser.setHtml(
                "<p style='color:#7A7F85;margin:16px;'>没有匹配的分类，"
                "换个关键字试试（如：缩放、双击、备份、升级）。</p>")

    def _filter(self, text: str = ""):
        kw = str(text or "").strip().lower()
        if not kw:
            self._sections = list(help_text.HELP_SECTIONS)
        else:
            hit = []
            for t, items in help_text.HELP_SECTIONS:
                blob = (t + "\n" + "\n".join(items)).lower()
                if kw in blob:
                    hit.append((t, items))
            self._sections = hit
        self._reload_list()

    def _show_section(self, row: int):
        if row < 0 or row >= len(self._sections):
            return
        title, items = self._sections[row]
        self.browser.setHtml(help_text.render_html(title, items))
        self.browser.verticalScrollBar().setValue(0)


class ViewSettingsDialog(QDialog):
    """显示设置：流程图选中任务后的视觉表现方式 + 个体矩阵冻结列数。

    - 是否淡化无关任务与连线（默认开，便于突出当前链路）
    - 连线高亮范围：紧邻一环 / 整条链 / 不高亮
    - 个体矩阵冻结最左侧 N 列（0~5，只能取连续前缀）
    """

    def __init__(self, parent=None, opts=None):
        super().__init__(parent)
        self.setWindowTitle("显示设置")
        self.setMinimumWidth(420)
        opts = dict(VIEW_DEFAULTS if opts is None else opts)
        box = QVBoxLayout(self)

        gb_flow = QGroupBox("流程视图")
        form = QFormLayout(gb_flow)
        self.chk_dim = QCheckBox("选中任务后，淡化无关任务与连线")
        self.chk_dim.setChecked(bool(opts.get("dim_unrelated", True)))
        form.addRow(self.chk_dim)

        self.cmb_scope = QComboBox()
        for key, label in ARROW_SCOPE_LABELS:
            self.cmb_scope.addItem(label, key)
        idx = self.cmb_scope.findData(opts.get("arrow_scope", "adjacent"))
        self.cmb_scope.setCurrentIndex(idx if idx >= 0 else 0)
        form.addRow("连线高亮范围", self.cmb_scope)

        tip = QLabel("■ 蓝 = 上游（前置）　■ 橙 = 下游（后续）\n"
                     "「紧邻一环」只画与选中任务直接相连的连线，视觉最清爽；\n"
                     "「整条传递链」会把前后所有间接依赖一并高亮。")
        tip.setStyleSheet("color:#7A7F85;")
        form.addRow("", tip)
        box.addWidget(gb_flow)

        gb_matrix = QGroupBox("个体矩阵")
        form_m = QFormLayout(gb_matrix)
        self.spin_freeze = QSpinBox()
        self.spin_freeze.setRange(0, FROZEN_COL_MAX)
        self.spin_freeze.setSuffix(" 列")
        self.spin_freeze.setValue(int(opts.get("matrix_freeze_cols",
                                               VIEW_DEFAULTS["matrix_freeze_cols"])))
        form_m.addRow("左右滚动时冻结最左侧", self.spin_freeze)
        tip_m = QLabel("只能冻结最前面【连续】的 N 列（0 = 不冻结）：\n"
                       "个体SN / 名称 / 进度 / 当前停在 / 受阻·返工。\n"
                       "任务多的时候往右滚，这几列钉住不动，一眼就知道\n"
                       "这一行是第几个个体、正停在哪个任务上。\n"
                       "没法只冻结第 1、3、5 列 —— 矩阵的列号本身带含义\n"
                       "（前面是汇总列，之后每列一个任务）。")
        tip_m.setWordWrap(True)
        tip_m.setStyleSheet("color:#7A7F85;")
        form_m.addRow("", tip_m)
        box.addWidget(gb_matrix)

        row = QHBoxLayout()
        btn_reset = QPushButton("恢复默认")
        btn_reset.clicked.connect(self._reset)
        row.addWidget(btn_reset)
        row.addStretch(1)
        btn_ok = QPushButton("确定")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        box.addLayout(row)

    def _reset(self):
        self.chk_dim.setChecked(VIEW_DEFAULTS["dim_unrelated"])
        idx = self.cmb_scope.findData(VIEW_DEFAULTS["arrow_scope"])
        self.cmb_scope.setCurrentIndex(idx if idx >= 0 else 0)
        self.spin_freeze.setValue(VIEW_DEFAULTS["matrix_freeze_cols"])

    def values(self) -> dict:
        return {
            "dim_unrelated": self.chk_dim.isChecked(),
            "arrow_scope": self.cmb_scope.currentData(),
            "matrix_freeze_cols": int(self.spin_freeze.value()),
        }


class FilePickerDialog(QDialog):
    """关联文件/文件夹多于一个时，弹出让你挑一个打开（双击即打开）。"""

    def __init__(self, paths_list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择要打开的文件/文件夹")
        self.setMinimumWidth(520)
        box = QVBoxLayout(self)
        box.addWidget(QLabel("该任务关联了 %d 项，请选择要打开的一项："
                             % len(paths_list)))
        self.list = QListWidget()
        for p in paths_list:
            it = QListWidgetItem(describe_link(p))
            it.setData(Qt.UserRole, p)
            it.setToolTip(p)
            if not os.path.exists(p):
                it.setForeground(QBrush(QColor("#C04B4B")))
                it.setToolTip("%s\n（路径不存在）" % p)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        box.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_open = QPushButton("打开")
        btn_open.setDefault(True)
        btn_open.clicked.connect(self.accept)
        btn_cancel = QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        row.addWidget(btn_open)
        row.addWidget(btn_cancel)
        box.addLayout(row)

    def selected_path(self) -> str:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else ""


class DownComboBox(QComboBox):
    """始终向下弹出的下拉框：可见行数限制 10，超出出竖向滚动条。

    Qt 默认按屏幕剩余空间自动决定弹出方向，项目多、窗口位置偏下时
    会向上翻；这里强制把弹出容器挪到控件正下方，并显式限高。
    """

    MAX_VISIBLE = 10

    def showPopup(self):
        super().showPopup()
        view = self.view()
        popup = view.parentWidget()          # QComboBoxPrivateContainer
        if popup is None or popup is self:
            return
        # 限制可见行数：超出 10 行时收缩弹层高度，滚动条自动出现
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        if self.count() > self.MAX_VISIBLE:
            row_h = view.sizeHintForRow(0)
            if row_h and row_h > 0:
                chrome = max(popup.height() - view.height(), 0)  # 容器边框占位
                max_h = row_h * self.MAX_VISIBLE + chrome + 4
                popup.setMaximumHeight(max_h)
                if popup.height() > max_h:
                    popup.resize(popup.width(), max_h)
        # 强制向下：把弹出容器定位到本控件正下方
        popup.move(self.mapToGlobal(self.rect().bottomLeft()))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("产研项目进度管理 · 六维力传感器")
        self.resize(1500, 900)
        self.project_id = None
        self.project = None
        self.tasks = []
        self.selected_tid = None
        self._loading = False
        self._refreshing = False   # 刷新期间禁止信号重入（防止树重建时二次 clear 崩溃）
        self.view_settings = load_view_settings()   # 流程图显示方式（设置菜单里可改）

        self._build_menus()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()
        self.refresh_projects()
        if paths.MIGRATION_MESSAGE:
            self.sb.showMessage(paths.MIGRATION_MESSAGE, 20000)

    # ---------------- 界面构建 ----------------

    def _build_menus(self):
        m_file = self.menuBar().addMenu("文件(&F)")
        act_new = QAction("新建项目...", self)
        act_new.setShortcut("Ctrl+N")
        act_new.triggered.connect(self.new_project)
        act_prev = QAction("上一个项目", self)
        act_prev.setShortcut("Ctrl+PgUp")
        act_prev.triggered.connect(lambda: self._cycle_project(-1))
        act_next = QAction("下一个项目", self)
        act_next.setShortcut("Ctrl+PgDown")
        act_next.triggered.connect(lambda: self._cycle_project(1))
        act_export = QAction("导出全部数据...", self)
        act_export.setShortcut("Ctrl+E")
        act_export.triggered.connect(self.export_data)
        act_import = QAction("导入数据...", self)
        act_import.triggered.connect(self.import_data)
        act_migrate = QAction("迁移旧数据（选含 projects.db 的目录）...", self)
        act_migrate.triggered.connect(self.migrate_old_data)
        act_tpl = QAction("模板管理...", self)
        act_tpl.triggered.connect(self.manage_templates)
        act_open_data = QAction("打开数据目录", self)
        act_open_data.triggered.connect(self.open_data_dir)
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.close)
        m_file.addActions([act_new, act_prev, act_next, act_export, act_import,
                           act_migrate, act_tpl, act_open_data, act_quit])

        m_task = self.menuBar().addMenu("任务(&T)")
        self.act_add = QAction("添加任务", self)
        self.act_add.setShortcut("Ctrl+T")
        self.act_add.triggered.connect(self.add_task)
        self.act_units = QAction("批次个体管理...", self)
        self.act_units.setShortcut("Ctrl+U")
        self.act_units.triggered.connect(self.manage_units)
        self.act_stages = QAction("阶段管理...", self)
        self.act_stages.setShortcut("Ctrl+G")
        self.act_stages.triggered.connect(self.stage_manage)
        self.act_del = QAction("删除当前任务", self)
        self.act_del.triggered.connect(self.delete_task)
        m_task.addActions([self.act_add, self.act_units, self.act_stages,
                           self.act_del])

        m_rep = self.menuBar().addMenu("报告(&R)")
        act_html = QAction("导出进度报告 (HTML)...", self)
        act_html.setShortcut("Ctrl+R")
        act_html.triggered.connect(self.export_html)
        act_xlsx = QAction("导出进度报告 (Excel)...", self)
        act_xlsx.triggered.connect(self.export_excel)
        m_rep.addActions([act_html, act_xlsx])

        m_set = self.menuBar().addMenu("设置(&S)")
        act_view = QAction("显示设置...", self)
        act_view.setShortcut("Ctrl+,")
        act_view.triggered.connect(self.view_settings_dialog)
        m_set.addAction(act_view)

        m_help = self.menuBar().addMenu("帮助(&H)")
        act_help = QAction("使用说明", self)
        act_help.setShortcut("F1")
        act_help.setStatusTip("分类显示所有操作方法")
        act_help.triggered.connect(self.show_help)
        act_about = QAction("关于 / 数据位置...", self)
        act_about.triggered.connect(self.show_about)
        m_help.addActions([act_help, act_about])

    def _build_toolbar(self):
        tb = QToolBar("主工具栏")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        # 项目快速切换下拉框（强制向下弹出，最多 10 行，超出滚动）
        lbl = QLabel("项目：")
        self.cmb_project = DownComboBox()
        self.cmb_project.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cmb_project.setMinimumWidth(240)
        self.cmb_project.setMaxVisibleItems(DownComboBox.MAX_VISIBLE)
        self.cmb_project.currentIndexChanged.connect(self.on_project_combo)
        tb.addWidget(lbl)
        tb.addWidget(self.cmb_project)
        tb.addSeparator()
        act_new = QAction("新建项目", self)
        act_new.triggered.connect(self.new_project)
        self.act_add_tb = QAction("添加任务", self)
        self.act_add_tb.triggered.connect(self.add_task)
        self.act_units_tb = QAction("批次个体", self)
        self.act_units_tb.triggered.connect(self.manage_units)
        self.act_tpl_tb = QAction("模板管理", self)
        self.act_tpl_tb.triggered.connect(self.manage_templates)
        act_rep = QAction("导出报告", self)
        act_rep.triggered.connect(self.export_html)
        act_open = QAction("打开文件", self)
        act_open.triggered.connect(self.open_file_link)
        tb.addActions([act_new, self.act_add_tb, self.act_units_tb,
                       self.act_tpl_tb, act_rep, act_open])
        self.addToolBar(tb)

    def _build_central(self):
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # ---- 左侧：任务树（项目切换统一走工具栏下拉框）----
        left = QGroupBox("任务（按阶段）")
        vl2 = QVBoxLayout(left)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemSelectionChanged.connect(self.on_tree_selected)
        # 阶段抬头（顶层项）右键 → 上移/下移/重命名/删除，不用再开 Ctrl+G 对话框
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_stage_menu)
        vl2.addWidget(self.tree)

        # ---- 中央：四视图 ----
        self.tabs = QTabWidget()
        self.flow_view = ZoomableView()
        self.flow_view.setScene(QGraphicsScene())
        self.flow_view.setRenderHint(QPainter.Antialiasing)
        # 点流程图空白处（非节点）取消选中
        self.flow_view.set_blank_click_callback(self.clear_task_selection)
        self.gantt_view = ZoomableView()
        self.gantt_view.setScene(QGraphicsScene())
        self.gantt_view.setRenderHint(QPainter.Antialiasing)
        self.tbl = QTableWidget()
        self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.itemSelectionChanged.connect(self.on_table_selected)
        # 个体矩阵：行=个体，列=任务；双击循环状态，右键选全部状态。
        # 支持像 Excel 一样多选：Ctrl+左键 点选不连续格子、按住左键拖出矩形块、
        # Shift+左键 选范围 —— 然后右键一次性把状态刷给所有选中的格子。
        self.matrix = QTableWidget()
        self.matrix.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.matrix.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.matrix.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.matrix.verticalHeader().setVisible(False)
        self.matrix.setContextMenuPolicy(Qt.CustomContextMenu)
        self.matrix.customContextMenuRequested.connect(self._matrix_menu)
        self.matrix.cellDoubleClicked.connect(self._matrix_cell_double)
        # 选中态由 MatrixCellDelegate 描框（默认高亮是实心蓝，会把状态色盖掉）
        self.matrix.setItemDelegate(MatrixCellDelegate(self.matrix))
        # 冻结列层：横向滚动时最左侧 N 列（个体SN/名称/进度/当前停在/受阻返工）
        # 钉住不动 —— Qt 没有原生冻结列，靠叠一层共用 model/选区的视图实现。
        # 默认 5 列，可在 设置 → 显示设置 里改（0 ~ 5，只能取连续前缀）。
        self.matrix_frozen = FrozenColumnsView(self.matrix)
        self.matrix_frozen.hide()
        self.matrix.horizontalHeader().sectionResized.connect(
            lambda *_: self._sync_matrix_frozen())
        self.matrix.verticalScrollBar().valueChanged.connect(
            self._matrix_vertical_scrolled)
        # 矩阵尺寸变化（拖分隔条/缩放窗口）→ 重排冻结列层
        self.matrix.installEventFilter(self)
        self.tabs.addTab(self.flow_view, "流程视图")
        self.tabs.addTab(self.gantt_view, "甘特视图")
        self.tabs.addTab(self.tbl, "任务表格")
        self.tabs.addTab(self.matrix, "个体矩阵")

        # ---- 右侧：详情面板 ----
        self.detail = self._build_detail()

        splitter.addWidget(left)
        splitter.addWidget(self.tabs)
        splitter.addWidget(self.detail)
        splitter.setSizes([260, 800, 420])

    def _build_detail(self):
        box = QGroupBox("任务详情")
        form = QFormLayout(box)
        self.ed_name = QLineEdit()
        # 注意：编辑框失焦类信号只做"静默写库"，不重建视图——
        # 否则点树/表格的瞬间视图被重建，正在接收点击的控件被销毁，
        # 点击会被吞掉（左树/表格点选失效的根源）。
        self.ed_name.editingFinished.connect(self._flush_detail_edits)
        self.ed_owner = QLineEdit()
        self.ed_owner.editingFinished.connect(self._flush_detail_edits)
        self.cmb_stage = QComboBox()
        self.cmb_stage.setEditable(True)
        self.cmb_stage.currentIndexChanged.connect(self.save_detail)
        self.cmb_status = QComboBox()
        for k, v in STATUS_NAMES.items():
            self.cmb_status.addItem(v, k)
        self.cmb_status.currentIndexChanged.connect(self.save_detail)
        self.spin_prog = QSpinBox()
        self.spin_prog.setRange(0, 100)
        self.spin_prog.setSuffix("%")
        self.spin_prog.editingFinished.connect(self._flush_detail_edits)
        self.dt_ps = NullableDateEdit()
        self.dt_pe = NullableDateEdit()
        self.dt_as = NullableDateEdit()
        self.dt_ae = NullableDateEdit()
        # 计划天数：填天数自动算结束日期，改结束日期自动回算天数（不落库，见 PlanDurationLink）
        self.spin_days = QSpinBox()
        # 实时保存：日期一改即落库（日历弹层内操作，无吞点击风险，可全量刷新）
        for w in (self.dt_as, self.dt_ae):
            w.edit.dateChanged.connect(self.save_detail)
        # 计划开始/结束/天数 三者联动，联动完再统一保存（只写库里真正变了的字段）
        self.plan_link = PlanDurationLink(
            self.dt_ps, self.dt_pe, self.spin_days, on_change=self.save_detail)
        self.list_deps = QListWidget()
        self.list_deps.setSelectionMode(QAbstractItemView.MultiSelection)
        self.ed_note = QTextEdit()
        self.ed_note.setFixedHeight(52)
        self.ed_note.viewport().installEventFilter(self)
        # 关联文件/文件夹：可多项——不同文件夹里的文件、同一文件夹里的部分文件
        # 都能各自挂上；双击某项直接打开，多于一项时点「打开」会弹出选择窗。
        self.list_files = QListWidget()
        self.list_files.setFixedHeight(78)
        self.list_files.setToolTip("关联的本地文件/文件夹，可多项；双击可直接打开")
        self.list_files.itemDoubleClicked.connect(lambda _it: self.open_file_link())
        btn_add_f = QPushButton("添加文件")
        btn_add_f.clicked.connect(lambda _c=False: self.add_file_links(False))
        btn_add_d = QPushButton("添加文件夹")
        btn_add_d.clicked.connect(lambda _c=False: self.add_file_links(True))
        btn_open = QPushButton("打开")
        btn_open.clicked.connect(self.open_file_link)
        btn_del = QPushButton("移除")
        btn_del.clicked.connect(self.remove_file_link)
        btn_save = QPushButton("保存全部修改")
        btn_save.clicked.connect(self.save_detail)
        btn_save.setStyleSheet("font-weight:600;")

        form.addRow("任务名称", self.ed_name)
        form.addRow("阶段", self.cmb_stage)
        form.addRow("负责人", self.ed_owner)
        form.addRow("状态", self.cmb_status)
        form.addRow("进度", self.spin_prog)
        form.addRow("计划开始", self.dt_ps)
        form.addRow("计划结束", self.dt_pe)
        form.addRow("计划天数", self.spin_days)
        form.addRow("实际开始", self.dt_as)
        form.addRow("实际结束", self.dt_ae)
        form.addRow("前置依赖", self.list_deps)
        form.addRow("备注", self.ed_note)
        frow = QVBoxLayout()
        frow.setContentsMargins(0, 0, 0, 0)
        frow.setSpacing(3)
        frow.addWidget(self.list_files)
        brow = QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(4)
        brow.addWidget(btn_add_f)
        brow.addWidget(btn_add_d)
        brow.addWidget(btn_open)
        brow.addWidget(btn_del)
        brow.addStretch(1)
        frow.addLayout(brow)
        wrap = QWidget()
        wrap.setLayout(frow)
        form.addRow("关联文件", wrap)
        form.addRow(btn_save)
        return box

    def _build_statusbar(self):
        self.sb = QStatusBar()
        self.setStatusBar(self.sb)

    # ---------------- 数据刷新 ----------------

    def refresh_projects(self, select_id=None):
        self._loading = True
        projects = db.list_projects()
        self.cmb_project.clear()
        for p in projects:
            prog = db.project_progress(db.list_tasks(p["id"]))
            self.cmb_project.addItem("%s（%d%%）" % (p["name"], prog), p["id"])
        self._loading = False
        if projects:
            ids = [p["id"] for p in projects]
            target = select_id
            if target is None:
                last = get_settings().value("last_project", 0, type=int)
                target = last if last in ids else projects[0]["id"]
            self.switch_project(target)
        else:
            self.project_id = None
            self.project = None
            self.tasks = []
            self.refresh_task_views()

    def switch_project(self, pid):
        """切换当前项目：工具栏下拉 + 上次记忆两处同步。"""
        if pid is None:
            return
        self.project_id = pid
        self.project = db.get_project(pid)
        self.selected_tid = None
        get_settings().setValue("last_project", pid)
        self.setWindowTitle("%s · %s" % (APP_TITLE, self.project["name"]))
        self._loading = True
        idx = self.cmb_project.findData(pid)
        if idx >= 0:
            self.cmb_project.setCurrentIndex(idx)
        self._loading = False
        self.refresh_task_views()

    def on_project_combo(self, idx):
        if self._loading:
            return
        pid = self.cmb_project.itemData(idx)
        if pid:
            self.switch_project(pid)

    def _cycle_project(self, delta):
        """快速切换上/下一个项目（Ctrl+PgUp / Ctrl+PgDown）。"""
        n = self.cmb_project.count()
        if n < 1:
            return
        cur = self.cmb_project.currentIndex()
        if cur < 0:
            cur = 0
        self.switch_project(self.cmb_project.itemData((cur + delta) % n))

    def refresh_task_views(self):
        # 刷新期间屏蔽一切信号驱动的重入：树 clear() 会发出选择变更信号，
        # 若在信号回调里再次触发刷新（如 save_detail），会在 Qt 正在销毁
        # 树节点时二次 clear，导致访问违例崩溃。
        #
        # ⚠ 每个视图单独兜异常，**绝不允许一个视图出错影响后面的步骤**：
        # 以前是顺序直调，_build_gantt 一处 TypeError 就让后面的表格/矩阵/
        # _load_detail 全部跳过 —— 详情面板于是停在旧任务上，之后每次切换任务
        # 都会把旧任务的数据写进新选中的任务（数据被逐个覆盖、不可逆）。
        # 视图可以坏，数据不能坏。
        self._refreshing = True
        try:
            view_errors = []
            if self.project_id:
                db.sync_task_progress(self.project_id)  # 个体模式自动重算
                self.tasks = db.list_tasks(self.project_id)
            else:
                self.tasks = []
            # 记住矩阵滚动位置，重建后恢复（打点时不跳）
            m_scroll = (self.matrix.verticalScrollBar().value()
                        if self.matrix.columnCount() else 0)
            for step in (self._build_tree, self._build_flow, self._build_gantt,
                         self._build_table, self._build_matrix):
                try:
                    step()
                except Exception:                       # noqa: BLE001
                    view_errors.append(step.__name__)
                    traceback.print_exc()
            self._view_errors = view_errors
            self.matrix.verticalScrollBar().setValue(m_scroll)
            try:
                self._load_detail()
            except Exception:                           # noqa: BLE001
                traceback.print_exc()
                view_errors.append("_load_detail")
            self._update_statusbar()
            has = bool(self.project_id)
            self.act_add.setEnabled(has)
            self.act_add_tb.setEnabled(has)
            if hasattr(self, "act_units"):
                self.act_units.setEnabled(has)
            if hasattr(self, "act_stages"):
                self.act_stages.setEnabled(has)
        finally:
            self._refreshing = False

    def _update_statusbar(self):
        if not self.project_id:
            self.sb.showMessage("尚未创建项目：文件 → 新建项目")
            return
        blockers = db.analyze_blockers(self.tasks)
        prog = db.project_progress(self.tasks)
        msg = ("项目：%s ｜ 任务 %d 个 ｜ 总进度 %d%% ｜ 卡点 %d 处"
               % (self.project["name"], len(self.tasks), prog, len(blockers)))
        errors = getattr(self, "_view_errors", None)
        if errors:
            msg += " ｜ ⚠ 视图异常：%s（详见 帮助 → 使用说明 / 控制台）" % "、".join(errors)
        self.sb.showMessage(msg)

    # ---------------- 树 ----------------

    @staticmethod
    def _stage_of(t):
        return t["stage"] or "未分组"

    def _all_stages(self):
        """项目阶段列表（有序）：以显式登记的阶段为准，再补任务里出现过的阶段。

        · 新建项目按模板任务里出现的顺序登记阶段；空白项目**不登记任何阶段**，
          所以空白项目的任务树一开始是空的，阶段由用户自己建 ——
          先建的排第一，后建的往后排；
        · 老项目（建库时还没有 project_stages 表）没有登记记录，就完全按任务里
          出现的顺序排。老项目的任务本来就是按内置模板顺序创建的，导出的顺序与
          内置四阶段一致，历史观感不变。
        · **刻意不再回退内置 STAGES**：任务的 stage 全是空（"未分组"）时，
          若还硬塞内置四阶段，空白项目加一个没选阶段的任务就会把
          「研发设计/制作与投板/生产装配/标定与入库」又变出来。
        """
        stages = db.list_stages(self.project_id) if self.project_id else []
        for t in self.tasks:
            s = self._stage_of(t)
            if s not in stages:
                stages.append(s)
        return stages

    def _build_tree(self):
        # 重建期间阻断信号：clear()/addChild 会发出选择变更信号，回调里
        # 读取正在销毁的节点数据会崩溃。重建完成后再恢复选中。
        explicit = set(db.list_stages(self.project_id)) if self.project_id else set()
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            for stage in self._all_stages():
                st_tasks = [t for t in self.tasks if self._stage_of(t) == stage]
                if not st_tasks and stage not in explicit:
                    continue   # 空阶段只有显式登记过才显示（供后续往里加任务）
                prog = (round(sum(int(t["progress"] or 0) for t in st_tasks)
                              / len(st_tasks)) if st_tasks else 0)
                top = QTreeWidgetItem(["%s（%d%%，%d 项）" % (stage, prog, len(st_tasks))])
                top.setFlags(Qt.ItemIsEnabled)
                top.setData(0, STAGE_ROLE, stage)   # 供右键菜单取真实阶段名
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                self.tree.addTopLevelItem(top)
                for t in st_tasks:
                    label = "[%s] %s — %s" % (STATUS_NAMES.get(t["status"], "?"),
                                               t["name"], t["owner"] or "未指派")
                    child = QTreeWidgetItem([label])
                    child.setData(0, Qt.UserRole, t["id"])
                    color = {"done": QColor("#27AE60"),
                             "doing": QColor("#2F80ED"),
                             "blocked": QColor("#EB5757"),
                             "rework": QColor("#F2994A")}.get(t["status"], QColor("#888888"))
                    child.setForeground(0, color)
                    top.addChild(child)
            self.tree.expandAll()
        finally:
            self.tree.blockSignals(False)
        # 重建后恢复原选中项
        if self.selected_tid is not None:
            self._reselect_in_tree(self.selected_tid)

    def on_tree_selected(self):
        if self._loading or self._refreshing:
            return
        items = self.tree.selectedItems()
        if items:
            tid = items[0].data(0, Qt.UserRole)
            if tid:
                # 延迟到事件循环下一拍：选择信号处理中立即重建树，
                # 会把刚点的选中条冲回旧任务（滞后一拍 bug 的根源）。
                QTimer.singleShot(0, lambda tid=tid: self.select_task(tid))

    # ---------------- 阶段顺序（任务树右键）----------------

    def _ensure_stages_registered(self):
        """老项目还没阶段记录时，按当前显示顺序补登记，之后才好调顺序。"""
        if self.project_id and not db.list_stages(self.project_id):
            for s in self._all_stages():
                db.add_stage(self.project_id, s)

    def _stage_names_ordered(self):
        self._ensure_stages_registered()
        return db.list_stages(self.project_id) if self.project_id else []

    def _tree_stage_menu(self, pos):
        """任务树里右键阶段抬头：上移 / 下移 / 重命名 / 删除 / 新建阶段。

        顺序就是任务树、流程图、报告里的分组顺序；点在任务行或空白处不弹。
        """
        if not self.project_id:
            return
        it = self.tree.itemAt(pos)
        if it is None or it.parent() is not None:
            return
        stage = it.data(0, STAGE_ROLE)
        if not stage:
            return
        names = self._stage_names_ordered()
        i = names.index(stage) if stage in names else -1
        m = QMenu(self)
        a_up = m.addAction("上移")
        a_up.triggered.connect(lambda: self.move_stage_order(stage, -1))
        a_down = m.addAction("下移")
        a_down.triggered.connect(lambda: self.move_stage_order(stage, 1))
        a_up.setEnabled(i > 0)
        a_down.setEnabled(0 <= i < len(names) - 1)
        m.addSeparator()
        a_ren = m.addAction("重命名阶段…")
        a_ren.triggered.connect(lambda: self.rename_stage_inline(stage))
        a_del = m.addAction("删除阶段")
        a_del.triggered.connect(lambda: self.delete_stage_inline(stage))
        m.addSeparator()
        a_new = m.addAction("新建阶段…")
        a_new.triggered.connect(self.add_stage_inline)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def move_stage_order(self, stage, delta):
        """阶段上/下移一格。越界就什么都不做（首行不能再上移）。"""
        names = self._stage_names_ordered()
        if stage not in names:
            return
        i = names.index(stage)
        j = i + delta
        if not (0 <= j < len(names)):
            return
        names[i], names[j] = names[j], names[i]
        db.set_stage_order(self.project_id, names)
        self.refresh_task_views()

    def add_stage_inline(self):
        """新建阶段：一律排到末尾（先建的在前，后建的在后）。"""
        name, ok = QInputDialog.getText(self, "新建阶段", "阶段名称：")
        if not (ok and name.strip()):
            return
        name = name.strip()
        if name in db.list_stages(self.project_id):
            QMessageBox.information(self, "提示", "该阶段已存在")
            return
        db.add_stage(self.project_id, name)
        self.refresh_task_views()

    def rename_stage_inline(self, old):
        """改阶段名（同步该阶段下所有任务）；refresh 会把详情面板重新装载，
        否则面板里残留的旧阶段名会在下一次保存时把旧名字写回去。"""
        new, ok = QInputDialog.getText(self, "重命名阶段", "新名称：", text=old)
        if not (ok and new.strip()) or new.strip() == old:
            return
        new = new.strip()
        if new in db.list_stages(self.project_id):
            QMessageBox.warning(self, "提示", "已存在同名阶段")
            return
        db.rename_stage(self.project_id, old, new)
        self.refresh_task_views()

    def delete_stage_inline(self, name):
        """删阶段：只删分组名，里面的任务不能跟着消失 —— 有任务就拦下。"""
        n = sum(1 for t in self.tasks if self._stage_of(t) == name)
        if n:
            QMessageBox.warning(
                self, "不能删除",
                "阶段「%s」下还有 %d 个任务。\n请先把任务移到别的阶段（任务详情里改）。"
                % (name, n))
            return
        if QMessageBox.question(self, "确认删除",
                                "删除阶段「%s」？" % name) == QMessageBox.Yes:
            db.delete_stage(self.project_id, name)
            self.refresh_task_views()

    # ---------------- 流程视图 ----------------

    def _build_flow(self):
        scene = self.flow_view.scene()
        scene.clear()
        self._flow_nodes = {}
        self._flow_keepalive = []   # 强引用：防止 PySide6 GC 把无 Python 引用的 QGraphicsItem 从 scene 中删除
        node_by_tid = {}
        self._flow_up, self._flow_down = set(), set()
        if not self.tasks:
            self._flow_keepalive.append(
                scene.addText("暂无任务").setDefaultTextColor(QColor("#999")))
            return
        # 选中后的绘制方式由「设置 → 显示设置」决定：
        #   arrow_scope = none / adjacent（默认，只画紧邻一环）/ closure（整条传递链）
        #   dim_unrelated = 是否把无关任务与连线淡化
        sel = self.selected_tid
        scope = self.view_settings.get("arrow_scope", "adjacent")
        dim = bool(self.view_settings.get("dim_unrelated", True))
        by_id = {t["id"]: t for t in self.tasks}
        if sel and sel in by_id:
            if scope == "closure":
                self._flow_up = self._upstream_closure(sel, by_id)
                self._flow_down = self._downstream_closure(sel, by_id)
            else:
                self._flow_up = {d for d in db.task_dep_ids(by_id[sel]) if d in by_id}
                self._flow_down = {t["id"] for t in self.tasks
                                   if sel in db.task_dep_ids(t)}
            self._flow_up.discard(sel)
            self._flow_down.discard(sel)
        # 整条链模式：落在链上的两端连线都要高亮
        chain_up = (self._flow_up | {sel}) if (sel and scope == "closure") else set()
        chain_down = (self._flow_down | {sel}) if (sel and scope == "closure") else set()
        node_w, node_h, gap_x = 150, 46, 26
        lane_h = 120
        font = QFont("Microsoft YaHei", 9)
        # 计算每个阶段任务数，横向排布
        all_stages = self._all_stages()
        stage_tasks = {s: [t for t in self.tasks if self._stage_of(t) == s]
                       for s in all_stages}
        total_w = 0
        for s, ts in stage_tasks.items():
            total_w = max(total_w, 90 + len(ts) * (node_w + gap_x) + 40)
        scene.setSceneRect(0, 0, max(total_w, 900), 40 + len(all_stages) * lane_h + 20)
        for si, stage in enumerate(all_stages):
            ts = stage_tasks[stage]
            if not ts:
                continue
            y0 = 40 + si * lane_h
            lane = QGraphicsRectItem(0, y0, scene.width(), lane_h - 16)
            lane.setBrush(QBrush(QColor("#F7F8FA")))
            lane.setPen(QPen(QColor("#E3E5E8"), 1))
            lane.setAcceptedMouseButtons(Qt.NoButton)   # 泳道只作背景，不抢点击
            scene.addItem(lane)
            self._flow_keepalive.append(lane)
            label = scene.addText(stage, QFont("Microsoft YaHei", 10, QFont.Bold))
            label.setDefaultTextColor(QColor("#666"))
            label.setPos(10, y0 + lane_h / 2 - 30)
            label.setAcceptedMouseButtons(Qt.NoButton)
            self._flow_keepalive.append(label)
            for j, t in enumerate(ts):
                x = 90 + j * (node_w + gap_x)
                fill, border, text_c = STATUS_STYLE.get(t["status"], STATUS_STYLE["pending"])
                fm = QFontMetrics(font)
                title = fm.elidedText(t["name"], Qt.ElideRight, node_w - 12)
                subtitle = "%s · %d%%" % (t["owner"] or "未指派",
                                          int(t["progress"] or 0))
                node = FlowNode(t["id"], self.select_task,
                                QRectF(x, y0 + 24, node_w, node_h),
                                title, subtitle, text_c)
                node.setBrush(QBrush(QColor(fill)))
                node.setPen(QPen(QColor(border), 1.4))
                if sel:
                    if t["id"] == sel:
                        node.setPen(QPen(QColor("#1F5FA8"), 2.6))   # 选中任务加粗描边
                    elif scope != "none" and t["id"] in self._flow_up:
                        node.setPen(QPen(QColor("#2F80ED"), 2.4))   # 上游（前置）
                    elif scope != "none" and t["id"] in self._flow_down:
                        node.setPen(QPen(QColor("#F2994A"), 2.4))   # 下游（后续）
                    elif dim:
                        node.setOpacity(0.3)                        # 无关任务淡化
                scene.addItem(node)
                self._flow_keepalive.append(node)
                node_by_tid[t["id"]] = node
                self._flow_nodes[t["id"]] = (x, y0 + 24, node_w, node_h)
        # 依赖箭头
        for t in self.tasks:
            tx, ty, tw, th = self._flow_nodes.get(t["id"], (0, 0, 0, 0))
            for did in db.task_dep_ids(t):
                if did not in self._flow_nodes:
                    continue
                dx, dy, dw, dh = self._flow_nodes[did]
                x1, y1 = dx + dw / 2, dy + dh / 2
                x2, y2 = tx + tw / 2, ty + th / 2
                if abs(y1 - y2) < 2:  # 同泳道：右→左
                    x1, y1, x2, y2 = dx + dw, dy + dh / 2, tx, ty + th / 2
                else:  # 跨泳道：底→顶
                    x1, y1, x2, y2 = dx + dw / 2, dy + dh, tx + tw / 2, ty
                if scope == "closure" and sel:
                    in_up = (did in chain_up) and (t["id"] in chain_up)
                    in_down = (did in chain_down) and (t["id"] in chain_down)
                else:
                    # 紧邻一环：只高亮与选中任务直接相连的线——
                    # 指向它的(蓝=前置)、从它出发的(橙=后续)
                    in_up = (scope != "none") and (did in self._flow_up) \
                        and (t["id"] == sel)
                    in_down = (scope != "none") and (did == sel) \
                        and (t["id"] in self._flow_down)
                if in_up:
                    arr = self._add_arrow(scene, x1, y1, x2, y2,
                                          color="#2F80ED", width=2.4, z=6)
                elif in_down:
                    arr = self._add_arrow(scene, x1, y1, x2, y2,
                                          color="#F2994A", width=2.4, z=6)
                else:
                    arr = self._add_arrow(scene, x1, y1, x2, y2,
                                          color="#C9CCD0" if (sel and dim) else "#A0A4A8")
                self._flow_keepalive.extend(arr)
        # 卡点标记：挂成节点子项 + 不吃鼠标，点红点等于点卡片。
        # 红点画在卡片**矩形内部**右下角：放在卡片外时那一小片不属于任何接收鼠标的
        # 图元，点它既选不中卡片也不落到卡片上，观感就是"点红点没反应 / 像点空白"。
        blockers = db.analyze_blockers(self.tasks)
        for tid in blockers:
            node = node_by_tid.get(tid)
            if node is None:
                continue
            nr = node.rect()
            # ⚠ 必须用 nr.right()/nr.bottom() 定位，不能写成 width()/height()：
            #   节点 rect 的原点是 (x, y0+24) 不是 (0,0)（卡片是按绝对坐标建出来的），
            #   以前写成 width()-12/-12 的结果是红点全跑到泳道抬头那一条带上，
            #   既不在卡片角上、点它也不落在卡片里 —— 这就是"莫名其妙的红点"。
            mark = QGraphicsEllipseItem(
                QRectF(nr.right() - 14, nr.bottom() - 14, 12, 12), node)
            mark.setPen(QPen(QColor("#EB5757")))
            mark.setBrush(QBrush(QColor("#EB5757")))
            mark.setAcceptedMouseButtons(Qt.NoButton)
            mark.setZValue(7)
        # 这里原来有一行常驻操作提示（"Ctrl+滚轮缩放 · Ctrl+0 复位 · 点空白处
        # 取消选中"）。按用户要求，软件里不再直接挂这类提示文字 ——
        # 全部操作说明集中到 帮助 → 使用说明（F1）的分类弹窗里。

    @staticmethod
    def _upstream_closure(sel, by_id):
        """选中任务的全部上游（含间接前置）。整条链模式用。"""
        out, stack = set(), [d for d in db.task_dep_ids(by_id[sel]) if d in by_id]
        while stack:
            d = stack.pop()
            if d in out or d == sel:
                continue
            out.add(d)
            stack.extend(x for x in db.task_dep_ids(by_id[d]) if x in by_id)
        return out

    @staticmethod
    def _downstream_closure(sel, by_id):
        """选中任务的全部下游（含间接后续）。整条链模式用。"""
        out, stack = set(), []
        for t in by_id.values():
            if sel in db.task_dep_ids(t):
                stack.append(t["id"])
        while stack:
            d = stack.pop()
            if d in out or d == sel:
                continue
            out.add(d)
            for t in by_id.values():
                if d in db.task_dep_ids(t):
                    stack.append(t["id"])
        return out

    @staticmethod
    def _add_arrow(scene, x1, y1, x2, y2, color="#A0A4A8", width=1.2, z=-1):
        line = QGraphicsLineItem(x1, y1, x2, y2)
        pen = QPen(QColor(color), width)
        line.setPen(pen)
        line.setZValue(z)
        line.setAcceptedMouseButtons(Qt.NoButton)
        scene.addItem(line)
        ang = math.atan2(y2 - y1, x2 - x1)
        L, Wd = 9, 5
        pts = [QPointF(x2, y2),
               QPointF(x2 - L * math.cos(ang) + Wd * math.sin(ang),
                       y2 - L * math.sin(ang) - Wd * math.cos(ang)),
               QPointF(x2 - L * math.cos(ang) - Wd * math.sin(ang),
                       y2 - L * math.sin(ang) + Wd * math.cos(ang))]
        tri = QGraphicsPolygonItem(QPolygonF(pts))
        tri.setBrush(QBrush(QColor(color)))
        tri.setPen(QPen(Qt.NoPen))
        tri.setZValue(z)
        tri.setAcceptedMouseButtons(Qt.NoButton)
        scene.addItem(tri)
        return line, tri

    # ---------------- 甘特视图 ----------------

    def _build_gantt(self):
        scene = self.gantt_view.scene()
        scene.clear()
        self._gantt_keepalive = []
        if not self.tasks:
            self._gantt_keepalive.append(
                scene.addText("暂无任务").setDefaultTextColor(QColor("#999")))
            return
        dated = [t for t in self.tasks if t["plan_start"] or t["plan_end"]]
        if not dated:
            self._gantt_keepalive.append(
                scene.addText("所有任务均未设置计划日期").setDefaultTextColor(QColor("#999")))
            return

        def pd(s):
            try:
                return QDate.fromString(s, "yyyy-MM-dd").toPython()
            except Exception:
                return None

        import datetime as _dt
        ds = [pd(t["plan_start"]) for t in dated if pd(t["plan_start"])]
        de = [pd(t["plan_end"]) for t in dated if pd(t["plan_end"])]
        today = _dt.date.today()
        dmin = min(ds + [today])
        dmax = max(de + [today])
        days = max((dmax - dmin).days, 1)
        left = 220
        day_w = max(900.0 / days, 1.5)

        def x_of(d):
            return left + (d - dmin).days * day_w

        width = left + days * day_w + 60
        height = 40 + len(dated) * 30 + 60
        scene.setSceneRect(0, 0, width, height)
        # 月刻度
        cur = dmin.replace(day=1)
        while cur <= dmax:
            nx = x_of(cur)
            if nx >= left - 1:
                tick = QGraphicsLineItem(nx, 24, nx, height - 40)
                tick.setPen(QPen(QColor("#EDEEF0"), 1))
                scene.addItem(tick)
                self._gantt_keepalive.append(tick)
                lab = scene.addText(cur.strftime("%Y-%m"), QFont("Microsoft YaHei", 8))
                lab.setDefaultTextColor(QColor("#999"))
                lab.setPos(nx + 3, 2)
                self._gantt_keepalive.append(lab)
            import calendar
            days_in_month = calendar.monthrange(cur.year, cur.month)[1]
            cur = (cur + _dt.timedelta(days=days_in_month)).replace(day=1)
        # 任务条
        font = QFont("Microsoft YaHei", 9)
        fm = QFontMetrics(font)
        for i, t in enumerate(dated):
            y = 40 + i * 30
            name = fm.elidedText(t["name"], Qt.ElideRight, 190)
            label = scene.addText(name, font)
            label.setDefaultTextColor(QColor("#444"))
            label.setPos(8, y)
            self._gantt_keepalive.append(label)
            s = pd(t["plan_start"]) or pd(t["plan_end"])
            e = pd(t["plan_end"]) or pd(t["plan_start"])
            if not (s and e):
                tag = scene.addText("日期不全", QFont("Microsoft YaHei", 8))
                tag.setDefaultTextColor(QColor("#C0392B"))
                tag.setPos(left, y + 2)
                self._gantt_keepalive.append(tag)
                continue
            if e < s:
                s, e = e, s
            x1, x2 = x_of(s), x_of(e)
            bar_w = max(x2 - x1, 6)
            fill, border, _ = STATUS_STYLE.get(t["status"], STATUS_STYLE["pending"])
            # 甘特条用 ClickRectItem（不是泳道里那种带标题的 FlowNode 卡片）：
            # FlowNode 自 V1.3.3 起必需 title/subtitle/text_color，
            # 这里以前传 3 个参数直接 TypeError —— 只要有任务设了计划日期，
            # 整个 refresh_task_views 就会在这里中断，后面的表格/矩阵/详情面板
            # 全部不刷新，详情面板停在旧任务上，随后每次点击都会把旧任务数据
            # 写进新选中的任务（数据被逐个覆盖）。别再让它退回去。
            bar = ClickRectItem(t["id"], self.select_task, QRectF(x1, y + 4, bar_w, 14))
            bar.setBrush(QBrush(QColor(fill)))
            bar.setPen(QPen(QColor(border), 1.2))
            scene.addItem(bar)
            self._gantt_keepalive.append(bar)
            prog_w = bar_w * int(t["progress"] or 0) / 100.0
            if prog_w > 0:
                prog = QGraphicsRectItem(x1, y + 4, prog_w, 14)
                prog.setBrush(QBrush(QColor("#27AE60")))
                prog.setPen(QPen(Qt.NoPen))
                prog.setAcceptedMouseButtons(Qt.NoButton)   # 覆盖条不吃点击
                scene.addItem(prog)
                self._gantt_keepalive.append(prog)
            info = "%d%% · %s" % (int(t["progress"] or 0), t["owner"] or "")
            if t["status"] == "blocked":
                info += " · 受阻"
            meta = scene.addText(info, QFont("Microsoft YaHei", 8))
            meta.setDefaultTextColor(QColor("#999"))
            meta.setPos(x1, y + 19)
            self._gantt_keepalive.append(meta)
        # 今日线
        tx = x_of(today)
        today_line = QGraphicsLineItem(tx, 24, tx, height - 40)
        today_line.setPen(QPen(QColor("#EB5757"), 1.4))
        scene.addItem(today_line)
        self._gantt_keepalive.append(today_line)
        tl = scene.addText("今天", QFont("Microsoft YaHei", 8))
        tl.setDefaultTextColor(QColor("#EB5757"))
        tl.setPos(tx + 3, 24)
        self._gantt_keepalive.append(tl)

    # ---------------- 表格视图 ----------------

    def _build_table(self):
        headers = ["阶段", "任务", "负责人", "状态", "进度", "计划开始",
                   "计划结束", "依赖", "卡点"]
        self.tbl.setColumnCount(len(headers))
        self.tbl.setHorizontalHeaderLabels(headers)
        self.tbl.setRowCount(0)
        by_id = {t["id"]: t for t in self.tasks}
        blockers = db.analyze_blockers(self.tasks)
        self.tbl.setRowCount(len(self.tasks))
        for r, t in enumerate(self.tasks):
            dep_names = []
            for did in db.task_dep_ids(t):
                d = by_id.get(did)
                if d:
                    dep_names.append(d["name"])
            vals = [t["stage"], t["name"], t["owner"] or "—",
                    STATUS_NAMES.get(t["status"], t["status"]),
                    "%d%%" % int(t["progress"] or 0),
                    fmt_date(t["plan_start"]), fmt_date(t["plan_end"]),
                    ", ".join(dep_names) or "—",
                    "；".join(blockers.get(t["id"], [])) or "—"]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(str(v))
                if c == 3:
                    _, border, _ = STATUS_STYLE.get(t["status"], STATUS_STYLE["pending"])
                    item.setForeground(QColor(border))
                if c == 8 and v != "—":
                    item.setForeground(QColor("#EB5757"))
                item.setData(Qt.UserRole, t["id"])
                self.tbl.setItem(r, c, item)
        self.tbl.resizeColumnsToContents()
        self.tbl.horizontalHeader().setStretchLastSection(True)
        # 重建后恢复当前任务所在行的高亮（以前只有树有，表格点选后高亮会丢）
        if self.selected_tid is not None:
            for r in range(self.tbl.rowCount()):
                it = self.tbl.item(r, 0)
                if it and it.data(Qt.UserRole) == self.selected_tid:
                    self.tbl.blockSignals(True)
                    self.tbl.selectRow(r)
                    self.tbl.blockSignals(False)
                    break

    def on_table_selected(self):
        if self._loading or self._refreshing:
            return
        items = self.tbl.selectedItems()
        if items:
            tid = items[0].data(Qt.UserRole)
            if tid:
                QTimer.singleShot(0, lambda tid=tid: self.select_task(tid))

    # ---------------- 个体矩阵视图 ----------------

    _STATUS_SHORT = {"doing": "进", "done": "完", "blocked": "阻", "rework": "返"}

    def _paint_matrix_cell(self, item, st):
        fill, border, _ = STATUS_STYLE.get(st, STATUS_STYLE["pending"])
        item.setBackground(QColor(fill))
        item.setForeground(QColor(border))
        item.setText(self._STATUS_SHORT.get(st, ""))
        # 状态标记居中显示（"进/完/阻/返"只有一个字，靠左很难看）
        item.setTextAlignment(Qt.AlignCenter)
        st_name = STATUS_NAMES.get(st, st)
        item.setToolTip(
            "%s\n状态：%s\n单点=选中，Ctrl+左键=加选，按住左键拖=框选一片，\n"
            "双击=循环（未开始→进行中→完成），右键=给整片选区改状态"
            % (item.data(Qt.UserRole + 1) or "", st_name))

    def _build_matrix(self):
        m = self.matrix
        m.clear()
        if not self.project_id:
            units = []
        else:
            units = db.list_units(self.project_id)
        if not units:
            m.setColumnCount(1)
            m.setRowCount(1)
            m.setHorizontalHeaderLabels(["提示"])
            it = QTableWidgetItem("本批次尚未创建个体")
            it.setFlags(Qt.ItemIsEnabled)
            m.setItem(0, 0, it)
            self._sync_matrix_frozen()
            return
        tasks = self.tasks
        statuses = db.get_unit_statuses(self.project_id)
        stats = {s["unit"]["id"]: s
                 for s in db.unit_stats(self.project_id, tasks, statuses)}
        headers = list(MATRIX_SUMMARY_HEADERS)
        task_headers = ["%d.%s" % (i + 1, t["name"]) for i, t in enumerate(tasks)]
        m.setColumnCount(5 + len(tasks))
        m.setHorizontalHeaderLabels(headers + task_headers)
        for i, t in enumerate(tasks):
            hit = m.horizontalHeaderItem(5 + i)
            hit.setToolTip("[%s] %s" % (t["stage"], t["name"]))
            hit.setTextAlignment(Qt.AlignCenter)
        m.setRowCount(len(units))
        for r, u in enumerate(units):
            s = stats.get(u["id"])
            row_vals = [u["sn"], u["name"],
                        "%d%%（%d/%d）" % (s["progress"], s["done"], len(tasks)),
                        s["current"], "；".join(s["issues"]) or "—"]
            for c, v in enumerate(row_vals):
                it = QTableWidgetItem(str(v))
                it.setFlags(Qt.ItemIsEnabled)
                it.setData(Qt.UserRole, (u["id"], None))
                if c == 2:
                    it.setForeground(QColor("#1E7E45" if s["progress"] == 100
                                            else "#185FA5"))
                m.setItem(r, c, it)
            for i, t in enumerate(tasks):
                st = statuses.get(u["id"], {}).get(t["id"], "pending")
                it = QTableWidgetItem()
                # 状态格可选中（多选打点靠它）；前 5 列汇总格保持不可选，
                # 这样 Ctrl/拖动多选只会命中真正的状态格
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setData(Qt.UserRole, (u["id"], t["id"]))
                it.setData(Qt.UserRole + 1, t["name"])
                self._paint_matrix_cell(it, st)
                m.setItem(r, 5 + i, it)
        for c, w in enumerate([90, 80, 90, 150, 170]):
            m.setColumnWidth(c, w)
        # 任务列宽按表头文字实测宽度定，保证 "1.弹性体仿真与制作" 能完整显示；
        # 46px 那种固定窄列会把表头截成 "1.弹性…"，用户根本对不上列。
        # 超长任务名（>320px）才截断，完整名字在表头 tooltip 里。
        hfm = QFontMetrics(m.horizontalHeader().font())
        for i, text in enumerate(task_headers):
            w = hfm.horizontalAdvance(text) + 20    # 20 = 列内边距 + 排序/边线余量
            m.setColumnWidth(5 + i, max(56, min(w, 320)))
        self._sync_matrix_frozen()

    def _sync_matrix_frozen(self):
        """要求冻结列层按主表当前几何重新对齐；不需要冻结时直接隐藏。

        什么时候要冻结：**真的有横向溢出**（列宽之和 > 视口宽）且列数 > 冻结列数。
        没有溢出时冻结毫无意义，干脆不显示 —— 免得平白遮住主表左侧
        （虽然有溢出时它盖的就是同一批格子，但少一层控件少一类怪问题）。
        """
        fz = getattr(self, "matrix_frozen", None)
        if fz is None:
            return
        m = self.matrix
        try:
            m.columnCount()
        except RuntimeError:      # 关窗时残留的延迟同步：底层 C++ 对象已销毁
            return
        try:
            cols = int(self.view_settings.get("matrix_freeze_cols",
                                              FROZEN_COL_MAX))
        except (TypeError, ValueError):
            cols = FROZEN_COL_MAX
        cols = max(0, min(cols, FROZEN_COL_MAX))
        total_w = sum(m.columnWidth(c) for c in range(m.columnCount()))
        if (cols <= 0 or m.rowCount() <= 0 or m.columnCount() <= cols
                or total_w <= m.viewport().width()):
            fz.hide()
            return
        fz.sync(cols)
        fz.show()
        fz.raise_()
        fz.verticalScrollBar().setValue(m.verticalScrollBar().value())

    def _matrix_vertical_scrolled(self, value):
        """主表纵向滚动 → 冻结层跟着滚（两边行高一致，数值直接对拷）。"""
        fz = getattr(self, "matrix_frozen", None)
        if fz is not None and fz.isVisible():
            fz.verticalScrollBar().setValue(value)

    def _matrix_selected_cells(self):
        """矩阵当前选中的状态格 [(uid, tid)]。

        只认任务状态列（列号 ≥ 5）：前 5 列是汇总信息，本来就不可选，
        这里再过滤一次是防御性的（万一以后有人给汇总格开了可选）。
        """
        out, seen = [], set()
        for mi in self.matrix.selectedIndexes():
            if mi.column() < 5:
                continue
            it = self.matrix.item(mi.row(), mi.column())
            data = it.data(Qt.UserRole) if it else None
            if not data or data[1] is None:
                continue
            key = (data[0], data[1])
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _matrix_select_rows(self, row):
        """把某一行的所有状态格设为当前选区（右键菜单「选中本行」用）。"""
        m = self.matrix
        sel = QItemSelection()
        for c in range(5, m.columnCount()):
            if m.item(row, c) is not None:
                sel.select(m.model().index(row, c), m.model().index(row, c))
        m.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _matrix_select_col(self, col):
        """把某一列（某个任务）的所有状态格设为当前选区。"""
        m = self.matrix
        sel = QItemSelection()
        for r in range(m.rowCount()):
            if m.item(r, col) is not None:
                sel.select(m.model().index(r, col), m.model().index(r, col))
        m.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _matrix_cell_double(self, row, col):
        item = self.matrix.item(row, col)
        data = item.data(Qt.UserRole) if item else None
        if not data or data[1] is None:
            return
        uid, tid = data
        st = db.get_unit_statuses(self.project_id).get(uid, {}).get(tid, "pending")
        nxt = {"pending": "doing", "doing": "done", "done": "pending"}.get(st, "pending")
        self._apply_unit_status(uid, tid, nxt)

    def _matrix_menu(self, pos):
        item = self.matrix.itemAt(pos)
        data = item.data(Qt.UserRole) if item else None
        if not data or data[1] is None:
            return
        uid, tid = data
        row, col = item.row(), item.column()
        # 右键落在已有选区里 → 对整片选区生效（Excel 手感）；
        # 落在选区外 → 只改右键的那一格
        cells = self._matrix_selected_cells()
        if (uid, tid) not in cells:
            cells = [(uid, tid)]
            self._matrix_select_single_cell(item)   # 让用户看清"要改的是这一格"
        menu = QMenu(self.matrix)
        head = menu.addAction("标记 %d 个格子为：" % len(cells)
                              if len(cells) > 1 else "标记该格子为：")
        head.setEnabled(False)
        for key, name in STATUS_NAMES.items():
            menu.addAction("    %s" % name).setData(("status", key))
        menu.addSeparator()
        act_row = menu.addAction("选中该个体（本行全部任务）")
        act_row.setData(("row", row))
        act_col = menu.addAction("选中该任务（全部个体本列）")
        act_col.setData(("col", col))
        chosen = menu.exec(self.matrix.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        kind, payload = chosen.data()
        if kind == "row":
            self._matrix_select_rows(payload)
        elif kind == "col":
            self._matrix_select_col(payload)
        else:
            self._apply_unit_statuses([(u, t, payload) for u, t in cells])

    def _matrix_select_single_cell(self, item):
        """只选中右键的那一格（不动数据）——与 Excel 右键选区外一致。"""
        m = self.matrix
        sel = QItemSelection()
        sel.select(m.model().index(item.row(), item.column()),
                   m.model().index(item.row(), item.column()))
        m.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)

    def _apply_unit_status(self, uid, tid, st):
        self._apply_unit_statuses([(uid, tid, st)])

    def _apply_unit_statuses(self, changes):
        """批量写矩阵 → 自动重算任务进度 → 就地刷新（不重建矩阵，保住选区/滚动）。

        `changes` = [(uid, tid, status), ...]：右键多选后一次改一片，
        以前是逐格写库 + 每格全量刷新，选 20 格要点 20 次。
        """
        changes = [(u, t, s) for (u, t, s) in changes if t is not None]
        if not changes:
            return
        for uid, tid, st in changes:
            db.set_unit_status(uid, tid, st)
        db.sync_task_progress(self.project_id)
        self.tasks = db.list_tasks(self.project_id)
        self._build_tree()
        self._build_flow()
        self._build_gantt()
        self._build_table()
        self._load_detail()
        self._update_statusbar()
        # 就地更新矩阵：受影响的单元格 + 行汇总 + 工具栏进度
        order = {t["id"]: i for i, t in enumerate(self.tasks)}
        by_unit = {}
        for uid, tid, st in changes:
            by_unit.setdefault(uid, {})[tid] = st
        for r in range(self.matrix.rowCount()):
            it0 = self.matrix.item(r, 0)
            d0 = it0.data(Qt.UserRole) if it0 else None
            urow = d0[0] if d0 else None
            if urow is None:
                continue
            for tid, st in (by_unit.get(urow) or {}).items():
                tidx = order.get(tid)
                if tidx is None:
                    continue
                it = self.matrix.item(r, 5 + tidx)
                if it and it.data(Qt.UserRole) == (urow, tid):
                    self._paint_matrix_cell(it, st)
        statuses = db.get_unit_statuses(self.project_id)
        stats = {s["unit"]["id"]: s
                 for s in db.unit_stats(self.project_id, self.tasks, statuses)}
        for r in range(self.matrix.rowCount()):
            it0 = self.matrix.item(r, 0)
            d0 = it0.data(Qt.UserRole) if it0 else None
            s = stats.get(d0[0]) if d0 else None
            if s is None:
                continue
            for c, text in ((2, "%d%%（%d/%d）" % (s["progress"], s["done"],
                                                  len(self.tasks))),
                            (3, s["current"]),
                            (4, "；".join(s["issues"]) or "—")):
                it = self.matrix.item(r, c)
                if it is not None:
                    it.setText(text)
        idx = self.cmb_project.findData(self.project_id)
        if idx >= 0 and self.project:
            self.cmb_project.setItemText(
                idx, "%s（%d%%）" % (self.project["name"],
                                     db.project_progress(self.tasks)))

    # ---------------- 详情面板 ----------------

    def clear_task_selection(self):
        """取消选中（点流程图空白处触发）：详情面板置空、树/表格高亮清除。"""
        if self._loading or self._refreshing:
            return
        if self.selected_tid is None:
            return
        self._flush_detail_edits()   # 先把未落盘的编辑写库，再清选中
        self.selected_tid = None
        self.refresh_task_views()

    def select_task(self, tid):
        if self._loading or self._refreshing:
            return
        if tid == self.selected_tid:
            return
        if self.selected_tid is not None:
            # 切换前把上一个任务未落盘的编辑先写库（只写库不重建视图，
            # 保证正在进行的点击不被吞掉）
            self._flush_detail_edits()
        self.selected_tid = tid
        # 全量刷新：_build_tree / _build_table 末尾会按"新"任务恢复高亮，
        # 选中条与右侧详情从此同步（修复滞后一拍）。
        self.refresh_task_views()

    def eventFilter(self, obj, ev):
        # 分隔条拖动 / 窗口缩放会改变矩阵视口宽度 → 冻结列层要重新对齐。
        # 注意：矩阵比详情面板先建好，这里必须容忍 ed_note 还不存在。
        if obj is getattr(self, "matrix", None) and ev.type() == QEvent.Resize:
            self._schedule_matrix_frozen_sync()
        # 备注框失焦即保存（QTextEdit 无 editingFinished 信号）
        elif (obj is getattr(getattr(self, "ed_note", None), "viewport", lambda: None)()
              and ev.type() == QEvent.FocusOut):
            if not self._loading and self.selected_tid:
                self._flush_detail_edits()
        return super().eventFilter(obj, ev)

    def _schedule_matrix_frozen_sync(self):
        """延迟一拍重排冻结列层；同一拍内的多次请求只做一次。

        不能在事件过滤器里当场同步：那时目标控件还没处理 Resize，
        viewport 的尺寸仍是旧的（拖分隔条会一路错位）。
        """
        if getattr(self, "_frozen_sync_queued", False):
            return
        self._frozen_sync_queued = True

        def _run():
            self._frozen_sync_queued = False
            self._sync_matrix_frozen()

        QTimer.singleShot(0, _run)

    def _current_task(self):
        for t in self.tasks:
            if t["id"] == self.selected_tid:
                return t
        return None

    def _load_detail(self):
        """把"当前选中任务"装载进详情面板。

        `_detail_tid` 记录面板里现在装的是哪个任务 —— 这是数据安全的关键标记：
        `_flush_detail_edits` 只允许在面板确实属于当前选中任务时才写库，
        否则就会把上一个任务的数据写到新选中的任务上。
        try/finally 保证 `_loading` 一定复位：它卡在 True 会让整个界面失去响应。
        """
        self._loading = True
        self.plan_link.set_active(False)   # 装载期间关掉日期联动，免得算串
        try:
            t = self._current_task()
            self._detail_tid = t["id"] if t else None
            self.detail.setEnabled(bool(t))
            # 有个体的批次：状态/进度由矩阵自动算，详情面板只读这两项
            has_units = bool(self.project_id and db.list_units(self.project_id))
            self.cmb_status.setEnabled(not has_units)
            self.spin_prog.setEnabled(not has_units)
            tip = "该批次有个体：状态与进度由「个体矩阵」自动计算" if has_units else ""
            self.cmb_status.setToolTip(tip)
            self.spin_prog.setToolTip(tip)
            # 阶段下拉按当前项目的阶段列表刷新（含新加的自定义阶段）
            self.cmb_stage.blockSignals(True)
            self.cmb_stage.clear()
            self.cmb_stage.addItems(self._all_stages())
            self.cmb_stage.blockSignals(False)
            if not t:
                self.ed_name.clear()
                self.ed_owner.clear()
                self.spin_prog.setValue(0)
                self.ed_note.clear()
                self.list_files.clear()
                self.list_deps.clear()
                # 日期控件有状态，必须显式复位，否则空面板上挂着上一个任务的日期
                for w in (self.dt_ps, self.dt_pe, self.dt_as, self.dt_ae):
                    w.clear_value()
                self.plan_link.clear()
                return
            self.ed_name.setText(t["name"])
            self.ed_owner.setText(t["owner"])
            idx = self.cmb_stage.findText(t["stage"])
            if idx < 0:
                self.cmb_stage.setEditText(t["stage"])
            else:
                self.cmb_stage.setCurrentIndex(idx)
            self.cmb_status.setCurrentIndex(
                list(STATUS_NAMES.keys()).index(t["status"])
                if t["status"] in STATUS_NAMES else 0)
            self.spin_prog.setValue(int(t["progress"] or 0))
            self.dt_ps.set_value(t["plan_start"])
            self.dt_pe.set_value(t["plan_end"])
            self.dt_as.set_value(t["actual_start"])
            self.dt_ae.set_value(t["actual_end"])
            self.plan_link.sync_from_dates()   # 天数由两个日期反推（不落库）
            self.ed_note.setPlainText(t["note"] or "")
            self._fill_file_list(db.parse_links(t["file_link"]))
            # 依赖列表
            self.list_deps.clear()
            sel_ids = set(db.task_dep_ids(t))
            for other in self.tasks:
                if other["id"] == t["id"]:
                    continue
                self.list_deps.addItem(other["name"])
                it = self.list_deps.item(self.list_deps.count() - 1)
                it.setData(Qt.UserRole, other["id"])
                if other["id"] in sel_ids:
                    it.setSelected(True)
        finally:
            self._loading = False
            self.plan_link.set_active(True)   # 装载完毕，恢复日期联动
        self._loading = False

    def _flush_detail_edits(self):
        """把详情面板当前内容写库（不重建视图）。

        供三类时机调用：编辑框失焦、切换任务前、备注失焦。
        只写库不刷新，避免重建控件时吞掉正在进行中的鼠标点击；
        有字段变化时，再延迟一拍做一次界面刷新（标签会更新，
        高亮按当前选中任务恢复）。
        """
        if self._loading or self._refreshing or not self.selected_tid:
            return
        t = self._current_task()
        if t is None:
            return
        tid = self.selected_tid
        # 面板里必须确实装着"当前选中任务"的数据，否则一律不写库。
        # 面板一旦与选中项脱节（视图构建异常、嵌套事件循环打乱顺序等），
        # 盲写就会把面板里那个任务的数据刷到当前任务上，而且会连环覆盖 ——
        # 宁可不保存（状态栏提示），也绝不写错任务。
        if getattr(self, "_detail_tid", None) != tid:
            self.sb.showMessage(
                "⚠ 详情面板与当前任务不同步，本次未保存（避免写错任务）", 8000)
            return
        deps = [str(self.list_deps.item(i).data(Qt.UserRole))
                for i in range(self.list_deps.count())
                if self.list_deps.item(i).isSelected()]
        updates = {
            "name": self.ed_name.text().strip() or "未命名任务",
            "stage": self.cmb_stage.currentText().strip(),
            "owner": self.ed_owner.text().strip(),
            "status": self.cmb_status.currentData(),
            "progress": self.spin_prog.value(),
            "plan_start": self.dt_ps.value(),
            "plan_end": self.dt_pe.value(),
            "actual_start": self.dt_as.value(),
            "actual_end": self.dt_ae.value(),
            "depends_on": ",".join(deps),
            "note": self.ed_note.toPlainText().strip(),
            "file_link": db.join_links(self._file_paths()),
        }
        # 只写真正变化的字段：既避免每次点选都全字段写库，
        # 也让"串写"这类问题无处藏身（没变化就是没变化）
        changed = {k: v for k, v in updates.items()
                   if str(t.get(k) or "") != str(v)}
        for k, v in changed.items():
            db.update_task(tid, k, v)
        # 新输入的阶段自动登记进项目阶段列表（下次可直接下拉选到）
        stage = updates["stage"]
        if stage and stage != "未分组" and stage not in db.list_stages(self.project_id):
            db.add_stage(self.project_id, stage)
        if changed:
            QTimer.singleShot(0, self._deferred_visual_refresh)

    def _deferred_visual_refresh(self):
        """静默写库后延迟刷新：等当前点击/信号处理完再重建视图。"""
        if self._loading or self._refreshing:
            return
        self.refresh_task_views()

    def save_detail(self):
        # 守卫不可省：_load_detail 装载控件会触发 currentIndexChanged，
        # 没有守卫会递归刷新直至栈溢出
        if self._loading or self._refreshing or not self.selected_tid:
            return
        self._flush_detail_edits()
        self.refresh_task_views()

    def _reselect_in_tree(self, tid):
        self._loading = True
        try:
            for i in range(self.tree.topLevelItemCount()):
                top = self.tree.topLevelItem(i)
                for j in range(top.childCount()):
                    child = top.child(j)
                    if child.data(0, Qt.UserRole) == tid:
                        self.tree.clearSelection()
                        self.tree.setCurrentItem(child)
                        child.setSelected(True)
                        self.tree.scrollToItem(child)
        finally:
            self._loading = False

    # ---------------- 动作 ----------------

    def new_project(self):
        dlg = NewProjectDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        name, code, tpl_key, unit_count, sn_prefix = dlg.values()
        if not name:
            QMessageBox.warning(self, "提示", "请填写项目名称")
            return
        if db.project_name_exists(name):
            # 对话框已挡一道，这里兜底（例如后续再接其它入口）
            QMessageBox.warning(self, "项目已存在",
                                "已存在同名项目「%s」，请换个名称。" % name)
            return
        if isinstance(tpl_key, tuple) and tpl_key and tpl_key[0] == "custom":
            # 自定义模板（模板管理里保存的）
            ct = db.get_template(tpl_key[1])
            tpl = {"name": ct["name"], "tasks": ct["tasks"]} if ct \
                else {"name": "空白项目", "tasks": []}
        else:
            tpl = TEMPLATES.get(tpl_key, TEMPLATES["blank"])
        try:
            pid = db.create_project(name, code, tpl["name"], tpl["tasks"])
        except ValueError as e:      # 名称为空 / 重名等
            QMessageBox.warning(self, "无法创建项目", str(e))
            return
        if unit_count > 0:
            db.create_units(pid, unit_count, sn_prefix)
            db.sync_task_progress(pid)
        self.refresh_projects(select_id=pid)
        msg = "项目已创建，共 %d 个任务。" % len(tpl["tasks"])
        if unit_count > 0:
            msg += "\n\n已创建 %d 个批次个体（%s01 起）。\n到「个体矩阵」视图打点，" \
                   "任务进度自动计算。" % (unit_count, sn_prefix)
        else:
            msg += "\n\n如需按个体跟踪（如本批 10 个弹性体），按 Ctrl+U 批次个体管理。"
        QMessageBox.information(self, "完成", msg)

    def manage_units(self):
        if not self.project_id:
            return
        dlg = UnitManageDialog(self, self.project_id, self.project["name"])
        dlg.exec()
        self.refresh_projects(select_id=self.project_id)

    def migrate_old_data(self):
        """把老版本/另一台电脑的 projects.db 迁进当前数据目录。

        安装版与开发版的数据目录不同（%LOCALAPPDATA% vs 项目根），
        这里给出一个显式入口；替换前会自动备份现有数据库。
        """
        src_dir = QFileDialog.getExistingDirectory(
            self, "选择旧数据目录（里面应有 projects.db）", "")
        if not src_dir:
            return
        src = os.path.join(src_dir, "projects.db")
        if not os.path.isfile(src):
            QMessageBox.warning(self, "未找到数据文件",
                                "该目录下没有 projects.db：\n%s" % src_dir)
            return
        if QMessageBox.question(
                self, "确认迁移",
                "将用所选数据库覆盖当前数据：\n%s\n\n"
                "当前数据库会先自动备份到 data\\backups\\，不会丢失。\n\n继续？"
                % src) != QMessageBox.Yes:
            return
        try:
            backup = db.install_db_from(src)
        except Exception as e:  # noqa: BLE001 - 任何异常都提示用户，不崩
            QMessageBox.warning(self, "迁移失败", str(e))
            return
        self.refresh_projects()
        QMessageBox.information(
            self, "迁移完成",
            "已切换到所选数据库，当前 %d 个项目。\n\n原数据库备份：\n%s"
            % (self.cmb_project.count(), backup or "（原无数据库，未产生备份）"))

    def open_data_dir(self):
        """打开用户数据目录（安装版在 %LOCALAPPDATA%\\ProjectManager\\data）。"""
        try:
            paths.ensure_dirs()
            os.startfile(str(paths.DATA_DIR))
        except Exception as e:  # noqa: BLE001 - 打不开也要把路径告诉用户
            QMessageBox.information(self, "数据目录",
                                    "%s\n\n（无法自动打开：%s）" % (paths.DATA_DIR, e))

    def view_settings_dialog(self):
        """显示设置：流程图选中后的淡化 / 连线高亮范围（确定后立即生效并记住）。"""
        dlg = ViewSettingsDialog(self, self.view_settings)
        if dlg.exec() != QDialog.Accepted:
            return
        self.view_settings = dlg.values()
        save_view_settings(self.view_settings)
        self.refresh_task_views()
        self.sb.showMessage("显示设置已保存", 4000)

    def show_help(self):
        """帮助 → 使用说明（F1）：分类逐条解释所有操作方法。

        界面上不再直接显示任何常驻操作提示，全部集中到本弹窗。
        """
        HelpDialog(self).exec()

    def show_about(self):
        lines = ["%s" % APP_TITLE,
                 "版本标识：v%s（%s）"
                 % (paths.BUILD_VERSION or APP_VERSION_LABEL,
                    getattr(paths, "VARIANT_LABEL", ""))]
        if getattr(paths, "IS_DEBUG_BUILD", False):
            lines.append("调试序号：debug.%s（与正式版并存安装，数据各自独立）"
                         % getattr(paths, "DEBUG_NUMBER", ""))
        lines += ["",
                  "数据目录：%s" % paths.DATA_DIR,
                  "数据库：%s" % paths.DB_PATH,
                  "",
                  "升级安装新版本只覆盖程序文件（{app}\\），"
                  "以上数据目录不会被安装/升级/卸载改动，数据不会丢。"]
        QMessageBox.information(self, "关于", "\n".join(lines))

    def export_data(self):
        default = "ProjectManager_全部数据_%s.json" % db.today_str().replace("-", "")
        path, _ = QFileDialog.getSaveFileName(self, "导出全部数据", default,
                                              "JSON 数据 (*.json)")
        if not path:
            return
        data = db.export_all(path)
        QMessageBox.information(
            self, "导出完成",
            "已导出 %d 个项目（含任务/依赖/个体/矩阵状态）：\n%s\n\n"
            "把此文件拷到另一台电脑，菜单 文件 → 导入数据 即可恢复。" %
            (len(data["projects"]), path))

    def import_data(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入数据", "",
                                              "JSON 数据 (*.json)")
        if not path:
            return
        try:
            added, skipped = db.import_all(path)
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "导入失败", "文件格式不正确：%s" % e)
            return
        self.refresh_projects()
        QMessageBox.information(
            self, "导入完成",
            "新增 %d 个项目，跳过 %d 个同名项目。\n"
            "（同名同编号项目不覆盖；需要重导请先删除本地同名项目）"
            % (added, skipped))

    def add_task(self):
        if not self.project_id:
            return
        cur = self._current_task()
        if cur:
            default_stage = cur["stage"]
        elif self.tasks:
            default_stage = self.tasks[-1]["stage"]
        else:
            # 空白项目（一条任务都没有）：阶段列表也是空的，
            # 不预选任何内置阶段，让用户自己建/自己填
            _proj_stages = self._all_stages()
            default_stage = _proj_stages[-1] if _proj_stages else ""
        dlg = NewTaskDialog(self, self.tasks, default_stage,
                            stages=self._all_stages())
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        if not v["name"]:
            QMessageBox.warning(self, "提示", "请填写任务名称")
            return
        tid = db.add_task(self.project_id, {
            "stage": v["stage"], "name": v["name"], "owner": v["owner"],
            "order_idx": len(self.tasks), "plan_start": v["plan_start"],
            "plan_end": v["plan_end"], "depends_on": ",".join(v["pred_ids"]),
        })
        # 新写的阶段（下拉直接输入或「新建…」按钮）登记进项目阶段列表
        if (v["stage"] and v["stage"] != "未分组"
                and v["stage"] not in db.list_stages(self.project_id)):
            db.add_stage(self.project_id, v["stage"])
        # 反向依赖：把本任务写入所选"后续任务"的依赖里
        by_id = {t["id"]: t for t in self.tasks}
        for sid in v["succ_ids"]:
            st = by_id.get(int(sid))
            if st:
                ids = set(db.task_dep_ids(st)) | {tid}
                db.update_task(int(sid), "depends_on",
                               ",".join(str(i) for i in ids))
        self.select_task(tid)   # 内部会刷新全部视图并高亮新任务

    def stage_manage(self):
        """阶段管理：新增/重命名（同步任务）/删除（须为空）/上下移排序。"""
        if not self.project_id:
            return
        # 老项目没有阶段记录：先按当前显示顺序补登记（空白项目 = 无阶段，保持空）
        self._ensure_stages_registered()
        dlg = StageManageDialog(self, self.project_id, self.project["name"])
        dlg.exec()
        self.refresh_task_views()

    def manage_templates(self):
        """模板管理：当前项目存为模板 / 新建空模板 / 重命名 / 删除。"""
        dlg = TemplateManageDialog(self, self.project, self.tasks)
        dlg.exec()

    def delete_task(self):
        t = self._current_task()
        if not t:
            QMessageBox.information(self, "提示", "请先选择一个任务")
            return
        ret = QMessageBox.question(
            self, "确认删除", "确定删除任务「%s」？" % t["name"])
        if ret == QMessageBox.Yes:
            db.delete_task(t["id"])
            self.selected_tid = None
            self.refresh_task_views()

    def _prompt_text(self, title, label):
        return QInputDialog.getText(self, title, label)

    def _file_paths(self) -> list:
        """关联文件列表里的全部路径。"""
        return [self.list_files.item(i).data(Qt.UserRole)
                for i in range(self.list_files.count())]

    def _fill_file_list(self, links) -> None:
        """按路径列表填充关联文件列表（不存在的项标红提示）。"""
        self.list_files.clear()
        for p in (links or []):
            it = QListWidgetItem(describe_link(p))
            it.setData(Qt.UserRole, p)
            if os.path.exists(p):
                it.setToolTip(p)
            else:
                it.setForeground(QBrush(QColor("#C04B4B")))
                it.setToolTip("%s\n（路径不存在）" % p)
            self.list_files.addItem(it)

    def add_file_links(self, want_dir=False):
        """添加关联项：文件可一次多选（不同文件夹也行），也可整体挂一个文件夹。"""
        if not self.selected_tid:
            return
        if want_dir:
            d = QFileDialog.getExistingDirectory(self, "选择要关联的文件夹")
            picked = [d] if d else []
        else:
            picked, _ = QFileDialog.getOpenFileNames(
                self, "选择要关联的文件（可按住 Ctrl / Shift 多选）")
        if not picked:
            return
        merged = self._file_paths()
        for p in picked:
            if p not in merged:
                merged.append(p)
        self._fill_file_list(merged)
        self._flush_detail_edits()   # 立即落库，不用再点保存

    def remove_file_link(self):
        """移除列表中选中的关联项（不删磁盘上的文件）。"""
        if not self.selected_tid:
            return
        rows = sorted((self.list_files.row(it)
                       for it in self.list_files.selectedItems()), reverse=True)
        if not rows:
            QMessageBox.information(self, "提示", "请先在列表里选中要移除的项")
            return
        for r in rows:
            self.list_files.takeItem(r)
        self._flush_detail_edits()

    def open_file_link(self):
        """打开关联项：只有一项直接打开；多项弹出让用户挑一个。"""
        t = self._current_task()
        links = db.parse_links(t.get("file_link") if t else "")
        if not links:
            QMessageBox.information(self, "提示", "当前任务未设置关联文件")
            return
        if len(links) == 1:
            self._open_path(links[0])
            return
        dlg = FilePickerDialog(links, self)
        if dlg.exec() == QDialog.Accepted:
            p = dlg.selected_path()
            if p:
                self._open_path(p)

    def _open_path(self, path):
        if os.path.exists(path):
            os.startfile(path)
        else:
            QMessageBox.warning(self, "提示", "路径不存在：%s" % path)

    def export_html(self):
        if not self.project:
            QMessageBox.information(self, "提示", "请先创建项目")
            return
        path = report.export_html(self.project, self.tasks)
        report.open_file(path)
        self.sb.showMessage("报告已导出：%s" % path, 10000)

    def export_excel(self):
        if not self.project:
            QMessageBox.information(self, "提示", "请先创建项目")
            return
        path = report.export_excel(self.project, self.tasks)
        if path:
            self.sb.showMessage("报告已导出：%s" % path, 10000)
        else:
            QMessageBox.warning(self, "提示", "未安装 openpyxl，无法导出 Excel。\n"
                                                  "pip install openpyxl 后重试。")
