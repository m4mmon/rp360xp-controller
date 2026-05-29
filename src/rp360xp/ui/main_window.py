"""Main application window."""

from __future__ import annotations

import json
import logging
import re
import sys

log = logging.getLogger(__name__)

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressDialog,
    QPushButton, QStatusBar, QVBoxLayout, QWidget,
)

_RP360XP_VID = 0x1210
_RP360XP_PID = 0x0032

try:
    import serial.tools.list_ports as _list_ports

    def _available_ports() -> list[str]:
        return [p.device for p in _list_ports.comports()]

    def _find_rp360xp() -> str | None:
        for p in _list_ports.comports():
            if p.vid == _RP360XP_VID and p.pid == _RP360XP_PID:
                return p.device
        return None

except ImportError:
    def _available_ports() -> list[str]:
        return []

    def _find_rp360xp() -> str | None:
        return None

from .worker import DeviceWorker
from .widgets import (
    PresetPanel, PresetListPanel, SystemBar,
    PRESET_HDR_H, SLOT_CARD_H, DETAIL_H, BOTTOM_H,
)

# Default window dimensions computed from chain and panel constants.
# Width: enough to show all 10 possible slots (0-9) without horizontal scrolling.
_MAX_CHAIN_SLOTS = 10   # RP360XP slot indices 0-9
_SLOT_CARD_W     = 152  # SlotCard.setFixedWidth
_CHAIN_SPACING   = 6
_CHAIN_MARGINS   = 12   # 6 + 6 in chain QHBoxLayout

_DEFAULT_W = (
    _MAX_CHAIN_SLOTS * _SLOT_CARD_W
    + (_MAX_CHAIN_SLOTS - 1) * _CHAIN_SPACING
    + _CHAIN_MARGINS
)
_PRESET_LIST_W = 200   # PresetListPanel fixed width
_DEFAULT_H = (
    PRESET_HDR_H
    + (SLOT_CARD_H + 16)   # chain scroll area = card + layout margins
    + DETAIL_H + BOTTOM_H
    + 30                   # status bar + window chrome
)


