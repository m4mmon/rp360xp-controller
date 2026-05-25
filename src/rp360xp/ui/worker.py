"""Device worker — runs all blocking I/O in a dedicated QThread."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from ..device import Device, BANK_USER, BANK_FACTORY
from ..effects_db import EffectsDB


class DeviceWorker(QObject):
    # Signals → UI thread
    connection_changed = Signal(bool, str)           # connected, port
    preset_changed = Signal(object, bool, str, int)  # Preset, dirty, bank, slot_1
    error_occurred = Signal(str)
    notification_received = Signal(list)

    # Internal: queued trigger so _emit_preset() runs on the worker thread,
    # not on the transport reader thread where notifications arrive.
    _refresh_needed = Signal()

    def __init__(self):
        super().__init__()
        self._device: Device | None = None
        self._db = EffectsDB()
        self._refresh_needed.connect(self.refresh_preset)

    # ---------------------------------------------------------- connection

    @Slot(str)
    def connect_device(self, port: str):
        self._safe_disconnect()
        try:
            dev = Device(port=port or None)
            dev.on_notification(self._on_notification)
            dev.connect()
            self._device = dev
            self.connection_changed.emit(True, port or "auto")
            self._emit_preset()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            self.connection_changed.emit(False, "")

    @Slot()
    def disconnect_device(self):
        self._safe_disconnect()
        self.connection_changed.emit(False, "")

    # ---------------------------------------------------------- preset

    @Slot()
    def refresh_preset(self):
        self._emit_preset()

    @Slot(int, str)
    def load_preset(self, index: int, bank: str):
        def _do():
            if bank == BANK_USER:
                self._device.load_user_preset(index)
            else:
                self._device.load_factory_preset(index)
            self._emit_preset()
        self._run(_do)

    @Slot()
    def save_preset(self):
        def _do():
            raw = self._device.last_preset_index()
            if raw < 100:
                self._device.save_to_user_slot(raw)
                self._emit_preset()
            else:
                self.error_occurred.emit("Active preset is a factory preset — use Save As")
        self._run(_do)

    @Slot(int, str)
    def save_preset_as(self, index: int, name: str):
        def _do():
            if name:
                self._device.save_and_rename(index, name)
            else:
                self._device.save_to_user_slot(index)
            self._emit_preset()
        self._run(_do)

    # ---------------------------------------------------------- live edits

    @Slot(int, bool)
    def set_enable(self, slot: int, enabled: bool):
        self._run(lambda: self._device.set_enable(slot, enabled))

    @Slot(int, str, int)
    def set_param(self, slot: int, param: str, value: int):
        self._run(lambda: self._device.set_param(slot, param, value))

    @Slot(int)
    def set_preset_level(self, level: int):
        self._run(lambda: self._device.set_preset_level(level))

    @Slot(int, str)
    def set_model(self, slot: int, model_id: str):
        self._run(lambda: self._device.set_model(slot, model_id))

    @Slot(int)
    def delete_effect(self, slot: int):
        self._run(lambda: self._device.delete_effect(slot))

    @Slot(int, str)
    def add_effect(self, slot: int, address: str):
        slot_data = self._db.build_slot_data(address)
        if slot_data is None:
            self.error_occurred.emit(f"Unknown effect: {address!r}")
            return
        self._run(lambda: self._device.add_effect(slot, slot_data))

    # ---------------------------------------------------------- stomp

    @Slot(str, int)
    def assign_stomp(self, ctrl: str, slot: int):
        self._run(lambda: self._device.assign_stomp(ctrl, slot))

    @Slot(str)
    def clear_stomp(self, ctrl: str):
        self._run(lambda: self._device.clear_stomp(ctrl))

    # ---------------------------------------------------------- ctrl fields

    @Slot(str, str, int)
    def set_ctrl_field(self, ctrl: str, field: str, value: int):
        self._run(lambda: self._device.set_ctrl_field(ctrl, field, value))

    # ---------------------------------------------------------- private

    def _run(self, fn):
        if not self._device:
            self.error_occurred.emit("Not connected")
            return
        try:
            fn()
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def _emit_preset(self):
        if not self._device:
            return
        try:
            preset = self._device.get_active_preset()
            dirty = self._device.is_preset_dirty()
            bank, idx = self._device.last_preset_info()
            self.preset_changed.emit(preset, dirty, bank, idx + 1)
        except Exception as exc:
            self.error_occurred.emit(str(exc))

    def _on_notification(self, msg: list):
        # Called from the transport reader thread — must not call send_command here.
        self.notification_received.emit(msg)
        if msg and msg[0] in ("cm", "nac", "ndc", "nsc"):
            self._refresh_needed.emit()  # queued → executes on worker thread

    def _safe_disconnect(self):
        if self._device:
            try:
                self._device.disconnect()
            except Exception:
                pass
            self._device = None
