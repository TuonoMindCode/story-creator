"""Tab 8 — LLM Settings: one settings group per server (KoboldCpp / llama.cpp /
Ollama) plus appearance. Which server each pipeline section uses is chosen on
the Story Start tab."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

import backends
from backends import (
    BACKENDS,
    CHAT_TEMPLATES,
    DEFAULT_URLS,
    TEMPLATE_MODE_AUTO,
    TEMPLATE_MODE_MANUAL,
    BackendSettings,
    SectionConfig,
)
from ui_common import make_infinite_spin, make_spin

BACKEND_TITLES = {
    "koboldcpp": "KoboldCpp server",
    "llama.cpp": "llama.cpp server (llama-server)",
    "ollama": "Ollama server",
}


class BackendSettingsBox(QGroupBox):
    def __init__(self, main, name: str, parent=None):
        super().__init__(BACKEND_TITLES.get(name, name), parent)
        self.main = main
        self.name = name
        self.cfg: BackendSettings = main.state.backends_cfg[name]

        grid = QGridLayout(self)
        row = 0

        self.url_edit = QLineEdit(self.cfg.base_url or DEFAULT_URLS[name])
        self.url_edit.setPlaceholderText(DEFAULT_URLS[name])
        grid.addWidget(QLabel("Server URL"), row, 0)
        grid.addWidget(self.url_edit, row, 1)
        row += 1

        self.mode_box = QComboBox()
        self.mode_box.addItem("Auto (server chat template)", TEMPLATE_MODE_AUTO)
        for tpl_name in CHAT_TEMPLATES:
            self.mode_box.addItem(f"Manual: {tpl_name}", tpl_name)
        self.mode_box.addItem("Manual: Custom", "custom")
        self._select_mode()
        grid.addWidget(QLabel("Chat template"), row, 0)
        grid.addWidget(self.mode_box, row, 1)
        row += 1

        self.custom_edit = QPlainTextEdit(self.cfg.custom_template)
        self.custom_edit.setPlaceholderText(
            "Custom template with {system} and {user} placeholders…")
        self.custom_edit.setFixedHeight(70)
        self.custom_edit.setVisible(self.mode_box.currentData() == "custom")
        grid.addWidget(self.custom_edit, row, 0, 1, 2)
        row += 1

        self.ctx_spin = make_spin(self.cfg.context_length, 512, 262144, 512)
        self.ctx_spin.setToolTip(
            "Must match the context size the server was started with (e.g. "
            "llama-server -c 8192, koboldcpp --contextsize 8192, Ollama "
            "num_ctx). The scene prompt plus the section's Max tokens are "
            "budgeted to fit inside this — the Scene Writer tab shows the "
            "live numbers per scene.")
        grid.addWidget(QLabel("Context length"), row, 0)
        grid.addWidget(self.ctx_spin, row, 1)
        row += 1

        self.timeout_spin = make_infinite_spin(self.cfg.timeout, 0, 7200, 10)
        self.timeout_spin.setToolTip(
            "Read timeout in seconds; 'infinite' (0) never aborts a slow "
            "generation. You can type the word 'infinite' or set 0.")
        grid.addWidget(QLabel("Timeout (s)"), row, 0)
        grid.addWidget(self.timeout_spin, row, 1)
        row += 1

        test_row = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self.test_connection)
        reset_btn = QPushButton("Reset to defaults")
        reset_btn.setToolTip(
            f"Restore the typical settings for {BACKEND_TITLES.get(name, name)}: "
            f"URL {DEFAULT_URLS[name]}, Auto chat template, context 8192, "
            "infinite timeout.")
        reset_btn.clicked.connect(self.reset_to_defaults)
        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        test_row.addWidget(test_btn)
        test_row.addWidget(reset_btn)
        test_row.addWidget(self.result_label, 1)
        grid.addLayout(test_row, row, 0, 1, 2)

        self.url_edit.textChanged.connect(
            lambda t: setattr(self.cfg, "base_url", t.strip()))
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.custom_edit.textChanged.connect(
            lambda: setattr(self.cfg, "custom_template", self.custom_edit.toPlainText()))
        self.ctx_spin.valueChanged.connect(
            lambda v: setattr(self.cfg, "context_length", int(v)))
        self.timeout_spin.valueChanged.connect(
            lambda v: setattr(self.cfg, "timeout", int(v)))

    def _select_mode(self):
        if self.cfg.template_mode == TEMPLATE_MODE_AUTO:
            self.mode_box.setCurrentIndex(0)
        elif self.cfg.custom_template:
            self.mode_box.setCurrentIndex(self.mode_box.count() - 1)
        else:
            idx = self.mode_box.findData(self.cfg.template_name)
            self.mode_box.setCurrentIndex(idx if idx >= 0 else 0)

    def _mode_changed(self):
        data = self.mode_box.currentData()
        if data == TEMPLATE_MODE_AUTO:
            self.cfg.template_mode = TEMPLATE_MODE_AUTO
            self.custom_edit.setVisible(False)
        elif data == "custom":
            self.cfg.template_mode = TEMPLATE_MODE_MANUAL
            self.custom_edit.setVisible(True)
        else:
            self.cfg.template_mode = TEMPLATE_MODE_MANUAL
            self.cfg.template_name = data
            self.cfg.custom_template = ""
            self.custom_edit.setPlainText("")
            self.custom_edit.setVisible(False)

    def reset_to_defaults(self):
        defaults = BackendSettings(base_url=DEFAULT_URLS[self.name])
        # mutate in place — the AppState dict and this widget share the object
        self.cfg.base_url = defaults.base_url
        self.cfg.template_mode = defaults.template_mode
        self.cfg.template_name = defaults.template_name
        self.cfg.custom_template = defaults.custom_template
        self.cfg.context_length = defaults.context_length
        self.cfg.timeout = defaults.timeout
        self.url_edit.setText(self.cfg.base_url)
        self.mode_box.setCurrentIndex(0)
        self.custom_edit.setPlainText("")
        self.ctx_spin.setValue(self.cfg.context_length)
        self.timeout_spin.setValue(self.cfg.timeout)
        self.result_label.setText("Reset to typical defaults.")

    def test_connection(self):
        probe = SectionConfig(backend=self.name).with_backend_settings(self.cfg)
        try:
            msg = backends.test_connection(probe)
            self.result_label.setText(msg)
        except backends.BackendError as e:
            self.result_label.setText(str(e))


class SettingsTab(QWidget):
    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main

        outer = QVBoxLayout(self)
        info = QLabel(
            "Settings per LLM server. Which server (and model, and sampling "
            "parameters) each pipeline section uses is chosen on the Story Start tab."
        )
        info.setWordWrap(True)
        outer.addWidget(info)

        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Appearance:"))
        self.theme_box = QComboBox()
        self.theme_box.addItem("Dark (night)", "dark")
        self.theme_box.addItem("Light (classic)", "light")
        idx = self.theme_box.findData(main.state.ui.get("theme", "dark"))
        self.theme_box.setCurrentIndex(max(0, idx))
        self.theme_box.currentIndexChanged.connect(self._theme_changed)
        theme_row.addWidget(self.theme_box)
        theme_row.addStretch(1)
        outer.addLayout(theme_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        self.backend_boxes = {}
        for name in BACKENDS:
            w = BackendSettingsBox(main, name)
            self.backend_boxes[name] = w
            lay.addWidget(w)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self._save)
        outer.addWidget(save_btn)

    def _theme_changed(self):
        from theme import apply_theme
        name = self.theme_box.currentData()
        self.main.state.ui["theme"] = name
        apply_theme(name)
        self.main.state.save_settings()

    def _save(self):
        self.main.state.save_settings()
        self.main.statusBar().showMessage("Settings saved.")
