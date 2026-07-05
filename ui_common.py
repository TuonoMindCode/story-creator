"""Shared UI widgets used by several tabs."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

import backends
from backends import BACKEND_KOBOLDCPP, BACKENDS, RANGE_PARAMS, SectionConfig


def hline() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def make_dspin(value, lo, hi, step, decimals=2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(decimals)
    s.setValue(value)
    return s


def make_spin(value, lo, hi, step=1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(value)
    return s


class InfiniteSpin(QSpinBox):
    """QSpinBox whose special value text (e.g. 'infinite') can also be TYPED."""

    def _special(self) -> str:
        return self.specialValueText().strip().lower()

    def validate(self, text: str, pos: int):
        from PySide6.QtGui import QValidator
        t = text.strip().lower()
        special = self._special()
        if special:
            if t == special:
                return QValidator.Acceptable, text, pos
            if t and special.startswith(t):
                return QValidator.Intermediate, text, pos
        return super().validate(text, pos)

    def valueFromText(self, text: str) -> int:
        if text.strip().lower() == self._special():
            return self.minimum()
        return super().valueFromText(text)


def make_infinite_spin(value, lo, hi, step=1, special="infinite") -> InfiniteSpin:
    s = InfiniteSpin()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setValue(value)
    s.setSpecialValueText(special)
    return s


class SectionQuickConfig(QGroupBox):
    """Backend + model + sampling params for one pipeline section (Tab 1).

    Every sampling parameter is a min–max range: a value is rolled randomly
    inside the range once per story (same value for every scene of a story).
    Keep min == max for a fixed value.
    """

    def __init__(self, title: str, cfg: SectionConfig, runtime_cfg=None,
                 status_cb=None, defaults_factory=None, parent=None):
        super().__init__(title, parent)
        self.cfg = cfg
        # callable returning the config merged with the backend's server settings
        self.runtime_cfg = runtime_cfg or (lambda: cfg)
        self.status_cb = status_cb or (lambda msg: None)
        self.defaults_factory = defaults_factory
        self._range_spins: dict[str, tuple] = {}
        self._kobold_rows: list[QWidget] = []

        SPIN_W = 84  # keep min/max boxes compact and close together

        grid = QGridLayout(self)
        grid.setVerticalSpacing(3)
        grid.setHorizontalSpacing(6)
        grid.setColumnStretch(4, 1)  # push everything to the left
        row = 0

        self.backend_box = QComboBox()
        self.backend_box.addItems(BACKENDS)
        self.backend_box.setCurrentText(cfg.backend)
        grid.addWidget(QLabel("Backend"), row, 0)
        grid.addWidget(self.backend_box, row, 1, 1, 3)
        row += 1

        self.model_box = QComboBox()
        self.model_box.setEditable(True)
        self.model_box.setEditText(cfg.model)
        refresh = QPushButton("↻")
        refresh.setFixedWidth(28)
        refresh.setToolTip("Fetch model list from the server")
        refresh.clicked.connect(self.refresh_models)
        grid.addWidget(QLabel("Model"), row, 0)
        grid.addWidget(self.model_box, row, 1, 1, 3)
        grid.addWidget(refresh, row, 4, Qt.AlignLeft)
        row += 1

        min_header = QLabel("min")
        max_header = QLabel("max")
        for h in (min_header, max_header):
            h.setStyleSheet("color: gray;")
            h.setAlignment(Qt.AlignCenter)
        grid.addWidget(min_header, row, 1)
        grid.addWidget(max_header, row, 3)
        row += 1

        for name, label, lo_lim, hi_lim, step, decimals, _default, kobold_only in RANGE_PARAMS:
            pair = cfg.params.get_range(name)
            if decimals == 0:
                lo_spin = make_spin(int(pair[0]), int(lo_lim), int(hi_lim), int(step) or 1)
                hi_spin = make_spin(int(pair[1]), int(lo_lim), int(hi_lim), int(step) or 1)
            else:
                lo_spin = make_dspin(pair[0], lo_lim, hi_lim, step, decimals)
                hi_spin = make_dspin(pair[1], lo_lim, hi_lim, step, decimals)
            lo_spin.setFixedWidth(SPIN_W)
            hi_spin.setFixedWidth(SPIN_W)
            lbl = QLabel(label)
            if kobold_only:
                lbl.setToolTip("KoboldCpp only — ignored by other backends")
            grid.addWidget(lbl, row, 0)
            grid.addWidget(lo_spin, row, 1)
            dash = QLabel("–")
            dash.setFixedWidth(10)
            dash.setAlignment(Qt.AlignCenter)
            grid.addWidget(dash, row, 2)
            grid.addWidget(hi_spin, row, 3)
            lo_spin.valueChanged.connect(
                lambda v, n=name: self._range_changed(n, 0, v))
            hi_spin.valueChanged.connect(
                lambda v, n=name: self._range_changed(n, 1, v))
            self._range_spins[name] = (lo_spin, hi_spin)
            if kobold_only:
                self._kobold_rows += [lbl, lo_spin, dash, hi_spin]
            row += 1

        self.max_tok = make_spin(cfg.params.max_tokens, 64, 32768, 64)
        self.max_tok.setFixedWidth(SPIN_W)
        grid.addWidget(QLabel("Max tokens"), row, 0)
        grid.addWidget(self.max_tok, row, 1)
        row += 1

        self.seed = make_spin(cfg.params.seed, -1, 2_000_000_000)
        self.seed.setSpecialValueText("random")
        self.seed.setFixedWidth(SPIN_W + 30)
        grid.addWidget(QLabel("Seed"), row, 0)
        grid.addWidget(self.seed, row, 1, 1, 3)
        row += 1

        if self.defaults_factory is not None:
            reset_btn = QPushButton("Reset to defaults")
            reset_btn.setToolTip("Restore this section's typical safe parameters.")
            reset_btn.clicked.connect(self.reset_to_defaults)
            grid.addWidget(reset_btn, row, 0, 1, 4)
            row += 1

        self.backend_box.currentTextChanged.connect(self._backend_changed)
        self.model_box.editTextChanged.connect(lambda t: setattr(cfg, "model", t.strip()))
        self.max_tok.valueChanged.connect(
            lambda v: setattr(cfg.params, "max_tokens", int(v)))
        self.seed.valueChanged.connect(lambda v: setattr(cfg.params, "seed", int(v)))
        self._update_kobold_rows()

    def _range_changed(self, name: str, which: int, value):
        self.cfg.params.get_range(name)[which] = value

    def _update_kobold_rows(self):
        show = self.cfg.backend == BACKEND_KOBOLDCPP
        for w in self._kobold_rows:
            w.setVisible(show)

    def _backend_changed(self, name: str):
        self.cfg.backend = name
        self.cfg.model = ""
        self.model_box.clear()
        self._update_kobold_rows()
        # fetch the model list right away so e.g. Ollama always gets a model name
        self.refresh_models(quiet=True)

    def refresh_models(self, quiet: bool = False):
        try:
            models = backends.list_models(self.runtime_cfg())
        except backends.BackendError as e:
            if not quiet:
                try:
                    self.status_cb(str(e))
                except RuntimeError:
                    pass  # window is closing
            return
        current = self.model_box.currentText()
        self.model_box.clear()
        self.model_box.addItems(models)
        if current and current in models:
            self.model_box.setEditText(current)
        elif models:
            self.model_box.setEditText(models[0])
        self.status_cb(f"{len(models)} model(s) found for {self.cfg.backend}.")

    def reset_to_defaults(self):
        if self.defaults_factory is None:
            return
        self.cfg.params = self.defaults_factory()
        self.sync_from_config()
        self.status_cb(f"{self.title()} parameters reset to defaults.")

    def sync_from_config(self):
        """Refresh widget values after the config was edited elsewhere."""
        cfg = self.cfg
        self.backend_box.setCurrentText(cfg.backend)
        self.model_box.setEditText(cfg.model)
        for name, (lo_spin, hi_spin) in self._range_spins.items():
            pair = cfg.params.get_range(name)
            lo_spin.setValue(pair[0])
            hi_spin.setValue(pair[1])
        self.max_tok.setValue(cfg.params.max_tokens)
        self.seed.setValue(cfg.params.seed)
        self._update_kobold_rows()
