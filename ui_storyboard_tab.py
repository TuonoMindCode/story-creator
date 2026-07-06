"""Tab 3 — Storyboards: reusable library, left list / right editor."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

import pipeline
import project as prj
from project import StoryProject


class StoryboardTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._loading = False
        self._stream_buffer = ""
        self._viewing_stream = False

        lay = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        # left: two lists (latest run on top, older below) + buttons
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Latest run"))
        self.list = QListWidget()
        self.list.currentTextChanged.connect(
            lambda name: self._list_selected(name, self.list))
        ll.addWidget(self.list, 1)
        ll.addWidget(QLabel("Older storyboards"))
        self.list_old = QListWidget()
        self.list_old.currentTextChanged.connect(
            lambda name: self._list_selected(name, self.list_old))
        ll.addWidget(self.list_old, 2)

        btn_new = QPushButton("New (from concept)")
        btn_new.setToolTip("Generate a new storyboard from the concept on the Story Start tab.")
        btn_new.clicked.connect(self._new_from_concept)
        btn_dup = QPushButton("Duplicate")
        btn_dup.clicked.connect(self._duplicate)
        btn_ren = QPushButton("Rename")
        btn_ren.clicked.connect(self._rename)
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete)
        for b in (btn_new, btn_dup, btn_ren, btn_del):
            ll.addWidget(b)
        splitter.addWidget(left)

        # right: editor + actions
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.title_label = QLabel("No storyboard selected")
        rl.addWidget(self.title_label)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "Storyboard text — generate one from the Story Start tab, or write your own."
        )
        self.editor.textChanged.connect(self._text_edited)
        rl.addWidget(self.editor, 1)

        row = QHBoxLayout()
        self.regen_btn = QPushButton("Regenerate (from concept)")
        self.regen_btn.clicked.connect(self._regenerate)
        self.use_btn = QPushButton("Use for New Story →")
        self.use_btn.setToolTip(
            "Start a new story project from this storyboard and generate its scene outline."
        )
        self.use_btn.clicked.connect(self._use_for_story)
        row.addWidget(self.regen_btn)
        row.addStretch(1)
        row.addWidget(self.use_btn)
        rl.addLayout(row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 900])

        self.state.storyboards_changed.connect(self.refresh_list)
        self.refresh_list()

    # -- list handling --------------------------------------------------------

    def refresh_list(self):
        current = self.state.selected_storyboard
        all_boards = prj.list_storyboards()
        batch = [b for b in self.state.ui.get("batch_boards", []) if b in all_boards]
        older = [b for b in all_boards if b not in batch]
        for widget, names in ((self.list, batch), (self.list_old, older)):
            widget.blockSignals(True)
            widget.clear()
            widget.addItems(names)
            widget.blockSignals(False)
        if current:
            self.select_storyboard(current)

    def select_storyboard(self, name: str):
        for widget in (self.list, self.list_old):
            items = widget.findItems(name, Qt.MatchExactly)
            if items:
                widget.setCurrentItem(items[0])
                return

    def _list_selected(self, name: str, source: QListWidget):
        if not name or self._loading:
            return
        other = self.list_old if source is self.list else self.list
        other.blockSignals(True)
        other.setCurrentRow(-1)
        other.blockSignals(False)
        self._selected(name)

    def _selected(self, name: str):
        if not name:
            return
        self._viewing_stream = False  # user chose a saved board over the stream
        self._save_current_edits()
        self.state.selected_storyboard = name
        self._loading = True
        self.editor.setPlainText(prj.load_storyboard(name))
        self._loading = False
        self.title_label.setText(f"Storyboard: {name}")
        self.main.tab_lorebook.refresh()

    def _text_edited(self):
        if self._loading:
            return
        self._dirty = True

    def _save_current_edits(self):
        name = self.state.selected_storyboard
        if name and getattr(self, "_dirty", False):
            prj.save_storyboard(name, self.editor.toPlainText())
        self._dirty = False

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_list()

    def hideEvent(self, event):
        self._save_current_edits()
        super().hideEvent(event)

    # -- streaming hooks (used by Story Start tab and batch runs) ----------------

    def begin_external_stream(self, label: str = "Generating storyboard…"):
        """Prepare the editor to receive streamed text from a job."""
        self._save_current_edits()
        self._loading = True
        for widget in (self.list, self.list_old):
            widget.blockSignals(True)
            widget.setCurrentRow(-1)
            widget.blockSignals(False)
        self.editor.clear()
        self._loading = False
        self._dirty = False
        self._stream_buffer = ""
        self._viewing_stream = True  # editor currently shows the live stream
        self.title_label.setText(label)

    def stream_piece(self, piece: str):
        self._stream_buffer += piece
        if not self._viewing_stream:
            # the user switched to an old storyboard — don't pollute its view;
            # the finished board is selected automatically when done
            return
        self._loading = True
        cursor = self.editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(piece)
        self.editor.setTextCursor(cursor)
        self._loading = False
        self._dirty = False

    # -- actions ---------------------------------------------------------------

    def _new_from_concept(self):
        self.main.tab_start.create_storyboard()

    def _duplicate(self):
        name = self.state.selected_storyboard
        if not name:
            return
        self._save_current_edits()
        text = prj.load_storyboard(name)
        path = prj.unique_path(prj.STORYBOARDS_DIR, name, ".md")
        path.write_text(text, encoding="utf-8")
        entries = prj.load_lorebook(name)
        if entries:
            prj.save_lorebook(path.stem, entries)
        self.state.selected_storyboard = path.stem
        self.refresh_list()

    def _rename(self):
        name = self.state.selected_storyboard
        if not name:
            return
        new, ok = QInputDialog.getText(self, "Rename storyboard", "New name:", text=name)
        if not ok or not new.strip():
            return
        self._save_current_edits()
        self.state.selected_storyboard = prj.rename_storyboard(name, new.strip())
        self.refresh_list()

    def _delete(self):
        name = self.state.selected_storyboard
        if not name:
            return
        answer = QMessageBox.question(
            self, "Delete storyboard",
            f"Delete storyboard '{name}' and its lorebook?")
        if answer != QMessageBox.Yes:
            return
        prj.delete_storyboard(name)
        self.state.selected_storyboard = ""
        self._loading = True
        self.editor.clear()
        self._loading = False
        self.title_label.setText("No storyboard selected")
        self.refresh_list()

    def _regenerate(self):
        name = self.state.selected_storyboard
        concept = self.main.tab_start.get_concept()
        if not name or not concept:
            self.main.statusBar().showMessage(
                "Select a storyboard and enter a concept on the Story Start tab.")
            return
        cfg = self.state.runtime_cfg("storyboard").rolled()
        num_scenes = self.state.ui.get("num_scenes", 6)
        language = self.state.ui.get("story_language", "English")
        plan_in_language = bool(self.state.ui.get("plan_in_language", False))
        self._loading = True
        self.editor.clear()
        self._loading = False

        def job(worker):
            return pipeline.generate_storyboard(
                cfg, concept, num_scenes, language=language,
                plan_in_language=plan_in_language, cancel=worker.cancel,
                on_chunk=worker.chunk.emit)

        def chunk(piece):
            self._loading = True
            self.editor.insertPlainText(piece)
            self._loading = False

        def done(text):
            if text and text.strip():
                prj.save_storyboard(name, text.strip())
                self._loading = True
                self.editor.setPlainText(text.strip())
                self._loading = False
                self._dirty = False

        self.main.run_job(job, on_chunk=chunk, on_done=done,
                          status=f"Regenerating storyboard '{name}'…")

    def _use_for_story(self):
        name = self.state.selected_storyboard
        if not name:
            self.main.statusBar().showMessage("Select a storyboard first.")
            return
        self._save_current_edits()
        board_text = self.editor.toPlainText().strip()
        if not board_text:
            self.main.statusBar().showMessage("The storyboard is empty.")
            return
        title = prj.extract_storyboard_title(board_text) or name
        story = StoryProject(
            name=prj.new_project_name(title),
            concept=self.main.tab_start.get_concept(),
            storyboard_name=name,
            storyboard_text=board_text,
            num_scenes=self.state.ui.get("num_scenes", 6),
            target_length=self.state.ui.get("target_length", 900),
        )
        self.state.project = story
        self.state.project_changed.emit()
        self.main.tabs.setCurrentWidget(self.main.tab_outline)
        self.main.tab_outline.generate_outline()
