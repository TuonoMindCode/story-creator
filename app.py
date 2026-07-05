"""Story Creator — local LLM story writing app (PySide6).

Run with:  python app.py
"""
from __future__ import annotations

import json
import sys
import threading
import traceback

import applog

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
)

from backends import (
    BACKENDS,
    SECTION_KEYS,
    SECTION_LABELS,
    BackendError,
    BackendSettings,
    SectionConfig,
    default_backend_settings,
    default_section_params,
)
from project import SETTINGS_FILE, StoryProject
from theme import apply_theme, dark_palette

__version__ = "1.0.1"


class Worker(QThread):
    """Runs a blocking job function on a thread; fn(worker) -> result."""

    chunk = Signal(str)
    progress = Signal(str)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()
        # soft stop: finish the story being generated, then end the batch
        self.soft_stop = threading.Event()

    def run(self):
        try:
            result = self.fn(self)
            self.done.emit(result)
        except BackendError as e:
            # expected/operational failure (server down, model missing, cut
            # off …) — already logged and shown in the UI; no traceback spam
            print(f"[story-creator] {e}", file=sys.stderr)
            self.error.emit(str(e))
        except Exception as e:  # a real bug — keep the full traceback
            traceback.print_exc()
            applog.log("app", f"unexpected error: {e}")
            self.error.emit(str(e))


