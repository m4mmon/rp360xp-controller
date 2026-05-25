"""Widgets: ParamRow, SlotCard, AmpPanel, DetailPanel, PresetPanel."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..effects_db import EffectsDB
from ..model import Ctrl, FxSlot, LFO_WAVEFORMS, Preset

_db = EffectsDB()
_CABINET_NAMES: list[str] = _db.cabinet_names()   # loaded once at import time

# ---------------------------------------------------------------------------
# Fixed heights — computed once from the effects catalogue so the UI never
# resizes when switching presets or selecting slots.
# ---------------------------------------------------------------------------

def _max_param_count(category: str | None = None) -> int:
    effects = _db.by_category(category) if category else _db._effects
    if not effects:
        return 0
    return max(
        len([p for p in e.get("params", []) if p["address"] != "ENABLE"])
        for e in effects
    )

_N_AMP = _max_param_count("Amplifier")   # 5
_N_ALL = _max_param_count()              # 9  (EQ)

_ROW_H   = 28   # ParamRow (slider + spinbox)
_TEXT_H  = 17   # small text label inside a SlotCard
_LABEL_H = 20   # regular label (name, category)

# CABINET will be added to the JSON later; reserve one extra row now so the
# layout doesn't change when the updated DB arrives.
_N_AMP_WITH_CAB = _N_AMP + 1   # currently 5 slider rows + 1 combo row

# PresetHeader: single HBoxLayout row (14pt label + buttons)
PRESET_HDR_H = 44

# AmpPanel: outer margins 12px + name label + N slider rows + 1 combo row
AMP_PANEL_H  = 12 + _LABEL_H + 4 + _N_AMP * (_ROW_H + 2) + (_ROW_H + 2)

# SlotCard: outer margins 8px + hdr 26px + category 16px + name 20px +
#           separator 8px + N_ALL text rows
SLOT_CARD_H  = 8 + 26 + 16 + 20 + 8 + _N_ALL * (_TEXT_H + 1)

# DetailPanel: outer margins 14px + header label + N_ALL param rows
DETAIL_H     = 14 + _LABEL_H + _N_ALL * (_ROW_H + 2)

# BottomPanel: 4 sections side by side; height sized to the tallest (LFO) —
#   outer margins 10+10, title label, lnk label, 4 rows (MIN MAX SPEED WAVEFORM)
_N_LFO_ROWS = 4
BOTTOM_H = 10 + _LABEL_H + 4 + _TEXT_H + _N_LFO_ROWS * (_ROW_H + 2) + 10

# ---------------------------------------------------------------------------


def _slot_from_lnk(lnk: str) -> int | None:
    m = re.search(r'fxc/(\d+)/ENABLE', lnk)
    return int(m.group(1)) if m else None


def _effect_info(slot: FxSlot) -> tuple[str, str]:
    """Return (category, displayName) from the effects DB."""
    address = slot.model.split(".")[-1] if "." in slot.model else slot.model
    effect = _db.by_address(address)
    if effect:
        return effect["category"], effect["displayName"]
    return "", address


def _lnk_label(lnk: str, preset: Preset | None) -> str:
    """Human-readable description of a ctrl LNK path."""
    if not lnk:
        return "— not assigned"
    m = re.search(r'fxc/(\d+)/(?:fx/)?([^/]+)$', lnk)
    if not m:
        return lnk
    n, param = int(m.group(1)), m.group(2)
    if preset and n in preset.slots:
        _, display = _effect_info(preset.slots[n])
        return f"Slot {n} · {display} · {param}"
    return f"Slot {n} · {param}"


def _param_ranges(slot: FxSlot) -> dict[str, tuple[int, int]]:
    address = slot.model.split(".")[-1] if "." in slot.model else slot.model
    effect = _db.by_address(address)
    if not effect:
        return {}
    return {
        p["address"]: (p.get("min", 0), p.get("max", 99))
        for p in effect.get("params", [])
        if p["address"] != "ENABLE"
    }


# ---------------------------------------------------------------- ParamRow

class ParamRow(QWidget):
    value_changed = Signal(str, int)   # param_name, value

    def __init__(self, name: str, value: int, lo: int, hi: int, parent=None):
        super().__init__(parent)
        self._name = name
        self._suppress = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lbl = QLabel(name)
        lbl.setFixedWidth(96)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(lbl)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(lo, hi)
        self._slider.setValue(value)
        lay.addWidget(self._slider, 1)

        self._spin = QSpinBox()
        self._spin.setRange(lo, hi)
        self._spin.setValue(value)
        self._spin.setFixedWidth(60)
        lay.addWidget(self._spin)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(80)
        self._timer.timeout.connect(
            lambda: self.value_changed.emit(self._name, self._slider.value())
        )
        self._slider.valueChanged.connect(self._from_slider)
        self._spin.valueChanged.connect(self._from_spin)

    def _from_slider(self, v: int):
        if self._suppress:
            return
        self._suppress = True
        self._spin.setValue(v)
        self._suppress = False
        self._timer.start()

    def _from_spin(self, v: int):
        if self._suppress:
            return
        self._suppress = True
        self._slider.setValue(v)
        self._suppress = False
        self._timer.start()

    def set_value(self, v: int):
        self._suppress = True
        self._slider.setValue(v)
        self._spin.setValue(v)
        self._suppress = False


# ---------------------------------------------------------------- CabinetRow

class CabinetRow(QWidget):
    """Combo-based selector for the CABINET param.

    Items are populated from a list of option strings.  When the effects DB is
    updated with cabinet names, pass them via ``set_options``; until then the
    widget shows plain numeric values.
    """
    value_changed = Signal(str, int)   # param_name ("CABINET"), value

    _PARAM = "CABINET"

    def __init__(self, value: int, options: list[str] | None = None, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        lbl = QLabel(self._PARAM)
        lbl.setFixedWidth(96)
        lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(lbl)

        self._combo = QComboBox()
        self._combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self._combo, 1)

        self._options: list[str] = []
        # Use DB names if the caller didn't provide explicit options
        self.set_options(options if options is not None else _CABINET_NAMES, value)
        self._combo.currentIndexChanged.connect(self._on_change)

    def set_options(self, options: list[str], current: int | None = None):
        """Replace combo items.  ``options`` is a list of display strings
        indexed from 0.  If empty, fall back to plain numbers 0..max_value."""
        self._combo.blockSignals(True)
        self._combo.clear()
        self._options = options
        if options:
            for label in options:
                self._combo.addItem(label)
        else:
            # Placeholder: show numeric values.  Use the current value to
            # determine a minimum range; expand later when DB is updated.
            _max = max(current or 0, 30)
            for i in range(_max + 1):
                self._combo.addItem(str(i))
        if current is not None:
            idx = current if current < self._combo.count() else 0
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def set_value(self, v: int):
        self._combo.blockSignals(True)
        if v < self._combo.count():
            self._combo.setCurrentIndex(v)
        self._combo.blockSignals(False)

    def _on_change(self, idx: int):
        self.value_changed.emit(self._PARAM, idx)


# ---------------------------------------------------------------- PresetHeader

class PresetHeader(QWidget):
    save_clicked = Signal()
    refresh_clicked = Signal()
    level_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(PRESET_HDR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)

        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet("font-size: 14pt; font-weight: bold;")
        lay.addWidget(self._name_lbl)

        self._bank_lbl = QLabel()
        self._bank_lbl.setStyleSheet("color: gray;")
        lay.addWidget(self._bank_lbl)

        self._dirty_lbl = QLabel("●")
        self._dirty_lbl.setStyleSheet("color: orange; font-size: 10pt;")
        self._dirty_lbl.setToolTip("Unsaved changes")
        self._dirty_lbl.setVisible(False)
        lay.addWidget(self._dirty_lbl)

        lay.addStretch()

        lay.addWidget(QLabel("Level:"))
        self._level_slider = QSlider(Qt.Horizontal)
        self._level_slider.setRange(0, 99)
        self._level_slider.setFixedWidth(100)
        self._level_spin = QSpinBox()
        self._level_spin.setRange(0, 99)
        self._level_spin.setFixedWidth(52)
        lay.addWidget(self._level_slider)
        lay.addWidget(self._level_spin)

        self._level_suppress = False
        self._level_timer = QTimer(self)
        self._level_timer.setSingleShot(True)
        self._level_timer.setInterval(80)
        self._level_timer.timeout.connect(
            lambda: self.level_changed.emit(self._level_slider.value())
        )
        self._level_slider.valueChanged.connect(self._level_from_slider)
        self._level_spin.valueChanged.connect(self._level_from_spin)

        lay.addSpacing(12)
        btn_save = QPushButton("Save")
        btn_save.setFixedWidth(72)
        btn_save.clicked.connect(self.save_clicked)
        lay.addWidget(btn_save)

        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedWidth(36)
        btn_refresh.setToolTip("Refresh from device")
        btn_refresh.clicked.connect(self.refresh_clicked)
        lay.addWidget(btn_refresh)

    def mark_dirty(self):
        self._dirty_lbl.setVisible(True)

    def update(self, preset: Preset, dirty: bool, bank: str, slot_1: int):
        self._name_lbl.setText(preset.name)
        self._bank_lbl.setText(f"{bank} #{slot_1}")
        self._dirty_lbl.setVisible(dirty)
        self._level_suppress = True
        self._level_slider.setValue(preset.prs_levl)
        self._level_spin.setValue(preset.prs_levl)
        self._level_suppress = False

    def update_level(self, value: int):
        self._level_suppress = True
        self._level_slider.setValue(value)
        self._level_spin.setValue(value)
        self._level_suppress = False

    def _level_from_slider(self, v: int):
        if self._level_suppress:
            return
        self._level_suppress = True
        self._level_spin.setValue(v)
        self._level_suppress = False
        self._level_timer.start()

    def _level_from_spin(self, v: int):
        if self._level_suppress:
            return
        self._level_suppress = True
        self._level_slider.setValue(v)
        self._level_suppress = False
        self._level_timer.start()


# ---------------------------------------------------------------- AmpPanel

class AmpPanel(QFrame):
    """Full-width amp slot panel — always visible at the top."""

    enable_toggled = Signal(int, bool)    # slot_idx, enabled
    param_changed  = Signal(int, str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "AmpPanel { border: 1px solid #888; border-radius: 4px;"
            " background: #2d2d2d; }"
        )
        self._idx = -1
        self._param_rows: dict[str, ParamRow] = {}

        self.setFixedHeight(AMP_PANEL_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(12)

        # Left column: enable + name
        left = QVBoxLayout()
        left.setSpacing(4)
        hdr = QHBoxLayout()
        self._enable_cb = QCheckBox()
        self._enable_cb.setStyleSheet("color: white;")
        hdr.addWidget(self._enable_cb)
        self._slot_lbl = QLabel()
        self._slot_lbl.setStyleSheet("color: #aaa; font-size: 8pt;")
        hdr.addWidget(self._slot_lbl)
        hdr.addStretch()
        left.addLayout(hdr)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            "color: #ffcc44; font-weight: bold; font-size: 11pt;"
        )
        left.addWidget(self._name_lbl)
        left.addStretch()
        lay.addLayout(left)

        # Right: param rows (expand to fill)
        self._params_widget = QWidget()
        self._params_layout = QVBoxLayout(self._params_widget)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(2)
        lay.addWidget(self._params_widget, 1)

        self.setVisible(False)

    def load_slot(self, slot_idx: int, slot: FxSlot):
        self._idx = slot_idx
        category, display = _effect_info(slot)
        self._slot_lbl.setText(f"Slot {slot_idx}")
        self._name_lbl.setText(display)

        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(bool(slot.enable))
        self._enable_cb.blockSignals(False)
        try:
            self._enable_cb.toggled.disconnect()
        except RuntimeError:
            pass
        self._enable_cb.toggled.connect(
            lambda v: self.enable_toggled.emit(self._idx, v)
        )

        while self._params_layout.count():
            w = self._params_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._param_rows.clear()

        ranges = _param_ranges(slot)
        for param, raw in slot.params.items():
            lo, hi = ranges.get(param, (0, 99))
            value = max(lo, min(hi, int(raw)))
            if param == CabinetRow._PARAM:
                row = CabinetRow(value)
            else:
                row = ParamRow(param, value, lo, hi)
            row.value_changed.connect(
                lambda p, v, idx=slot_idx: self.param_changed.emit(idx, p, v)
            )
            self._params_layout.addWidget(row)
            self._param_rows[param] = row

        self.setVisible(True)

    def update_enable(self, enabled: bool):
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(enabled)
        self._enable_cb.blockSignals(False)

    def update_param(self, param: str, value: int):
        if param in self._param_rows:
            self._param_rows[param].set_value(value)


# ---------------------------------------------------------------- SlotCard

class SlotCard(QFrame):
    """Compact slot card in the chain view."""

    selected_changed = Signal(int, bool)   # slot_idx, selected
    enable_toggled   = Signal(int, bool)   # slot_idx, enabled

    _STYLE_NORMAL   = ("QFrame { border: 1px solid #888; border-radius: 4px; }")
    _STYLE_SELECTED = ("QFrame { border: 2px solid #4a9eff; border-radius: 4px;"
                       " background: #1a3a5a; }")
    _STYLE_DISABLED = ("QFrame { border: 1px solid #555; border-radius: 4px;"
                       " color: #888; }")

    def __init__(self, slot_idx: int, slot: FxSlot, parent=None):
        super().__init__(parent)
        self._idx = slot_idx
        self._selected = False
        self._param_labels: dict[str, QLabel] = {}

        self.setFixedWidth(152)
        self.setFixedHeight(SLOT_CARD_H)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._enable_cb = QCheckBox()
        self._enable_cb.toggled.connect(
            lambda v: self.enable_toggled.emit(self._idx, v)
        )
        hdr.addWidget(self._enable_cb)
        num_lbl = QLabel(f"<small>Slot {slot_idx}</small>")
        num_lbl.setStyleSheet("color: #aaa;")
        hdr.addWidget(num_lbl)
        hdr.addStretch()
        lay.addLayout(hdr)

        self._category_lbl = QLabel()
        self._category_lbl.setStyleSheet("font-size: 8pt; color: #aaa;")
        lay.addWidget(self._category_lbl)

        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
        lay.addWidget(self._name_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #555;")
        lay.addWidget(sep)

        self._params_layout = QVBoxLayout()
        self._params_layout.setSpacing(0)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(self._params_layout)
        lay.addStretch()

        self._populate(slot)
        self.setStyleSheet(self._STYLE_NORMAL)

    # ---------------------------------------------------------- public

    def set_selected(self, selected: bool):
        self._selected = selected
        self.setStyleSheet(self._STYLE_SELECTED if selected else self._STYLE_NORMAL)

    def update_slot(self, slot: FxSlot):
        _, display = _effect_info(slot)
        if display not in self._name_lbl.text():
            self._populate(slot)
            return
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(bool(slot.enable))
        self._enable_cb.blockSignals(False)
        for param, value in slot.params.items():
            if param in self._param_labels:
                self._param_labels[param].setText(
                    f"<small>{param}: <b>{value}</b></small>"
                )

    def update_enable(self, enabled: bool):
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(enabled)
        self._enable_cb.blockSignals(False)

    def update_param(self, param: str, value):
        if param in self._param_labels:
            self._param_labels[param].setText(
                f"<small>{param}: <b>{value}</b></small>"
            )

    # ---------------------------------------------------------- private

    def _populate(self, slot: FxSlot):
        while self._params_layout.count():
            w = self._params_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._param_labels.clear()

        category, display = _effect_info(slot)
        self._category_lbl.setText(category)
        self._name_lbl.setText(display)

        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(bool(slot.enable))
        self._enable_cb.blockSignals(False)

        for param, value in slot.params.items():
            lbl = QLabel(f"<small>{param}: <b>{value}</b></small>")
            self._params_layout.addWidget(lbl)
            self._param_labels[param] = lbl

    def mousePressEvent(self, event):
        # QCheckBox consumes its own clicks; anything else toggles selection.
        new_sel = not self._selected
        self.set_selected(new_sel)
        self.selected_changed.emit(self._idx, new_sel)
        super().mousePressEvent(event)


# ---------------------------------------------------------------- AmpMarker

class AmpMarker(QFrame):
    """Visual placeholder for the amp's position in the signal chain."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(56)
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background: #3a2a00; border: 1px solid #aa7700;"
            " border-radius: 4px; }"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 6, 4, 6)
        lbl = QLabel("AMP")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            "color: #ffcc44; font-weight: bold; font-size: 9pt;"
            " background: transparent; border: none;"
        )
        lay.addWidget(lbl)
        lay.addStretch()


