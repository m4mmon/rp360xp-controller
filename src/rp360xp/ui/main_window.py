"""Main application window."""

from __future__ import annotations

import re
import sys

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QStatusBar, QVBoxLayout, QWidget,
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
from .widgets import PresetPanel


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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RP360XP Controller")
        self.resize(760, 700)
        self._build_ui()
        self._build_worker()

    # ---------------------------------------------------------- UI layout

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_connection_bar())

        self._preset_panel = PresetPanel()
        self._preset_panel.enable_toggled.connect(self._enable_changed)
        self._preset_panel.enable_toggled.connect(lambda *_: self._preset_panel.mark_dirty())
        self._preset_panel.param_changed.connect(self._param_changed)
        self._preset_panel.param_changed.connect(lambda *_: self._preset_panel.mark_dirty())
        self._preset_panel.level_changed.connect(self._level_changed)
        self._preset_panel.level_changed.connect(lambda *_: self._preset_panel.mark_dirty())
        self._preset_panel.save_clicked.connect(self._save_requested)
        self._preset_panel.refresh_clicked.connect(self._refresh_requested)
        self._preset_panel.stomp_changed.connect(self._on_stomp_changed)
        self._preset_panel.ctrl_field_changed.connect(self._ctrl_field_changed)
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

        # worker → UI
        self._worker.connection_changed.connect(self._on_connection_changed)
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
        self._preset_panel.setEnabled(connected)
        if connected:
            self._conn_lbl.setText(f"Connected · {port}")
            self._conn_lbl.setStyleSheet("color: #7fc97f;")
        else:
            self._conn_lbl.setText("Disconnected")
            self._conn_lbl.setStyleSheet("color: #aaa;")

    @Slot(object, bool, str, int)
    def _on_preset_changed(self, preset, dirty: bool, bank: str, slot_1: int):
        if preset is None:
            return
        self._preset_panel.update_preset(preset, dirty, bank, slot_1)
        marker = "  [unsaved]" if dirty else ""
        self.statusBar().showMessage(f'"{preset.name}"  —  {bank} #{slot_1}{marker}', 5000)

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

            # preset/fxc/N/fx/PARAM
            m = re.search(r'fxc/(\d+)/fx/([^/]+)$', path)
            if m:
                self._preset_panel.update_param(int(m.group(1)), m.group(2), int(value))
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
                return

            # preset name or ctrls assignment — require full model refresh
            if path in ("preset/name", "name") or path.startswith("ctrls/"):
                self._refresh_requested.emit()
                return

            # system settings — show in status bar only
            if path == "system/MASTERVOL":
                self.statusBar().showMessage(f"Master vol → {value}", 3000)
                return

            self.statusBar().showMessage(f"np  {path} = {value}", 3000)

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