class AppState(QObject):
    """Shared state + change signals for all tabs."""

    storyboards_changed = Signal()
    descriptions_changed = Signal()
    project_changed = Signal()          # new/loaded project (outline included)
    outline_changed = Signal()
    scene_updated = Signal(int)         # scene index whose text/status changed
    busy_changed = Signal(bool)
    status_message = Signal(str)
    queue_changed = Signal()            # batch queue contents changed

    def __init__(self):
        super().__init__()
        self.sections: dict[str, SectionConfig] = {}
        self.backends_cfg: dict[str, BackendSettings] = default_backend_settings()
        self.ui: dict = {}
        self.project: StoryProject | None = None
        self.selected_storyboard: str = ""  # selection in the storyboard tab
        self.job_queue: list = []           # upcoming BatchSpec items
        self.load_settings()

    # -- settings -----------------------------------------------------------

    def load_settings(self):
        data = {}
        if SETTINGS_FILE.is_file():
            try:
                data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
        raw_sections = data.get("sections", {})
        for key in SECTION_KEYS:
            if key in raw_sections:
                self.sections[key] = SectionConfig.from_dict(raw_sections[key])
            else:
                cfg = SectionConfig()
                cfg.params = default_section_params(key)
                self.sections[key] = cfg
        self.backends_cfg = default_backend_settings()
        raw_backends = data.get("backends", {})
        if raw_backends:
            for name in BACKENDS:
                if name in raw_backends:
                    self.backends_cfg[name] = BackendSettings.from_dict(raw_backends[name])
                    if not self.backends_cfg[name].base_url:
                        self.backends_cfg[name] = default_backend_settings()[name]
        else:
            # migrate from the old per-section server settings
            for cfg in self.sections.values():
                if cfg.backend in self.backends_cfg and cfg.base_url:
                    b = self.backends_cfg[cfg.backend]
                    b.base_url = cfg.base_url
                    b.template_mode = cfg.template_mode
                    b.template_name = cfg.template_name
                    b.custom_template = cfg.custom_template
                    b.context_length = cfg.context_length
                    b.timeout = cfg.timeout
        self.ui = data.get("ui", {})
        self.ui.setdefault("num_scenes", 6)
        self.ui.setdefault("target_length", 900)
        self.ui.setdefault("theme", "dark")
        self.ui.setdefault("log_options", {})
        applog.OPTIONS.update(
            {k: bool(v) for k, v in self.ui["log_options"].items()
             if k in applog.OPTIONS})
        self.ui.setdefault("log_max_mb", 2)
        applog.MAX_BYTES = max(1, int(self.ui["log_max_mb"])) * 1_000_000

    def save_settings(self):
        data = {
            "sections": {k: cfg.to_dict() for k, cfg in self.sections.items()},
            "backends": {k: b.to_dict() for k, b in self.backends_cfg.items()},
            "ui": self.ui,
        }
        SETTINGS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def runtime_cfg(self, key: str) -> SectionConfig:
        """Section choice merged with the chosen backend's server settings."""
        sec = self.sections[key]
        return sec.with_backend_settings(self.backends_cfg[sec.backend])

    # -- convenience --------------------------------------------------------

    def autosave_project(self):
        if self.project is not None:
            try:
                self.project.save()
            except OSError as e:
                self.status_message.emit(f"Autosave failed: {e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Story Creator {__version__} — Local LLM Story Writer")
        self.resize(1280, 840)

        self.state = AppState()
        self.worker: Worker | None = None

        # the status bar must exist BEFORE the tabs — several widgets show
        # messages on it during construction
        self.setStatusBar(QStatusBar())
        self.state.status_message.connect(self.statusBar().showMessage)
        # let the pipeline surface warnings (e.g. output cut off by Max tokens)
        import pipeline
        pipeline.NOTIFY = self.state.status_message.emit

        # import here to avoid circulars (tabs import app for Worker typing)
        from ui_start_tab import StartTab
        from ui_builder_tab import BuilderTab
        from ui_storyboard_tab import StoryboardTab
        from ui_outline_tab import OutlineTab
        from ui_writer_tab import WriterTab
        from ui_lorebook_tab import LorebookTab
        from ui_complete_tab import CompleteTab
        from ui_settings_tab import SettingsTab
        from ui_prompts_tab import PromptsTab
        from ui_log_tab import LogTab
        from ui_queue_tab import QueueTab

        self.tabs = QTabWidget()
        self.tab_start = StartTab(self)
        self.tab_builder = BuilderTab(self)
        self.tab_storyboard = StoryboardTab(self)
        self.tab_outline = OutlineTab(self)
        self.tab_writer = WriterTab(self)
        self.tab_lorebook = LorebookTab(self)
        self.tab_complete = CompleteTab(self)
        self.tab_settings = SettingsTab(self)
        self.tab_prompts = PromptsTab(self)
        self.tab_log = LogTab(self)
        self.tab_queue = QueueTab(self)

        self.tabs.addTab(self.tab_start, "Story Start")
        self.tabs.addTab(self.tab_builder, "Detailed Builder")
        self.tabs.addTab(self.tab_storyboard, "Storyboards")
        self.tabs.addTab(self.tab_outline, "Scene Outline")
        self.tabs.addTab(self.tab_writer, "Scene Writer")
        self.tabs.addTab(self.tab_lorebook, "Lorebook")
        self.tabs.addTab(self.tab_complete, "Complete Story")
        self.tabs.addTab(self.tab_queue, "Queue")
        self.tabs.addTab(self.tab_settings, "LLM Settings")
        self.tabs.addTab(self.tab_prompts, "Prompts")
        self.tabs.addTab(self.tab_log, "Log")
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("Ready — configure your LLM servers in the LLM Settings tab.")

    # -- job running ----------------------------------------------------------

    def is_busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def run_job(self, fn, on_chunk=None, on_progress=None, on_done=None,
                on_error=None, status: str = "Working…"):
        """Run fn(worker) on a background thread. One job at a time."""
        if self.is_busy():
            QMessageBox.information(self, "Busy", "A generation is already running.")
            return None
        worker = Worker(fn, self)
        self.worker = worker
        if on_chunk:
            worker.chunk.connect(on_chunk)
        if on_progress:
            worker.progress.connect(on_progress)
        worker.progress.connect(self.statusBar().showMessage)

        def finish():
            self.state.busy_changed.emit(False)
            self.worker = None

        def handle_done(result):
            finish()
            self.statusBar().showMessage("Done.")
            if on_done:
                on_done(result)

        def handle_error(msg):
            finish()
            self.statusBar().showMessage("Error.")
            if on_error:
                on_error(msg)
            else:
                QMessageBox.critical(self, "Generation error", msg)

        worker.done.connect(handle_done)
        worker.error.connect(handle_error)
        self.state.busy_changed.emit(True)
        self.statusBar().showMessage(status)
        worker.start()
        return worker

    def cancel_job(self):
        if self.worker is not None:
            self.worker.cancel.set()
            self.statusBar().showMessage("Cancelling…")

    def soft_cancel_job(self):
        """Let the current story finish, then end the running batch."""
        if self.worker is not None:
            self.worker.soft_stop.set()
            self.statusBar().showMessage(
                "Finishing the current story, then skipping the rest of this batch…")

    def closeEvent(self, event):
        self.state.save_settings()
        self.state.autosave_project()
        if self.is_busy():
            self.worker.cancel.set()
            self.worker.wait(3000)
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    apply_theme(win.state.ui.get("theme", "dark"))
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
