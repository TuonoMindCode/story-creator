"""Tab 6 — Lorebook: world & character facts tied to the selected storyboard."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import pipeline
import project as prj
from project import LorebookEntry


class LorebookTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self.entries: list[LorebookEntry] = []
        self._loading = False

        lay = QVBoxLayout(self)
        self.header = QLabel("Lorebook — select a storyboard in the Storyboards tab first.")
        lay.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selected)
        ll.addWidget(self.list, 1)
        btn_add = QPushButton("Add Entry")
        btn_add.clicked.connect(self._add)
        btn_del = QPushButton("Delete Entry")
        btn_del.clicked.connect(self._delete)
        self.btn_import = QPushButton("Import characters from storyboard")
        self.btn_import.clicked.connect(self._import_characters)
        for b in (btn_add, btn_del, self.btn_import):
            ll.addWidget(b)
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self.name_edit = QLineEdit()
        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("comma-separated triggers, e.g. Anna, the detective, Miss Berg")
        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText("Facts that must stay consistent…")
        self.always_check = QCheckBox("Always include (even if the keywords don't appear)")
        form.addRow("Name", self.name_edit)
        form.addRow("Keywords", self.keywords_edit)
        form.addRow("Facts", self.content_edit)
        form.addRow("", self.always_check)
        info = QLabel(
            "Entries are injected into the scene-writing prompt when their name or a "
            "keyword appears in the scene's outline beat or the end of the previous scene."
        )
        info.setWordWrap(True)
        form.addRow(info)
        splitter.addWidget(right)
        splitter.setSizes([300, 860])

        self.name_edit.textChanged.connect(self._field_changed)
        self.keywords_edit.textChanged.connect(self._field_changed)
        self.content_edit.textChanged.connect(self._field_changed)
        self.always_check.toggled.connect(self._field_changed)

        self.state.storyboards_changed.connect(self.refresh)
        self.state.busy_changed.connect(lambda b: self.btn_import.setEnabled(not b))
        self.refresh()

    # -- persistence ------------------------------------------------------------

    def refresh(self):
        name = self.state.selected_storyboard
        self.entries = prj.load_lorebook(name) if name else []
        self.header.setText(
            f"Lorebook for storyboard: {name}" if name
            else "Lorebook — select a storyboard in the Storyboards tab first.")
        self._reload_list()

    def _reload_list(self):
        self._loading = True
        self.list.clear()
        for e in self.entries:
            self.list.addItem(e.name or "(unnamed)")
        self._loading = False
        if self.entries:
            self.list.setCurrentRow(0)
            self._selected(0)
        else:
            self._clear_form()

    def _save(self):
        name = self.state.selected_storyboard
        if name:
            prj.save_lorebook(name, self.entries)

    def _clear_form(self):
        self._loading = True
        self.name_edit.clear()
        self.keywords_edit.clear()
        self.content_edit.clear()
        self.always_check.setChecked(False)
        self._loading = False

    def _selected(self, row: int):
        if self._loading or not (0 <= row < len(self.entries)):
            return
        e = self.entries[row]
        self._loading = True
        self.name_edit.setText(e.name)
        self.keywords_edit.setText(", ".join(e.keywords))
        self.content_edit.setPlainText(e.content)
        self.always_check.setChecked(e.always_include)
        self._loading = False

    def _field_changed(self, *args):
        row = self.list.currentRow()
        if self._loading or not (0 <= row < len(self.entries)):
            return
        e = self.entries[row]
        e.name = self.name_edit.text().strip()
        e.keywords = [k.strip() for k in self.keywords_edit.text().split(",") if k.strip()]
        e.content = self.content_edit.toPlainText().strip()
        e.always_include = self.always_check.isChecked()
        self.list.item(row).setText(e.name or "(unnamed)")
        self._save()

    def _add(self):
        if not self.state.selected_storyboard:
            self.main.statusBar().showMessage("Select a storyboard first.")
            return
        self.entries.append(LorebookEntry(name="New entry"))
        self._save()
        self._reload_list()
        self.list.setCurrentRow(len(self.entries) - 1)

    def _delete(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.entries):
            self.entries.pop(row)
            self._save()
            self._reload_list()

    def _import_characters(self):
        name = self.state.selected_storyboard
        if not name:
            self.main.statusBar().showMessage("Select a storyboard first.")
            return
        board = prj.load_storyboard(name)
        if not board.strip():
            self.main.statusBar().showMessage("The storyboard is empty.")
            return
        cfg = self.state.runtime_cfg("planner").rolled()

        def job(worker):
            return pipeline.generate_character_cards(cfg, board, cancel=worker.cancel)

        def done(cards):
            cards = cards or []
            existing = {e.name.lower() for e in self.entries}
            added = 0
            for card in cards:
                if card.name.lower() not in existing:
                    self.entries.append(card)
                    added += 1
            self._save()
            self._reload_list()
            self.main.statusBar().showMessage(f"Imported {added} character card(s).")

        self.main.run_job(job, on_done=done, status="Extracting characters…")
