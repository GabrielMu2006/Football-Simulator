"""全局 QSS：颜色只引用 tokens，组件通过 objectName / 动态属性获得状态。"""

from __future__ import annotations

from football_simulator.ui import tokens as t

APP_STYLE = f"""
QMainWindow, QWidget#AppRoot, QStackedWidget, QWidget#PageBody, QWidget#PageRoot {{
    background: {t.BG};
    color: {t.TEXT};
}}
QWidget {{
    color: {t.TEXT};
    font-family: {t.FONT_FAMILY};
    font-size: {t.FONT_BODY}px;
}}
QLabel {{
    background: transparent;
}}
QLabel#Eyebrow {{
    color: {t.MUTED};
    font-size: {t.FONT_META}px;
    font-weight: 650;
    letter-spacing: 1.1px;
}}
QLabel#Achievement {{
    color: {t.ACCENT};
    font-size: {t.FONT_META}px;
    font-weight: 700;
    letter-spacing: 1.0px;
}}
QLabel#PageTitle {{
    color: {t.TEXT_STRONG};
    font-size: {t.FONT_H1}px;
    font-weight: 700;
    letter-spacing: -0.3px;
}}
QLabel#PageSubtitle {{
    color: {t.MUTED};
    font-size: {t.FONT_BODY}px;
}}
QLabel#SectionTitle {{
    color: {t.TEXT_STRONG};
    font-size: {t.FONT_H2}px;
    font-weight: 700;
}}
QLabel#CardTitle {{
    color: {t.TEXT_STRONG};
    font-size: {t.FONT_H3}px;
    font-weight: 650;
}}
QLabel#Meta {{
    color: {t.MUTED};
    font-size: {t.FONT_SMALL}px;
}}
QLabel#Strong {{
    color: {t.TEXT_STRONG};
    font-weight: 600;
}}
QLabel#Score {{
    color: {t.FOCUS_TEXT};
    font-family: {t.FONT_NUMERIC};
    font-size: {t.FONT_SCORE}px;
    font-weight: 800;
    letter-spacing: -2px;
}}
QLabel#ScoreSmall {{
    color: {t.FOCUS_TEXT};
    font-family: {t.FONT_NUMERIC};
    font-size: {t.FONT_SCORE_SMALL}px;
    font-weight: 800;
}}
QLabel#FocusTeam {{
    color: {t.FOCUS_TEXT};
    font-size: 20px;
    font-weight: 800;
}}
QLabel#FocusMeta {{
    color: {t.FOCUS_MUTED};
    font-size: {t.FONT_SMALL}px;
}}
QAbstractButton#CompactMatchRow {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: {t.RADIUS_SM}px;
    text-align: left;
}}
QAbstractButton#CompactMatchRow:hover {{ background: {t.CARD_ALT}; }}
QAbstractButton#CompactMatchRow:focus {{ border: 1px solid {t.ACCENT}; }}
QAbstractButton#CompactMatchRow:pressed {{ background: {t.TABLE_HOVER}; }}
QLabel#CompactScore {{
    font-family: {t.FONT_NUMERIC};
    font-size: 16px;
    font-weight: 800;
    color: {t.TEXT_STRONG};
}}
QLabel#FactText {{ color: {t.TEXT}; font-size: {t.FONT_BODY}px; }}
QLabel#RoleMetricValue {{
    font-family: {t.FONT_NUMERIC};
    font-size: 22px;
    font-weight: 700;
    color: {t.TEXT_STRONG};
}}
QLabel#RoleMetricLabel {{ color: {t.MUTED}; font-size: {t.FONT_SMALL}px; }}
QLabel#HeroStat {{
    font-family: {t.FONT_NUMERIC};
    font-size: 26px;
    font-weight: 700;
    color: {t.TEXT_STRONG};
}}
QFrame#Card {{
    background: {t.CARD};
    border: 1px solid {t.BORDER};
    border-radius: {t.RADIUS_MD}px;
}}
QFrame#CardAlt {{
    background: {t.CARD_ALT};
    border: 1px solid {t.BORDER};
    border-radius: {t.RADIUS_MD}px;
}}
QFrame#FocusPanel {{
    background: {t.FOCUS};
    border: 1px solid {t.FOCUS_ALT};
    border-radius: {t.RADIUS_LG}px;
}}
QFrame#Sidebar {{
    background: {t.SURFACE};
    border-right: 1px solid {t.BORDER};
}}
QFrame#TopBar {{
    background: {t.SURFACE};
    border-bottom: 1px solid {t.BORDER};
}}
QFrame#Divider {{
    background: {t.BORDER};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}
QPushButton {{
    background: {t.PRIMARY};
    color: #FFFFFF;
    border: none;
    border-radius: {t.RADIUS_SM}px;
    padding: 9px 16px;
    font-weight: 700;
    font-size: {t.FONT_BODY}px;
}}
QPushButton:hover {{ background: {t.PRIMARY_HOVER}; }}
QPushButton:pressed {{ background: {t.PRIMARY_PRESSED}; }}
QPushButton:disabled {{ background: {t.BORDER_STRONG}; color: {t.SOFT}; }}
QPushButton#Secondary {{
    background: transparent;
    color: {t.TEXT};
    border: 1px solid {t.BORDER_STRONG};
}}
QPushButton#Secondary:hover {{ background: {t.CARD_ALT}; }}
QPushButton#Ghost {{
    background: transparent;
    color: {t.MUTED};
    border: none;
    padding: 6px 10px;
}}
QPushButton#Ghost:hover {{ color: {t.TEXT_STRONG}; background: {t.CARD_ALT}; }}
QPushButton#Link {{
    background: transparent;
    color: {t.LINK};
    border: none;
    padding: 2px 0;
    text-align: left;
    font-weight: 650;
}}
QPushButton#Link:hover {{ color: {t.LINK_HOVER}; text-decoration: underline; }}
QPushButton#PathLink {{
    background: transparent;
    color: {t.LINK};
    border: none;
    padding: 0;
    font-size: {t.FONT_META}px;
    font-weight: 600;
    text-align: left;
}}
QPushButton#PathLink:hover {{ color: {t.LINK_HOVER}; text-decoration: underline; }}
QPushButton#PathMenu {{
    background: transparent;
    color: {t.MUTED};
    border: 1px solid {t.BORDER};
    border-radius: 5px;
    padding: 1px 8px;
    font-size: {t.FONT_META}px;
    font-weight: 600;
}}
QPushButton#PathMenu:hover {{ color: {t.TEXT_STRONG}; border-color: {t.PRIMARY_HOVER}; }}
QPushButton#PathMenu::menu-indicator {{ width: 8px; height: 8px; }}
QPushButton#PathLink:focus {{ color: {t.LINK_HOVER}; text-decoration: underline; }}
QPushButton#Link:focus {{ outline: 2px solid {t.ACCENT}; }}
QPushButton#FocusAction {{
    background: {t.FOCUS_ACCENT};
    color: {t.FOCUS};
    border: none;
    font-weight: 800;
}}
QPushButton#FocusAction:hover {{ background: #E6C98D; }}
QPushButton#Danger {{
    background: transparent;
    color: {t.DANGER};
    border: 1px solid {t.DANGER};
}}
QPushButton#Danger:hover {{ background: {t.DANGER_BG}; }}
QLineEdit, QComboBox, QSpinBox {{
    background: {t.CARD};
    border: 1px solid {t.BORDER_STRONG};
    border-radius: {t.RADIUS_SM}px;
    padding: 7px 10px;
    selection-background-color: {t.PRIMARY};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 2px solid {t.PRIMARY}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {t.CARD};
    border: 1px solid {t.BORDER_STRONG};
    selection-background-color: {t.SELECTION if hasattr(t, 'SELECTION') else t.TABLE_SELECT};
    selection-color: {t.TEXT_STRONG};
    outline: none;
}}
QCheckBox {{ spacing: 8px; }}
QTableWidget {{
    background: {t.CARD};
    border: 1px solid {t.BORDER};
    border-radius: {t.RADIUS_MD}px;
    gridline-color: transparent;
    selection-background-color: {t.TABLE_SELECT};
    selection-color: {t.TEXT_STRONG};
    alternate-background-color: {t.TABLE_ALT};
}}
QTableWidget::item {{ padding: 7px 6px; border: none; }}
QTableWidget::item:selected {{ background: {t.TABLE_SELECT}; color: {t.TEXT_STRONG}; }}
QHeaderView::section {{
    background: {t.TABLE_HEADER_BG};
    color: {t.MUTED};
    border: none;
    border-bottom: 1px solid {t.BORDER};
    padding: 9px 6px;
    font-size: {t.FONT_META}px;
    font-weight: 750;
    text-align: left;
}}
QTableCornerButton::section {{ background: {t.TABLE_HEADER_BG}; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea#SidebarScroll, QScrollArea#SidebarScroll > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QWidget#SidebarBody {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {t.BORDER_STRONG}; border-radius: 3px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {t.BORDER_STRONG}; border-radius: 3px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar::tab {{
    background: transparent;
    color: {t.MUTED};
    padding: 9px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 650;
}}
QTabBar::tab:selected {{ color: {t.TEXT_STRONG}; border-bottom: 2px solid {t.PRIMARY}; }}
QTabBar::tab:hover {{ color: {t.TEXT_STRONG}; }}
QListWidget#NavList {{
    background: transparent;
    border: none;
    outline: none;
    padding: 4px;
}}
QListWidget#NavList::item {{
    color: {t.TEXT};
    padding: 10px 12px;
    margin: 2px 6px;
    border-radius: {t.RADIUS_SM}px;
    font-size: {t.FONT_BODY}px;
}}
QListWidget#NavList::item:hover {{ background: {t.CARD_ALT}; }}
QListWidget#NavList::item:selected {{ background: {t.PRIMARY}; color: white; font-weight: 700; }}
QListWidget#NavList::item:selected:!active {{ background: {t.PRIMARY}; color: white; }}
QProgressBar {{
    border: none;
    background: {t.BORDER};
    border-radius: 3px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {t.ACCENT}; border-radius: 3px; }}
QToolTip {{
    background: {t.FOCUS};
    color: {t.FOCUS_TEXT};
    border: none;
    padding: 6px 8px;
}}
QLabel#Badge {{
    border-radius: 7px;
    padding: 3px 8px;
    font-size: {t.FONT_META}px;
    font-weight: 700;
}}
QLabel#Badge[variant="success"] {{ background: {t.SUCCESS_BG}; color: {t.SUCCESS}; }}
QLabel#Badge[variant="danger"] {{ background: {t.DANGER_BG}; color: {t.DANGER}; }}
QLabel#Badge[variant="warning"] {{ background: {t.WARNING_BG}; color: {t.WARNING}; }}
QLabel#Badge[variant="info"] {{ background: {t.INFO_BG}; color: {t.INFO}; }}
QLabel#Badge[variant="neutral"] {{ background: {t.NEUTRAL_BG}; color: {t.NEUTRAL}; }}
QLabel#Badge[variant="accent"] {{ background: {t.ACCENT_SOFT}; color: {t.ACCENT}; }}
QLabel#BigMetric {{
    color: {t.TEXT_STRONG};
    font-family: {t.FONT_NUMERIC};
    font-size: 30px;
    font-weight: 800;
}}
QLabel#MetricLabel {{ color: {t.MUTED}; font-size: {t.FONT_SMALL}px; }}
QLabel#ErrorText {{ color: {t.DANGER}; }}
QLabel#EmptyTitle {{ color: {t.TEXT_STRONG}; font-size: 18px; font-weight: 750; }}
QLabel#EmptyBody {{ color: {t.MUTED}; }}
QMenu {{ background: {t.CARD}; border: 1px solid {t.BORDER}; }}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background: {t.CARD_ALT}; }}
"""