# ---------------------------------------------------------------- DetailPanel

class DetailPanel(QFrame):
    """Param sliders for the selected slot — hidden when nothing is selected."""

    param_changed = Signal(int, str, int)   # slot_idx, param, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(DETAIL_H)
        self._slot_idx = -1
        self._param_rows: dict[str, ParamRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(4)

        self._header = QLabel()
        self._header.setStyleSheet("font-weight: bold; font-size: 10pt;")
        outer.addWidget(self._header)

        self._placeholder = QLabel("← Sélectionnez un slot pour éditer ses paramètres")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; font-style: italic;")
        outer.addWidget(self._placeholder, 1)

        self._params_widget = QWidget()
        self._params_layout = QVBoxLayout(self._params_widget)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(2)
        self._params_widget.setVisible(False)
        outer.addWidget(self._params_widget)

    @property
    def current_slot(self) -> int:
        return self._slot_idx

    def show_slot(self, slot_idx: int, slot: FxSlot):
        self._slot_idx = slot_idx

        while self._params_layout.count():
            w = self._params_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._param_rows.clear()

        category, display = _effect_info(slot)
        self._header.setText(
            f"Slot {slot_idx}  ·  {category}  ·  {display}"
            if category else f"Slot {slot_idx}  ·  {display}"
        )

        ranges = _param_ranges(slot)
        for param, raw in slot.params.items():
            lo, hi = ranges.get(param, (0, 99))
            value = max(lo, min(hi, int(raw)))
            row = ParamRow(param, value, lo, hi)
            row.value_changed.connect(
                lambda p, v, idx=slot_idx: self.param_changed.emit(idx, p, v)
            )
            self._params_layout.addWidget(row)
            self._param_rows[param] = row

        self._placeholder.setVisible(False)
        self._params_widget.setVisible(True)

    def hide_slot(self):
        self._slot_idx = -1
        self._params_widget.setVisible(False)
        self._placeholder.setVisible(True)
        self._header.setText("")

    def update_param(self, param: str, value: int):
        if param in self._param_rows:
            self._param_rows[param].set_value(value)


# ---------------------------------------------------------------- StompsSection

class StompsSection(QFrame):
    """Toggle-control assignments: ctrlA, ctrlB, ctrlC, and ctrlVSw (toe switch)."""

    assignment_changed = Signal(str, int)   # ctrl_name, slot_idx (-1 = clear)

    _CTRLS = (("A", "ctrlA"), ("B", "ctrlB"), ("C", "ctrlC"), ("Toe", "ctrlVSw"))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title = QLabel("Stomps")
        title.setStyleSheet("font-weight: bold; font-size: 10pt;")
        lay.addWidget(title)

        self._combos: dict[str, QComboBox] = {}
        for letter, ctrl_name in self._CTRLS:
            row = QWidget()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)
            lbl = QLabel(f"{letter}:")
            lbl.setFixedWidth(28)
            rl.addWidget(lbl)
            cb = QComboBox()
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cb.currentIndexChanged.connect(
                lambda _, c=cb, cn=ctrl_name: self._on_change(c, cn)
            )
            rl.addWidget(cb, 1)
            lay.addWidget(row)
            self._combos[ctrl_name] = cb

        lay.addStretch()

    def update_preset(self, preset: Preset):
        slot_ids = sorted(preset.slots.keys())
        for ctrl_name, cb in self._combos.items():
            cb.blockSignals(True)
            cb.clear()
            cb.addItem("—", -1)
            for s in slot_ids:
                _, label = _effect_info(preset.slots[s])
                cb.addItem(f"Slot {s} · {label}", s)
            ctrl = preset.ctrls.get(ctrl_name)
            assigned = _slot_from_lnk(ctrl.lnk) if ctrl and ctrl.lnk else None
            idx = cb.findData(assigned) if assigned is not None else 0
            cb.setCurrentIndex(max(0, idx))
            cb.blockSignals(False)

    def _on_change(self, cb: QComboBox, ctrl_name: str):
        slot = cb.currentData()
        self.assignment_changed.emit(ctrl_name, int(slot) if slot is not None else -1)


