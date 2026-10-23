"""语义设计 token：暖浅色主体 + 深色焦点比赛区。

页面与组件只从这里取颜色/间距；不写局部十六进制。改名或换主题时只改本文件
与 theme.py。
"""

from __future__ import annotations

import sys

# 主体
BG = "#F6F3EC"
SURFACE = "#FFFCF6"
CARD = "#FFFFFF"
CARD_ALT = "#F2EEE3"
TEXT = "#222722"
TEXT_STRONG = "#151A16"
MUTED = "#6C7268"
SOFT = "#8B9087"
BORDER = "#E4DECD"
BORDER_STRONG = "#CFC6B2"

# 主操作 / 强调
PRIMARY = "#174F3B"
PRIMARY_HOVER = "#1F6A4F"
PRIMARY_PRESSED = "#0F3B2B"
ACCENT = "#A9762F"
ACCENT_SOFT = "#F0E2C6"
FOCUS = "#121B20"
FOCUS_ALT = "#1B262C"
FOCUS_TEXT = "#F8F4E9"
FOCUS_MUTED = "#AEB8B2"
FOCUS_ACCENT = "#D8B36A"

# 语义
SUCCESS = "#2E6B4F"
SUCCESS_BG = "#E6EFE7"
DANGER = "#A6412F"
DANGER_BG = "#F7E6E1"
WARNING = "#8A621E"
WARNING_BG = "#F7EEDB"
INFO = "#2D5D7A"
INFO_BG = "#E4EEF2"
NEUTRAL = "#5E655D"
NEUTRAL_BG = "#ECE9E0"
RESULT_DRAW_BG = "#EBEEEC"
LINK = "#174F3B"
LINK_HOVER = "#0F3B2B"

# 位置
POS_GK = "#2D5D7A"
POS_DF = "#2E6B4F"
POS_MF = "#8A621E"
POS_FW = "#9B3D2A"

# 表格
TABLE_HEADER_BG = "#EFEADF"
TABLE_ALT = "#FAF7F0"
TABLE_SELECT = "#DDE8DF"
TABLE_HOVER = "#F0EDE4"

# macOS tournament canvas: quiet transfer routes, stronger paths on inspection.
BRACKET_TRANSFER = "#938B7A"
BRACKET_WINNER_BAND = "#EEF2EB"
BRACKET_LOWER_BAND = "#F0EDE5"
BRACKET_FINAL_BAND = "#F3EBD9"

# 间距/圆角
S2, S4, S6, S8, S10, S12, S14, S16, S18, S20, S24, S32, S40 = 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 24, 32, 40
RADIUS_SM = 8
RADIUS_MD = 12
RADIUS_LG = 18

# 字号
FONT_BODY = 15
FONT_SMALL = 12
FONT_META = 12
FONT_H1 = 26
FONT_H2 = 20
FONT_H3 = 16
FONT_SCORE = 64
FONT_SCORE_SMALL = 44

FONT_FAMILIES_MACOS = (
    "Avenir Next",
    "PingFang SC",
    "Hiragino Sans GB",
    "Helvetica Neue",
)
FONT_FAMILIES_WINDOWS = (
    "Segoe UI Variable Text",
    "Segoe UI",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
)
FONT_NUMERIC_MACOS = ("SF Mono", "Menlo", "Consolas")
FONT_NUMERIC_WINDOWS = ("Consolas", "Cascadia Mono", "Segoe UI")

IS_WINDOWS = sys.platform.startswith("win")

#: 正文字体族顺序：Windows 用 Segoe UI，中文回退 Microsoft YaHei UI；
#: macOS 保持原来的 Avenir Next → PingFang SC 顺序。
FONT_FAMILIES = FONT_FAMILIES_WINDOWS if IS_WINDOWS else FONT_FAMILIES_MACOS
FONT_NUMERIC_FAMILIES = FONT_NUMERIC_WINDOWS if IS_WINDOWS else FONT_NUMERIC_MACOS


def _font_stack(families) -> str:
    quoted = ", ".join(f'"{name}"' for name in families)
    return f"{quoted}, sans-serif"


def _numeric_stack(families) -> str:
    quoted = ", ".join(f'"{name}"' for name in families)
    return f"{quoted}, monospace"


FONT_FAMILY = _font_stack(FONT_FAMILIES)
FONT_NUMERIC = _numeric_stack(FONT_NUMERIC_FAMILIES)