class MainWindow(QMainWindow):
    # Signals → worker (queued automatically across threads)
    _connect_requested   = Signal(str)
    _disconnect_requested = Signal()
    _refresh_requested   = Signal()
    _enable_changed      = Signal(int, bool)
    _param_changed       = Signal(int, str, int)
    _level_changed       = Signal(int)
    _save_requested      = Signal()
    _stomp_assign        = Signal(str, int)
    _stomp_clear         = Signal(str)
    _ctrl_field_changed  = Signal(str, str, int)
    _expression_assign   = Signal(str, int, str, int, int, bool)
    _lfo_assign          = Signal(int, str, int, int, int, int, bool)
    _model_changed       = Signal(int, str)
    _delete_requested       = Signal(int)
    _slot_add_requested     = Signal(int, str)
    _slot_replace_requested = Signal(int, str, list)
    _reorder_requested      = Signal(list)
    _import_requested       = Signal(str)
    _export_requested       = Signal(str)
    _save_as_requested      = Signal(int, str)   # index 0-based, name
    _system_param_changed   = Signal(str, int)   # param_name, value
    _load_preset_requested  = Signal(int, str)   # index 0-based, bank
    _backup_requested       = Signal(str)        # path
    _restore_requested      = Signal(str)        # path

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RP360XP Controller")
        self.resize(_DEFAULT_W + _PRESET_LIST_W + 4, _DEFAULT_H)
        self._user_preset_names: list = []   # list[str | None], indices 0-98
        self._progress_dlg: QProgressDialog | None = None
        self._op_progress_dlg: QProgressDialog | None = None
        self._current_dirty = False
        self._current_bank = "user"
        self._current_slot_0 = 0
        self._current_preset_name = ""
        self._suppress_next_preset_change = False
        self._user_disconnecting = False
        self._build_ui()
        self._build_worker()

    # ---------------------------------------------------------- UI layout

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)

        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        self._preset_list = PresetListPanel()
        self._preset_list.preset_selected.connect(self._on_list_preset_selected)
        self._preset_list.setEnabled(False)
        outer.addWidget(self._preset_list)

        right = QWidget()
        root = QVBoxLayout(right)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        outer.addWidget(right, 1)

        root.addWidget(self._build_connection_bar())

        self._system_bar = SystemBar()
        self._system_bar.param_changed.connect(self._system_param_changed)
        self._system_bar.import_requested.connect(self._on_system_import)
        self._system_bar.export_requested.connect(self._on_system_export)
        self._system_bar.backup_requested.connect(self._on_backup_clicked)
        self._system_bar.restore_requested.connect(self._on_restore_clicked)
        self._system_bar.setEnabled(False)
        root.addWidget(self._system_bar)

        self._preset_panel = PresetPanel()
        self._preset_panel.enable_toggled.connect(self._enable_changed)
        self._preset_panel.enable_toggled.connect(self._preset_panel.update_enable)
        self._preset_panel.enable_toggled.connect(lambda *_: self._mark_dirty())
        self._preset_panel.param_changed.connect(self._param_changed)
        self._preset_panel.param_changed.connect(lambda *_: self._mark_dirty())
        self._preset_panel.level_changed.connect(self._level_changed)
        self._preset_panel.level_changed.connect(lambda *_: self._mark_dirty())
        self._preset_panel.save_clicked.connect(self._save_requested)
        self._preset_panel.refresh_clicked.connect(self._refresh_requested)
        self._preset_panel.stomp_changed.connect(self._on_stomp_changed)
        self._preset_panel.stomp_changed.connect(lambda *_: self._mark_dirty())
        self._preset_panel.ctrl_field_changed.connect(self._ctrl_field_changed)
        self._preset_panel.ctrl_field_changed.connect(lambda *_: self._mark_dirty())
        self._preset_panel.expression_assign.connect(self._expression_assign)
        self._preset_panel.expression_assign.connect(lambda *_: self._mark_dirty())
        self._preset_panel.lfo_assign.connect(self._lfo_assign)
        self._preset_panel.lfo_assign.connect(lambda *_: self._mark_dirty())
        self._preset_panel.model_changed.connect(self._model_changed)
        self._preset_panel.model_changed.connect(lambda *_: self._mark_dirty())
        self._preset_panel.delete_requested.connect(self._delete_requested)
        self._preset_panel.slot_add_requested.connect(self._slot_add_requested)
        self._preset_panel.slot_replace_requested.connect(self._slot_replace_requested)
        self._preset_panel.reorder_requested.connect(self._reorder_requested)
        self._preset_panel.reorder_requested.connect(lambda *_: self._mark_dirty())
        self._preset_panel.import_requested.connect(self._import_requested)
        self._preset_panel.export_requested.connect(self._export_requested)
        self._preset_panel.store_new_clicked.connect(self._on_store_new_clicked)
        self._preset_panel.setEnabled(False)
        root.addWidget(self._preset_panel, 1)

        self.setStatusBar(QStatusBar())

    def _build_connection_bar(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet("background: #2b2b2b; color: white;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(8)

        lay.addWidget(QLabel("Port:"))

        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.setMinimumWidth(160)
        self._populate_ports()
        lay.addWidget(self._port_combo)

        scan_btn = QPushButton("⟳")
        scan_btn.setFixedWidth(32)
        scan_btn.setToolTip("Scan serial ports")
        scan_btn.clicked.connect(self._scan_ports)
        lay.addWidget(scan_btn)
        self._scan_btn = scan_btn

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedWidth(90)
        self._connect_btn.clicked.connect(self._toggle_connect)
        lay.addWidget(self._connect_btn)

        self._conn_lbl = QLabel("Disconnected")
        self._conn_lbl.setStyleSheet("color: #aaa;")
        lay.addWidget(self._conn_lbl)

        lay.addStretch()
        return bar

    # ---------------------------------------------------------- worker setup

    def _build_worker(self):
        self._worker = DeviceWorker()
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        # UI → worker
        self._connect_requested.connect(self._worker.connect_device)
        self._disconnect_requested.connect(self._worker.disconnect_device)
        self._refresh_requested.connect(self._worker.refresh_preset)
        self._enable_changed.connect(self._worker.set_enable)
        self._param_changed.connect(self._worker.set_param)
        self._level_changed.connect(self._worker.set_preset_level)
        self._save_requested.connect(self._worker.save_preset)
        self._stomp_assign.connect(self._worker.assign_stomp)
        self._stomp_clear.connect(self._worker.clear_stomp)
        self._ctrl_field_changed.connect(self._worker.set_ctrl_field)
        self._expression_assign.connect(self._worker.assign_expression)
        self._lfo_assign.connect(self._worker.assign_lfo)
        self._model_changed.connect(self._worker.set_model)
        self._delete_requested.connect(self._worker.delete_effect)
        self._slot_add_requested.connect(self._worker.add_effect)
        self._slot_replace_requested.connect(self._worker.replace_effect)
        self._reorder_requested.connect(self._worker.reorder_chain)
        self._import_requested.connect(self._worker.import_preset)
        self._export_requested.connect(self._worker.export_preset)
        self._save_as_requested.connect(self._worker.save_preset_as)
        self._load_preset_requested.connect(self._worker.load_preset)
        self._backup_requested.connect(self._worker.backup_device)
        self._restore_requested.connect(self._worker.restore_device)

        self._worker.operation_progress.connect(self._on_operation_progress)
        self._worker.operation_done.connect(
            lambda msg: self.statusBar().showMessage(msg, 5000)
        )
        self._worker.preset_names_changed.connect(self._on_preset_names_changed)
        self._worker.preset_name_progress.connect(self._on_preset_name_progress)
        self._worker.system_params_changed.connect(self._on_system_params_changed)
        self._worker.factory_names_changed.connect(self._on_factory_names_changed)
        self._system_param_changed.connect(self._worker.set_system_param)

        # worker → UI
        self._worker.connection_changed.connect(self._on_connection_changed)
        self._worker.reorder_done.connect(self._preset_panel.unlock_chain)
        self._worker.preset_changed.connect(self._on_preset_changed)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.notification_received.connect(self._on_notification)

    # ---------------------------------------------------------- UI → worker

    @Slot(str, int)
    def _on_stomp_changed(self, ctrl: str, slot: int):
        if slot == -1:
            self._stomp_clear.emit(ctrl)
        else:
            self._stomp_assign.emit(ctrl, slot)

    def _toggle_connect(self):
        if self._connect_btn.text() == "Connect":
            port = self._port_combo.currentText().strip()
            self._connect_requested.emit(port)
        else:
            self._user_disconnecting = True
            self._disconnect_requested.emit()

    def _populate_ports(self):
        self._port_combo.clear()
        self._port_combo.addItem("")
        rp_port = _find_rp360xp()
        for p in _available_ports():
            self._port_combo.addItem(p)
        if rp_port:
            idx = self._port_combo.findText(rp_port)
            if idx >= 0:
                self._port_combo.setCurrentIndex(idx)

    def _scan_ports(self):
        self._populate_ports()

    # ---------------------------------------------------------- worker → UI

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, port: str):
        self._connect_btn.setText("Disconnect" if connected else "Connect")
        self._port_combo.setEnabled(not connected)
        self._scan_btn.setEnabled(not connected)
        self._system_bar.setEnabled(connected)
        self._preset_list.setEnabled(connected)
        self._preset_panel.setEnabled(connected)
        if connected:
            self._conn_lbl.setText(f"Connected · {port}")
            self._conn_lbl.setStyleSheet("color: #7fc97f;")
        else:
            unexpected = not self._user_disconnecting
            self._user_disconnecting = False
            self._preset_panel.clear()
            self._preset_list.clear()
            self._current_dirty = False
            self._current_preset_name = ""
            self._conn_lbl.setText("Disconnected")
            self._conn_lbl.setStyleSheet("color: #aaa;")
            if unexpected:
                QMessageBox.warning(
                    self,
                    "Connection lost",
                    "The connection to the RP360XP was lost.\n"
                    "Check the USB cable and reconnect.",
                )

    @Slot(object, bool, str, int)
    def _on_preset_changed(self, preset, dirty: bool, bank: str, slot_1: int):
        if preset is None:
            return
        self._current_dirty = dirty
        self._current_bank = bank
        self._current_slot_0 = slot_1 - 1
        self._current_preset_name = preset.name
        if self._suppress_next_preset_change:
            self._suppress_next_preset_change = False
            return
        self._preset_panel.setEnabled(True)
        self._preset_panel.set_readonly(bank == "factory")
        self._preset_panel.update_preset(preset, dirty, bank, slot_1)
        marker = "  [unsaved]" if dirty else ""
        self.statusBar().showMessage(f'"{preset.name}"  —  {bank} #{slot_1}{marker}', 5000)
        self._preset_list.select_preset(bank, slot_1 - 1)

    @Slot(list)
    def _on_preset_names_changed(self, names: list):
        self._user_preset_names = names
        self._preset_list.set_user_presets(names)
        if self._current_bank == "user":
            self._preset_list.select_preset("user", self._current_slot_0)

    @Slot(list)
    def _on_factory_names_changed(self, names: list):
        self._preset_list.set_factory_presets(names)
        if self._current_bank == "factory":
            self._preset_list.select_preset("factory", self._current_slot_0)

    @Slot(dict)
    def _on_system_params_changed(self, params: dict):
        self._system_bar.update_params(params)

    @Slot()
    def _on_system_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export system parameters", "", "RP360XP System (*.rp360s)"
        )
        if not path:
            return
        if not path.endswith(".rp360s"):
            path += ".rp360s"
        params = self._system_bar.get_params()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(params, f, indent=2)
            self.statusBar().showMessage(f"System parameters exported to {path}", 4000)
        except OSError as exc:
            QMessageBox.warning(self, "Export failed", str(exc))

    @Slot()
    def _on_system_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import system parameters", "", "RP360XP System (*.rp360s)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                params = json.load(f)
        except Exception as exc:
            QMessageBox.warning(self, "Import failed", f"Cannot read file: {exc}")
            return
        if not isinstance(params, dict):
            QMessageBox.warning(self, "Import failed", "Invalid file format.")
            return
        for name, value in params.items():
            try:
                raw = int(value)
            except (TypeError, ValueError):
                continue
            self._system_param_changed.emit(name, raw)
        self._system_bar.update_params({k: int(v) for k, v in params.items()
                                        if isinstance(v, (int, float))})
        self.statusBar().showMessage("System parameters imported.", 4000)

    @Slot()
    def _on_backup_clicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Backup presets", "", "RP360XP Backup (*.rp360b)"
        )
        if not path:
            return
        if not path.endswith(".rp360b"):
            path += ".rp360b"
        self._backup_requested.emit(path)

    @Slot()
    def _on_restore_clicked(self):
        answer = QMessageBox.warning(
            self,
            "Restore presets",
            "For best results, perform a restore shortly after powering on the device "
            "(cold boot). Heavy use before restoring may cause timeouts.\n\n"
            "Continue?",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Ok:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore presets", "", "RP360XP Backup (*.rp360b)"
        )
        if not path:
            return
        self._restore_requested.emit(path)

    @Slot(int, int, str)
    def _on_operation_progress(self, done: int, total: int, label: str):
        if self._op_progress_dlg is None:
            self._op_progress_dlg = QProgressDialog(label, None, 0, total, self)
            self._op_progress_dlg.setWindowTitle("Please wait")
            self._op_progress_dlg.setWindowModality(Qt.WindowModal)
            self._op_progress_dlg.setMinimumDuration(0)
            self._op_progress_dlg.setValue(0)
        self._op_progress_dlg.setLabelText(label)
        self._op_progress_dlg.setValue(done)
        if done >= total:
            self._op_progress_dlg.close()
            self._op_progress_dlg = None

    @Slot(int, int, str)
    def _on_preset_name_progress(self, done: int, total: int, label: str):
        if self._progress_dlg is None:
            self._progress_dlg = QProgressDialog(label, None, 0, total, self)
            self._progress_dlg.setWindowTitle("Connecting")
            self._progress_dlg.setWindowModality(Qt.WindowModal)
            self._progress_dlg.setMinimumDuration(0)
            self._progress_dlg.setValue(0)
        self._progress_dlg.setLabelText(label)
        self._progress_dlg.setValue(done)
        if done >= total:
            self._progress_dlg.close()
            self._progress_dlg = None

    @Slot()
    def _on_store_new_clicked(self):
        names = self._user_preset_names
        if not names:
            self.statusBar().showMessage("Preset list not loaded yet", 4000)
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Store New")
        dlg.setModal(True)
        dlg.setMinimumWidth(360)

        lay = QVBoxLayout(dlg)
        lay.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(6)

        name_edit = QLineEdit()
        name_edit.setMaxLength(16)
        name_edit.setPlaceholderText("max 16 characters")
        if self._preset_panel._current_preset:
            name_edit.setText(self._preset_panel._current_preset.name[:16])
        form.addRow("Preset Name:", name_edit)

        loc_combo = QComboBox()
        loc_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        for i, n in enumerate(names):
            label = f"{i + 1}.  {n}" if n else f"{i + 1}.  (empty)"
            loc_combo.addItem(label, i)
        form.addRow("Preset location:", loc_combo)

        lay.addLayout(form)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        lay.addWidget(btn_box)

        if dlg.exec() == QDialog.Accepted:
            idx = loc_combo.currentData()
            name = name_edit.text().strip()
            self._preset_panel.setEnabled(False)
            self._save_as_requested.emit(idx, name)

    @Slot(int, str)
    def _on_list_preset_selected(self, index_0: int, bank: str):
        if bank == self._current_bank and index_0 == self._current_slot_0:
            return
        if self._current_dirty and self._current_bank != "factory":
            result = self._ask_save_changes()
            if result == "cancel":
                self._preset_list.select_preset(self._current_bank, self._current_slot_0)
                return
            if result == "store_new":
                self._on_store_new_clicked()
                return
            if result == "quick_store":
                self._save_requested.emit()
                self._suppress_next_preset_change = True
        self._preset_panel.setEnabled(False)
        self._load_preset_requested.emit(index_0, bank)

    def _ask_save_changes(self) -> str:
        msg = QMessageBox(self)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText(
            f'The preset "{self._current_preset_name}" has been modified.\n'
            f'Do you want to store the changes?'
        )
        btn_quick  = msg.addButton("Quick Store", QMessageBox.ButtonRole.AcceptRole)
        btn_new    = msg.addButton("Store New",   QMessageBox.ButtonRole.AcceptRole)
        btn_no     = msg.addButton("No",          QMessageBox.ButtonRole.NoRole)
        btn_cancel = msg.addButton("Cancel",      QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_cancel)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_quick:
            return "quick_store"
        if clicked == btn_new:
            return "store_new"
        if clicked == btn_no:
            return "no"
        return "cancel"

    def _mark_dirty(self):
        self._current_dirty = True
        self._preset_panel.mark_dirty()

    @Slot(str)
    def _on_error(self, msg: str):
        self.statusBar().showMessage(f"Error: {msg}", 8000)

    @Slot(list)
    def _on_notification(self, msg: list):
        if not msg:
            return
        cmd = msg[0]

        if cmd == "np" and len(msg) >= 4:
            path, value = str(msg[2]), msg[3]
            try:
                # preset/fxc/N/fx/PARAM
                m = re.search(r'fxc/(\d+)/fx/([^/]+)$', path)
                if m:
                    self._preset_panel.update_param(int(m.group(1)), m.group(2), int(value))
                    self._mark_dirty()
                    return

                # preset/fxc/N/ENABLE or preset/fxc/N/FLATPARAM
                m = re.search(r'fxc/(\d+)/([^/]+)$', path)
                if m:
                    slot, param = int(m.group(1)), m.group(2)
                    if param == "ENABLE":
                        self._preset_panel.update_enable(slot, bool(value))
                    else:
                        self._preset_panel.update_param(slot, param, int(value))
                    return

                # preset level
                if path in ("preset/PRS LEVL", "PRS LEVL"):
                    self._preset_panel.update_level(int(value))
                    self._mark_dirty()
                    return

                # preset name or ctrls assignment — require full model refresh
                if path in ("preset/name", "name") or path.startswith("ctrls/"):
                    self._refresh_requested.emit()
                    return

                # system settings
                if path.startswith("system/"):
                    param = path[len("system/"):]
                    if param in ("FSWMODE", "EXTFSWMODE", "LOOPERPOS",
                                 "STEREO", "OUTPUTSW", "USB REC", "USB PBKQ"):
                        self._system_bar.update_param(param, int(value))
                        return
                    if param == "MASTERVOL":
                        self.statusBar().showMessage(f"Master vol → {value}", 3000)
                        return

                self.statusBar().showMessage(f"np  {path} = {value}", 3000)
            except (TypeError, ValueError):
                log.warning(
                    "np notification ignored — unexpected value type: path=%r value=%r",
                    path, value
                )

        elif cmd == "cm":
            # Save confirmation: cm path='preset' value='banks/user/N'
            if len(msg) >= 4 and str(msg[2]) == "preset":
                val = str(msg[3])
                if val.startswith("banks/user/"):
                    self.statusBar().showMessage("Saved", 3000)
                    return
            self.statusBar().showMessage("Preset changed", 2000)

    # ---------------------------------------------------------- cleanup

    def closeEvent(self, event):
        self._user_disconnecting = True
        self._disconnect_requested.emit()
        self._thread.quit()
        self._thread.wait(2000)
        super().closeEvent(event)


# ------------------------------------------------------------------ entry point

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("RP360XP")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