# ---------------------------------------------------------------- CtrlRangePanel

class CtrlRangePanel(QFrame):
    """Display/edit panel for a continuous controller with MIN and MAX (treadle or altTreadle)."""

    ctrl_field_changed = Signal(str, str, int)   # ctrl_name, field, value

    def __init__(self, ctrl_name: str, title: str, parent=None):
        super().__init__(parent)
        self._ctrl_name = ctrl_name
        self._rows: dict[str, ParamRow] = {}

        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        lay.addWidget(title_lbl)

        self._lnk_lbl = QLabel("— not assigned")
        self._lnk_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        lay.addWidget(self._lnk_lbl)

        for field, lo, hi in (("MIN", 0, 99), ("MAX", 0, 99)):
            row = ParamRow(field, 0, lo, hi)
            row.value_changed.connect(
                lambda f, v, cn=ctrl_name: self.ctrl_field_changed.emit(cn, f, v)
            )
            lay.addWidget(row)
            self._rows[field] = row

        lay.addStretch()

    def update_ctrl(self, ctrl: Ctrl | None, preset: Preset | None = None):
        assigned = bool(ctrl and ctrl.lnk)
        self._lnk_lbl.setText(_lnk_label(ctrl.lnk if ctrl else "", preset))
        for row in self._rows.values():
            row.setEnabled(assigned)
        if ctrl:
            if ctrl.min is not None:
                self._rows["MIN"].set_value(ctrl.min)
            if ctrl.max is not None:
                self._rows["MAX"].set_value(ctrl.max)


