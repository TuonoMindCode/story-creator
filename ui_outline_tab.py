"""Tab 4 — Scene Outline: the scene plan of the current story project."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import pipeline
import project as prj
from project import Scene


class OutlineTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._loading = False

        lay = QVBoxLayout(self)
        self.header = QLabel("No story project — use a storyboard ('Use for New Story') first.")
        lay.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setDragDropMode(QAbstractItemView.InternalMove)
        self.list.currentRowChanged.connect(self._selected)
        self.list.model().rowsMoved.connect(self._reordered)
        ll.addWidget(self.list, 1)

        btn_add = QPushButton("Add Scene")
        btn_add.clicked.connect(self._add_scene)
        btn_del = QPushButton("Delete Scene")
        btn_del.clicked.connect(self._delete_scene)
        self.btn_regen = QPushButton("Regenerate Outline")
        self.btn_regen.clicked.connect(self.generate_outline)
        for b in (btn_add, btn_del, self.btn_regen):
            ll.addWidget(b)
        splitter.addWidget(left)

        right = QWidget()
        form = QFormLayout(right)
        self.title_edit = QLineEdit()
        self.location_edit = QLineEdit()
        self.characters_edit = QLineEdit()
        self.beat_edit = QPlainTextEdit()
        self.beat_edit.setFixedHeight(140)
        self.purpose_edit = QPlainTextEdit()
        self.purpose_edit.setFixedHeight(90)
        form.addRow("Title", self.title_edit)
        form.addRow("Location", self.location_edit)
        form.addRow("Characters", self.characters_edit)
        form.addRow("What happens", self.beat_edit)
        form.addRow("Purpose / conflict", self.purpose_edit)
        self.raw_label = QLabel("")
        self.raw_label.setWordWrap(True)
        form.addRow(self.raw_label)
        self.raw_view = QPlainTextEdit()
        self.raw_view.setReadOnly(True)
        self.raw_view.setPlaceholderText("Outline streams in here while generating…")
        self.raw_view.setVisible(False)
        form.addRow(self.raw_view)
        splitter.addWidget(right)
        splitter.setSizes([300, 860])

        for w, attr in (
            (self.title_edit, "title"),
            (self.location_edit, "location"),
            (self.characters_edit, "characters"),
        ):
            w.textChanged.connect(lambda t, a=attr: self._field_changed(a, t))
        self.beat_edit.textChanged.connect(
            lambda: self._field_changed("beat", self.beat_edit.toPlainText()))
        self.purpose_edit.textChanged.connect(
            lambda: self._field_changed("purpose", self.purpose_edit.toPlainText()))

        self.state.project_changed.connect(self.refresh)
        self.state.busy_changed.connect(lambda b: self.btn_regen.setEnabled(not b))
        self.refresh()

    # -- model sync -----------------------------------------------------------

    # -- streaming hooks (also used by batch runs) -------------------------------

    def begin_stream(self):
        self.raw_view.clear()
        self.raw_view.setVisible(True)

    def stream_piece(self, piece: str):
        cursor = self.raw_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(piece)
        self.raw_view.setTextCursor(cursor)

    def refresh(self):
        self.raw_view.setVisible(False)
        story = self.state.project
        self._loading = True
        self.list.clear()
        if story is None:
            self.header.setText(
                "No story project — use a storyboard ('Use for New Story') first.")
        else:
            self.header.setText(
                f"Story: {story.title}  —  {len(story.scenes)} scene(s)")
            for i, scene in enumerate(story.scenes, 1):
                item = QListWidgetItem(f"{i}. {scene.title or '(untitled)'}")
                self.list.addItem(item)
        self._loading = False
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self._clear_form()

    def _renumber(self):
        for i in range(self.list.count()):
            story = self.state.project
            title = story.scenes[i].title if story and i < len(story.scenes) else ""
            self.list.item(i).setText(f"{i + 1}. {title or '(untitled)'}")

    def _clear_form(self):
        self._loading = True
        for w in (self.title_edit, self.location_edit, self.characters_edit):
            w.clear()
        self.beat_edit.clear()
        self.purpose_edit.clear()
        self._loading = False

    def _selected(self, row: int):
        story = self.state.project
        if self._loading or story is None or not (0 <= row < len(story.scenes)):
            return
        scene = story.scenes[row]
        self._loading = True
        self.title_edit.setText(scene.title)
        self.location_edit.setText(scene.location)
        self.characters_edit.setText(scene.characters)
        self.beat_edit.setPlainText(scene.beat)
        self.purpose_edit.setPlainText(scene.purpose)
        status = scene.status
        self.raw_label.setText(f"Status: {status}" + (
            " — scene already has text; outline edits affect regeneration." if scene.text else ""))
        self._loading = False

    def _field_changed(self, attr: str, value: str):
        story = self.state.project
        row = self.list.currentRow()
        if self._loading or story is None or not (0 <= row < len(story.scenes)):
            return
        setattr(story.scenes[row], attr, value)
        if attr == "title":
            self.list.item(row).setText(f"{row + 1}. {value or '(untitled)'}")
        self.state.autosave_project()

    def _reordered(self, parent, start, end, dest, dest_row):
        story = self.state.project
        if story is None:
            return
        scene = story.scenes.pop(start)
        target = dest_row if dest_row < start else dest_row - 1
        story.scenes.insert(target, scene)
        self._renumber()
        self.state.autosave_project()
        self.state.outline_changed.emit()

    def _add_scene(self):
        story = self.state.project
        if story is None:
            return
        story.scenes.append(Scene(title="New scene"))
        story.num_scenes = len(story.scenes)
        self.refresh()
        self.list.setCurrentRow(self.list.count() - 1)
        self.state.autosave_project()
        self.state.outline_changed.emit()

    def _delete_scene(self):
        story = self.state.project
        row = self.list.currentRow()
        if story is None or not (0 <= row < len(story.scenes)):
            return
        story.scenes.pop(row)
        story.num_scenes = len(story.scenes)
        self.refresh()
        self.state.autosave_project()
        self.state.outline_changed.emit()

    # -- generation -------------------------------------------------------------

    def generate_outline(self):
        story = self.state.project
        if story is None:
            self.main.statusBar().showMessage("Create a story project first (Storyboards tab).")
            return
        cfg = self.state.runtime_cfg("planner").rolled()
        board = story.storyboard_text
        num_scenes = story.num_scenes or self.state.ui.get("num_scenes", 6)
        language = self.state.ui.get("story_language", "English")
        plan_in_language = bool(self.state.ui.get("plan_in_language", False))

        self.begin_stream()

        def job(worker):
            return pipeline.generate_outline(cfg, board, num_scenes,
                                             language=language,
                                             plan_in_language=plan_in_language,
                                             cancel=worker.cancel,
                                             on_chunk=worker.chunk.emit)

        def done(result):
            self.raw_view.setVisible(False)
            raw, scenes = result
            if not scenes:
                self.main.statusBar().showMessage(
                    "Could not parse any scenes from the outline response.")
                return
            story.scenes = scenes
            story.num_scenes = len(scenes)
            self.state.autosave_project()
            self.refresh()
            self.state.outline_changed.emit()
            self.main.statusBar().showMessage(f"Outline ready — {len(scenes)} scenes.")

        def error(msg):
            self.raw_view.setVisible(False)
            self.main.statusBar().showMessage(f"Outline failed: {msg}")

        self.main.run_job(job, on_chunk=self.stream_piece, on_done=done,
                          on_error=error, status="Generating scene outline…")
