"""Widgets: ParamRow, SlotCard, AmpPanel, DetailPanel, PresetPanel."""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QMimeData, Qt, QTimer, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
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

_N_ALL = _max_param_count()              # 9  (EQ)

_ROW_H   = 28   # ParamRow (slider + spinbox)
_TEXT_H  = 17   # small text label inside a SlotCard
_LABEL_H = 20   # regular label (name, category)

# PresetHeader: single HBoxLayout row (14pt label + buttons)
PRESET_HDR_H = 44

# SlotCard: outer margins 8px + hdr 26px + category 16px + name 20px +
#           separator 8px + N_ALL text rows
SLOT_CARD_H  = 8 + 26 + 16 + 20 + 8 + _N_ALL * (_TEXT_H + 1)

# DetailPanel: outer margins 14px + header label + N_ALL param rows
DETAIL_H     = 14 + _LABEL_H + _N_ALL * (_ROW_H + 2)

# BottomPanel: 4 sections side by side; height sized to the tallest (LFO) —
#   outer margins 10+10, title label, 6 rows (SLOT PARAM MIN MAX SPEED WAVEFORM)
_N_LFO_ROWS = 6
BOTTOM_H = 10 + _LABEL_H + _N_LFO_ROWS * (_ROW_H + 2) + 10

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

    @property
    def value(self) -> int:
        return self._slider.value()

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
    save_clicked      = Signal()
    store_new_clicked = Signal()
    refresh_clicked   = Signal()
    level_changed     = Signal(int)
    import_requested  = Signal(str)   # file path
    export_requested  = Signal(str)   # file path

    _FILE_FILTER = "RP360XP Preset (*.rp360p);;All files (*)"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(PRESET_HDR_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)

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

        # Level
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

        lay.addWidget(self._make_sep())

        self._btn_save = QPushButton("Quick Store")
        self._btn_save.setFixedWidth(88)
        self._btn_save.setToolTip("Save to current user slot")
        self._btn_save.clicked.connect(self.save_clicked)
        lay.addWidget(self._btn_save)

        btn_store_new = QPushButton("Store New")
        btn_store_new.setFixedWidth(82)
        btn_store_new.setToolTip("Save preset to a different slot with a new name")
        btn_store_new.clicked.connect(self.store_new_clicked)
        lay.addWidget(btn_store_new)

        lay.addWidget(self._make_sep())

        self._btn_import = QPushButton("Import")
        self._btn_import.setFixedWidth(68)
        self._btn_import.setToolTip("Load a preset from a .rp360p file")
        self._btn_import.clicked.connect(self._on_import_clicked)
        lay.addWidget(self._btn_import)

        btn_export = QPushButton("Export")
        btn_export.setFixedWidth(68)
        btn_export.setToolTip("Save the current preset to a .rp360p file")
        btn_export.clicked.connect(self._on_export_clicked)
        lay.addWidget(btn_export)

        lay.addWidget(self._make_sep())

        btn_refresh = QPushButton("↺")
        btn_refresh.setFixedWidth(36)
        btn_refresh.setToolTip("Refresh from device")
        btn_refresh.clicked.connect(self.refresh_clicked)
        lay.addWidget(btn_refresh)

    def set_readonly(self, readonly: bool):
        self._btn_save.setEnabled(not readonly)
        self._btn_import.setEnabled(not readonly)

    def clear(self):
        self._name_lbl.setText("")
        self._bank_lbl.setText("")
        self._dirty_lbl.setVisible(False)
        self._level_suppress = True
        self._level_slider.setValue(0)
        self._level_spin.setValue(0)
        self._level_suppress = False
        self.set_readonly(False)

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

    def _make_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        return sep

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Preset", "", self._FILE_FILTER
        )
        if path:
            self.import_requested.emit(path)

    def _on_export_clicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Preset", "", self._FILE_FILTER
        )
        if path:
            if not path.endswith(".rp360p"):
                path += ".rp360p"
            self.export_requested.emit(path)


# ---------------------------------------------------------------- PresetListPanel

class PresetListPanel(QFrame):
    """Left panel: tabbed user / factory preset browser."""

    preset_selected = Signal(int, str)   # index 0-based, bank ("user"/"factory")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self.setFixedWidth(200)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._tabs = QTabWidget()
        self._user_list = QListWidget()
        self._factory_list = QListWidget()
        self._tabs.addTab(self._user_list, "User")
        self._tabs.addTab(self._factory_list, "Factory")
        lay.addWidget(self._tabs)

        self._user_list.itemClicked.connect(self._on_item_clicked)
        self._factory_list.itemClicked.connect(self._on_item_clicked)

    def _on_item_clicked(self, item: QListWidgetItem):
        index = item.data(Qt.UserRole)
        bank = "user" if self._tabs.currentIndex() == 0 else "factory"
        self.preset_selected.emit(index, bank)

    def set_user_presets(self, names: list):
        self._user_list.clear()
        for i, name in enumerate(names):
            label = f"{i + 1}: {name}" if name else f"{i + 1}: (empty)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            self._user_list.addItem(item)

    def set_factory_presets(self, names: list):
        self._factory_list.clear()
        for i, name in enumerate(names):
            label = f"{i + 1}: {name}" if name else f"{i + 1}: (empty)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, i)
            self._factory_list.addItem(item)

    def select_preset(self, bank: str, index: int):
        """Highlight the given preset without triggering a load."""
        tab_idx = 0 if bank == "user" else 1
        lst = self._user_list if bank == "user" else self._factory_list
        other = self._factory_list if bank == "user" else self._user_list
        self._tabs.setCurrentIndex(tab_idx)
        other.blockSignals(True)
        other.setCurrentItem(None)
        other.blockSignals(False)
        lst.blockSignals(True)
        for i in range(lst.count()):
            item = lst.item(i)
            if item.data(Qt.UserRole) == index:
                lst.setCurrentItem(item)
                lst.scrollToItem(item)
                break
        lst.blockSignals(False)

    def clear(self):
        self._user_list.clear()
        self._factory_list.clear()


# ---------------------------------------------------------------- SystemBar