# ---------------------------------------------------------------- LfoPanel

class LfoPanel(QFrame):
    """Display/edit panel for lfo1 (MIN, MAX, SPEED, WAVEFORM)."""

    ctrl_field_changed = Signal(str, str, int)   # "lfo1", field, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, ParamRow] = {}
        self._wave_suppress = False

        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title_lbl = QLabel("LFO")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        lay.addWidget(title_lbl)

        self._lnk_lbl = QLabel("— not assigned")
        self._lnk_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        lay.addWidget(self._lnk_lbl)

        for field, lo, hi in (("MIN", 0, 99), ("MAX", 0, 99), ("SPEED", 0, 185)):
            row = ParamRow(field, 0, lo, hi)
            row.value_changed.connect(
                lambda f, v: self.ctrl_field_changed.emit("lfo1", f, v)
            )
            lay.addWidget(row)
            self._rows[field] = row

        # Waveform combo row
        wave_row = QWidget()
        wl = QHBoxLayout(wave_row)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        wave_lbl = QLabel("WAVEFORM")
        wave_lbl.setFixedWidth(96)
        wave_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        wl.addWidget(wave_lbl)
        self._wave_combo = QComboBox()
        self._wave_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for k in sorted(LFO_WAVEFORMS):
            self._wave_combo.addItem(LFO_WAVEFORMS[k], k)
        self._wave_combo.currentIndexChanged.connect(self._on_waveform)
        wl.addWidget(self._wave_combo, 1)
        lay.addWidget(wave_row)
        self._wave_row = wave_row

        lay.addStretch()

    def _on_waveform(self, _idx: int):
        if not self._wave_suppress:
            self.ctrl_field_changed.emit("lfo1", "WAVEFORM", self._wave_combo.currentData())

    def update_ctrl(self, ctrl: Ctrl | None, preset: Preset | None = None):
        assigned = bool(ctrl and ctrl.lnk)
        self._lnk_lbl.setText(_lnk_label(ctrl.lnk if ctrl else "", preset))
        for row in self._rows.values():
            row.setEnabled(assigned)
        self._wave_row.setEnabled(assigned)
        if ctrl:
            if ctrl.min is not None:
                self._rows["MIN"].set_value(ctrl.min)
            if ctrl.max is not None:
                self._rows["MAX"].set_value(ctrl.max)
            if ctrl.speed is not None:
                self._rows["SPEED"].set_value(ctrl.speed)
            if ctrl.waveform is not None:
                self._wave_suppress = True
                idx = self._wave_combo.findData(ctrl.waveform)
                if idx >= 0:
                    self._wave_combo.setCurrentIndex(idx)
                self._wave_suppress = False


