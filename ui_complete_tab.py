"""Tab 7 — Complete Story: saved-story list left, combined text right, export."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import pipeline
import project as prj
from project import StoryProject


class CompleteTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._shown: StoryProject | None = None

        lay = QVBoxLayout(self)
        self.header = QLabel("Saved stories — every story is autosaved to projects/.")
        lay.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._selected)
        ll.addWidget(self.list, 1)
        refresh_btn = QPushButton("Refresh List")
        refresh_btn.clicked.connect(self.refresh_all)
        ll.addWidget(refresh_btn)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.info = QLabel("")
        rl.addWidget(self.info)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setPlaceholderText(
            "Select a story on the left — the combined scenes appear here.")
        rl.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.load_btn = QPushButton("Open as Current Story")
        self.load_btn.setToolTip(
            "Load this story into the Scene Outline / Scene Writer tabs to keep working on it.")
        self.load_btn.clicked.connect(self._load_as_current)
        export_txt = QPushButton("Export .txt")
        export_txt.clicked.connect(lambda: self._export(False))
        export_md = QPushButton("Export .md")
        export_md.clicked.connect(lambda: self._export(True))
        row.addWidget(self.load_btn)
        row.addStretch(1)
        row.addWidget(export_txt)
        row.addWidget(export_md)
        rl.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([280, 900])

        self.state.project_changed.connect(self.refresh_all)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_all()

    # -- list --------------------------------------------------------------------

    def refresh_all(self):
        names = prj.list_projects()
        current = (self.state.project.name if self.state.project else
                   self.list.currentItem().text() if self.list.currentItem() else "")
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems(names)
        self.list.blockSignals(False)
        self.header.setText(f"Saved stories ({len(names)}) — autosaved in projects/, "
                            "exports land in output/.")
        if current and current in names:
            items = self.list.findItems(current, Qt.MatchExactly)
            if items:
                self.list.setCurrentItem(items[0])
                return
        if names:
            self.list.setCurrentRow(0)  # newest story is at the top

    def _selected(self, name: str):
        if not name:
            self._shown = None
            self.view.clear()
            self.info.setText("")
            return
        # show the live current project without reloading it from disk
        if self.state.project is not None and self.state.project.name == name:
            self._shown = self.state.project
        else:
            try:
                self._shown = StoryProject.load(prj.PROJECTS_DIR / f"{name}.json")
            except (OSError, ValueError):
                self._shown = None
                self.view.setPlainText("Could not load this story file.")
                return
        story = self._shown
        written = sum(1 for s in story.scenes if s.text.strip())
        text = story.combined_text(markdown=False)
        is_current = self.state.project is not None and self.state.project.name == name
        self.info.setText(
            f"{story.title} — {written}/{len(story.scenes)} scene(s) written · "
            f"{len(text.split())} words · ≈{pipeline.estimate_tokens(text)} tokens"
            + ("  [current story]" if is_current else ""))
        self.view.setPlainText(text)
        self.load_btn.setEnabled(not is_current)

    # -- actions ------------------------------------------------------------------

    def refresh(self):
        """Re-render the currently selected story (used after scenes finish)."""
        item = self.list.currentItem()
        if item:
            self._selected(item.text())
        else:
            self.refresh_all()

    def _load_as_current(self):
        if self._shown is None:
            return
        self.state.project = self._shown
        self.state.project_changed.emit()
        self.main.statusBar().showMessage(
            f"'{self._shown.title}' is now the current story — see Scene Writer tab.")

    def _export(self, markdown: bool):
        if self._shown is None:
            self.main.statusBar().showMessage("Select a story first.")
            return
        path = self._shown.export(markdown=markdown)
        self.main.statusBar().showMessage(f"Exported to {path}")
