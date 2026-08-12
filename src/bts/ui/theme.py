"""EBZ nano power light theme for Battery Test Sequencer."""

# Accent from EBZ logo blue block
ACCENT = "#008FC1"
ACCENT_HOVER = "#00a6dc"
ACCENT_SOFT = "#e3f3f9"       # tinted fill for subtle highlights
# Two-surface system: ONE gray (page + insets) and white cards, so the eye sees
# only white↔gray, never a stack of near-identical grays. Contrast comes from a
# stronger border + darker secondary text rather than extra fill shades.
BG = "#e7ecf2"               # the single gray: page background AND card insets
BG_PANEL = "#ffffff"         # every card / panel surface
BG_INPUT = "#ffffff"
BG_SUBTLE = BG               # alias — keep old call sites on the one gray
BORDER = "#cdd7e1"           # crisper hairline for readable card edges
BORDER_STRONG = "#b6c2cf"    # splitters / emphasis
TEXT = "#182330"
TEXT_DIM = "#586675"         # darker → small labels stay legible
DANGER = "#c0392b"
DANGER_HOVER = "#d64536"
OK = "#1f8a5b"
CHART_BG = BG                # plot wells use the same single gray

# Shared card look — reuse for inline-styled frames so spacing/rounding stay
# consistent across the app. Restrained rounding for an instrument-style tool.
CARD_RADIUS = 6
CTRL_RADIUS = 4


def card_style(object_name: str, *, bg: str = BG_PANEL, border: str = BORDER) -> str:
    """QSS for a rounded surface card keyed by objectName."""
    return (
        f"#{object_name} {{ background: {bg}; border: 1px solid {border}; "
        f"border-radius: {CARD_RADIUS}px; }}"
    )


APP_STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Segoe UI", "IBM Plex Sans", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {BG};
}}
QTabWidget::pane {{
    border: none;
    background: {BG};
    top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 10px 20px;
    margin-right: 4px;
    border: none;
    border-bottom: 2px solid transparent;
    font-weight: 600;
}}
QTabBar::tab:selected {{
    color: {ACCENT};
    border-bottom: 2px solid {ACCENT};
    background: {BG};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
    background: {BG_SUBTLE};
    border-top-left-radius: {CTRL_RADIUS}px;
    border-top-right-radius: {CTRL_RADIUS}px;
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget, QPlainTextEdit {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {CTRL_RADIUS}px;
    padding: 7px 10px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {ACCENT};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
    border: 1px solid {BORDER_STRONG};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {BG_SUBTLE};
    color: #9aa6b2;
    border: 1px solid {BORDER};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {BG_INPUT};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: white;
    border: 1px solid {BORDER};
    border-radius: {CTRL_RADIUS}px;
    padding: 4px;
    outline: none;
}}
QPushButton {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                               stop:0 #ffffff, stop:1 #e6edf3);
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: {CTRL_RADIUS}px;
    padding: 8px 18px;
    min-height: 18px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                stop:0 #ffffff, stop:1 {ACCENT_SOFT});
}}
QPushButton:pressed {{
    background: #d3e2ea;
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: #aab3bd;
    background: {BG_SUBTLE};
    border-color: {BORDER};
}}
QPushButton#btnPrimary {{
    background: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
    padding: 8px 20px;
}}
QPushButton#btnPrimary:hover {{
    background: {ACCENT_HOVER};
}}
QPushButton#btnPrimary:disabled {{
    background: #a9cede;
    color: #eaf5fa;
}}
QPushButton#btnDanger {{
    background: {DANGER};
    color: white;
    border: none;
    font-weight: 700;
    padding: 8px 20px;
}}
QPushButton#btnDanger:hover {{
    background: {DANGER_HOVER};
}}
QPushButton#btnDanger:disabled {{
    background: #dcb0ab;
    color: #f7ecea;
}}
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 3px;
    background: white;
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QGroupBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS}px;
    margin-top: 16px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 6px;
    color: {TEXT_DIM};
}}
QSplitter::handle {{
    background: transparent;
    width: 10px;
}}
QSplitter::handle:hover {{
    background: {BORDER};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #c5ced8;
    border-radius: 3px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: #aab6c2;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #c5ced8;
    border-radius: 3px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: #aab6c2;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QStatusBar {{
    background: #ffffff;
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
}}
QToolTip {{
    background: white;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: {CTRL_RADIUS}px;
    padding: 6px 8px;
}}
QHeaderView::section {{
    background: {BG_SUBTLE};
    color: {TEXT_DIM};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 8px 6px;
    font-weight: 600;
}}
QTableWidget {{
    background: white;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
    border-radius: {CARD_RADIUS}px;
}}
QMessageBox {{
    background: white;
}}
"""
