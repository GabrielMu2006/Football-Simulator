APP_STYLE = """
QWidget {
    background: #0b1220;
    color: #e8eef7;
    font-family: "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
    font-size: 14px;
}
QMainWindow, QFrame, QSplitter, QStackedWidget, QTabWidget::pane {
    background: #0b1220;
}
QLabel#titleLabel {
    font-size: 22px;
    font-weight: 900;
    color: #f8fbff;
}
QLabel#subtitleLabel {
    color: #91a8c5;
    font-size: 13px;
}
QFrame#navPanel, QFrame#statusPanel, QFrame#contentPanel, QFrame#cardFrame {
    background: #111c2e;
    border: 1px solid #263b5b;
    border-radius: 10px;
}
QListWidget#navList {
    background: transparent;
    border: none;
    outline: none;
    padding: 8px;
}
QListWidget#navList::item {
    padding: 11px 13px;
    margin: 3px 4px;
    border-radius: 9px;
    color: #cbd7e6;
}
QListWidget#navList::item:hover {
    background: #1b304d;
    color: #f8fbff;
}
QListWidget#navList::item:selected {
    background: #1167d8;
    color: #ffffff;
}
QPushButton {
    background: #1167d8;
    color: white;
    border: none;
    border-radius: 9px;
    padding: 10px 16px;
    font-weight: 800;
}
QPushButton:hover {
    background: #2784ff;
}
QPushButton:disabled {
    background: #314863;
    color: #9ab0c8;
}
QComboBox, QLineEdit, QPlainTextEdit, QTextEdit, QTableWidget, QListWidget, QTabWidget {
    background: #0f1b2d;
    border: 1px solid #223653;
    border-radius: 9px;
    padding: 6px;
}
QHeaderView::section {
    background: #172942;
    color: #dfe9f7;
    border: none;
    padding: 9px;
    font-weight: 900;
}
QTableWidget {
    gridline-color: #23344d;
    selection-background-color: #155bb6;
    selection-color: white;
    alternate-background-color: #13243a;
}
QTableWidget::item {
    padding: 10px 8px;
}
QTableWidget::item:hover {
    background: #1d3555;
}
QTabBar::tab {
    background: #122238;
    color: #c7d5e4;
    padding: 10px 16px;
    border-top-left-radius: 9px;
    border-top-right-radius: 9px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background: #1167d8;
    color: white;
}
QScrollBar:vertical {
    background: #0f1b2d;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2e4f76;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QLabel#badgeLabel {
    border-radius: 8px;
    padding: 4px 8px;
    font-weight: 800;
    color: #06111f;
}
QPushButton#reviewDecisionApprove, QPushButton#reviewDecisionReject {
    min-height: 34px;
    padding: 8px 18px;
    border-radius: 8px;
}
QPushButton#reviewDecisionApprove:checked {
    background: #16a34a;
    color: #ffffff;
}
QPushButton#reviewDecisionReject:checked {
    background: #dc2626;
    color: #ffffff;
}
/* 危险操作（删除、批量拒绝）：红色填充层级，与主操作蓝色区分。 */
QPushButton#savesDeleteButton,
QPushButton#transferRejectAllButton {
    background: #7f1d1d;
    color: #ffffff;
}
QPushButton#savesDeleteButton:hover,
QPushButton#transferRejectAllButton:hover {
    background: #b91c1c;
    color: #ffffff;
}
/* 行内决策按钮：未选中为描边态，选中为填充色，避免“两个都一样蓝”。 */
QPushButton#transferApproveButton,
QPushButton#reviewApproveButton {
    background: transparent;
    border: 1px solid #2b4a6f;
    color: #cbd7e6;
}
QPushButton#transferApproveButton:hover,
QPushButton#reviewApproveButton:hover {
    background: #122238;
    color: #ffffff;
}
QPushButton#transferApproveButton:checked,
QPushButton#reviewApproveButton:checked {
    background: #16a34a;
    border: 1px solid #16a34a;
    color: #ffffff;
}
QPushButton#transferRejectButton {
    background: transparent;
    border: 1px solid #7f1d1d;
    color: #fca5a5;
}
QPushButton#transferRejectButton:hover {
    background: #3f1d1d;
    color: #ffffff;
}
QPushButton#transferRejectButton:checked {
    background: #dc2626;
    border: 1px solid #dc2626;
    color: #ffffff;
}
QPushButton#reviewKeepButton {
    background: transparent;
    border: 1px solid #475569;
    color: #cbd5e1;
}
QPushButton#reviewKeepButton:hover {
    background: #293548;
    color: #ffffff;
}
QPushButton#reviewKeepButton:checked {
    background: #475569;
    border: 1px solid #475569;
    color: #ffffff;
}
"""
