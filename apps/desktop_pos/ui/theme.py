"""App-wide visual theme for the desktop POS.

Centralized here (rather than scattered setStyleSheet() calls) so the
color palette stays consistent across the login screen, main window,
and every dialog, and so it's a single place to retune later.

Palette: a single navy-blue family throughout -- no accent color --
with white text for contrast. Status colors (stock levels, payment
remaining) stay bright red/amber/green since those carry functional
meaning a cashier needs to catch at a glance; everything else (chrome,
buttons, headers) uses the same blue family.
"""

PRIMARY = "#2c4a7c"
PRIMARY_DARK = "#1f3660"
PRIMARY_LIGHT = "#1b2a4a"
ACCENT = "#3a5f9e"
BG = "#0f1a30"
SURFACE = "#152140"
BORDER = "#2a3a5c"
TEXT = "#ffffff"
TEXT_MUTED = "#9aa6bf"

APP_STYLESHEET = f"""
QWidget {{
    background-color: {BG};
    color: {TEXT};
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {BG};
}}

QLabel {{
    color: {TEXT};
}}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: {TEXT};
}}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border: 1px solid {ACCENT};
}}

QPushButton {{
    background-color: {PRIMARY};
    color: {TEXT};
    border: none;
    border-radius: 4px;
    padding: 6px 14px;
    font-weight: 600;
}}

QPushButton:hover {{
    background-color: {PRIMARY_DARK};
}}

QPushButton:pressed {{
    background-color: {PRIMARY_DARK};
    padding-top: 7px;
}}

QPushButton:disabled {{
    background-color: #3a3f4d;
    color: #7a8094;
}}

QListWidget, QTableWidget {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 4px;
    alternate-background-color: {PRIMARY_LIGHT};
    gridline-color: {BORDER};
    color: {TEXT};
}}

QHeaderView::section {{
    background-color: {PRIMARY_LIGHT};
    color: {TEXT};
    padding: 4px;
    border: none;
    border-bottom: 2px solid {ACCENT};
    font-weight: 600;
}}

QTableWidget::item:selected, QListWidget::item:selected {{
    background-color: {ACCENT};
    color: {TEXT};
}}

QMenuBar, QToolBar {{
    background-color: {SURFACE};
    color: {TEXT};
}}
"""


def apply_theme(app) -> None:
    app.setStyleSheet(APP_STYLESHEET)
