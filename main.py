# -*- coding: utf-8 -*-
"""产研项目进度管理工具（单机版）—— 入口。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app import db, paths
from app.ui_main import MainWindow

APP_QSS = """
QMainWindow { background: #F2F3F5; }
QGroupBox { font-weight: 600; }
QToolBar { background: #FAFAFA; border-bottom: 1px solid #E0E0E0; }
QTabWidget::pane { border: 1px solid #DDE0E4; }
QTreeWidget::item:selected, QListWidget::item:selected, QTableWidget::item:selected {
    background: #DCEBFD; color: #185FA5;
}
"""


def main():
    db.init_db()
    # 调试版（带控制台的构建）与开发模式下启用崩溃转储：
    # 正式（--windowed 无控制台）构建中 sys.stderr 为 None，自动跳过。
    import faulthandler
    if sys.stderr is not None:
        faulthandler.enable()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    icon_path = paths.resource_path("app.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
