"""Tab 6 — Lorebook: world & character facts tied to the selected storyboard."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
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
        ll.addWidget(QLabel("Lorebook entries (saved with the storyboard)"))
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

        # characters the app noted while writing the current story — visible so
        # you can see what the scenes are being told, and fix or keep any of it
        ll.addWidget(QLabel("Characters tracked in the current story (automatic)"))
        self.cast_list = QListWidget()
        self.cast_list.setToolTip(
            "Filled in after each scene when 'Track characters automatically' is "
            "on (Story Start tab). These are sent to every later scene so names, "
            "roles and genders stay fixed. They live with this story only.")
        self.cast_list.currentRowChanged.connect(self._cast_selected)
        ll.addWidget(self.cast_list, 1)
        cast_row = QHBoxLayout()
        self.btn_cast_scan = QPushButton("Scan story for characters")
        self.btn_cast_scan.setToolTip(
            "Read every written scene of the current story and fill in the "
            "cast. Use it for stories written before automatic tracking "
            "existed, or to rebuild the list from scratch.")
        self.btn_cast_scan.clicked.connect(self._scan_story_for_cast)
        cast_row.addWidget(self.btn_cast_scan)
        self.btn_cast_keep = QPushButton("Copy to Lorebook")
        self.btn_cast_keep.setToolTip(
            "Save this tracked character as a permanent lorebook entry, so every "
            "future story from this storyboard gets it too.")
        self.btn_cast_keep.clicked.connect(self._promote_cast_entry)
        self.btn_cast_del = QPushButton("Forget")
        self.btn_cast_del.setToolTip("Remove a wrongly detected character.")
        self.btn_cast_del.clicked.connect(self._forget_cast_entry)
        cast_row.addWidget(self.btn_cast_keep)
        cast_row.addWidget(self.btn_cast_del)
        ll.addLayout(cast_row)
        splitter.addWidget(left)

        # right side switches between the lorebook form and the cast details
        self.right_stack = QStackedWidget()
        right = QWidget()
        form = QFormLayout(right)
        self.name_edit = QLineEdit()
        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("comma-separated triggers, e.g. Anna, the detective, Miss Berg")
        self.content_edit = QPlainTextEdit()
        self.content_edit.setPlaceholderText("Facts that must stay consistent…")
        self.always_check = QCheckBox("Always include (even if the keywords don't appear)")
        self.pronouns_box = QComboBox()
        self.pronouns_box.setEditable(True)
        self.pronouns_box.addItems(list(prj.PRONOUN_CHOICES))
        self.pronouns_box.setToolTip(
            "Sent with this character to every scene so their gender cannot "
            "drift. Leave blank for places, objects or world facts.")
        self.pronouns_box.editTextChanged.connect(self._field_changed)
        form.addRow("Name", self.name_edit)
        form.addRow("Pronouns", self.pronouns_box)
        form.addRow("Keywords", self.keywords_edit)
        form.addRow("Facts", self.content_edit)
        form.addRow("", self.always_check)
        info = QLabel(
            "Entries are injected into the scene-writing prompt when their name or a "
            "keyword appears in the scene's outline beat or the end of the previous scene."
        )
        info.setWordWrap(True)
        form.addRow(info)
        self.right_stack.addWidget(right)          # page 0: lorebook entry

        cast_page = QWidget()
        cf = QFormLayout(cast_page)
        self.cast_name_label = QLabel("")
        self.cast_name_label.setStyleSheet("font-weight: bold;")
        self.cast_first_label = QLabel("")
        self.cast_scenes_label = QLabel("")
        self.cast_scenes_label.setWordWrap(True)
        self.cast_desc_edit = QPlainTextEdit()
        self.cast_desc_edit.setPlaceholderText(
            "What the scenes are told about this character…")
        self.cast_desc_edit.textChanged.connect(self._cast_desc_edited)
        self.cast_pronouns_box = QComboBox()
        self.cast_pronouns_box.setEditable(True)
        self.cast_pronouns_box.addItems(list(prj.PRONOUN_CHOICES))
        self.cast_pronouns_box.setToolTip(
            "Sent to every later scene so this character's gender cannot "
            "drift. Detected from the first scene they appear in.")
        self.cast_pronouns_box.editTextChanged.connect(self._cast_pronouns_edited)
        cf.addRow("Character", self.cast_name_label)
        cf.addRow("Pronouns", self.cast_pronouns_box)
        cf.addRow("First appears", self.cast_first_label)
        cf.addRow("Appears in", self.cast_scenes_label)
        cf.addRow("Facts", self.cast_desc_edit)
        cast_info = QLabel(
            "Noted automatically after each scene and sent to every later "
            "scene so names, roles and genders stay fixed. Edits here are "
            "saved with the story; Copy to Lorebook makes it permanent for "
            "every story from this storyboard."
        )
        cast_info.setWordWrap(True)
        cf.addRow(cast_info)
        self.right_stack.addWidget(cast_page)      # page 1: tracked character

        splitter.addWidget(self.right_stack)
        splitter.setSizes([300, 860])

        self.name_edit.textChanged.connect(self._field_changed)
        self.keywords_edit.textChanged.connect(self._field_changed)
        self.content_edit.textChanged.connect(self._field_changed)
        self.always_check.toggled.connect(self._field_changed)

        self.state.storyboards_changed.connect(self.refresh)
        self.state.project_changed.connect(self.refresh_cast)
        self.state.cast_changed.connect(self.refresh_cast)
        self.state.busy_changed.connect(lambda _b: self.refresh_cast())
        self.state.project_changed.connect(self.refresh_cast)
        self.state.cast_changed.connect(self.refresh_cast)
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
        self.refresh_cast()

    # -- automatically tracked cast of the current story ------------------------

    def refresh_cast(self):
        story = self.state.project
        cast = story.cast if story else {}
        current = self._cast_name()
        self.cast_list.blockSignals(True)
        self.cast_list.clear()
        for person in cast:
            first = story.cast_first_scene(person)
            label = f"{person}   (scene {first})" if first else person
            self.cast_list.addItem(label)
        self.cast_list.blockSignals(False)
        has = bool(cast)
        self.btn_cast_keep.setEnabled(has)
        self.btn_cast_del.setEnabled(has)
        self.btn_cast_scan.setEnabled(
            story is not None and any(s.text.strip() for s in story.scenes)
            and not self.main.is_busy())
        if not has:
            self.cast_list.addItem(
                "(nothing tracked yet — filled in as scenes are written)")
            if self.right_stack.currentIndex() == 1:
                self.right_stack.setCurrentIndex(0)
            return
        if current in cast:  # keep the selection across refreshes
            self.cast_list.setCurrentRow(list(cast).index(current))
            self._show_cast_details(current)

    def _cast_name(self) -> str:
        story = self.state.project
        row = self.cast_list.currentRow()
        names = list(story.cast) if story else []
        return names[row] if 0 <= row < len(names) else ""

    def _cast_selected(self, row: int):
        story = self.state.project
        if self._loading or story is None:
            return
        names = list(story.cast)
        if not (0 <= row < len(names)):
            return
        self.list.blockSignals(True)      # the two lists are alternatives
        self.list.setCurrentRow(-1)
        self.list.blockSignals(False)
        self._show_cast_details(names[row])

    def _show_cast_details(self, person: str):
        story = self.state.project
        if story is None or person not in story.cast:
            return
        scenes = story.cast_scenes(person)
        first = story.cast_first_scene(person)
        self._loading = True
        self.cast_name_label.setText(person)
        self.cast_first_label.setText(
            f"Scene {first}" if first else "(scene not recorded)")
        self.cast_scenes_label.setText(
            ", ".join(f"scene {s}" for s in scenes) if scenes else "—")
        self.cast_desc_edit.setPlainText(story.cast_desc(person))
        self.cast_pronouns_box.setEditText(story.cast_pronouns(person))
        self._loading = False
        self.right_stack.setCurrentIndex(1)

    def _cast_pronouns_edited(self):
        story = self.state.project
        person = self._cast_name()
        if self._loading or story is None or not person:
            return
        story.set_cast_pronouns(person, self.cast_pronouns_box.currentText())
        self.state.autosave_project()

    def _cast_desc_edited(self):
        story = self.state.project
        person = self._cast_name()
        if self._loading or story is None or not person:
            return
        rec = story.cast.get(person)
        if isinstance(rec, dict):
            rec["desc"] = self.cast_desc_edit.toPlainText().strip()
        else:
            story.cast[person] = {"desc": self.cast_desc_edit.toPlainText().strip(),
                                  "scenes": []}
        self.state.autosave_project()

    def _promote_cast_entry(self):
        story = self.state.project
        person = self._cast_name()
        if not story or not person:
            return
        if not self.state.selected_storyboard:
            self.main.statusBar().showMessage(
                "Select a storyboard first — lorebook entries are saved with it.")
            return
        if any(e.name.strip().lower() == person.lower() for e in self.entries):
            self.main.statusBar().showMessage(f"'{person}' is already in the lorebook.")
            return
        self.entries.append(LorebookEntry(
            name=person, keywords=[person], content=story.cast_desc(person),
            always_include=True, pronouns=story.cast_pronouns(person)))
        self._save()
        self._reload_list()
        self.main.statusBar().showMessage(
            f"'{person}' copied to the lorebook — every future story from this "
            "storyboard will include it.")

    def _scan_story_for_cast(self):
        """Rebuild the tracked cast by reading the story's written scenes."""
        story = self.state.project
        if story is None:
            self.main.statusBar().showMessage("No story open.")
            return
        written = [(i + 1, s) for i, s in enumerate(story.scenes) if s.text.strip()]
        if not written:
            self.main.statusBar().showMessage("This story has no written scenes yet.")
            return
        cfg = self.state.runtime_cfg("summarizer").rolled()
        language = self.state.ui.get("story_language", "English")

        def job(worker):
            total_new = 0
            for number, scene in written:
                if worker.cancel.is_set():
                    break
                worker.progress.emit(
                    f"Reading scene {number} of {len(story.scenes)} for characters…")
                found = pipeline.generate_cast_update(
                    cfg, scene.text, story.cast, cancel=worker.cancel)
                total_new += pipeline.merge_cast(story, found, number)
            return total_new

        def done(total_new):
            self.state.autosave_project()
            self.refresh_cast()
            self.main.statusBar().showMessage(
                f"Scan finished — {len(story.cast)} character(s) tracked "
                f"({total_new} new).")

        self.main.run_job(job, on_done=done,
                          status="Scanning the story for characters…")

    def _forget_cast_entry(self):
        story = self.state.project
        person = self._cast_name()
        if story and person:
            story.cast.pop(person, None)
            self.state.autosave_project()
            self.refresh_cast()
            self.main.statusBar().showMessage(f"Stopped tracking '{person}'.")

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
        self.pronouns_box.setEditText("")
        self._loading = False

    def _selected(self, row: int):
        if self._loading or not (0 <= row < len(self.entries)):
            return
        self.cast_list.blockSignals(True)   # the two lists are alternatives
        self.cast_list.setCurrentRow(-1)
        self.cast_list.blockSignals(False)
        self.right_stack.setCurrentIndex(0)
        e = self.entries[row]
        self._loading = True
        self.name_edit.setText(e.name)
        self.keywords_edit.setText(", ".join(e.keywords))
        self.content_edit.setPlainText(e.content)
        self.always_check.setChecked(e.always_include)
        self.pronouns_box.setEditText(e.pronouns)
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
        e.pronouns = self.pronouns_box.currentText().strip()
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
