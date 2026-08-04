"""EBZ nano power light theme for Battery Test Sequencer."""

# Accent from EBZ logo blue block
ACCENT = "#008FC1"
BG = "#f4f6f8"
BG_PANEL = "#ffffff"
BG_INPUT = "#ffffff"
BORDER = "#d5dde5"
TEXT = "#1a222c"
TEXT_DIM = "#667484"
DANGER = "#c0392b"
OK = "#1f8a5b"
CHART_BG = "#eef2f5"

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
QTabBar::tab {{
    background: transparent;
    color: {TEXT_DIM};
    padding: 9px 18px;
    margin-right: 2px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
    background: {BG};
}}
QTabBar::tab:hover:!selected {{
    color: {TEXT};
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit, QListWidget, QPlainTextEdit {{
    background: {BG_INPUT};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {ACCENT};
    selection-color: white;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: {BG_INPUT};
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: white;
    border: 1px solid {BORDER};
}}
QPushButton {{
    background: {BG_PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 5px;
    padding: 6px 14px;
    min-height: 18px;
}}
QPushButton:hover {{
    border-color: {ACCENT};
    color: {ACCENT};
}}
QPushButton:pressed {{
    background: #e8eef2;
}}
QPushButton#btnPrimary {{
    background: {ACCENT};
    color: white;
    border: none;
    font-weight: 600;
}}
QPushButton#btnPrimary:hover {{
    background: #00a3d6;
}}
QPushButton#btnDanger {{
    background: {DANGER};
    color: white;
    border: none;
    font-weight: 700;
}}
QPushButton#btnDanger:hover {{
    background: #d45045;
}}
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {BORDER};
    border-radius: 3px;
    background: white;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QGroupBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 10px 10px 10px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: {TEXT_DIM};
}}
QSplitter::handle {{
    background: {BORDER};
    width: 1px;
}}
QScrollBar:vertical {{
    background: {BG};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #c5ced8;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
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
}}
QHeaderView::section {{
    background: #f0f3f6;
    color: {TEXT_DIM};
    border: none;
    padding: 4px;
}}
QTableWidget {{
    background: white;
    gridline-color: {BORDER};
    border: 1px solid {BORDER};
}}
QMessageBox {{
    background: white;
}}
"""