class SystemBar(QFrame):
    """Horizontal bar exposing the seven writable device system parameters."""

    param_changed     = Signal(str, int)   # param_name, raw_value
    import_requested  = Signal()
    export_requested  = Signal()
    backup_requested  = Signal()
    restore_requested = Signal()

    _COMBOS = [
        ("FSWMODE",    "Footswitch:",     ["Preset", "Stomp", "Bank"]),
        ("EXTFSWMODE", "Control In:",     ["FS3X", "Looper"]),
        ("LOOPERPOS",  "Phrase Sampler:", ["Sound Check", "Looper"]),
        ("STEREO",     "Output:",         ["Mono", "Stereo"]),
        ("OUTPUTSW",   "Output To:",      ["Amp", "Mixer"]),
    ]
    # (name, label, lo_display, hi_display, suffix, raw_offset)
    # raw = display + raw_offset
    _SPINS = [
        ("USB REC",  "USB Record Level:", -12, 24, " dB", 12),
        ("USB PBKQ", "USB Play Mix:",       0, 100, "",    0),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFrameShadow(QFrame.Plain)
        self._suppress = False
        self._combos: dict[str, QComboBox] = {}
        self._spins: dict[str, QSpinBox] = {}
        self._spin_offsets: dict[str, int] = {}

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(6)

        for name, label, options in self._COMBOS:
            lay.addWidget(QLabel(label))
            cb = QComboBox()
            for opt in options:
                cb.addItem(opt)
            cb.currentIndexChanged.connect(
                lambda idx, n=name: self._on_changed(n, idx)
            )
            lay.addWidget(cb)
            self._combos[name] = cb

        sep = QFrame()
        sep.setFrameShape(QFrame.VLine)
        sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep)

        for name, label, lo, hi, suffix, offset in self._SPINS:
            lay.addWidget(QLabel(label))
            spin = QSpinBox()
            spin.setRange(lo, hi)
            if suffix:
                spin.setSuffix(suffix)
            spin.setFixedWidth(68)
            spin.valueChanged.connect(
                lambda v, n=name, o=offset: self._on_changed(n, v + o)
            )
            lay.addWidget(spin)
            self._spins[name] = spin
            self._spin_offsets[name] = offset

        lay.addStretch()

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep2)

        btn_import = QPushButton("Import")
        btn_import.setFixedWidth(64)
        btn_import.clicked.connect(self.import_requested)
        lay.addWidget(btn_import)

        btn_export = QPushButton("Export")
        btn_export.setFixedWidth(64)
        btn_export.clicked.connect(self.export_requested)
        lay.addWidget(btn_export)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.VLine)
        sep3.setFrameShadow(QFrame.Sunken)
        lay.addWidget(sep3)

        btn_backup = QPushButton("Backup")
        btn_backup.setFixedWidth(64)
        btn_backup.clicked.connect(self.backup_requested)
        lay.addWidget(btn_backup)

        btn_restore = QPushButton("Restore")
        btn_restore.setFixedWidth(64)
        btn_restore.clicked.connect(self.restore_requested)
        lay.addWidget(btn_restore)

    def get_params(self) -> dict:
        """Return current displayed values as {name: raw_value}."""
        result = {}
        for name, cb in self._combos.items():
            result[name] = cb.currentIndex()
        for name, spin in self._spins.items():
            result[name] = spin.value() + self._spin_offsets.get(name, 0)
        return result

    def _on_changed(self, name: str, raw: int):
        if not self._suppress:
            self.param_changed.emit(name, raw)

    def update_params(self, params: dict):
        self._suppress = True
        for name, cb in self._combos.items():
            if name in params:
                v = int(params[name])
                if 0 <= v < cb.count():
                    cb.setCurrentIndex(v)
        for name, spin in self._spins.items():
            if name in params:
                offset = self._spin_offsets.get(name, 0)
                spin.setValue(int(params[name]) - offset)
        self._suppress = False

    def update_param(self, name: str, raw: int):
        self._suppress = True
        if name in self._combos:
            cb = self._combos[name]
            if 0 <= raw < cb.count():
                cb.setCurrentIndex(raw)
        elif name in self._spins:
            offset = self._spin_offsets.get(name, 0)
            self._spins[name].setValue(raw - offset)
        self._suppress = False

# ---------------------------------------------------------------- SlotCard

