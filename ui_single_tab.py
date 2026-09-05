"""Tab — Single Output: write a whole story in one call.

Two steps. A small model turns the concept into a brief — a system prompt
(how to write) and a user prompt (what to write) — which is saved so it can
be reused or edited. Then the writing model gets that pair verbatim, in one
call, and returns the whole story.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
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

import applog
import briefs
import pipeline
import project as prj
from project import Scene, StoryProject


class SingleOutputTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._loading = False
        self._current_file = ""      # saved brief being viewed, if any
        self._buffer = ""

        lay = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        # -- left: the saved brief library -----------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Saved briefs (newest first)"))
        self.list = QListWidget()
        self.list.setToolTip(
            "Every generated brief is saved here. Select one to load its two "
            "prompts, edit them, and write another story from it.")
        self.list.currentRowChanged.connect(self._brief_selected)
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        btn_del = QPushButton("Delete")
        btn_del.clicked.connect(self._delete_brief)
        btn_save = QPushButton("Save edits")
        btn_save.setToolTip("Write the two prompt boxes back to the selected brief file.")
        btn_save.clicked.connect(self._save_edits)
        row.addWidget(btn_save)
        row.addWidget(btn_del)
        ll.addLayout(row)
        splitter.addWidget(left)

        # -- right: instruction, concept, the prompt pair, the story ---------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)

        instr_row = QHBoxLayout()
        instr_row.addWidget(QLabel("Brief instruction:"))
        self.instr_box = QComboBox()
        self.instr_box.setToolTip(
            "How the brief is written. This is the system prompt sent to the "
            "Storyboard model, which replies with the story's two prompts. A "
            "short set of extra rules is appended to whichever instruction you "
            "pick — no padding, no summarising, play the ending out in scene. "
            "Turn on 'Brief instruction sent' in the Log tab to see the whole "
            "thing exactly as sent.")
        self.instr_box.currentTextChanged.connect(self._instruction_changed)
        instr_row.addWidget(self.instr_box, 1)
        self.btn_edit = QPushButton("Show/Edit")
        self.btn_edit.setCheckable(True)
        self.btn_edit.toggled.connect(self._toggle_instruction_editor)
        instr_row.addWidget(self.btn_edit)
        self.btn_instr_save = QPushButton("Save as new…")
        self.btn_instr_save.clicked.connect(self._save_instruction_as)
        instr_row.addWidget(self.btn_instr_save)
        self.btn_instr_del = QPushButton("Delete")
        self.btn_instr_del.setToolTip("Delete one of your own instructions. Built-ins cannot be deleted.")
        self.btn_instr_del.clicked.connect(self._delete_instruction)
        instr_row.addWidget(self.btn_instr_del)
        rl.addLayout(instr_row)

        self.instr_edit = QPlainTextEdit()
        self.instr_edit.setPlaceholderText("The instruction text…")
        self.instr_edit.setFixedHeight(160)
        self.instr_edit.hide()
        rl.addWidget(self.instr_edit)

        rl.addWidget(QLabel("What should the story be about?"))
        self.concept_edit = QPlainTextEdit()
        self.concept_edit.setPlaceholderText(
            'e.g. "a detective works a locked-room murder in a snowbound hotel"')
        self.concept_edit.setFixedHeight(70)
        rl.addWidget(self.concept_edit)

        gen_row = QHBoxLayout()
        self.btn_brief = QPushButton("Generate Brief")
        self.btn_brief.setToolTip(
            "Ask the Storyboard model to turn the concept above into the two "
            "prompts below. Saved to briefs/ automatically.")
        self.btn_brief.clicked.connect(self.generate_brief)
        gen_row.addWidget(self.btn_brief)
        self.btn_write = QPushButton("Write Story")
        self.btn_write.setToolTip(
            "Send the two prompts below to the Scene Writer model in ONE call. "
            "Needs a big Max tokens — a 6000-word story is roughly 8000 tokens.")
        self.btn_write.clicked.connect(self.write_story)
        gen_row.addWidget(self.btn_write)
        gen_row.addStretch(1)
        rl.addLayout(gen_row)

        # hand-written prompts kept as .txt, picked instead of generating a brief
        files_row = QHBoxLayout()
        files_row.addWidget(QLabel("Or load prompt files:"))
        self.sys_file_box = QComboBox()
        self.sys_file_box.setToolTip(
            f"Plain .txt files in {prj.SYSTEM_PROMPTS_DIR.name}/ — selecting "
            "one loads it into the System prompt box below.")
        self.sys_file_box.currentTextChanged.connect(self._load_system_file)
        self.user_file_box = QComboBox()
        self.user_file_box.setToolTip(
            f"Plain .txt files in {prj.USER_PROMPTS_DIR.name}/ — selecting one "
            "loads it into the User prompt box below.")
        self.user_file_box.currentTextChanged.connect(self._load_user_file)
        files_row.addWidget(QLabel("system:"))
        files_row.addWidget(self.sys_file_box, 1)
        files_row.addWidget(QLabel("user:"))
        files_row.addWidget(self.user_file_box, 1)
        self.btn_files_refresh = QPushButton("↻")
        self.btn_files_refresh.setFixedWidth(28)
        self.btn_files_refresh.clicked.connect(self.refresh_prompt_files)
        files_row.addWidget(self.btn_files_refresh)
        self.btn_files_save = QPushButton("Save boxes as .txt…")
        self.btn_files_save.setToolTip(
            "Write the two boxes below to the prompt folders, so this pair can "
            "be picked again here or reused in another app.")
        self.btn_files_save.clicked.connect(self._save_prompt_files)
        files_row.addWidget(self.btn_files_save)
        rl.addLayout(files_row)

        rl.addWidget(QLabel("System prompt — how to write it:"))
        self.system_edit = QPlainTextEdit()
        self.system_edit.setPlaceholderText(
            "Generated by the brief, or paste your own…")
        rl.addWidget(self.system_edit, 2)
        rl.addWidget(QLabel("User prompt — what to write:"))
        self.user_edit = QPlainTextEdit()
        self.user_edit.setPlaceholderText(
            "Generated by the brief, or paste your own…")
        rl.addWidget(self.user_edit, 2)

        # the usual way this mode fails is a budget sized for one scene, so
        # say so before the run rather than after it is cut off
        self.budget_label = QLabel("")
        self.budget_label.setWordWrap(True)
        rl.addWidget(self.budget_label)

        self.story_label = QLabel("Story")
        rl.addWidget(self.story_label)
        self.story_edit = QPlainTextEdit()
        self.story_edit.setPlaceholderText("The finished story appears here (streamed live)…")
        rl.addWidget(self.story_edit, 3)
        self.counter = QLabel("0 words · ≈0 tokens")
        rl.addWidget(self.counter)
        self.issues_label = QLabel("")
        self.issues_label.setWordWrap(True)
        self.issues_label.setStyleSheet("color: #d08770;")
        self.issues_label.hide()
        rl.addWidget(self.issues_label)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        self.story_edit.textChanged.connect(self._count)
        self.state.busy_changed.connect(self._busy_changed)
        self.refresh_instructions()
        self.refresh_briefs()
        self.refresh_prompt_files()
        self.update_budget_hint()

    # -- instruction library ------------------------------------------------

    def refresh_instructions(self):
        self._loading = True
        current = self.state.ui.get("brief_instruction",
                                    briefs.DEFAULT_INSTRUCTION_NAME)
        self.instr_box.clear()
        names = briefs.list_instructions()
        self.instr_box.addItems(names)
        if current in names:
            self.instr_box.setCurrentText(current)
        self._loading = False
        self.instr_edit.setPlainText(briefs.get_instruction(self.instr_box.currentText()))

    def _instruction_changed(self, name: str):
        if self._loading or not name:
            return
        self.state.ui["brief_instruction"] = name
        self.instr_edit.setPlainText(briefs.get_instruction(name))

    def _toggle_instruction_editor(self, shown: bool):
        self.instr_edit.setVisible(shown)

    def _save_instruction_as(self):
        name, ok = QInputDialog.getText(self, "Save instruction",
                                        "Name for this instruction:")
        if not ok or not name.strip():
            return
        briefs.save_custom_instruction(name.strip(), self.instr_edit.toPlainText())
        self.state.ui["brief_instruction"] = prj.slugify(name.strip())
        self.refresh_instructions()
        self.main.statusBar().showMessage(f"Saved instruction “{name.strip()}”.")

    def _delete_instruction(self):
        name = self.instr_box.currentText()
        if briefs.is_builtin(name):
            self.main.statusBar().showMessage(
                "Built-in instructions cannot be deleted — save your own copy instead.")
            return
        if QMessageBox.question(self, "Delete instruction",
                                f"Delete “{name}”?") != QMessageBox.Yes:
            return
        briefs.delete_custom_instruction(name)
        self.state.ui["brief_instruction"] = briefs.DEFAULT_INSTRUCTION_NAME
        self.refresh_instructions()

    # -- hand-written .txt prompt files -------------------------------------

    def refresh_prompt_files(self):
        """Reload both dropdowns, keeping the current pick where possible."""
        for box, kind in ((self.sys_file_box, briefs.SYSTEM),
                          (self.user_file_box, briefs.USER)):
            current = box.currentText()
            box.blockSignals(True)
            box.clear()
            # a blank first entry means "none chosen", so opening the tab does
            # not overwrite whatever is already in the prompt boxes
            box.addItem("")
            box.addItems(briefs.list_prompt_files(kind))
            idx = box.findText(current)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            box.blockSignals(False)

    def _load_system_file(self, name: str):
        if name:
            self.system_edit.setPlainText(
                briefs.load_prompt_file(briefs.SYSTEM, name))

    def _load_user_file(self, name: str):
        if name:
            self.user_edit.setPlainText(
                briefs.load_prompt_file(briefs.USER, name))

    def _save_prompt_files(self):
        if not self.user_edit.toPlainText().strip():
            self.main.statusBar().showMessage("Nothing to save — the prompt boxes are empty.")
            return
        name, ok = QInputDialog.getText(
            self, "Save prompt files",
            "Name for this pair (both files get this name):")
        if not ok or not name.strip():
            return
        written = []
        if self.system_edit.toPlainText().strip():
            written.append(briefs.save_prompt_file(
                briefs.SYSTEM, name.strip(), self.system_edit.toPlainText()))
        written.append(briefs.save_prompt_file(
            briefs.USER, name.strip(), self.user_edit.toPlainText()))
        self.refresh_prompt_files()
        self.main.statusBar().showMessage(
            "Saved " + ", ".join(f"{p.parent.name}/{p.name}" for p in written))

    # -- saved briefs -------------------------------------------------------

    def refresh_briefs(self):
        self._loading = True
        self.list.clear()
        self._files = briefs.list_briefs()
        for name in self._files:
            brief = briefs.load_brief(name)
            self.list.addItem(brief.title if brief and brief.title else name)
        self._loading = False

    def _brief_selected(self, row: int):
        if self._loading or not (0 <= row < len(self._files)):
            return
        brief = briefs.load_brief(self._files[row])
        if brief is None:
            return
        self._current_file = self._files[row]
        self.concept_edit.setPlainText(brief.concept)
        self.system_edit.setPlainText(brief.system_prompt)
        self.user_edit.setPlainText(brief.user_prompt)
        if brief.instruction_name:
            idx = self.instr_box.findText(brief.instruction_name)
            if idx >= 0:
                self.instr_box.setCurrentIndex(idx)

    def _save_edits(self):
        if not self._current_file:
            self.main.statusBar().showMessage("Select a saved brief first.")
            return
        brief = briefs.load_brief(self._current_file)
        if brief is None:
            return
        brief.system_prompt = self.system_edit.toPlainText().strip()
        brief.user_prompt = self.user_edit.toPlainText().strip()
        brief.concept = self.concept_edit.toPlainText().strip()
        briefs.overwrite_brief(self._current_file, brief)
        self.main.statusBar().showMessage("Brief saved.")

    def _delete_brief(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self._files)):
            return
        if QMessageBox.question(self, "Delete brief",
                                "Delete the selected brief?") != QMessageBox.Yes:
            return
        briefs.delete_brief(self._files[row])
        self._current_file = ""
        self.refresh_briefs()

    # -- generation ---------------------------------------------------------

    def generate_brief(self):
        concept = self.concept_edit.toPlainText().strip()
        if not concept:
            self.main.statusBar().showMessage("Describe what the story should be about first.")
            return
        instruction = self.instr_edit.toPlainText().strip() or \
            briefs.get_instruction(self.instr_box.currentText())
        cfg = self.state.runtime_cfg("storyboard")
        name = self.instr_box.currentText()
        self._buffer = ""
        self.system_edit.setPlainText("")
        self.user_edit.setPlainText("(waiting for the model…)")

        def job(worker):
            return pipeline.generate_brief(
                cfg, concept, instruction,
                cancel=worker.cancel, on_chunk=worker.chunk.emit)

        def chunk(piece: str):
            self._buffer += piece
            self.user_edit.setPlainText(self._buffer)
            cursor = self.user_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.user_edit.setTextCursor(cursor)

        def done(pair):
            self.system_edit.setPlainText(pair.get("system_prompt", ""))
            self.user_edit.setPlainText(pair.get("user_prompt", ""))
            brief = briefs.Brief(
                concept=concept, instruction_name=name,
                system_prompt=pair.get("system_prompt", ""),
                user_prompt=pair.get("user_prompt", ""),
                gen_info={"backend": cfg.backend, "model": cfg.model})
            path = briefs.save_brief(brief)
            self._current_file = path.name
            self.refresh_briefs()
            self.main.statusBar().showMessage(f"Brief saved as {path.name}.")

        self.main.run_job(job, on_chunk=chunk, on_done=done,
                          status="Writing the brief…")

    def write_story(self):
        system = self.system_edit.toPlainText().strip()
        user = self.user_edit.toPlainText().strip()
        if not user:
            self.main.statusBar().showMessage(
                "Generate a brief first, or paste a user prompt.")
            return
        cfg = self.state.runtime_cfg("writer")
        language = self.state.ui.get("story_language", "English")
        title = self.concept_edit.toPlainText().strip()[:60] or "single-output story"
        brief_name = self._current_file
        self._buffer = ""
        self.story_edit.setPlainText("")
        self.issues_label.hide()

        def job(worker):
            return pipeline.generate_single_story(
                cfg, system, user, language,
                cancel=worker.cancel, on_chunk=worker.chunk.emit)

        def chunk(piece: str):
            self._buffer += piece
            self.story_edit.setPlainText(self._buffer)
            cursor = self.story_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.story_edit.setTextCursor(cursor)

        def done(text: str):
            self.story_edit.setPlainText(text)
            issues = pipeline.check_single_story(text)
            self.issues_label.setText("\n".join(issues))
            self.issues_label.setVisible(bool(issues))
            story = prj.make_single_output_project(title, text, brief_name, issues)
            story.save()
            path = story.export()
            self.state.project = story
            self.state.project_changed.emit()   # refreshes the Complete Story list
            self.main.statusBar().showMessage(f"Story written and exported to {path.name}.")

        self.main.run_job(job, on_chunk=chunk, on_done=done,
                          status="Writing the whole story in one call…")

    # -- live view of a brief written by a batch run ------------------------

    def begin_external_brief(self):
        self._buffer = ""
        self.system_edit.setPlainText("")
        self.user_edit.setPlainText("")

    def stream_brief_piece(self, piece: str):
        self._buffer += piece
        self.user_edit.setPlainText(self._buffer)
        cursor = self.user_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.user_edit.setTextCursor(cursor)

    def begin_external_story(self, _index: int = 0):
        self._buffer = ""
        self.story_edit.setPlainText("")
        self.issues_label.hide()

    def stream_story_piece(self, _index: int, piece: str):
        self._buffer += piece
        self.story_edit.setPlainText(self._buffer)
        cursor = self.story_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.story_edit.setTextCursor(cursor)

    def external_story_done(self, _index: int = 0):
        story = self.state.project
        if story is None or not story.scenes:
            return
        self.story_edit.setPlainText(story.scenes[0].text)
        issues = list(getattr(story.scenes[0], "issues", []) or [])
        self.issues_label.setText("\n".join(issues))
        self.issues_label.setVisible(bool(issues))

    def external_brief_done(self, filename: str):
        self.refresh_briefs()
        brief = briefs.load_brief(filename)
        if brief is not None:
            self._current_file = filename
            self.system_edit.setPlainText(brief.system_prompt)
            self.user_edit.setPlainText(brief.user_prompt)
            self.concept_edit.setPlainText(brief.concept)

    # -- small helpers ------------------------------------------------------

    def _count(self):
        text = self.story_edit.toPlainText()
        words = len(text.split())
        self.counter.setText(
            f"{words} words · ≈{pipeline.estimate_tokens(text)} tokens")

    def _busy_changed(self, busy: bool):
        self.btn_brief.setEnabled(not busy)
        self.btn_write.setEnabled(not busy)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh_prompt_files()   # files may have been added outside the app
        self.update_budget_hint()

    def update_budget_hint(self):
        """Warn when the budgets cannot hold a whole story or a whole brief.

        A brief asks for 5500-9000 words — roughly 7000-12000 tokens — while
        the Scene Writer default is sized for one 900-word scene.
        """
        writer = self.state.runtime_cfg("writer")
        board = self.state.runtime_cfg("storyboard")
        notes = []
        if writer.params.max_tokens < 6000:
            notes.append(
                f"Scene Writer Max tokens is {writer.params.max_tokens} — a "
                "whole story needs about 8192. It will be cut off mid-sentence.")
        if writer.context_length < writer.params.max_tokens + 1024:
            notes.append(
                f"Scene Writer context length ({writer.context_length}) leaves "
                "no room for the prompt on top of the reply.")
        if board.params.max_tokens < 2048:
            notes.append(
                f"Storyboard Max tokens is {board.params.max_tokens} — the "
                "brief itself needs about 2048 to come back complete.")
        self.budget_label.setText(
            ("⚠ " + "  ".join(notes)) if notes else
            f"Budgets look right: brief {board.params.max_tokens} tokens, "
            f"story {writer.params.max_tokens} tokens.")
        self.budget_label.setStyleSheet(
            "color: #d08770;" if notes else "color: gray;")