# ---------------------------------------------------------------- BottomPanel

class BottomPanel(QWidget):
    """Four-section bottom bar: Stomps · Expression · LFO · Wah."""

    stomp_changed      = Signal(str, int)        # ctrl_name, slot_idx (-1 = clear)
    ctrl_field_changed = Signal(str, str, int)   # ctrl_name, field, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BOTTOM_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        self._stomps = StompsSection()
        self._stomps.assignment_changed.connect(self.stomp_changed)
        lay.addWidget(self._stomps, 1)

        self._expr = CtrlRangePanel("treadle", "Expression")
        self._expr.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._expr, 1)

        self._lfo = LfoPanel()
        self._lfo.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._lfo, 1)

        self._wah = CtrlRangePanel("altTreadle", "Wah")
        self._wah.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._wah, 1)

    def update_preset(self, preset: Preset):
        self._stomps.update_preset(preset)
        self._expr.update_ctrl(preset.ctrls.get("treadle"), preset)
        self._lfo.update_ctrl(preset.ctrls.get("lfo1"), preset)
        self._wah.update_ctrl(preset.ctrls.get("altTreadle"), preset)


# ---------------------------------------------------------------- PresetPanel

class PresetPanel(QWidget):
    """Full preset editing panel: header + amp + chain + detail + bottom."""

    enable_toggled     = Signal(int, bool)
    param_changed      = Signal(int, str, int)
    level_changed      = Signal(int)
    save_clicked       = Signal()
    refresh_clicked    = Signal()
    stomp_changed      = Signal(str, int)
    ctrl_field_changed = Signal(str, str, int)   # ctrl_name, field, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._amp_idx = -1          # slot index of the amp
        self._selected_idx = -1     # currently selected non-amp slot
        self._slot_cards: dict[int, SlotCard] = {}
        self._current_preset: Preset | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Preset name / level / save bar
        self._header = PresetHeader()
        self._header.save_clicked.connect(self.save_clicked)
        self._header.refresh_clicked.connect(self.refresh_clicked)
        self._header.level_changed.connect(self.level_changed)
        lay.addWidget(self._header)

        # Amp panel
        self._amp_panel = AmpPanel()
        self._amp_panel.enable_toggled.connect(self.enable_toggled)
        self._amp_panel.param_changed.connect(self.param_changed)
        lay.addWidget(self._amp_panel)

        # Horizontal chain
        self._chain_scroll = QScrollArea()
        self._chain_scroll.setWidgetResizable(True)
        self._chain_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chain_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._chain_scroll.setFrameShape(QFrame.NoFrame)

        self._chain_widget = QWidget()
        self._chain_layout = QHBoxLayout(self._chain_widget)
        self._chain_layout.setContentsMargins(6, 6, 6, 6)
        self._chain_layout.setSpacing(6)
        self._chain_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._chain_scroll.setWidget(self._chain_widget)
        self._chain_scroll.setFixedHeight(SLOT_CARD_H + 16)  # card + layout margins
        lay.addWidget(self._chain_scroll)

        # Detail panel (hidden until a slot is selected)
        self._detail = DetailPanel()
        self._detail.param_changed.connect(self.param_changed)
        lay.addWidget(self._detail)

        # Bottom panel (stomps + expression + LFO + wah)
        self._bottom = BottomPanel()
        self._bottom.stomp_changed.connect(self.stomp_changed)
        self._bottom.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._bottom)

    # ---------------------------------------------------------- public API

    def mark_dirty(self):
        self._header.mark_dirty()

    def update_preset(self, preset: Preset, dirty: bool, bank: str, slot_1: int):
        self._current_preset = preset
        self._header.update(preset, dirty, bank, slot_1)

        # Find amp slot
        amp_idx = next(
            (idx for idx, s in preset.slots.items()
             if s.model.startswith("amp.")),
            -1,
        )
        self._amp_idx = amp_idx
        if amp_idx >= 0:
            self._amp_panel.load_slot(amp_idx, preset.slots[amp_idx])
        else:
            self._amp_panel.setVisible(False)

        self._rebuild_chain(preset)
        self._bottom.update_preset(preset)

        # Refresh detail panel if its slot still exists
        if self._selected_idx >= 0:
            if self._selected_idx in preset.slots:
                self._detail.show_slot(
                    self._selected_idx, preset.slots[self._selected_idx]
                )
            else:
                self._selected_idx = -1
                self._detail.hide_slot()

    def update_param(self, slot: int, param: str, value: int):
        if slot == self._amp_idx:
            self._amp_panel.update_param(param, value)
            return
        if slot in self._slot_cards:
            self._slot_cards[slot].update_param(param, value)
        if slot == self._selected_idx:
            self._detail.update_param(param, value)

    def update_level(self, value: int):
        self._header.update_level(value)

    def update_enable(self, slot: int, enabled: bool):
        if slot == self._amp_idx:
            self._amp_panel.update_enable(enabled)
            return
        if slot in self._slot_cards:
            self._slot_cards[slot].update_enable(enabled)

    # ---------------------------------------------------------- private

    def _rebuild_chain(self, preset: Preset):
        # Remove all widgets from the chain layout
        while self._chain_layout.count():
            item = self._chain_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._slot_cards.clear()

        # Use the key order from the preset JSON (chain_order).
        # If the device serialises fxc in signal-chain order this is correct;
        # if not, it falls back gracefully to slot-index order.
        order = preset.chain_order if preset.chain_order else sorted(preset.slots.keys())
        for idx in order:
            if idx == self._amp_idx:
                self._chain_layout.addWidget(AmpMarker())
            else:
                slot = preset.slots[idx]
                card = SlotCard(idx, slot)
                card.enable_toggled.connect(self.enable_toggled)
                card.selected_changed.connect(self._on_card_selected)
                self._chain_layout.addWidget(card)
                self._slot_cards[idx] = card

    def _on_card_selected(self, slot_idx: int, selected: bool):
        prev = self._selected_idx

        if not selected:
            # Explicit deselect of the current card
            self._selected_idx = -1
            self._detail.hide_slot()
            return

        # Deselect previous card
        if prev >= 0 and prev != slot_idx and prev in self._slot_cards:
            self._slot_cards[prev].set_selected(False)

        self._selected_idx = slot_idx
        if self._current_preset and slot_idx in self._current_preset.slots:
            self._detail.show_slot(slot_idx, self._current_preset.slots[slot_idx])