class SlotCard(QFrame):
    """Compact slot card in the chain view."""

    selected_changed = Signal(int, bool)   # slot_idx, selected
    enable_toggled   = Signal(int, bool)   # slot_idx, enabled
    delete_requested = Signal(int)         # slot_idx

    _STYLE_NORMAL   = ("SlotCard { border: 1px solid #888; border-radius: 4px; }")
    _STYLE_SELECTED = ("SlotCard { border: 1px solid #4a9eff; border-radius: 4px; }")
    _STYLE_DISABLED = ("SlotCard { border: 1px solid #555; border-radius: 4px;"
                       " color: #888; }")

    def __init__(self, slot_idx: int, slot: FxSlot, parent=None):
        super().__init__(parent)
        self._idx = slot_idx
        self._selected = False
        self._param_labels: dict[str, QLabel] = {}
        self._drag_start_pos = None

        self.setFixedWidth(152)
        self.setFixedHeight(SLOT_CARD_H)
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)

        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self._cb_container = QWidget()
        self._cb_container.setFixedWidth(20)
        cb_inner = QHBoxLayout(self._cb_container)
        cb_inner.setContentsMargins(0, 0, 0, 0)
        self._enable_cb = QCheckBox()
        self._enable_cb.toggled.connect(
            lambda v: self.enable_toggled.emit(self._idx, v)
        )
        cb_inner.addWidget(self._enable_cb)
        hdr.addWidget(self._cb_container)
        num_lbl = QLabel(f"<small>Slot {slot_idx}</small>")
        num_lbl.setStyleSheet("color: #aaa;")
        hdr.addWidget(num_lbl)
        hdr.addStretch()
        self._delete_btn = QPushButton("×")
        self._delete_btn.setFixedSize(14, 14)
        self._delete_btn.setStyleSheet(
            "QPushButton { color: #777; border: none; padding: 0; font-size: 9pt; }"
            "QPushButton:hover { color: #f55; }"
        )
        self._delete_btn.clicked.connect(lambda: self.delete_requested.emit(self._idx))
        hdr.addWidget(self._delete_btn)
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

        has_enable = slot.category != "vol"
        self._enable_cb.setVisible(has_enable)
        self._enable_cb.blockSignals(True)
        self._enable_cb.setChecked(bool(slot.enable))
        self._enable_cb.blockSignals(False)

        for param, value in slot.params.items():
            lbl = QLabel(f"<small>{param}: <b>{value}</b></small>")
            self._params_layout.addWidget(lbl)
            self._param_labels[param] = lbl

    def set_deletable(self, deletable: bool):
        self._delete_btn.setVisible(deletable)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        new_sel = not self._selected
        self.set_selected(new_sel)
        self.selected_changed.emit(self._idx, new_sel)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton) or self._drag_start_pos is None:
            return
        if (event.pos() - self._drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setText(str(self._idx))
        drag.setMimeData(mime)
        drag.setPixmap(self.grab())
        drag.setHotSpot(event.pos())
        drag.exec(Qt.MoveAction)


# ---------------------------------------------------------------- AddCard

class AddCard(QWidget):
    """Placeholder card at the end of the chain — click to add a new slot."""

    add_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(152)
        self.setFixedHeight(SLOT_CARD_H)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Add a new effect slot")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        lbl = QLabel("+")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("font-size: 28pt; color: #555;")
        lay.addWidget(lbl)

        self.setStyleSheet(
            "AddCard { border: 1px dashed #555; border-radius: 4px; }"
            "AddCard:hover { border-color: #888; }"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.add_clicked.emit()
        super().mousePressEvent(event)


# ---------------------------------------------------------------- DetailPanel

class DetailPanel(QFrame):
    """Param sliders for the selected slot — hidden when nothing is selected."""

    param_changed         = Signal(int, str, int)   # slot_idx, param, value
    model_changed         = Signal(int, str)         # slot_idx, model_id (same category)
    slot_add_requested    = Signal(int, str)         # free_idx, address
    slot_replace_requested = Signal(int, str)        # slot_idx, address (cross-category)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(DETAIL_H)
        self._slot_idx = -1
        self._new_slot_idx = -1       # >= 0 when in "add new slot" mode
        self._original_category: str = ""
        self._suppress = False
        self._param_rows: dict[str, ParamRow] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(4)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(8)
        self._slot_lbl = QLabel()
        self._slot_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        hdr_row.addWidget(self._slot_lbl)

        self._category_combo = QComboBox()
        self._category_combo.setFixedWidth(110)
        self._category_combo.setVisible(False)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        hdr_row.addWidget(self._category_combo)

        self._model_combo = QComboBox()
        self._model_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_combo.setVisible(False)
        self._model_combo.currentIndexChanged.connect(self._on_model_changed)
        hdr_row.addWidget(self._model_combo)
        outer.addLayout(hdr_row)

        self._placeholder = QLabel("← Sélectionnez un slot pour éditer ses paramètres")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; font-style: italic;")
        outer.addWidget(self._placeholder, 1)

        self._params_widget = QWidget()
        self._params_layout = QVBoxLayout(self._params_widget)
        self._params_layout.setContentsMargins(0, 0, 0, 0)
        self._params_layout.setSpacing(2)
        self._params_layout.setAlignment(Qt.AlignTop)
        self._params_widget.setVisible(False)
        outer.addWidget(self._params_widget)
        outer.addStretch(1)

    @property
    def current_slot(self) -> int:
        return self._slot_idx

    # ---------------------------------------------------------- private helpers

    def _on_category_changed(self, _):
        if self._suppress:
            return
        cat = self._category_combo.currentData()
        if cat:
            # Always show placeholder: user is actively choosing a new category/model
            self._rebuild_model_combo(cat, use_placeholder=True)

    def _rebuild_model_combo(self, cat: str, use_placeholder: bool = False):
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        if use_placeholder:
            self._model_combo.addItem("— select a model —", None)
        for e in _db.by_category(cat) or []:
            self._model_combo.addItem(e["displayName"], e["address"])
        self._model_combo.blockSignals(False)

    def _on_model_changed(self, _):
        if self._suppress:
            return
        address = self._model_combo.currentData()
        if address is None:
            return
        if self._new_slot_idx >= 0:
            idx = self._new_slot_idx
            self._new_slot_idx = -1   # prevent double-emit
            self.slot_add_requested.emit(idx, address)
        elif self._slot_idx >= 0:
            new_cat = self._category_combo.currentData()
            self._params_widget.setEnabled(False)
            if new_cat and new_cat != self._original_category:
                # Cross-category: device requires delete → add → reorder
                self.slot_replace_requested.emit(self._slot_idx, address)
            else:
                model_id = _db.model_id(address)
                if model_id:
                    self.model_changed.emit(self._slot_idx, model_id)

    # ---------------------------------------------------------- public API

    def show_slot(self, slot_idx: int, slot: FxSlot, preset: "Preset | None" = None):
        self._suppress = True
        self._new_slot_idx = -1
        self._slot_idx = slot_idx

        while self._params_layout.count():
            w = self._params_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._param_rows.clear()

        category, display = _effect_info(slot)
        self._original_category = category
        self._slot_lbl.setText(f"Slot {slot_idx}")

        # Categories used by OTHER slots (not this one), using DB-format category strings
        used_cats: set[str] = set()
        if preset:
            for s_idx, s in preset.slots.items():
                if s_idx != slot_idx:
                    cat, _ = _effect_info(s)
                    if cat:
                        used_cats.add(cat)

        all_cats = sorted({e["category"] for e in _db._effects if e.get("category")})
        # Free categories = not used by any other slot (current category is always included)
        free_cats = [c for c in all_cats if c not in used_cats and c != category]

        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        # Current category first, then other available ones
        if category:
            self._category_combo.addItem(category, category)
        for cat in free_cats:
            self._category_combo.addItem(cat, cat)
        self._category_combo.setCurrentIndex(0)
        self._category_combo.blockSignals(False)
        # Enable only when there are other categories to choose from
        self._category_combo.setEnabled(bool(free_cats))

        # Model combo: all effects in current category, pre-select current model
        address = slot.model.split(".")[-1] if "." in slot.model else slot.model
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        effects = _db.by_category(category) if category else []
        if effects:
            for e in effects:
                self._model_combo.addItem(e["displayName"], e["address"])
            idx = self._model_combo.findData(address)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        else:
            self._model_combo.addItem(display, address)
        self._model_combo.blockSignals(False)

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

        self._category_combo.setVisible(True)
        self._model_combo.setVisible(True)
        self._placeholder.setVisible(False)
        self._params_widget.setEnabled(True)
        self._params_widget.setVisible(True)
        self._suppress = False

    def show_new_slot(self, free_idx: int, preset: "Preset | None" = None):
        """Switch to 'add new slot' mode — user picks category then model."""
        self._suppress = True
        self._new_slot_idx = free_idx
        self._slot_idx = -1

        while self._params_layout.count():
            w = self._params_layout.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._param_rows.clear()

        self._slot_lbl.setText(f"New slot {free_idx}")

        # Collect categories already used (DB-format strings)
        used_cats: set[str] = set()
        if preset:
            for s in preset.slots.values():
                cat, _ = _effect_info(s)
                if cat:
                    used_cats.add(cat)

        all_cats = sorted({e["category"] for e in _db._effects if e.get("category")})
        free_cats = [c for c in all_cats if c not in used_cats]

        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        for cat in free_cats:
            self._category_combo.addItem(cat, cat)
        self._category_combo.blockSignals(False)
        self._category_combo.setEnabled(True)

        first_cat = self._category_combo.currentData()
        self._rebuild_model_combo(first_cat or "", use_placeholder=True)

        self._category_combo.setVisible(True)
        self._model_combo.setVisible(True)
        self._params_widget.setVisible(False)
        self._placeholder.setVisible(False)
        self._suppress = False

    def hide_slot(self):
        self._slot_idx = -1
        self._new_slot_idx = -1
        self._params_widget.setVisible(False)
        self._placeholder.setVisible(True)
        self._slot_lbl.setText("")
        self._suppress = True
        self._category_combo.clear()
        self._category_combo.setVisible(False)
        self._model_combo.clear()
        self._model_combo.setVisible(False)
        self._suppress = False

    def update_param(self, param: str, value: int):
        if param in self._param_rows:
            self._param_rows[param].set_value(value)



# ---------------------------------------------------------------- helpers for bottom sections

def _combo_row(label: str, combo: QComboBox, label_width: int = 52) -> QWidget:
    w = QWidget()
    w.setFixedHeight(_ROW_H)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lbl = QLabel(label)
    lbl.setFixedWidth(label_width)
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lay.addWidget(lbl)
    lay.addWidget(combo, 1)
    return w


# ---------------------------------------------------------------- StompsSection

class StompsSection(QFrame):
    """Toggle-control assignments for ctrlA, ctrlB, ctrlC with per-slot enable buttons."""

    assignment_changed = Signal(str, int)    # ctrl_name, slot_idx (-1 = clear)
    enable_toggled     = Signal(int, bool)   # slot_idx, enabled

    _CTRLS = (("A", "ctrlA"), ("B", "ctrlB"), ("C", "ctrlC"))
    _TOGGLE_STYLE = (
        "QPushButton{color:#666;border:1px solid #555;border-radius:3px;background:#333;padding:0}"
        "QPushButton:checked{color:#5f5;border-color:#5a5;background:#253}"
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(BOTTOM_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title = QLabel("Stomps")
        title.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title.setFixedHeight(_LABEL_H)
        lay.addWidget(title)

        self._combos: dict[str, QComboBox] = {}
        self._toggles: dict[str, QPushButton] = {}

        for letter, ctrl_name in self._CTRLS:
            row = QWidget()
            row.setFixedHeight(_ROW_H)
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)

            lbl = QLabel(f"{letter}:")
            lbl.setFixedWidth(18)
            rl.addWidget(lbl)

            cb = QComboBox()
            cb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            cb.currentIndexChanged.connect(
                lambda _, c=cb, cn=ctrl_name: self._on_combo_change(c, cn)
            )
            rl.addWidget(cb, 1)

            toggle = QPushButton("●")
            toggle.setCheckable(True)
            toggle.setFixedWidth(26)
            toggle.setFixedHeight(22)
            toggle.setEnabled(False)
            toggle.setStyleSheet(self._TOGGLE_STYLE)
            toggle.toggled.connect(
                lambda checked, cn=ctrl_name: self._on_toggle(cn, checked)
            )
            rl.addWidget(toggle)

            lay.addWidget(row)
            self._combos[ctrl_name] = cb
            self._toggles[ctrl_name] = toggle

        lay.addStretch()

    def clear(self):
        for cb in self._combos.values():
            cb.blockSignals(True)
            cb.clear()
            cb.blockSignals(False)
        for toggle in self._toggles.values():
            toggle.blockSignals(True)
            toggle.setChecked(False)
            toggle.setEnabled(False)
            toggle.blockSignals(False)

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
            self._refresh_toggle(ctrl_name, assigned, preset)

    def update_enable(self, slot_idx: int, enabled: bool):
        for ctrl_name, cb in self._combos.items():
            if cb.currentData() == slot_idx:
                t = self._toggles[ctrl_name]
                t.blockSignals(True)
                t.setChecked(enabled)
                t.blockSignals(False)

    def _refresh_toggle(self, ctrl_name: str, assigned: int | None, preset: Preset):
        toggle = self._toggles[ctrl_name]
        toggle.blockSignals(True)
        if assigned is not None and assigned >= 0 and assigned in preset.slots:
            toggle.setEnabled(True)
            toggle.setChecked(bool(preset.slots[assigned].enable))
        else:
            toggle.setEnabled(False)
            toggle.setChecked(False)
        toggle.blockSignals(False)

    def _on_combo_change(self, cb: QComboBox, ctrl_name: str):
        slot_idx = int(cb.currentData()) if cb.currentData() is not None else -1
        self.assignment_changed.emit(ctrl_name, slot_idx)
        toggle = self._toggles[ctrl_name]
        toggle.blockSignals(True)
        toggle.setEnabled(slot_idx >= 0)
        toggle.blockSignals(False)

    def _on_toggle(self, ctrl_name: str, checked: bool):
        slot_idx = self._combos[ctrl_name].currentData()
        if slot_idx is not None and slot_idx >= 0:
            self.enable_toggled.emit(slot_idx, checked)


# ---------------------------------------------------------------- ExprCtrlSection

class ExprCtrlSection(QFrame):
    """Expression pedal section: slot + param selection, MIN and MAX range."""

    assign_requested = Signal(str, int, str, int, int, bool)  # ctrl, slot, param, min, max, flat

    def __init__(self, ctrl_name: str, title: str, parent=None):
        super().__init__(parent)
        self._ctrl_name = ctrl_name
        self._preset: Preset | None = None
        self._suppress = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(BOTTOM_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title_lbl.setFixedHeight(_LABEL_H)
        lay.addWidget(title_lbl)

        self._slot_combo = QComboBox()
        self._slot_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._slot_combo.currentIndexChanged.connect(self._on_slot_changed)
        lay.addWidget(_combo_row("Slot:", self._slot_combo))

        self._param_combo = QComboBox()
        self._param_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._param_combo.currentIndexChanged.connect(self._on_any_change)
        self._param_row = _combo_row("Param:", self._param_combo)
        lay.addWidget(self._param_row)

        self._row_min = ParamRow("MIN", 0, 0, 99)
        self._row_min.value_changed.connect(self._on_any_change)
        lay.addWidget(self._row_min)

        self._row_max = ParamRow("MAX", 99, 0, 99)
        self._row_max.value_changed.connect(self._on_any_change)
        lay.addWidget(self._row_max)

        lay.addStretch()
        self._update_controls_enabled(False)

    def _update_controls_enabled(self, assigned: bool):
        self._param_row.setEnabled(assigned)
        self._row_min.setEnabled(assigned)
        self._row_max.setEnabled(assigned)

    def update_ctrl(self, ctrl: Ctrl | None, preset: Preset | None = None):
        self._preset = preset
        self._suppress = True

        current_slot, current_param = -1, None
        if ctrl and ctrl.lnk:
            m = re.search(r'fxc/(\d+)/(?:fx/)?([^/]+)$', ctrl.lnk)
            if m:
                current_slot, current_param = int(m.group(1)), m.group(2)

        self._slot_combo.blockSignals(True)
        self._slot_combo.clear()
        self._slot_combo.addItem("— not assigned", -1)
        if preset:
            for idx in sorted(preset.slots.keys()):
                _, label = _effect_info(preset.slots[idx])
                self._slot_combo.addItem(f"Slot {idx} · {label}", idx)
        si = self._slot_combo.findData(current_slot)
        self._slot_combo.setCurrentIndex(max(0, si))
        self._slot_combo.blockSignals(False)

        self._rebuild_param_combo(current_slot, current_param)

        if current_slot >= 0 and ctrl:
            if ctrl.min is not None:
                self._row_min.set_value(ctrl.min)
            if ctrl.max is not None:
                self._row_max.set_value(ctrl.max)
        else:
            self._row_min.set_value(0)
            self._row_max.set_value(0)

        self._update_controls_enabled(current_slot >= 0)
        self._suppress = False

    def _on_slot_changed(self):
        slot_idx = self._slot_combo.currentData()
        self._rebuild_param_combo(slot_idx if slot_idx is not None else -1, None)
        self._update_controls_enabled(slot_idx is not None and slot_idx >= 0)
        if not (slot_idx is not None and slot_idx >= 0):
            self._row_min.set_value(0)
            self._row_max.set_value(0)
        self._on_any_change()

    def _rebuild_param_combo(self, slot_idx: int, current_param: str | None):
        self._param_combo.blockSignals(True)
        self._param_combo.clear()
        if slot_idx >= 0 and self._preset and slot_idx in self._preset.slots:
            for p in self._preset.slots[slot_idx].params:
                self._param_combo.addItem(p, p)
            if current_param:
                pi = self._param_combo.findData(current_param)
                self._param_combo.setCurrentIndex(max(0, pi))
        self._param_combo.blockSignals(False)

    def _on_any_change(self, *_):
        if self._suppress:
            return
        slot_idx = self._slot_combo.currentData()
        if slot_idx is None:
            return
        if slot_idx < 0:
            self.assign_requested.emit(self._ctrl_name, -1, "", 0, 99, False)
            return
        param = self._param_combo.currentData()
        if not param or self._preset is None:
            return
        slot = self._preset.slots.get(slot_idx)
        if slot is None:
            return
        self.assign_requested.emit(
            self._ctrl_name, slot_idx, param,
            self._row_min.value, self._row_max.value,
            not slot._use_fx_subdict,
        )


# ---------------------------------------------------------------- LfoSection

class LfoSection(QFrame):
    """LFO section: slot + param selection, MIN, MAX, SPEED, WAVEFORM."""

    assign_requested = Signal(int, str, int, int, int, int, bool)  # slot, param, min, max, speed, wf, flat

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preset: Preset | None = None
        self._suppress = False
        self._wave_val = 0

        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(BOTTOM_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title_lbl = QLabel("LFO")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title_lbl.setFixedHeight(_LABEL_H)
        lay.addWidget(title_lbl)

        self._slot_combo = QComboBox()
        self._slot_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._slot_combo.currentIndexChanged.connect(self._on_slot_changed)
        lay.addWidget(_combo_row("Slot:", self._slot_combo))

        self._param_combo = QComboBox()
        self._param_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._param_combo.currentIndexChanged.connect(self._on_any_change)
        self._param_row = _combo_row("Param:", self._param_combo)
        lay.addWidget(self._param_row)

        self._row_min = ParamRow("MIN", 0, 0, 99)
        self._row_min.value_changed.connect(self._on_any_change)
        lay.addWidget(self._row_min)

        self._row_max = ParamRow("MAX", 99, 0, 99)
        self._row_max.value_changed.connect(self._on_any_change)
        lay.addWidget(self._row_max)

        self._row_speed = ParamRow("SPEED", 74, 0, 185)
        self._row_speed.value_changed.connect(self._on_any_change)
        lay.addWidget(self._row_speed)

        self._wave_combo = QComboBox()
        self._wave_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for k in sorted(LFO_WAVEFORMS):
            self._wave_combo.addItem(LFO_WAVEFORMS[k], k)
        self._wave_combo.currentIndexChanged.connect(self._on_wave_changed)
        self._wave_row = _combo_row("WAVEFORM", self._wave_combo, label_width=72)
        lay.addWidget(self._wave_row)

        lay.addStretch()
        self._update_controls_enabled(False)

    def _update_controls_enabled(self, assigned: bool):
        self._param_row.setEnabled(assigned)
        self._row_min.setEnabled(assigned)
        self._row_max.setEnabled(assigned)
        self._row_speed.setEnabled(assigned)
        self._wave_row.setEnabled(assigned)

    def update_ctrl(self, ctrl: Ctrl | None, preset: Preset | None = None):
        self._preset = preset
        self._suppress = True

        current_slot, current_param = -1, None
        if ctrl and ctrl.lnk:
            m = re.search(r'fxc/(\d+)/(?:fx/)?([^/]+)$', ctrl.lnk)
            if m:
                current_slot, current_param = int(m.group(1)), m.group(2)

        self._slot_combo.blockSignals(True)
        self._slot_combo.clear()
        self._slot_combo.addItem("— not assigned", -1)
        if preset:
            for idx in sorted(preset.slots.keys()):
                _, label = _effect_info(preset.slots[idx])
                self._slot_combo.addItem(f"Slot {idx} · {label}", idx)
        si = self._slot_combo.findData(current_slot)
        self._slot_combo.setCurrentIndex(max(0, si))
        self._slot_combo.blockSignals(False)

        self._rebuild_param_combo(current_slot, current_param)

        if current_slot >= 0 and ctrl:
            if ctrl.min is not None:
                self._row_min.set_value(ctrl.min)
            if ctrl.max is not None:
                self._row_max.set_value(ctrl.max)
            if ctrl.speed is not None:
                self._row_speed.set_value(ctrl.speed)
            if ctrl.waveform is not None:
                self._wave_val = ctrl.waveform
                self._wave_combo.blockSignals(True)
                wi = self._wave_combo.findData(ctrl.waveform)
                if wi >= 0:
                    self._wave_combo.setCurrentIndex(wi)
                self._wave_combo.blockSignals(False)
        else:
            self._reset_values()

        self._update_controls_enabled(current_slot >= 0)
        self._suppress = False

    def _reset_values(self):
        self._row_min.set_value(0)
        self._row_max.set_value(0)
        self._row_speed.set_value(0)
        self._wave_val = 0
        self._wave_combo.blockSignals(True)
        self._wave_combo.setCurrentIndex(0)
        self._wave_combo.blockSignals(False)

    def _on_slot_changed(self):
        slot_idx = self._slot_combo.currentData()
        self._rebuild_param_combo(slot_idx if slot_idx is not None else -1, None)
        self._update_controls_enabled(slot_idx is not None and slot_idx >= 0)
        if not (slot_idx is not None and slot_idx >= 0):
            self._reset_values()
        self._on_any_change()

    def _rebuild_param_combo(self, slot_idx: int, current_param: str | None):
        self._param_combo.blockSignals(True)
        self._param_combo.clear()
        if slot_idx >= 0 and self._preset and slot_idx in self._preset.slots:
            for p in self._preset.slots[slot_idx].params:
                self._param_combo.addItem(p, p)
            if current_param:
                pi = self._param_combo.findData(current_param)
                self._param_combo.setCurrentIndex(max(0, pi))
        self._param_combo.blockSignals(False)

    def _on_wave_changed(self, _):
        v = self._wave_combo.currentData()
        if v is not None:
            self._wave_val = v
        self._on_any_change()

    def _on_any_change(self, *_):
        if self._suppress:
            return
        slot_idx = self._slot_combo.currentData()
        if slot_idx is None:
            return
        if slot_idx < 0:
            self.assign_requested.emit(-1, "", 0, 99, self._row_speed.value, self._wave_val, False)
            return
        param = self._param_combo.currentData()
        if not param or self._preset is None:
            return
        slot = self._preset.slots.get(slot_idx)
        if slot is None:
            return
        self.assign_requested.emit(
            slot_idx, param,
            self._row_min.value, self._row_max.value,
            self._row_speed.value, self._wave_val,
            not slot._use_fx_subdict,
        )


# ---------------------------------------------------------------- WahSection

class WahSection(QFrame):
    """Wah section: shows altTreadle assignment and MIN/MAX range."""

    ctrl_field_changed = Signal(str, str, int)   # "altTreadle", field, value

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: dict[str, ParamRow] = {}

        self.setFrameShape(QFrame.StyledPanel)
        self.setFixedHeight(BOTTOM_H)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(3)

        title_lbl = QLabel("Wah")
        title_lbl.setStyleSheet("font-weight: bold; font-size: 10pt;")
        title_lbl.setFixedHeight(_LABEL_H)
        lay.addWidget(title_lbl)

        self._lnk_lbl = QLabel("— not assigned")
        self._lnk_lbl.setStyleSheet("color: #888; font-size: 8pt;")
        self._lnk_lbl.setFixedHeight(_TEXT_H)
        lay.addWidget(self._lnk_lbl)

        for field, lo, hi in (("MIN", 0, 99), ("MAX", 0, 99)):
            row = ParamRow(field, 0, lo, hi)
            row.value_changed.connect(
                lambda f, v: self.ctrl_field_changed.emit("altTreadle", f, v)
            )
            lay.addWidget(row)
            self._rows[field] = row

        lay.addStretch()

    def update_ctrl(self, ctrl: Ctrl | None, preset: Preset | None = None):
        assigned = bool(ctrl and ctrl.lnk)
        self._lnk_lbl.setText(_lnk_label(ctrl.lnk if ctrl else "", preset))
        for row in self._rows.values():
            row.setEnabled(assigned)
        if assigned and ctrl:
            if ctrl.min is not None:
                self._rows["MIN"].set_value(ctrl.min)
            if ctrl.max is not None:
                self._rows["MAX"].set_value(ctrl.max)
        else:
            self._rows["MIN"].set_value(0)
            self._rows["MAX"].set_value(0)


# ---------------------------------------------------------------- BottomPanel

class BottomPanel(QWidget):
    """Four-section bottom bar: Stomps | Expression | LFO | Wah."""

    stomp_changed      = Signal(str, int)                         # ctrl_name, slot_idx (-1=clear)
    enable_toggled     = Signal(int, bool)                        # slot_idx, enabled
    expression_assign  = Signal(str, int, str, int, int, bool)   # ctrl, slot, param, min, max, flat
    lfo_assign         = Signal(int, str, int, int, int, int, bool)  # slot, param, min, max, spd, wf, flat
    ctrl_field_changed = Signal(str, str, int)                    # ctrl_name, field, value (wah)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(BOTTOM_H)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(6)

        self._stomps = StompsSection()
        self._stomps.assignment_changed.connect(self.stomp_changed)
        self._stomps.enable_toggled.connect(self.enable_toggled)
        lay.addWidget(self._stomps, 1)

        self._expr = ExprCtrlSection("treadle", "Expression")
        self._expr.assign_requested.connect(self.expression_assign)
        lay.addWidget(self._expr, 1)

        self._lfo = LfoSection()
        self._lfo.assign_requested.connect(self.lfo_assign)
        lay.addWidget(self._lfo, 1)

        self._wah = WahSection()
        self._wah.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._wah, 1)

    def clear(self):
        self._stomps.clear()
        self._expr.update_ctrl(None, None)
        self._lfo.update_ctrl(None, None)
        self._wah.update_ctrl(None, None)

    def update_preset(self, preset: Preset):
        self._stomps.update_preset(preset)
        self._expr.update_ctrl(preset.ctrls.get("treadle"), preset)
        self._lfo.update_ctrl(preset.ctrls.get("lfo1"), preset)
        self._wah.update_ctrl(preset.ctrls.get("altTreadle"), preset)

    def update_enable(self, slot_idx: int, enabled: bool):
        self._stomps.update_enable(slot_idx, enabled)


# ---------------------------------------------------------------- PresetPanel

class PresetPanel(QWidget):
    """Full preset editing panel: header + chain + detail + bottom."""

    enable_toggled     = Signal(int, bool)
    param_changed      = Signal(int, str, int)
    level_changed      = Signal(int)
    model_changed      = Signal(int, str)
    save_clicked       = Signal()
    store_new_clicked  = Signal()
    refresh_clicked    = Signal()
    stomp_changed      = Signal(str, int)
    ctrl_field_changed = Signal(str, str, int)
    expression_assign  = Signal(str, int, str, int, int, bool)
    lfo_assign         = Signal(int, str, int, int, int, int, bool)
    delete_requested       = Signal(int)           # slot_idx
    slot_add_requested     = Signal(int, str)      # slot_idx, address
    slot_replace_requested = Signal(int, str, list)# slot_idx, address, restore_order
    reorder_requested      = Signal(list)           # new chain order
    import_requested       = Signal(str)            # file path
    export_requested       = Signal(str)            # file path

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selected_idx = -1
        self._slot_cards: dict[int, SlotCard] = {}
        self._current_preset: Preset | None = None
        self._chain_order: list[int] = []
        self._add_card: AddCard | None = None
        self._pending_add_idx = -1
        self._insertion_target = -1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._header = PresetHeader()
        self._header.save_clicked.connect(self.save_clicked)
        self._header.store_new_clicked.connect(self.store_new_clicked)
        self._header.refresh_clicked.connect(self.refresh_clicked)
        self._header.level_changed.connect(self.level_changed)
        self._header.import_requested.connect(self.import_requested)
        self._header.export_requested.connect(self.export_requested)
        lay.addWidget(self._header)

        self._chain_scroll = QScrollArea()
        self._chain_scroll.setWidgetResizable(True)
        self._chain_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chain_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._chain_scroll.setFrameShape(QFrame.NoFrame)

        self._chain_widget = QWidget()
        self._chain_widget.setAcceptDrops(True)
        self._chain_widget.installEventFilter(self)
        self._chain_layout = QHBoxLayout(self._chain_widget)
        self._chain_layout.setContentsMargins(6, 6, 6, 6)
        self._chain_layout.setSpacing(6)
        self._chain_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._chain_scroll.setWidget(self._chain_widget)
        self._chain_scroll.setFixedHeight(SLOT_CARD_H + 16)
        lay.addWidget(self._chain_scroll)

        # Drag insertion marker — absolute child of chain_widget
        self._insertion_marker = QFrame(self._chain_widget)
        self._insertion_marker.setStyleSheet("QFrame { background: #4a9eff; }")
        self._insertion_marker.resize(3, SLOT_CARD_H)
        self._insertion_marker.setVisible(False)
        self._insertion_marker.setAttribute(Qt.WA_TransparentForMouseEvents)

        self._detail = DetailPanel()
        self._detail.param_changed.connect(self.param_changed)
        self._detail.model_changed.connect(self.model_changed)
        self._detail.slot_add_requested.connect(self._on_detail_slot_add_requested)
        self._detail.slot_replace_requested.connect(self._on_detail_slot_replace_requested)
        lay.addWidget(self._detail)

        self._bottom = BottomPanel()
        self._bottom.stomp_changed.connect(self.stomp_changed)
        self._bottom.enable_toggled.connect(self.enable_toggled)
        self._bottom.expression_assign.connect(self.expression_assign)
        self._bottom.lfo_assign.connect(self.lfo_assign)
        self._bottom.ctrl_field_changed.connect(self.ctrl_field_changed)
        lay.addWidget(self._bottom)

    # ---------------------------------------------------------- public API

    def clear(self):
        self._current_preset = None
        self._selected_idx = -1
        self._chain_order = []
        self._pending_add_idx = -1
        while self._chain_layout.count():
            item = self._chain_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._slot_cards.clear()
        self._add_card = None
        self._header.clear()
        self._detail.hide_slot()
        self._bottom.clear()

    def set_readonly(self, readonly: bool):
        self._header.set_readonly(readonly)

    def mark_dirty(self):
        self._header.mark_dirty()

    def update_preset(self, preset: Preset, dirty: bool, bank: str, slot_1: int):
        self._current_preset = preset
        self._header.update(preset, dirty, bank, slot_1)
        self._rebuild_chain(preset)
        self._bottom.update_preset(preset)

        # Auto-select a pending newly-added slot
        if self._pending_add_idx >= 0:
            idx = self._pending_add_idx
            self._pending_add_idx = -1
            if idx in preset.slots:
                self._selected_idx = idx
                if idx in self._slot_cards:
                    self._slot_cards[idx].set_selected(True)
                self._detail.show_slot(idx, preset.slots[idx], preset)
                return

        if self._selected_idx >= 0:
            if self._selected_idx in preset.slots:
                self._detail.show_slot(self._selected_idx, preset.slots[self._selected_idx], preset)
            else:
                self._selected_idx = -1
                self._detail.hide_slot()

    def update_param(self, slot: int, param: str, value: int):
        if slot in self._slot_cards:
            self._slot_cards[slot].update_param(param, value)
        if slot == self._selected_idx:
            self._detail.update_param(param, value)

    def update_level(self, value: int):
        self._header.update_level(value)

    def update_enable(self, slot: int, enabled: bool):
        if slot in self._slot_cards:
            self._slot_cards[slot].update_enable(enabled)
        self._bottom.update_enable(slot, enabled)

    def unlock_chain(self):
        """Re-enable all cards after a reorder has been acknowledged."""
        for card in self._slot_cards.values():
            card.setEnabled(True)
        if self._add_card:
            self._add_card.setEnabled(True)

    # ---------------------------------------------------------- drag & drop event filter

    def eventFilter(self, obj, event):
        if obj is self._chain_widget:
            t = event.type()
            if t == QEvent.DragEnter:
                if event.mimeData().hasText():
                    event.acceptProposedAction()
                    return True
            elif t == QEvent.DragMove:
                self._update_insertion_marker(event.pos())
                event.acceptProposedAction()
                return True
            elif t == QEvent.Drop:
                self._insertion_marker.setVisible(False)
                try:
                    src = int(event.mimeData().text())
                    self._finish_drop(src, self._insertion_target)
                except (ValueError, TypeError):
                    pass
                event.acceptProposedAction()
                return True
            elif t == QEvent.DragLeave:
                self._insertion_marker.setVisible(False)
                return True
        return super().eventFilter(obj, event)

    # ---------------------------------------------------------- private

    def _rebuild_chain(self, preset: Preset):
        while self._chain_layout.count():
            item = self._chain_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._slot_cards.clear()
        self._add_card = None

        order = preset.chain_order if preset.chain_order else sorted(preset.slots.keys())
        self._chain_order = list(order)

        for idx in order:
            slot = preset.slots.get(idx)
            if slot is None:
                continue
            card = SlotCard(idx, slot)
            card.enable_toggled.connect(self.enable_toggled)
            card.selected_changed.connect(self._on_card_selected)
            card.delete_requested.connect(self._on_card_delete)
            self._chain_layout.addWidget(card)
            self._slot_cards[idx] = card

        # × button only visible when there is more than one slot
        deletable = len(self._slot_cards) > 1
        for card in self._slot_cards.values():
            card.set_deletable(deletable)

        # Restore selection highlight
        if self._selected_idx in self._slot_cards:
            self._slot_cards[self._selected_idx].set_selected(True)

        # AddCard if fewer than 10 slots
        if len(self._slot_cards) < 10:
            self._add_card = AddCard()
            self._add_card.add_clicked.connect(self._on_add_card_clicked)
            self._chain_layout.addWidget(self._add_card)

    def _rebuild_chain_from_order(self, order: list[int], disable: bool = False):
        """Rebuild layout in-place without deleting cards — used after drag & drop."""
        while self._chain_layout.count():
            item = self._chain_layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)
        for idx in order:
            card = self._slot_cards.get(idx)
            if card:
                if disable:
                    card.setEnabled(False)
                self._chain_layout.addWidget(card)
        if self._add_card:
            if disable:
                self._add_card.setEnabled(False)
            self._chain_layout.addWidget(self._add_card)

    def _on_card_selected(self, slot_idx: int, selected: bool):
        prev = self._selected_idx
        if not selected:
            self._selected_idx = -1
            self._detail.hide_slot()
            return
        if prev >= 0 and prev != slot_idx and prev in self._slot_cards:
            self._slot_cards[prev].set_selected(False)
        self._selected_idx = slot_idx
        if self._current_preset and slot_idx in self._current_preset.slots:
            self._detail.show_slot(slot_idx, self._current_preset.slots[slot_idx], self._current_preset)

    def _on_card_delete(self, slot_idx: int):
        if self._selected_idx == slot_idx:
            self._selected_idx = -1
            self._detail.hide_slot()
        self.delete_requested.emit(slot_idx)

    def _on_add_card_clicked(self):
        used = set(self._slot_cards.keys())
        free = next((i for i in range(10) if i not in used), None)
        if free is None:
            return
        self._detail.show_new_slot(free, self._current_preset)

    def _on_detail_slot_add_requested(self, slot_idx: int, address: str):
        self._pending_add_idx = slot_idx
        self.slot_add_requested.emit(slot_idx, address)

    def _on_detail_slot_replace_requested(self, slot_idx: int, address: str):
        # Pass current chain order so the worker can restore position after delete+add
        self._pending_add_idx = slot_idx
        self.slot_replace_requested.emit(slot_idx, address, list(self._chain_order))

    def _drop_target_pos(self, pos) -> int:
        for i, idx in enumerate(self._chain_order):
            card = self._slot_cards.get(idx)
            if card and pos.x() < card.x() + card.width() // 2:
                return i
        return len(self._chain_order)

    def _update_insertion_marker(self, pos):
        target = self._drop_target_pos(pos)
        self._insertion_target = target
        order = self._chain_order
        if target < len(order):
            card = self._slot_cards.get(order[target])
            x = (card.x() - 4) if card else 0
        elif order:
            card = self._slot_cards.get(order[-1])
            x = (card.x() + card.width() + 1) if card else 0
        else:
            x = 6
        self._insertion_marker.move(x, 6)
        self._insertion_marker.resize(3, SLOT_CARD_H)
        self._insertion_marker.setVisible(True)
        self._insertion_marker.raise_()

    def _finish_drop(self, src_idx: int, target_pos: int):
        order = list(self._chain_order)
        if src_idx not in order:
            return
        src_pos = order.index(src_idx)
        order.pop(src_pos)
        if src_pos < target_pos:
            target_pos -= 1
        target_pos = max(0, min(target_pos, len(order)))
        order.insert(target_pos, src_idx)
        if order == list(self._chain_order):
            return
        self._chain_order = order
        self._rebuild_chain_from_order(order, disable=True)
        self.reorder_requested.emit(order)
