"""Tab 1 — Story Start: concept input, per-section LLM choice, batch queue."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import applog
import pipeline
import project as prj
from backends import (
    SECTION_KEYS,
    SECTION_LABELS,
    SectionConfig,
    default_section_params,
)
from project import Scene, StoryProject
from ui_common import SectionQuickConfig, make_spin

MODE_ALL_NEW = "all_new"
MODE_SAME_BOARD = "same_board"
MODE_SAME_OUTLINE = "same_outline"

MODE_LABELS = {
    MODE_ALL_NEW: "all new",
    MODE_SAME_BOARD: "same storyboard",
    MODE_SAME_OUTLINE: "same storyboard + outline",
}


@dataclass
class BatchSpec:
    """A queued batch run — a full snapshot of every setting at enqueue time."""
    concept: str = ""
    mode: str = MODE_ALL_NEW
    count: int = 1
    num_scenes: int = 6
    target_length: int = 900
    context_mode: str = "prev_full"
    language: str = "English"
    plan_in_language: bool = False
    track_cast: bool = True
    cfg_board: SectionConfig = field(default_factory=SectionConfig)
    cfg_plan: SectionConfig = field(default_factory=SectionConfig)
    cfg_write: SectionConfig = field(default_factory=SectionConfig)
    cfg_summ: SectionConfig = field(default_factory=SectionConfig)
    preset_board: str = ""
    preset_board_name: str = ""

    def label_for(self, count: int) -> str:
        concept = (self.concept[:38] + "…") if len(self.concept) > 40 else self.concept
        concept = concept.replace("\n", " ") or self.preset_board_name or "(no concept)"
        writer = self.cfg_write.model or self.cfg_write.backend
        return (f"{count} story(ies) — {MODE_LABELS[self.mode]} — "
                f"“{concept}” — writer: {writer}")

    @property
    def label(self) -> str:
        return self.label_for(self.count)


class BatchBridge(QObject):
    """Thread-safe signals so batch runs update the tabs live."""
    story_started = Signal(int, int)  # story number, total count
    board_started = Signal()
    board_chunk = Signal(str)
    board_done = Signal(str)          # storyboard name saved to the library
    outline_started = Signal()
    outline_chunk = Signal(str)
    project_ready = Signal(object)    # StoryProject with a fresh outline
    scene_started = Signal(int)
    scene_chunk = Signal(int, str)
    scene_done = Signal(int)
    summary_started = Signal(int)
    summary_chunk = Signal(int, str)
    summary_done = Signal(int)
    story_done = Signal(str)          # project name saved + exported


class StartTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self.current_spec: BatchSpec | None = None
        self.current_story_progress: tuple[int, int] | None = None  # (num, count)
        self._stop_all = False

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        # -- concept ---------------------------------------------------------
        concept_group = QGroupBox("Story concept")
        cg = QVBoxLayout(concept_group)

        # the radios live in their own row with fixed spacing, independent of
        # whatever widgets are shown below them
        radio_row = QHBoxLayout()
        radio_row.setSpacing(24)
        self.radio_text = QRadioButton("Free text")
        self.radio_file = QRadioButton("From saved description file")
        self.radio_board = QRadioButton("Use existing storyboard")
        self.radio_board.setToolTip(
            "Skip storyboard generation: every story in the batch is built "
            "from this finished storyboard (fresh outline + scenes each run).")
        self.radio_text.setChecked(True)
        radio_row.addWidget(self.radio_text)
        radio_row.addWidget(self.radio_file)
        radio_row.addWidget(self.radio_board)
        radio_row.addStretch(1)
        cg.addLayout(radio_row)

        # one panel per source mode; only the selected panel is shown
        self.free_panel = QWidget()
        fp = QVBoxLayout(self.free_panel)
        fp.setContentsMargins(0, 0, 0, 0)
        self.concept_edit = QPlainTextEdit()
        self.concept_edit.setPlaceholderText(
            'Describe your story idea, e.g. "a detective story that is a whodunit '
            'set in 1920s London"'
        )
        self.concept_edit.setFixedHeight(90)
        fp.addWidget(self.concept_edit)
        self.save_concept_btn = QPushButton("Save as Description File…")
        self.save_concept_btn.setToolTip(
            "Save this story idea to story-descriptions/ so it can be picked "
            "from the file dropdown later.")
        self.save_concept_btn.clicked.connect(self.save_concept_as_description)
        row = QHBoxLayout()
        row.addWidget(self.save_concept_btn)
        row.addStretch(1)
        fp.addLayout(row)
        cg.addWidget(self.free_panel)

        self.file_panel = QWidget()
        flp = QVBoxLayout(self.file_panel)
        flp.setContentsMargins(0, 0, 0, 0)
        pick_row = QHBoxLayout()
        self.file_box = QComboBox()
        self.file_refresh_btn = QPushButton("↻")
        self.file_refresh_btn.setFixedWidth(28)
        self.file_refresh_btn.clicked.connect(self.refresh_descriptions)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.browse_description)
        pick_row.addWidget(self.file_box, 1)
        pick_row.addWidget(self.file_refresh_btn)
        pick_row.addWidget(self.browse_btn)
        flp.addLayout(pick_row)
        self.file_preview = QPlainTextEdit()
        self.file_preview.setMinimumHeight(160)
        self.file_preview.setPlaceholderText(
            "The selected description file — editable; changes are saved to the file.")
        self.file_preview.textChanged.connect(self._preview_edited)
        flp.addWidget(self.file_preview)
        self.save_file_btn = QPushButton("Save Changes to File")
        self.save_file_btn.setEnabled(False)
        self.save_file_btn.clicked.connect(self.save_description_edits)
        row = QHBoxLayout()
        row.addWidget(self.save_file_btn)
        row.addStretch(1)
        flp.addLayout(row)
        cg.addWidget(self.file_panel)

        self.board_panel = QWidget()
        bp = QVBoxLayout(self.board_panel)
        bp.setContentsMargins(0, 0, 0, 0)
        board_row = QHBoxLayout()
        self.board_box = QComboBox()
        self.board_refresh_btn = QPushButton("↻")
        self.board_refresh_btn.setFixedWidth(28)
        self.board_refresh_btn.clicked.connect(self.refresh_storyboards)
        board_row.addWidget(self.board_box, 1)
        board_row.addWidget(self.board_refresh_btn)
        bp.addLayout(board_row)
        self.board_preview = QPlainTextEdit()
        self.board_preview.setMinimumHeight(300)
        self.board_preview.setPlaceholderText(
            "The selected storyboard — editable; changes are saved to the "
            "storyboard file (also visible in the Storyboards tab).")
        self.board_preview.textChanged.connect(self._board_edited)
        bp.addWidget(self.board_preview)
        self.save_board_btn = QPushButton("Save Changes to Storyboard")
        self.save_board_btn.setEnabled(False)
        self.save_board_btn.clicked.connect(self.save_board_edits)
        row = QHBoxLayout()
        row.addWidget(self.save_board_btn)
        row.addStretch(1)
        bp.addLayout(row)
        cg.addWidget(self.board_panel)
        lay.addWidget(concept_group)

        self._preview_loading = False
        self._preview_dirty = False
        self._preview_name = ""  # file currently shown in the preview editor
        self._board_loading = False
        self._board_dirty = False
        self._board_name = ""    # storyboard currently shown in its editor
        self.file_box.currentTextChanged.connect(self._preview_file)
        self.board_box.currentTextChanged.connect(self._board_selected)
        self.radio_file.toggled.connect(self._concept_mode_changed)
        self.radio_board.toggled.connect(self._concept_mode_changed)
        self._concept_mode_changed()
        self.refresh_descriptions()
        self.refresh_storyboards()
        self.state.descriptions_changed.connect(self.refresh_descriptions)
        self.state.storyboards_changed.connect(self.refresh_storyboards)

        # -- story shape -----------------------------------------------------
        shape_group = QGroupBox("Story shape")
        sg = QGridLayout(shape_group)
        self.scenes_spin = make_spin(self.state.ui.get("num_scenes", 6), 1, 40)
        self.length_spin = make_spin(self.state.ui.get("target_length", 900), 200, 3000, 50)
        sg.addWidget(QLabel("Number of scenes"), 0, 0)
        sg.addWidget(self.scenes_spin, 0, 1)
        sg.addWidget(QLabel("Target words per scene"), 0, 2)
        sg.addWidget(self.length_spin, 0, 3)
        sg.setColumnStretch(4, 1)
        self.scenes_spin.valueChanged.connect(
            lambda v: self.state.ui.__setitem__("num_scenes", int(v)))
        self.length_spin.valueChanged.connect(
            lambda v: self.state.ui.__setitem__("target_length", int(v)))

        sg.addWidget(QLabel("Context between scenes:"), 1, 0)
        self.ctx_summaries = QRadioButton(
            "Summaries of previous scenes + the ending of the last one (cheapest)")
        self.ctx_prev_full = QRadioButton(
            "Summaries of older scenes + the LAST SCENE IN FULL (recommended — "
            "best natural continuation)")
        self.ctx_full = QRadioButton(
            "Full text of all previous scenes (no summaries — heaviest)")
        self.ctx_summaries.setToolTip(
            "Scene 5 gets: summaries of scenes 1-4 + the last ~500 tokens of scene 4.")
        self.ctx_prev_full.setToolTip(
            "Scene 5 gets: summaries of scenes 1-3 + the complete text of scene 4, "
            "so the new scene can pick up naturally from everything that just "
            "happened — not only from the final paragraph.")
        self.ctx_full.setToolTip(
            "Scene 5 gets: the complete text of scenes 1-4. Most faithful, but "
            "runs out of context after a few scenes.")
        mode = self.state.ui.get("context_mode", "prev_full")
        if mode == "full":
            self.ctx_full.setChecked(True)
        elif mode == "summaries":
            self.ctx_summaries.setChecked(True)
        else:
            self.ctx_prev_full.setChecked(True)
        sg.addWidget(self.ctx_summaries, 1, 1, 1, 3)
        sg.addWidget(self.ctx_prev_full, 2, 1, 1, 3)
        sg.addWidget(self.ctx_full, 3, 1, 1, 3)
        for rb in (self.ctx_summaries, self.ctx_prev_full, self.ctx_full):
            rb.toggled.connect(self._ctx_mode_changed)
        ctx_hint = (
            "\n\nToken budget: storyboard + style guide + lorebook + context of "
            "earlier scenes + the Scene Writer's Max tokens (reply reserve) must "
            "fit in the server's context length (LLM Settings). Summaries are "
            "≈170 tok/scene, Detailed-Summary ≈340, a full scene ≈1300+ — the "
            "Scene Writer tab shows the live budget per scene.")
        for w in (self.ctx_summaries, self.ctx_prev_full, self.ctx_full):
            w.setToolTip(w.toolTip() + ctx_hint)

        self.track_cast_check = QCheckBox(
            "Track characters automatically (after each scene, note who "
            "appeared so later scenes keep names, roles and genders straight)")
        self.track_cast_check.setToolTip(
            "Costs one small extra call per scene, made with the Summarizer "
            "model. The story board's cast is always included; this adds "
            "everyone the scenes introduce. Turn off for the fastest batches.")
        self.track_cast_check.setChecked(bool(self.state.ui.get("track_cast", True)))
        self.track_cast_check.toggled.connect(
            lambda on: self.state.ui.__setitem__("track_cast", bool(on)))
        sg.addWidget(self.track_cast_check, 4, 0, 1, 4)

        sg.addWidget(QLabel("Story language:"), 5, 0)
        self.language_box = QComboBox()
        self.language_box.setEditable(True)
        self.language_box.addItems([
            "English", "Svenska", "Norsk", "Dansk", "Suomi",
            "Deutsch", "Français", "Español",
        ])
        self.language_box.setEditText(self.state.ui.get("story_language", "English"))
        self.language_box.setToolTip(
            "The Scene Writer (and scene summaries) write in this language. "
            "You can type any language.")
        sg.addWidget(self.language_box, 5, 1)
        self.plan_lang_check = QCheckBox(
            "Also write storyboard && outline in this language "
            "(English planning recommended — most models plan best in English)")
        self.plan_lang_check.setChecked(bool(self.state.ui.get("plan_in_language", False)))
        sg.addWidget(self.plan_lang_check, 5, 2, 1, 2)
        self.language_box.editTextChanged.connect(
            lambda t: self.state.ui.__setitem__("story_language", t.strip() or "English"))
        self.plan_lang_check.toggled.connect(
            lambda on: self.state.ui.__setitem__("plan_in_language", bool(on)))
        lay.addWidget(shape_group)

        # -- per-section LLM config -------------------------------------------
        llm_group = QGroupBox(
            "LLM per section — each parameter is a min–max range; a value is "
            "rolled per story (keep min = max for a fixed value)")
        lg = QGridLayout(llm_group)
        self.section_widgets: dict[str, SectionQuickConfig] = {}
        for i, key in enumerate(SECTION_KEYS):
            w = SectionQuickConfig(
                SECTION_LABELS[key],
                self.state.sections[key],
                runtime_cfg=lambda k=key: self.state.runtime_cfg(k),
                # resolve the status bar at call time, never capture it
                status_cb=lambda msg: self.main.statusBar().showMessage(msg),
                defaults_factory=lambda k=key: default_section_params(k),
            )
            self.section_widgets[key] = w
            lg.addWidget(w, 0, i)  # all four sections side by side in one row
        lay.addWidget(llm_group)

        # -- tips (collapsed by default) ----------------------------------------
        tips_group = QGroupBox("Tips: settings for better / more varied stories (click to show)")
        tips_group.setCheckable(True)
        tips_group.setChecked(False)
        tg = QVBoxLayout(tips_group)
        tips_label = QLabel(
            "• Every parameter is a min–max range: each story rolls one random "
            "value inside it, so a batch gives varied stories. Keep min = max "
            "for a fixed value.\n"
            "• More variety between stories: Scene Writer temperature range "
            "0.7 – 1.1 (higher max = wilder prose, above ~1.3 gets incoherent).\n"
            "• Smoothing factor 0.15 – 0.3 (KoboldCpp only, on by default): keeps "
            "creative high-temperature prose coherent — the popular fiction "
            "recipe is temperature ≈1.0 + smoothing 0.2–0.3. Other backends "
            "ignore it.\n"
            "• Repetitive prose? Raise Repeat penalty slightly (1.05 – 1.15). "
            "Too high (>1.3) makes text weird.\n"
            "• Story cut off mid-sentence → raise that section's Max tokens. "
            "'TOO BIG' in the Scene Writer's budget line → lower the Scene "
            "Writer's Max tokens (or raise the server context, see LLM Settings).\n"
            "• Thinking models (Qwen3…): give the Scene Writer 4096+ Max tokens — "
            "hidden reasoning eats from the same budget.\n"
            "• Same plot, different tellings: pick 'Use existing storyboard' + "
            "'Same storyboard + outline' and run a batch — only the prose "
            "(and the rolled parameters) differ per story."
        )
        tips_label.setWordWrap(True)
        tips_label.setVisible(False)
        tg.addWidget(tips_label)
        tips_group.toggled.connect(tips_label.setVisible)
        lay.addWidget(tips_group)

        # -- actions -----------------------------------------------------------
        action_row = QHBoxLayout()
        self.create_btn = QPushButton("Create Storyboard")
        self.create_btn.setToolTip(
            "Generate a storyboard from the concept — it lands in the Storyboards tab."
        )
        self.create_btn.clicked.connect(self.create_storyboard)
        action_row.addWidget(self.create_btn)
        action_row.addStretch(1)
        lay.addLayout(action_row)

        # -- batch runs ---------------------------------------------------------
        batch_group = QGroupBox("Batch runs — generate several complete stories unattended")
        bg = QGridLayout(batch_group)
        bg.addWidget(QLabel("Number of stories"), 0, 0)
        self.batch_spin = make_spin(1, 1, 100)
        bg.addWidget(self.batch_spin, 0, 1)

        self.mode_all_new = QRadioButton(
            "All new each time — new storyboard + outline + scenes per story")
        self.mode_same_board = QRadioButton(
            "Same storyboard — one storyboard, fresh outline + scenes per story")
        self.mode_same_outline = QRadioButton(
            "Same storyboard + outline — only the prose differs per story")
        self.mode_all_new.setChecked(True)
        bg.addWidget(self.mode_all_new, 1, 0, 1, 3)
        bg.addWidget(self.mode_same_board, 2, 0, 1, 3)
        bg.addWidget(self.mode_same_outline, 3, 0, 1, 3)

        hint = QLabel(
            "Run Batch snapshots ALL current settings into a queue item (see the "
            "Queue tab). You can change settings and queue another batch while one "
            "is running — every queued batch keeps the settings it was created with. "
            "Reuse modes use the storyboard selected in the Storyboards tab, or "
            "generate one first if none is selected."
        )
        hint.setWordWrap(True)
        bg.addWidget(hint, 4, 0, 1, 3)

        run_row = QHBoxLayout()
        self.batch_btn = QPushButton("Run Batch / Add to Queue")
        self.batch_btn.clicked.connect(self.run_batch)
        self.stop_btn = QPushButton("Stop All")
        self.stop_btn.setToolTip("Cancel the running batch and clear the queue.")
        self.stop_btn.clicked.connect(self.stop_all)
        self.stop_btn.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setValue(0)
        run_row.addWidget(self.batch_btn)
        run_row.addWidget(self.stop_btn)
        run_row.addWidget(self.progress, 1)
        bg.addLayout(run_row, 5, 0, 1, 3)
        lay.addWidget(batch_group)
        lay.addStretch(1)

        self.state.busy_changed.connect(self._busy_changed)
        self._concept_mode_changed()  # now that all widgets exist

    # -- concept helpers -------------------------------------------------------

    def _concept_mode_changed(self):
        from_file = self.radio_file.isChecked()
        from_board = self.radio_board.isChecked()
        free_text = not from_file and not from_board
        # show ONLY the panel of the selected source
        self.free_panel.setVisible(free_text)
        self.file_panel.setVisible(from_file)
        self.board_panel.setVisible(from_board)
        if hasattr(self, "create_btn"):  # widgets built after this group
            self.create_btn.setEnabled(not from_board and not self.main.is_busy())
            # with a finished storyboard, "all new each time" makes no sense
            self.mode_all_new.setEnabled(not from_board)
            if from_board and self.mode_all_new.isChecked():
                self.mode_same_board.setChecked(True)

    def refresh_descriptions(self):
        current = self.file_box.currentText()
        self.file_box.blockSignals(True)
        self.file_box.clear()
        self.file_box.addItems(prj.list_descriptions())
        self.file_box.blockSignals(False)
        if current:
            idx = self.file_box.findText(current)
            if idx >= 0:
                self.file_box.setCurrentIndex(idx)
        self._preview_file()

    def browse_description(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose story description", str(prj.DESCRIPTIONS_DIR), "Text files (*.txt *.md)")
        if path:
            text = prj.Path(path).read_text(encoding="utf-8")
            self.radio_text.setChecked(True)
            self.concept_edit.setPlainText(text)

    def _preview_file(self):
        self._flush_preview_edits()  # keep edits when switching files
        name = self.file_box.currentText()
        self._preview_loading = True
        self.file_preview.setPlainText(prj.load_description(name) if name else "")
        self._preview_loading = False
        self._preview_name = name
        self._preview_dirty = False
        self.save_file_btn.setEnabled(False)

    def _preview_edited(self):
        if self._preview_loading or not self._preview_name:
            return
        self._preview_dirty = True
        self.save_file_btn.setEnabled(True)

    def _flush_preview_edits(self):
        """Write pending preview edits back to their file."""
        if self._preview_dirty and self._preview_name:
            (prj.DESCRIPTIONS_DIR / self._preview_name).write_text(
                self.file_preview.toPlainText(), encoding="utf-8")
            self._preview_dirty = False
            self.save_file_btn.setEnabled(False)

    def save_description_edits(self):
        name = self._preview_name
        self._flush_preview_edits()
        if name:
            self.main.statusBar().showMessage(f"Saved changes to {name}.")

    # -- existing-storyboard source helpers -------------------------------------

    def refresh_storyboards(self):
        current = self.board_box.currentText()
        self.board_box.blockSignals(True)
        self.board_box.clear()
        self.board_box.addItems(prj.list_storyboards())
        self.board_box.blockSignals(False)
        if current:
            idx = self.board_box.findText(current)
            if idx >= 0:
                self.board_box.setCurrentIndex(idx)
        self._board_selected()

    def _board_selected(self, *_args):
        self._flush_board_edits()  # keep edits when switching storyboards
        name = self.board_box.currentText()
        self._board_loading = True
        self.board_preview.setPlainText(prj.load_storyboard(name) if name else "")
        self._board_loading = False
        self._board_name = name
        self._board_dirty = False
        self.save_board_btn.setEnabled(False)

    def _board_edited(self):
        if self._board_loading or not self._board_name:
            return
        self._board_dirty = True
        self.save_board_btn.setEnabled(True)

    def _flush_board_edits(self):
        """Write pending storyboard edits back to the storyboard file."""
        if self._board_dirty and self._board_name:
            prj.save_storyboard(self._board_name, self.board_preview.toPlainText())
            self._board_dirty = False
            self.save_board_btn.setEnabled(False)
            self.state.storyboards_changed.emit()

    def save_board_edits(self):
        name = self._board_name
        self._flush_board_edits()
        if name:
            self.main.statusBar().showMessage(f"Saved changes to storyboard '{name}'.")

    def save_concept_as_description(self):
        text = self.concept_edit.toPlainText().strip()
        if not text:
            self.main.statusBar().showMessage("Write a story idea first.")
            return
        default = prj.slugify(" ".join(text.split()[:5]))
        name, ok = QInputDialog.getText(
            self, "Save description file", "File name:", text=default)
        if not ok or not name.strip():
            return
        path = prj.save_description(name.strip(), text)
        self.state.descriptions_changed.emit()
        idx = self.file_box.findText(path.name)
        if idx >= 0:
            self.file_box.setCurrentIndex(idx)
        self.main.statusBar().showMessage(
            f"Saved {path.name} — selectable under 'From saved description file'.")

    def get_concept(self) -> str:
        if self.radio_file.isChecked():
            self._flush_preview_edits()  # generation uses what you see
            return self.file_preview.toPlainText().strip()
        return self.concept_edit.toPlainText().strip()

    def _ctx_mode_changed(self):
        if self.ctx_full.isChecked():
            mode = "full"
        elif self.ctx_summaries.isChecked():
            mode = "summaries"
        else:
            mode = "prev_full"
        self.state.ui["context_mode"] = mode

    def sync_section_widgets(self):
        for w in self.section_widgets.values():
            w.sync_from_config()

    def _busy_changed(self, busy: bool):
        self.create_btn.setEnabled(not busy and not self.radio_board.isChecked())
        # batch button stays enabled — clicking while busy queues another batch
        self.stop_btn.setEnabled(busy or bool(self.state.job_queue))
        if not busy:
            # a non-batch job may have finished — resume the queue if needed
            QTimer.singleShot(0, self._process_queue)

    # -- single storyboard -------------------------------------------------------

    def create_storyboard(self):
        if self.radio_board.isChecked():
            self.main.statusBar().showMessage(
                "You are using an existing storyboard — switch to Free text or "
                "a description file to create a new one.")
            return
        concept = self.get_concept()
        if not concept:
            self.main.statusBar().showMessage("Enter a story concept first.")
            return
        num_scenes = self.scenes_spin.value()
        cfg = self.state.runtime_cfg("storyboard").rolled()
        language = self.state.ui.get("story_language", "English")
        plan_in_language = bool(self.state.ui.get("plan_in_language", False))
        board_tab = self.main.tab_storyboard
        self.state.save_settings()

        # jump to the storyboard tab so the text can be watched streaming in
        self.main.tabs.setCurrentWidget(board_tab)
        board_tab.begin_external_stream()

        def job(worker):
            text = pipeline.generate_storyboard(
                cfg, concept, num_scenes, language=language,
                plan_in_language=plan_in_language, cancel=worker.cancel,
                on_chunk=worker.chunk.emit)
            if not text.strip():
                return None
            title = prj.extract_storyboard_title(text) or "storyboard"
            path = prj.unique_path(prj.STORYBOARDS_DIR, prj.slugify(title), ".md")
            path.write_text(text, encoding="utf-8")
            return path.stem

        def done(name):
            board_tab.end_external_stream()
            if not name:
                return
            self.state.selected_storyboard = name
            self.state.ui["batch_boards"] = [name]
            self.state.storyboards_changed.emit()
            board_tab.select_storyboard(name)

        self.main.run_job(job, on_chunk=board_tab.stream_piece, on_done=done,
                          status="Creating storyboard…")

    # -- batch queue ---------------------------------------------------------------

    def run_batch(self):
        """Snapshot all current settings into a BatchSpec and enqueue it."""
        mode = MODE_ALL_NEW
        if self.mode_same_board.isChecked():
            mode = MODE_SAME_BOARD
        elif self.mode_same_outline.isChecked():
            mode = MODE_SAME_OUTLINE

        preset_board = ""
        preset_board_name = ""
        if self.radio_board.isChecked():
            # source = an existing storyboard file: no storyboard generation
            self._flush_board_edits()
            preset_board_name = self.board_box.currentText()
            preset_board = self.board_preview.toPlainText().strip()
            if not preset_board:
                self.main.statusBar().showMessage(
                    "Select a storyboard file (or create one first).")
                return
            if mode == MODE_ALL_NEW:
                mode = MODE_SAME_BOARD
            concept = ""
        else:
            concept = self.get_concept()
            if mode != MODE_ALL_NEW and self.state.selected_storyboard:
                preset_board_name = self.state.selected_storyboard
                preset_board = prj.load_storyboard(preset_board_name)
            if not concept and not preset_board:
                self.main.statusBar().showMessage(
                    "Enter a concept or select a storyboard first.")
                return

        spec = BatchSpec(
            concept=concept,
            mode=mode,
            count=self.batch_spin.value(),
            num_scenes=self.scenes_spin.value(),
            target_length=self.length_spin.value(),
            context_mode=self.state.ui.get("context_mode", "prev_full"),
            language=self.state.ui.get("story_language", "English"),
            plan_in_language=bool(self.state.ui.get("plan_in_language", False)),
            track_cast=bool(self.state.ui.get("track_cast", True)),
            cfg_board=self.state.runtime_cfg("storyboard"),
            cfg_plan=self.state.runtime_cfg("planner"),
            cfg_write=self.state.runtime_cfg("writer"),
            cfg_summ=self.state.runtime_cfg("summarizer"),
            preset_board=preset_board,
            preset_board_name=preset_board_name,
        )
        self.state.job_queue.append(spec)
        self.state.save_settings()  # persist the settings used for this click
        self.state.queue_changed.emit()
        applog.log("queue", f"queued: {spec.label}")
        if self.main.is_busy():
            self.main.statusBar().showMessage(
                f"Added to queue ({len(self.state.job_queue)} waiting) — see the Queue tab.")
        self._process_queue()

    def stop_all(self):
        """Cancel the running batch and clear all upcoming queue items."""
        self._stop_all = True
        self.state.job_queue.clear()
        self.state.queue_changed.emit()
        if self.main.is_busy():
            self.main.cancel_job()
        else:
            self._stop_all = False
        self.stop_btn.setEnabled(False)

    def skip_current(self):
        """Cancel the running batch; the queue continues with the next item."""
        if self.main.is_busy():
            self.main.cancel_job()

    def finish_story_skip_rest(self):
        """Finish the story being generated, drop the batch's remaining stories,
        then continue with the next queue item."""
        if self.main.is_busy():
            self.main.soft_cancel_job()
            self.state.queue_changed.emit()

    def _batch_story_started(self, num: int, count: int):
        self.current_story_progress = (num, count)
        self.state.queue_changed.emit()

    def _process_queue(self):
        if self.main.is_busy() or not self.state.job_queue:
            return
        spec = self.state.job_queue.pop(0)
        self.current_spec = spec
        self.state.queue_changed.emit()
        self._start_batch(spec)

    def _start_batch(self, spec: BatchSpec):
        total_steps = spec.count * (spec.num_scenes + 2)
        self.progress.setRange(0, total_steps)
        self.progress.setValue(0)
        self.state.ui["batch_boards"] = []  # previous run's boards move to "older"
        self.state.storyboards_changed.emit()

        bridge = BatchBridge()
        board_tab = self.main.tab_storyboard
        writer_tab = self.main.tab_writer
        outline_tab = self.main.tab_outline
        bridge.story_started.connect(self._batch_story_started)
        bridge.board_started.connect(board_tab.begin_external_stream)
        bridge.board_chunk.connect(board_tab.stream_piece)
        bridge.board_done.connect(self._batch_board_done)
        bridge.outline_started.connect(outline_tab.begin_stream)
        bridge.outline_chunk.connect(outline_tab.stream_piece)
        bridge.project_ready.connect(self._batch_project_ready)
        bridge.scene_started.connect(writer_tab._on_scene_started)
        bridge.scene_chunk.connect(writer_tab._on_scene_chunk)
        bridge.scene_done.connect(writer_tab._on_scene_done)
        bridge.summary_started.connect(writer_tab._on_summary_started)
        bridge.summary_chunk.connect(writer_tab._on_summary_chunk)
        bridge.summary_done.connect(writer_tab._on_summary_done)
        bridge.story_done.connect(self._batch_story_done)
        self._batch_bridge = bridge  # keep alive while the job runs

        def job(worker):
            step = 0

            def tick():
                nonlocal step
                step += 1
                worker.chunk.emit("")  # progress-only pulse

            board_text = spec.preset_board
            board_name = spec.preset_board_name
            shared_scenes: list[Scene] | None = None
            finished: list[str] = []

            for i in range(spec.count):
                if worker.cancel.is_set() or worker.soft_stop.is_set():
                    break
                label = f"Story {i + 1}/{spec.count}"
                bridge.story_started.emit(i + 1, spec.count)
                # roll fresh parameter values for this story (stable per story)
                cfg_board = spec.cfg_board.rolled()
                cfg_plan = spec.cfg_plan.rolled()
                cfg_write = spec.cfg_write.rolled()
                cfg_summ = spec.cfg_summ.rolled()

                # 1) storyboard
                if spec.mode == MODE_ALL_NEW or not board_text:
                    worker.progress.emit(f"{label} — storyboard…")
                    bridge.board_started.emit()
                    board_text = pipeline.generate_storyboard(
                        cfg_board, spec.concept, spec.num_scenes,
                        language=spec.language,
                        plan_in_language=spec.plan_in_language,
                        cancel=worker.cancel,
                        on_chunk=bridge.board_chunk.emit)
                    if worker.cancel.is_set() or not board_text.strip():
                        break
                    title = prj.extract_storyboard_title(board_text) or "storyboard"
                    path = prj.unique_path(
                        prj.STORYBOARDS_DIR, prj.slugify(title), ".md")
                    path.write_text(board_text, encoding="utf-8")
                    board_name = path.stem
                    bridge.board_done.emit(board_name)
                tick()

                # 2) outline
                if spec.mode == MODE_SAME_OUTLINE and shared_scenes is not None:
                    scenes = [Scene.from_dict(s.to_dict()) for s in shared_scenes]
                    for s in scenes:
                        s.text = ""
                        s.summary = ""
                        s.status = prj.SCENE_OUTLINED
                else:
                    worker.progress.emit(f"{label} — scene outline…")
                    bridge.outline_started.emit()
                    _, scenes = pipeline.generate_outline(
                        cfg_plan, board_text, spec.num_scenes,
                        language=spec.language,
                        plan_in_language=spec.plan_in_language,
                        cancel=worker.cancel,
                        on_chunk=bridge.outline_chunk.emit)
                    if worker.cancel.is_set() or not scenes:
                        break
                    if spec.mode == MODE_SAME_OUTLINE:
                        shared_scenes = [Scene.from_dict(s.to_dict()) for s in scenes]
                tick()

                story = StoryProject(
                    name=prj.new_project_name(
                        prj.extract_storyboard_title(board_text) or "story"),
                    concept=spec.concept,
                    storyboard_name=board_name,
                    storyboard_text=board_text,
                    scenes=scenes,
                    num_scenes=len(scenes),
                    target_length=spec.target_length,
                    gen_info={
                        "context_mode": spec.context_mode,
                        "language": spec.language,
                        "storyboard": {"backend": cfg_board.backend,
                                       "model": cfg_board.model,
                                       "params": cfg_board.params.describe()},
                        "planner": {"backend": cfg_plan.backend,
                                    "model": cfg_plan.model,
                                    "params": cfg_plan.params.describe()},
                        "writer": {"backend": cfg_write.backend,
                                   "model": cfg_write.model,
                                   "params": cfg_write.params.describe()},
                        "summarizer": {"backend": cfg_summ.backend,
                                       "model": cfg_summ.model,
                                       "params": cfg_summ.params.describe()},
                    },
                )
                applog.log("batch", f"{label} '{story.name}' writer params: "
                           f"{cfg_write.params.describe()}")
                bridge.project_ready.emit(story)
                lorebook = prj.load_lorebook(board_name) if board_name else []

                # 3) scenes
                for k in range(len(story.scenes)):
                    if worker.cancel.is_set():
                        break
                    worker.progress.emit(
                        f"{label} — scene {k + 1}/{len(story.scenes)}…")
                    scene = story.scenes[k]
                    bridge.scene_started.emit(k)
                    scene.text = pipeline.generate_scene(
                        cfg_write, story, k, lorebook,
                        context_mode=spec.context_mode, language=spec.language,
                        cancel=worker.cancel,
                        on_chunk=lambda piece, i=k: bridge.scene_chunk.emit(i, piece))
                    scene.status = prj.SCENE_WRITTEN
                    if worker.cancel.is_set():
                        bridge.scene_done.emit(k)
                        break
                    if (spec.track_cast and scene.text.strip()
                            and not worker.cancel.is_set()):
                        worker.progress.emit(
                            f"{label} — noting characters in scene {k + 1}…")
                        found = pipeline.generate_cast_update(
                            cfg_summ, scene.text, story.cast,
                            cancel=worker.cancel)
                        if pipeline.merge_cast(story, found, k + 1):
                            self.state.cast_changed.emit()
                    if (spec.context_mode != "full" and k < len(story.scenes) - 1
                            and scene.text.strip()):
                        worker.progress.emit(f"{label} — summarizing scene {k + 1}…")
                        bridge.summary_started.emit(k)
                        scene.summary = pipeline.generate_summary(
                            cfg_summ, scene.text, language=spec.language,
                            cancel=worker.cancel,
                            on_chunk=lambda p, i=k: bridge.summary_chunk.emit(i, p))
                        bridge.summary_done.emit(k)
                    story.save()
                    bridge.scene_done.emit(k)
                    tick()

                story.save()
                if any(s.text.strip() for s in story.scenes):
                    story.export(markdown=False)
                    story.export(markdown=True)
                    finished.append(story.name)
                    bridge.story_done.emit(story.name)
                if worker.cancel.is_set():
                    break
                if worker.soft_stop.is_set():
                    applog.log("queue", f"batch ended early after {label} "
                               "(finish-story-skip-rest)")
                    break
            return finished

        def on_pulse(_):
            self.progress.setValue(min(self.progress.value() + 1, total_steps))

        def finish_item():
            if self.current_spec is spec:
                self.current_spec = None
                self.current_story_progress = None
            if self._stop_all:
                self._stop_all = False
                self.state.job_queue.clear()
            self.state.queue_changed.emit()

        def done(finished):
            finished = finished or []
            self.progress.setValue(total_steps)
            self.state.storyboards_changed.emit()
            self.main.tab_complete.refresh_all()
            finish_item()
            waiting = len(self.state.job_queue)
            self.main.statusBar().showMessage(
                f"Batch finished — {len(finished)} story(ies) done"
                + (f", {waiting} batch(es) still queued." if waiting else "."))
            QTimer.singleShot(0, self._process_queue)

        def error(msg):
            finish_item()
            applog.log("queue", f"batch stopped on error: {msg}")
            self.main.statusBar().showMessage(
                f"Batch stopped: {msg} — queue paused "
                f"({len(self.state.job_queue)} item(s) waiting; resume from the Queue tab).")

        self.main.run_job(job, on_chunk=on_pulse, on_done=done, on_error=error,
                          status=f"Batch running: {spec.label}")

    def _batch_board_done(self, name: str):
        self.main.tab_storyboard.end_external_stream()
        self.state.selected_storyboard = name
        self.state.ui.setdefault("batch_boards", []).append(name)
        self.state.storyboards_changed.emit()
        self.main.tab_storyboard.select_storyboard(name)

    def _batch_project_ready(self, story):
        self.state.project = story
        self.state.project_changed.emit()

    def _batch_story_done(self, name: str):
        self.main.tab_complete.refresh_all()
        self.main.statusBar().showMessage(f"Story finished: {name}")
