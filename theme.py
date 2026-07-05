"""App-wide theming: dark (night) and light (classic) palettes."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

THEMES = ["dark", "light"]


def dark_palette() -> QPalette:
    p = QPalette()
    bg = QColor(37, 37, 40)
    base = QColor(28, 28, 30)
    text = QColor(230, 230, 230)
    accent = QColor(42, 130, 218)
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, bg)
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, bg)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(255, 80, 80))
    p.setColor(QPalette.Highlight, accent)
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.Link, accent)
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    disabled = QColor(120, 120, 120)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, disabled)
    return p


# clearly visible check/radio indicators (the Fusion defaults are hard to see,
# especially on the dark palette)
_INDICATORS_DARK = """
QCheckBox::indicator, QRadioButton::indicator, QGroupBox::indicator {
    width: 15px; height: 15px;
    border: 2px solid #b8b8b8;
    background: #1c1c1e;
}
QCheckBox::indicator, QGroupBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QGroupBox::indicator:hover { border-color: #ffffff; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked {
    background: #2a82da; border-color: #6db3f2;
}
QRadioButton::indicator:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
        fx:0.5, fy:0.5, stop:0.55 #2a82da, stop:0.65 #1c1c1e);
    border-color: #6db3f2;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    border-color: #5a5a5a; background: #2a2a2c;
}
"""

_INDICATORS_LIGHT = """
QCheckBox::indicator, QRadioButton::indicator, QGroupBox::indicator {
    width: 15px; height: 15px;
    border: 2px solid #666666;
    background: #ffffff;
}
QCheckBox::indicator, QGroupBox::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover,
QGroupBox::indicator:hover { border-color: #000000; }
QCheckBox::indicator:checked, QGroupBox::indicator:checked {
    background: #2a82da; border-color: #1c5c9e;
}
QRadioButton::indicator:checked {
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
        fx:0.5, fy:0.5, stop:0.55 #2a82da, stop:0.65 #ffffff);
    border-color: #1c5c9e;
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    border-color: #b0b0b0; background: #e8e8e8;
}
"""


def apply_theme(name: str):
    """Apply 'dark' (night) or 'light' (classic) to the running application."""
    qapp = QApplication.instance()
    if qapp is None:
        return
    if name == "light":
        qapp.setPalette(qapp.style().standardPalette())
        qapp.setStyleSheet(_INDICATORS_LIGHT)
    else:
        qapp.setPalette(dark_palette())
        qapp.setStyleSheet(_INDICATORS_DARK)
