"""Tab 9 — Prompts: preset selection per section + template editor."""
from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt

import prompts
from backends import SECTION_KEYS, SECTION_LABELS

PROMPT_SECTION_LABELS = {
    "storyboard": "Storyboard",
    "outline": "Scene outline",
    "scene": "Scene writing",
    "summary": "Scene summary",
}

# one-click preset combinations, in plain language:
# (label, hint, presets per section, minimum Max tokens per section)
QUICK_SETUPS = [
    ("Loose & free",
     "Short plans, the model has freedom — fast, but the story may drift "
     "away from your description and scene outlines stay brief.",
     {"storyboard": "Default", "planner": "Default",
      "writer": "Default", "summarizer": "Default"},
     {}),
    ("Detailed & faithful  (recommended)",
     "The storyboard must keep every detail of your story description (it "
     "ends with a checklist proving it); scene outlines are long and concrete "
     "(6-10 sentences per scene plus key details); summaries are detailed. "
     "Selecting this also raises Max tokens where needed (planner 4096, "
     "storyboard 2048). Works best up to ~10-12 scenes on an 8192 context.",
     {"storyboard": "Faithful-Detailed", "planner": "Faithful-Detailed",
      "writer": "Default", "summarizer": "Detailed-Summary"},
     {"storyboard": 2048, "planner": 4096, "summarizer": 768}),
    ("Strict format",
     "Hard, rigid instructions everywhere — for models that ignore the format, "
     "add headings, or write past their scene.",
     {"storyboard": "Strict", "planner": "Strict",
      "writer": "Strict", "summarizer": "Strict"},
     {}),
]


class PromptsTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._loading = False

        lay = QVBoxLayout(self)

        # -- quick setup: one click sets all four sections ---------------------
        quick_group = QGroupBox("Quick setup — pick one, it sets all four prompts below")
        qg = QGridLayout(quick_group)
        self.quick_group = QButtonGroup(self)
        self.quick_radios: list[QRadioButton] = []
        for row, (label, hint, _combo, _tokens) in enumerate(QUICK_SETUPS):
            rb = QRadioButton(label)
            hint_label = QLabel(hint)
            hint_label.setWordWrap(True)
            hint_label.setStyleSheet("color: gray;")
            self.quick_group.addButton(rb, row)
            self.quick_radios.append(rb)
            qg.addWidget(rb, row, 0)
            qg.addWidget(hint_label, row, 1)
        qg.setColumnStretch(1, 1)
        self.quick_group.idClicked.connect(self._quick_setup_clicked)
        lay.addWidget(quick_group)

        # -- preset used per pipeline section ---------------------------------
        use_group = QGroupBox(
            "Advanced: preset per pipeline step (mix freely — e.g. Qwen-tuned "
            "writer with a Faithful-Detailed planner)")
        ug = QGridLayout(use_group)
        self.use_boxes: dict[str, QComboBox] = {}
        hints = {
            "storyboard": "storyboard prompt",
            "planner": "outline prompt",
            "writer": "scene-writing prompt",
            "summarizer": "scene-summary prompt",
        }
        for col, key in enumerate(SECTION_KEYS):
            ug.addWidget(QLabel(f"{SECTION_LABELS[key]}  ({hints[key]})"), 0, col)
            box = QComboBox()
            self.use_boxes[key] = box
            ug.addWidget(box, 1, col)
            box.currentTextChanged.connect(
                lambda name, k=key: self._use_preset_changed(k, name))
        lay.addWidget(use_group)

        # -- editor -------------------------------------------------------------
        edit_group = QGroupBox("Template editor")
        eg = QVBoxLayout(edit_group)
        top = QHBoxLayout()
        top.addWidget(QLabel("Preset"))
        self.preset_box = QComboBox()
        top.addWidget(self.preset_box, 1)
        top.addWidget(QLabel("Template"))
        self.section_box = QComboBox()
        for key, label in PROMPT_SECTION_LABELS.items():
            self.section_box.addItem(label, key)
        top.addWidget(self.section_box, 1)
        eg.addLayout(top)

        splitter = QSplitter(Qt.Vertical)
        sys_widget = QWidget()
        sv = QVBoxLayout(sys_widget)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(QLabel("System prompt"))
        self.system_edit = QPlainTextEdit()
        sv.addWidget(self.system_edit)
        splitter.addWidget(sys_widget)

        usr_widget = QWidget()
        uv = QVBoxLayout(usr_widget)
        uv.setContentsMargins(0, 0, 0, 0)
        uv.addWidget(QLabel("User prompt"))
        self.user_edit = QPlainTextEdit()
        uv.addWidget(self.user_edit)
        splitter.addWidget(usr_widget)
        eg.addWidget(splitter, 1)

        legend = QLabel(
            "Placeholders: " + " · ".join(f"{{{k}}}" for k in prompts.PLACEHOLDERS)
        )
        legend.setWordWrap(True)
        legend.setToolTip("\n".join(
            f"{{{k}}} — {v}" for k, v in prompts.PLACEHOLDERS.items()))
        eg.addWidget(legend)

        btn_row = QHBoxLayout()
        save_btn = QPushButton("Save as preset…")
        save_btn.setToolTip(
            "Saves the whole preset (all four templates, with your edits) under a "
            "new or existing name in prompt-presets/."
        )
        save_btn.clicked.connect(self._save_as_preset)
        reset_btn = QPushButton("Reset editor to built-in Default")
        reset_btn.clicked.connect(self._reset_editor)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch(1)
        eg.addLayout(btn_row)
        lay.addWidget(edit_group, 1)

        self.preset_box.currentTextChanged.connect(self._load_editor)
        self.section_box.currentIndexChanged.connect(self._load_editor)

        self._refresh_preset_lists()
        self._sync_quick_radios()
        self._load_editor()

    # -- helpers -----------------------------------------------------------------

    def _refresh_preset_lists(self):
        names = list(prompts.all_presets().keys())
        self._loading = True
        current_editor = self.preset_box.currentText()
        self.preset_box.clear()
        self.preset_box.addItems(names)
        if current_editor in names:
            self.preset_box.setCurrentText(current_editor)
        for key, box in self.use_boxes.items():
            box.blockSignals(True)
            box.clear()
            box.addItems(names)
            chosen = self.state.sections[key].prompt_preset
            box.setCurrentText(chosen if chosen in names else "Default")
            box.blockSignals(False)
        self._loading = False

    def _quick_setup_clicked(self, row: int):
        _label, _hint, combo, min_tokens = QUICK_SETUPS[row]
        for key, preset in combo.items():
            self.state.sections[key].prompt_preset = preset
            box = self.use_boxes[key]
            box.blockSignals(True)
            box.setCurrentText(preset)
            box.blockSignals(False)
        raised = []
        for key, minimum in min_tokens.items():
            params = self.state.sections[key].params
            if params.max_tokens < minimum:
                params.max_tokens = minimum
                raised.append(f"{SECTION_LABELS[key]} → {minimum}")
        if raised:
            self.main.tab_start.sync_section_widgets()
        self.state.save_settings()
        msg = f"Prompts set to: {QUICK_SETUPS[row][0].strip()}"
        if raised:
            msg += "  ·  Max tokens raised: " + ", ".join(raised)
        self.main.statusBar().showMessage(msg)

    def _sync_quick_radios(self):
        """Highlight the quick-setup row that matches the current dropdowns."""
        current = {k: self.state.sections[k].prompt_preset for k in SECTION_KEYS}
        match = next((i for i, (_l, _h, combo, _t) in enumerate(QUICK_SETUPS)
                      if combo == current), -1)
        self.quick_group.blockSignals(True)
        self.quick_group.setExclusive(False)
        for i, rb in enumerate(self.quick_radios):
            rb.setChecked(i == match)
        self.quick_group.setExclusive(True)
        self.quick_group.blockSignals(False)

    def _use_preset_changed(self, key: str, name: str):
        if self._loading or not name:
            return
        self.state.sections[key].prompt_preset = name
        self.state.save_settings()
        self._sync_quick_radios()

    def _load_editor(self, *args):
        if self._loading:
            return
        preset = self.preset_box.currentText()
        section = self.section_box.currentData()
        if not preset or not section:
            return
        tpl = prompts.get_prompt(preset, section)
        self.system_edit.setPlainText(tpl["system"])
        self.user_edit.setPlainText(tpl["user"])

    def _save_as_preset(self):
        source = self.preset_box.currentText()
        section = self.section_box.currentData()
        unknown = (
            prompts.find_unknown_placeholders(self.system_edit.toPlainText())
            + prompts.find_unknown_placeholders(self.user_edit.toPlainText())
        )
        if unknown:
            answer = QMessageBox.warning(
                self, "Unknown placeholders",
                "These placeholders are not known and will be left unfilled:\n"
                + ", ".join(sorted(set(unknown)))
                + "\n\nSave anyway?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return

        name, ok = QInputDialog.getText(
            self, "Save preset", "Preset name:", text=f"{source}-custom")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in prompts.BUILTIN_PRESETS:
            QMessageBox.information(
                self, "Reserved name",
                "That name is a built-in preset — choose another name.")
            return
        preset = copy.deepcopy(prompts.all_presets().get(source, prompts.BUILTIN_PRESETS["Default"]))
        preset[section] = {
            "system": self.system_edit.toPlainText(),
            "user": self.user_edit.toPlainText(),
        }
        prompts.save_user_preset(name, preset)
        self._refresh_preset_lists()
        self.preset_box.setCurrentText(name)
        self.main.statusBar().showMessage(f"Preset '{name}' saved to prompt-presets/.")

    def _reset_editor(self):
        section = self.section_box.currentData()
        tpl = prompts.BUILTIN_PRESETS["Default"][section]
        self.system_edit.setPlainText(tpl["system"])
        self.user_edit.setPlainText(tpl["user"])
