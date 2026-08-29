# UI v2 设计 Tokens：颜色 / 语义状态 / 间距 / 圆角的唯一来源。
# 组件层与页面层统一从这里取值，避免各页硬编码十六进制颜色。

from __future__ import annotations

# -- 链接配色（§7.2 青色链接、hover/焦点态） ----------------------------------
LINK_COLOR = "#7dd3fc"
LINK_COLOR_HOVER = "#bae6fd"
LINK_COLOR_FOCUS = "#38bdf8"

# -- 文本配色 -----------------------------------------------------------------
TEXT_COLOR = "#e8eef7"
TEXT_COLOR_BRIGHT = "#f8fbff"
TEXT_COLOR_MUTED = "#91a8c5"
TEXT_COLOR_SOFT = "#cbd7e6"

# -- 背景与边框 ---------------------------------------------------------------
BG_COLOR = "#0b1220"
BG_COLOR_CARD = "#111c2e"
BG_COLOR_INPUT = "#0f1b2d"
BORDER_COLOR = "#263b5b"
BORDER_COLOR_SOFT = "#223653"

# -- 强调色 -------------------------------------------------------------------
ACCENT_COLOR = "#1167d8"
ACCENT_COLOR_HOVER = "#2784ff"

# -- 表格配色 -----------------------------------------------------------------
HEADER_BG_COLOR = "#172942"
ALT_ROW_COLOR = "#13243a"
GRID_COLOR = "#23344d"
SELECTION_COLOR = "#155bb6"
ROW_HOVER_COLOR = "#1d3555"

# -- 语义状态色（胜/通过 / 负/拒绝 / 平/警告 / 中性） -------------------------
SUCCESS_COLOR = "#86efac"
SUCCESS_BG = "#0a2e1a"
DANGER_COLOR = "#f87171"
DANGER_BG = "#3f1d1d"
WARNING_COLOR = "#f9c74f"
WARNING_BG = "#33270a"
NEUTRAL_COLOR = "#cbd7e6"
NEUTRAL_BG = "#31394a"

# -- 徽标/附加语义色（页面徽标、面板文字等） ---------------------------------
SUCCESS_BRIGHT = "#34d399"
SUCCESS_DEEP_BG = "#052e1c"
DANGER_SOFT = "#fca5a5"
DANGER_DEEP_BG = "#3f0d0d"
NEUTRAL_LIGHT = "#cbd5e1"
NEUTRAL_DARK_BG = "#111827"
NEUTRAL_BADGE_FG = "#94a3b8"
NEUTRAL_BADGE_BG = "#0f172a"
LINK_DARK_BG = "#082032"
HEADER_TEXT_COLOR = "#dfe9f7"
SUCCESS_HIGHLIGHT = "#4ade80"
ACCENT_SOFT = "#93c5fd"
EVENT_TEXT_COLOR = "#dbe6f3"

# -- 间距与圆角 ---------------------------------------------------------------
SPACING_XS = 4
SPACING_SM = 8
SPACING_MD = 12
SPACING_LG = 16
SPACING_XL = 24
RADIUS_SM = 8
RADIUS_MD = 10
RADIUS_LG = 12
