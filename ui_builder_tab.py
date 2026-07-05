"""Tab 2 — Detailed Builder: compose a story description file on disk."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import project as prj

GENRES = ["Fantasy", "Sci-fi", "Detective/Crime", "Drama", "Action",
          "Horror", "Romance", "Thriller", "Other"]
TONES = ["Serious", "Comedic", "Dark", "Lighthearted", "Gritty"]
POVS = ["First person", "Third person limited", "Third person omniscient"]
TENSES = ["Past tense", "Present tense"]
RATINGS = ["All ages", "Teen", "Adult"]
LENGTHS = ["Short (3-5 scenes)", "Medium (6-10 scenes)", "Long (11-20 scenes)"]

# writing-style options (first entry = recommended default)
DIALOGUE = ["Balanced dialogue/description", "Dialogue-heavy", "Description-heavy"]
PACING = ["Moderate pacing", "Fast-paced", "Slow burn"]
DETAIL = ["Moderate detail", "Minimal detail", "Rich, vivid detail"]
PROSE = ["Neutral prose", "Simple, easy-to-read prose", "Literary, elaborate prose"]
HUMOR = ["No particular humor", "Light humor here and there", "Plenty of humor"]
EMOTION = ["Moderate emotional depth", "Understated emotions", "Strong emotional drama"]
VIOLENCE = ["Mild violence at most", "No violence", "Graphic violence allowed"]
ROMANCE = ["No romance", "Subtle romance subplot", "Prominent romance"]
ENDINGS = ["Let the story decide", "Happy ending", "Bittersweet ending",
           "Tragic ending", "Twist ending", "Open ending"]


def _radio_group(box_title: str, options: list[str], columns: int = 3):
    group_box = QGroupBox(box_title)
    grid = QGridLayout(group_box)
    button_group = QButtonGroup(group_box)
    for i, option in enumerate(options):
        rb = QRadioButton(option)
        if i == 0:
            rb.setChecked(True)
        button_group.addButton(rb, i)
        grid.addWidget(rb, i // columns, i % columns)
    return group_box, button_group, options


class BuilderTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        scroll.setWidget(inner)
        lay = QVBoxLayout(inner)

        self.groups = {}
        for title, options, cols in (
            ("Genre", GENRES, 3),
            ("Tone", TONES, 3),
            ("Point of view", POVS, 3),
            ("Tense", TENSES, 2),
            ("Audience rating", RATINGS, 3),
            ("Story length", LENGTHS, 3),
            ("Dialogue vs description", DIALOGUE, 3),
            ("Pacing", PACING, 3),
            ("Descriptive detail", DETAIL, 3),
            ("Prose style", PROSE, 3),
            ("Humor", HUMOR, 3),
            ("Emotional depth", EMOTION, 3),
            ("Violence", VIOLENCE, 3),
            ("Romance", ROMANCE, 3),
            ("Ending", ENDINGS, 3),
        ):
            box, group, opts = _radio_group(title, options, cols)
            self.groups[title] = (group, opts)
            lay.addWidget(box)

        setting_box = QGroupBox("Time period / setting")
        sb = QVBoxLayout(setting_box)
        self.setting_edit = QLineEdit()
        self.setting_edit.setPlaceholderText("e.g. 1920s London, a generation ship, a small Swedish town in winter…")
        sb.addWidget(self.setting_edit)
        lay.addWidget(setting_box)

        notes_box = QGroupBox("Extra notes (characters, must-have plot points, anything else)")
        nb = QVBoxLayout(notes_box)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(
            "e.g. the detective is a retired opera singer; the culprit must be "
            "sympathetic; include a storm scene…"
        )
        self.notes_edit.setFixedHeight(110)
        nb.addWidget(self.notes_edit)
        lay.addWidget(notes_box)

        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("File name"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("auto (from genre + tone)")
        save_row.addWidget(self.name_edit, 1)
        save_btn = QPushButton("Save Description")
        save_btn.clicked.connect(self.save_description)
        save_row.addWidget(save_btn)
        lay.addLayout(save_row)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("Preview of the description that will be saved…")
        lay.addWidget(self.preview, 1)

        preview_btn = QPushButton("Update Preview")
        preview_btn.clicked.connect(lambda: self.preview.setPlainText(self.compose()))
        lay.addWidget(preview_btn)

    def _choice(self, title: str) -> str:
        group, opts = self.groups[title]
        return opts[group.checkedId()]

    def compose(self) -> str:
        length = self._choice("Story length")
        parts = [
            "STORY DESCRIPTION",
            "",
            f"Genre: {self._choice('Genre')}",
            f"Tone: {self._choice('Tone')}",
            f"Point of view: {self._choice('Point of view')}, {self._choice('Tense').lower()}",
            f"Audience rating: {self._choice('Audience rating')}",
            f"Length: {length}",
        ]
        setting = self.setting_edit.text().strip()
        if setting:
            parts.append(f"Time period / setting: {setting}")
        parts += [
            "",
            "Writing style requirements:",
            f"- Dialogue vs description: {self._choice('Dialogue vs description')}",
            f"- Pacing: {self._choice('Pacing')}",
            f"- Descriptive detail: {self._choice('Descriptive detail')}",
            f"- Prose style: {self._choice('Prose style')}",
            f"- Humor: {self._choice('Humor')}",
            f"- Emotional depth: {self._choice('Emotional depth')}",
            f"- Violence: {self._choice('Violence')}",
            f"- Romance: {self._choice('Romance')}",
        ]
        ending = self._choice("Ending")
        if ending != "Let the story decide":
            parts.append(f"- Ending: {ending}")
        notes = self.notes_edit.toPlainText().strip()
        if notes:
            parts += ["", "Additional requirements:", notes]
        return "\n".join(parts) + "\n"

    def save_description(self):
        text = self.compose()
        self.preview.setPlainText(text)
        stem = self.name_edit.text().strip()
        if not stem:
            stem = f"{self._choice('Genre')}-{self._choice('Tone')}"
        path = prj.save_description(stem, text)
        self.main.state.descriptions_changed.emit()
        self.main.statusBar().showMessage(f"Saved {path.name} — selectable on the Story Start tab.")
