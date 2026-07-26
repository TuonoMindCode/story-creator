"""Tab 5 — Scene Writer: scene list left, streaming prose right."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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

STATUS_ICONS = {
    prj.SCENE_OUTLINED: "○",
    prj.SCENE_WRITING: "▶",
    prj.SCENE_WRITTEN: "✓",
    prj.SCENE_EDITED: "✎",
}


class WriteBridge(QObject):
    """Thread-safe signals from the generation job to the UI."""
    scene_started = Signal(int)
    scene_chunk = Signal(int, str)
    scene_done = Signal(int)
    summary_started = Signal(int)
    summary_chunk = Signal(int, str)
    summary_done = Signal(int)


class PromptViewer(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Last prompts sent to the LLM")
        self.resize(900, 700)
        lay = QVBoxLayout(self)
        self.combo = QComboBox()
        self.combo.addItems(sorted(pipeline.LAST_PROMPTS.keys()) or ["(nothing sent yet)"])
        self.combo.currentTextChanged.connect(self._show)
        lay.addWidget(self.combo)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        lay.addWidget(self.view, 1)
        self._show(self.combo.currentText())

    def _show(self, section: str):
        data = pipeline.LAST_PROMPTS.get(section)
        if not data:
            self.view.setPlainText("Nothing has been sent for this section yet.")
            return
        tokens = pipeline.estimate_tokens(data["system"] + data["user"])
        self.view.setPlainText(
            f"≈ {tokens} tokens total\n\n"
            f"───────── SYSTEM ─────────\n{data['system']}\n\n"
            f"───────── USER ─────────\n{data['user']}"
        )


class WriterTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state
        self._loading = False
        self._streaming_index: int | None = None
        self._stream_buffer = ""  # full text streamed so far for that scene
        self._summarizing_index: int | None = None
        self._summary_buffer = ""  # summary text (incl. thinking) so far

        lay = QVBoxLayout(self)
        self.header = QLabel("No story project — start one from the Storyboards tab.")
        lay.addWidget(self.header)

        splitter = QSplitter(Qt.Horizontal)
        lay.addWidget(splitter, 1)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._selected)
        ll.addWidget(self.list, 1)
        splitter.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.scene_label = QLabel("")
        rl.addWidget(self.scene_label)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Scene prose appears here (streamed live)…")
        self.editor.textChanged.connect(self._text_edited)
        rl.addWidget(self.editor, 1)
        self.counter = QLabel("0 words · ≈0 tokens")
        rl.addWidget(self.counter)
        self.budget_label = QLabel("")
        self.budget_label.setWordWrap(True)
        self.budget_label.setStyleSheet("color: gray;")
        rl.addWidget(self.budget_label)

        sum_row = QHBoxLayout()
        sum_row.addWidget(QLabel("Scene summary (context for later scenes — editable):"))
        sum_row.addStretch(1)
        self.btn_resummarize = QPushButton("Re-summarize Scene")
        self.btn_resummarize.setToolTip(
            "Regenerate this scene's summary with the Summarizer model.")
        self.btn_resummarize.clicked.connect(self.resummarize_current)
        sum_row.addWidget(self.btn_resummarize)
        rl.addLayout(sum_row)
        self.summary_edit = QPlainTextEdit()
        self.summary_edit.setFixedHeight(96)
        self.summary_edit.setPlaceholderText(
            "No summary yet — created automatically after the scene is written "
            "(unless full-scenes context mode is on).")
        self.summary_edit.textChanged.connect(self._summary_edited)
        rl.addWidget(self.summary_edit)
        splitter.addWidget(right)
        splitter.setSizes([240, 940])

        row = QHBoxLayout()
        self.btn_next = QPushButton("Write Next Scene")
        self.btn_next.clicked.connect(self.write_next)
        self.btn_regen = QPushButton("Regenerate Scene")
        self.btn_regen.clicked.connect(self.regenerate_current)
        self.btn_continue = QPushButton("Continue Scene")
        self.btn_continue.setToolTip("Extend the selected scene if it stopped early.")
        self.btn_continue.clicked.connect(self.continue_current)
        self.btn_auto = QPushButton("Auto-Write All")
        self.btn_auto.clicked.connect(self.auto_write_all)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self.main.cancel_job)
        self.btn_stop.setEnabled(False)
        self.btn_prompt = QPushButton("Show Last Prompt")
        self.btn_prompt.clicked.connect(lambda: PromptViewer(self).exec())
        for b in (self.btn_next, self.btn_regen, self.btn_continue,
                  self.btn_auto, self.btn_stop, self.btn_prompt):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.state.project_changed.connect(self.refresh)
        self.state.outline_changed.connect(self.refresh)
        self.state.busy_changed.connect(self._busy_changed)
        self.refresh()

    # -- UI sync ---------------------------------------------------------------

    def refresh(self):
        story = self.state.project
        self._loading = True
        self.list.clear()
        if story is None:
            self.header.setText("No story project — start one from the Storyboards tab.")
        else:
            self.header.setText(f"Story: {story.title}")
            for i, scene in enumerate(story.scenes, 1):
                icon = STATUS_ICONS.get(scene.status, "○")
                self.list.addItem(f"{icon}  Scene {i}: {scene.title or '(untitled)'}")
        self._loading = False
        if self.list.count():
            self.list.setCurrentRow(0)
            self._selected(0)
        else:
            self.editor.clear()
            self.scene_label.setText("")

    def _update_item(self, index: int):
        story = self.state.project
        if story is None or not (0 <= index < self.list.count()):
            return
        scene = story.scenes[index]
        icon = STATUS_ICONS.get(scene.status, "○")
        self.list.item(index).setText(
            f"{icon}  Scene {index + 1}: {scene.title or '(untitled)'}")

    def _selected(self, row: int):
        story = self.state.project
        if self._loading or story is None or not (0 <= row < len(story.scenes)):
            return
        scene = story.scenes[row]
        # switching back to the scene that is streaming right now: show
        # everything streamed so far, and keep appending from there
        text = self._stream_buffer if row == self._streaming_index else scene.text
        summarizing = row == self._summarizing_index
        self._loading = True
        self.editor.setPlainText(text)
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.summary_edit.setPlainText(
            self._summary_buffer if summarizing else scene.summary)
        if summarizing:
            cursor = self.summary_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.summary_edit.setTextCursor(cursor)
        self._loading = False
        status = ("writing…" if row == self._streaming_index
                  else "summarizing…" if summarizing else scene.status)
        self.scene_label.setText(
            f"Scene {row + 1}: {scene.title}   [{status}]")
        self._update_counter(text)
        self._update_budget(row)

    def _text_edited(self):
        if self._loading:
            return
        story = self.state.project
        row = self.list.currentRow()
        # never write UI edits into the scene that is being generated
        if row == self._streaming_index:
            return
        if story is None or not (0 <= row < len(story.scenes)):
            return
        scene = story.scenes[row]
        scene.text = self.editor.toPlainText()
        if scene.status in (prj.SCENE_WRITTEN, prj.SCENE_EDITED) and scene.text.strip():
            scene.status = prj.SCENE_EDITED
            scene.summary_stale = True
        self._update_item(row)
        self._update_counter(scene.text)

    def _summary_edited(self):
        if self._loading:
            return
        story = self.state.project
        row = self.list.currentRow()
        # never save the live view (scene prose or streaming summary) as edits
        if row == self._streaming_index or row == self._summarizing_index:
            return
        if story is None or not (0 <= row < len(story.scenes)):
            return
        scene = story.scenes[row]
        scene.summary = self.summary_edit.toPlainText()
        scene.summary_stale = False  # a hand-edited summary is authoritative
        self.state.autosave_project()

    def _update_counter(self, text: str):
        words = len(text.split())
        self.counter.setText(f"{words} words · ≈{pipeline.estimate_tokens(text)} tokens")

    def _update_budget(self, row: int):
        story = self.state.project
        if story is None or not (0 <= row < len(story.scenes)):
            self.budget_label.setText("")
            self.budget_label.setToolTip("")
            return
        cfg = self.state.runtime_cfg("writer")
        lorebook = (prj.load_lorebook(story.storyboard_name)
                    if story.storyboard_name else [])
        short, detail = pipeline.context_report(
            cfg, story, row, lorebook,
            context_mode=self.state.ui.get("context_mode", "summaries"))
        self.budget_label.setText(short)
        self.budget_label.setToolTip(detail)

    def _busy_changed(self, busy: bool):
        for b in (self.btn_next, self.btn_regen, self.btn_continue,
                  self.btn_auto, self.btn_resummarize):
            b.setEnabled(not busy)
        self.btn_stop.setEnabled(busy)
        if not busy:
            self._streaming_index = None
            self._summarizing_index = None

    # -- generation --------------------------------------------------------------

    def _make_bridge(self) -> WriteBridge:
        bridge = WriteBridge()
        bridge.scene_started.connect(self._on_scene_started)
        bridge.scene_chunk.connect(self._on_scene_chunk)
        bridge.scene_done.connect(self._on_scene_done)
        bridge.summary_started.connect(self._on_summary_started)
        bridge.summary_chunk.connect(self._on_summary_chunk)
        bridge.summary_done.connect(self._on_summary_done)
        return bridge

    # -- live summary view (shows thinking too, so it is never a blank wait) ----

    def _on_summary_started(self, index: int):
        self._summarizing_index = index
        self._summary_buffer = ""
        self._loading = True
        self.list.setCurrentRow(index)
        self.summary_edit.clear()
        self._loading = False
        story = self.state.project
        if story and 0 <= index < len(story.scenes):
            self.scene_label.setText(
                f"Scene {index + 1}: {story.scenes[index].title}   [summarizing…]")

    def _on_summary_chunk(self, index: int, piece: str):
        if index == self._summarizing_index:
            self._summary_buffer += piece
        if index != self.list.currentRow():
            return
        self._loading = True
        cursor = self.summary_edit.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(piece)
        self.summary_edit.setTextCursor(cursor)
        self._loading = False

    def _on_summary_done(self, index: int):
        self._summarizing_index = None
        self._summary_buffer = ""
        story = self.state.project
        if story and 0 <= index < len(story.scenes) and index == self.list.currentRow():
            scene = story.scenes[index]
            self._loading = True
            self.summary_edit.setPlainText(scene.summary)
            self._loading = False
            self.scene_label.setText(
                f"Scene {index + 1}: {scene.title}   [{scene.status}]")

    def _on_scene_started(self, index: int):
        story = self.state.project
        self._streaming_index = index
        self._stream_buffer = ""
        if story and 0 <= index < len(story.scenes):
            story.scenes[index].status = prj.SCENE_WRITING
            self._update_item(index)
        self._loading = True
        self.list.setCurrentRow(index)
        self.editor.clear()
        self._loading = False
        if story and 0 <= index < len(story.scenes):
            self.scene_label.setText(
                f"Scene {index + 1}: {story.scenes[index].title}   [writing…]")

    def _on_scene_chunk(self, index: int, piece: str):
        if index == self._streaming_index:
            self._stream_buffer += piece
        if index != self.list.currentRow():
            return
        self._loading = True
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(piece)
        self.editor.setTextCursor(cursor)
        self._loading = False

    def _on_scene_done(self, index: int):
        story = self.state.project
        self._streaming_index = None
        if story and 0 <= index < len(story.scenes):
            scene = story.scenes[index]
            self._update_item(index)
            if index == self.list.currentRow():
                self._loading = True
                self.editor.setPlainText(scene.text)
                self.summary_edit.setPlainText(scene.summary)
                self._loading = False
                self.scene_label.setText(
                    f"Scene {index + 1}: {scene.title}   [{scene.status}]")
                self._update_counter(scene.text)
                self._update_budget(index)

    def _write_scenes(self, indices: list[int], continuation_for: int | None = None):
        """Run a job that writes the given scene indices in order."""
        story = self.state.project
        if story is None or not indices:
            return
        # roll min/max ranges once for this job — stable across its scenes
        cfg_write = self.state.runtime_cfg("writer").rolled()
        cfg_summ = self.state.runtime_cfg("summarizer").rolled()
        context_mode = self.state.ui.get("context_mode", "summaries")
        language = self.state.ui.get("story_language", "English")
        story.gen_info.setdefault("writer", {})
        story.gen_info["writer"] = {"backend": cfg_write.backend,
                                    "model": cfg_write.model,
                                    "params": cfg_write.params.describe()}
        story.gen_info["context_mode"] = context_mode
        lorebook = prj.load_lorebook(story.storyboard_name) if story.storyboard_name else []
        bridge = self._make_bridge()
        was_incomplete = any(not s.text.strip() for s in story.scenes)
        continuation_text = ""
        if continuation_for is not None:
            continuation_text = story.scenes[continuation_for].text

        def job(worker):
            for index in indices:
                if worker.cancel.is_set():
                    break
                # freshen summaries for ALL earlier scenes in this scene's context
                if context_mode != "full":
                    for i in range(0, index):
                        prev = story.scenes[i]
                        if prev.text.strip() and (prev.summary_stale or not prev.summary.strip()):
                            if worker.cancel.is_set():
                                return None
                            worker.progress.emit(f"Summarizing scene {i + 1}…")
                            bridge.summary_started.emit(i)
                            prev.summary = pipeline.generate_summary(
                                cfg_summ, prev.text, language=language,
                                cancel=worker.cancel,
                                on_chunk=lambda p, k=i: bridge.summary_chunk.emit(k, p))
                            prev.summary_stale = False
                            bridge.summary_done.emit(i)

                worker.progress.emit(
                    f"Writing scene {index + 1}/{len(story.scenes)}…")
                bridge.scene_started.emit(index)
                cont = continuation_text if index == continuation_for else ""
                text = pipeline.generate_scene(
                    cfg_write, story, index, lorebook,
                    continuation=cont,
                    context_mode=context_mode,
                    language=language,
                    cancel=worker.cancel,
                    on_chunk=lambda piece, i=index: bridge.scene_chunk.emit(i, piece),
                )
                scene = story.scenes[index]
                if cont:
                    scene.text = (cont.rstrip() + "\n\n" + text).strip()
                else:
                    scene.text = text
                scene.status = prj.SCENE_WRITTEN
                scene.summary_stale = False
                if worker.cancel.is_set():
                    bridge.scene_done.emit(index)
                    break
                if context_mode != "full" and scene.text.strip():
                    worker.progress.emit(f"Summarizing scene {index + 1}…")
                    bridge.summary_started.emit(index)
                    scene.summary = pipeline.generate_summary(
                        cfg_summ, scene.text, language=language,
                        cancel=worker.cancel,
                        on_chunk=lambda p, k=index: bridge.summary_chunk.emit(k, p))
                    bridge.summary_done.emit(index)
                story.save()
                bridge.scene_done.emit(index)
            return True

        def done(_):
            self.state.autosave_project()
            # this job just completed the story -> save it as a text file too
            if was_incomplete and all(s.text.strip() for s in story.scenes):
                path = story.export(markdown=False)
                self.main.statusBar().showMessage(
                    f"Story complete — exported to {path}")
            self.main.tab_complete.refresh_all()

        self._bridge = bridge  # keep alive during the job
        self.main.run_job(job, on_done=done, status="Writing…")

    def write_next(self):
        story = self.state.project
        if story is None:
            self.main.statusBar().showMessage("No story project.")
            return
        for i, scene in enumerate(story.scenes):
            if not scene.text.strip():
                self._write_scenes([i])
                return
        self.main.statusBar().showMessage("All scenes are already written.")

    def regenerate_current(self):
        row = self.list.currentRow()
        if row >= 0:
            self._write_scenes([row])

    def continue_current(self):
        story = self.state.project
        row = self.list.currentRow()
        if story is None or not (0 <= row < len(story.scenes)):
            return
        if not story.scenes[row].text.strip():
            self.main.statusBar().showMessage("Scene has no text yet — write it first.")
            return
        self._write_scenes([row], continuation_for=row)

    def resummarize_current(self):
        story = self.state.project
        row = self.list.currentRow()
        if story is None or not (0 <= row < len(story.scenes)):
            return
        scene = story.scenes[row]
        if not scene.text.strip():
            self.main.statusBar().showMessage("Scene has no text to summarize.")
            return
        cfg = self.state.runtime_cfg("summarizer").rolled()
        language = self.state.ui.get("story_language", "English")
        # an explicit user request always gets a real attempt, even if this
        # model gave up on summaries earlier in the session
        pipeline.reset_thinking_state()
        self._loading = True
        self.summary_edit.clear()
        self._loading = False

        def job(worker):
            return pipeline.generate_summary(
                cfg, scene.text, language=language, cancel=worker.cancel,
                on_chunk=worker.chunk.emit)

        def chunk(piece):
            self._loading = True
            cursor = self.summary_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(piece)
            self.summary_edit.setTextCursor(cursor)
            self._loading = False

        def done(text):
            scene.summary = (text or "").strip()
            scene.summary_stale = False
            self._loading = True
            self.summary_edit.setPlainText(scene.summary)
            self._loading = False
            self.state.autosave_project()

        self.main.run_job(job, on_chunk=chunk, on_done=done,
                          status=f"Summarizing scene {row + 1}…")

    def auto_write_all(self):
        story = self.state.project
        if story is None:
            return
        remaining = [i for i, s in enumerate(story.scenes) if not s.text.strip()]
        if not remaining:
            self.main.statusBar().showMessage("All scenes are already written.")
            return
        self._write_scenes(remaining)