def status_variant(value: str) -> str:
    mapping = {
        "已完赛": "neutral",
        "已结束": "neutral",
        "进行中": "success",
        "未开放": "neutral",
        "未赛": "info",
        "待定": "neutral",
        "已归档": "info",
        "淘汰": "danger",
        "出线": "success",
        "冠军": "accent",
    }
    return mapping.get(value, "neutral")


def apply_theme(app) -> None:
    """强制浅色 Fusion 调色板，避免 macOS 深色外观把内容区画成黑底。"""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(t.BG))
    palette.setColor(QPalette.WindowText, QColor(t.TEXT))
    palette.setColor(QPalette.Base, QColor(t.CARD))
    palette.setColor(QPalette.AlternateBase, QColor(t.TABLE_ALT))
    palette.setColor(QPalette.ToolTipBase, QColor(t.FOCUS))
    palette.setColor(QPalette.ToolTipText, QColor(t.FOCUS_TEXT))
    palette.setColor(QPalette.Text, QColor(t.TEXT))
    palette.setColor(QPalette.Button, QColor(t.CARD))
    palette.setColor(QPalette.ButtonText, QColor(t.TEXT))
    palette.setColor(QPalette.BrightText, QColor(t.DANGER))
    palette.setColor(QPalette.Link, QColor(t.LINK))
    palette.setColor(QPalette.Highlight, QColor(t.TABLE_SELECT))
    palette.setColor(QPalette.HighlightedText, QColor(t.TEXT_STRONG))
    app.setPalette(palette)
    app.setStyleSheet(APP_STYLE)
