"""Tab 10 — Log: every LLM request/response, parameters used, scene summaries."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import applog

# (option key, checkbox label) — all off by default; for prompt debugging
LOG_DETAIL_OPTIONS = [
    ("summary_text", "Scene summary text"),
    ("prompts_storyboard", "Storyboard system + user prompt"),
    ("prompts_outline", "Scene outline system + user prompt"),
    ("prompts_scene", "Scene writer system + user prompt"),
    ("prompts_summary", "Summarizer system + user prompt"),
    ("prompts_brief", "Brief instruction sent (single output)"),
    ("prompts_single", "Single-output system + user prompt"),
    ("full_responses", "Full LLM responses (all sections)"),
]


class LogTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main

        lay = QVBoxLayout(self)
        info = QLabel(
            "Every LLM call is logged here: backend, model, the exact (rolled) "
            "sampling parameters, prompt size and errors. "
            f"Also written to {applog.LOG_FILE.name}."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        detail_group = QGroupBox(
            "Log extra details (for debugging wrong stories — applies to new "
            "generations, all off by default)")
        dg = QGridLayout(detail_group)
        self.opt_checks: dict[str, QCheckBox] = {}
        saved = self.main.state.ui.get("log_options", {})
        for i, (key, label) in enumerate(LOG_DETAIL_OPTIONS):
            cb = QCheckBox(label)
            cb.setChecked(bool(saved.get(key, False)))
            cb.toggled.connect(lambda on, k=key: self._option_toggled(k, on))
            self.opt_checks[key] = cb
            dg.addWidget(cb, i // 3, i % 3)
        lay.addWidget(detail_group)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.auto_check = QCheckBox("Auto-refresh")
        self.auto_check.setChecked(True)
        row.addWidget(self.auto_check)
        row.addSpacing(20)
        row.addWidget(QLabel("Max log size (MB):"))
        from ui_common import make_spin
        self.size_spin = make_spin(
            int(self.main.state.ui.get("log_max_mb", 2)), 1, 100)
        self.size_spin.setToolTip(
            "When the log file grows past this size, the oldest half is dropped "
            "automatically.")
        self.size_spin.valueChanged.connect(self._size_changed)
        row.addWidget(self.size_spin)
        row.addStretch(1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self._clear)
        row.addWidget(refresh_btn)
        row.addWidget(clear_btn)
        lay.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._tick)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _size_changed(self, mb: int):
        self.main.state.ui["log_max_mb"] = int(mb)
        applog.MAX_BYTES = max(1, int(mb)) * 1_000_000
        self.main.state.save_settings()

    def _option_toggled(self, key: str, on: bool):
        applog.OPTIONS[key] = bool(on)
        self.main.state.ui.setdefault("log_options", {})[key] = bool(on)
        self.main.state.save_settings()

    def _tick(self):
        if self.auto_check.isChecked():
            self.refresh()

    def refresh(self):
        text = applog.read_log()
        if text == self.view.toPlainText():
            return
        self.view.setPlainText(text)
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.view.setTextCursor(cursor)

    def _clear(self):
        applog.clear_log()
        self.refresh()
