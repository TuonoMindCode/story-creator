"""Tab — Queue: running batch + upcoming batches, each with its own settings."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class QueueTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.state = main.state

        lay = QVBoxLayout(self)
        info = QLabel(
            "Every 'Run Batch' click becomes a queue item holding a snapshot of the "
            "settings at that moment — changing settings afterwards only affects "
            "batches you queue later. Items run one after another."
        )
        info.setWordWrap(True)
        lay.addWidget(info)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.finish_btn = QPushButton("Finish Story, Skip Rest")
        self.finish_btn.setToolTip(
            "Let the story being generated finish completely, drop the batch's "
            "remaining stories, then continue with the next queue item.")
        self.finish_btn.clicked.connect(self.main.tab_start.finish_story_skip_rest)
        self.skip_btn = QPushButton("Cancel Batch Item")
        self.skip_btn.setToolTip(
            "Stop the running batch immediately (mid-scene); the queue continues "
            "with the next item.")
        self.skip_btn.clicked.connect(self.main.tab_start.skip_current)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.setToolTip("Remove an upcoming queue item (not the running one).")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.clear_btn = QPushButton("Clear Upcoming")
        self.clear_btn.clicked.connect(self._clear_upcoming)
        self.stop_btn = QPushButton("Stop All")
        self.stop_btn.setToolTip("Cancel the running batch AND clear the queue.")
        self.stop_btn.clicked.connect(self.main.tab_start.stop_all)
        self.resume_btn = QPushButton("Start / Resume Queue")
        self.resume_btn.setToolTip(
            "Start processing queued items (e.g. after a batch stopped on an error).")
        self.resume_btn.clicked.connect(self.main.tab_start._process_queue)
        for b in (self.finish_btn, self.skip_btn, self.remove_btn,
                  self.clear_btn, self.stop_btn, self.resume_btn):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.state.queue_changed.connect(self.refresh)
        self.state.busy_changed.connect(lambda _b: self.refresh())
        self.refresh()

    def _has_current(self) -> bool:
        return self.main.tab_start.current_spec is not None

    def refresh(self):
        selected = self.list.currentRow()
        self.list.clear()
        current = self.main.tab_start.current_spec
        soft_stopping = (self.main.worker is not None
                         and self.main.worker.soft_stop.is_set())
        if current is not None:
            progress = self.main.tab_start.current_story_progress
            line = "▶ RUNNING"
            if progress:
                num, count = progress
                # after "finish story, skip rest" only `num` stories will be made
                effective = num if soft_stopping else count
                line += f" — story {num} of {effective}"
                line += f" — {current.label_for(effective)}"
            else:
                line += f" — {current.label}"
            if soft_stopping:
                line += "   (finishing this story, then skipping the rest)"
            self.list.addItem(line)
        for i, spec in enumerate(self.state.job_queue, 1):
            self.list.addItem(f"{i}. {spec.label}")
        if not self.list.count():
            self.list.addItem("(queue is empty — add batches from the Story Start tab)")
        if 0 <= selected < self.list.count():
            self.list.setCurrentRow(selected)

        busy = self.main.is_busy()
        queued = bool(self.state.job_queue)
        self.finish_btn.setEnabled(busy and current is not None and not soft_stopping)
        self.remove_btn.setEnabled(queued)
        self.clear_btn.setEnabled(queued)
        self.skip_btn.setEnabled(busy and current is not None)
        self.stop_btn.setEnabled(busy or queued)
        self.resume_btn.setEnabled(queued and not busy)

    def _queue_index(self, row: int) -> int:
        """Map a list row to an index in state.job_queue (-1 if not a queue row)."""
        offset = 1 if self._has_current() else 0
        idx = row - offset
        if 0 <= idx < len(self.state.job_queue):
            return idx
        return -1

    def _remove_selected(self):
        idx = self._queue_index(self.list.currentRow())
        if idx < 0:
            self.main.statusBar().showMessage(
                "Select an upcoming item (the running one can only be skipped).")
            return
        spec = self.state.job_queue.pop(idx)
        self.state.queue_changed.emit()
        self.main.statusBar().showMessage(f"Removed from queue: {spec.label}")

    def _clear_upcoming(self):
        n = len(self.state.job_queue)
        self.state.job_queue.clear()
        self.state.queue_changed.emit()
        self.main.statusBar().showMessage(f"Cleared {n} queued batch(es).")
