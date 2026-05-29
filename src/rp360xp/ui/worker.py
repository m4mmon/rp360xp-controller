"""Device worker — runs all blocking I/O in a dedicated QThread."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, Signal, Slot

from ..device import Device, BANK_USER, BANK_FACTORY, NUM_PRESETS
from ..effects_db import EffectsDB
from ..model import Preset


class DeviceWorker(QObject):
    # Signals → UI thread
    connection_changed    = Signal(bool, str)            # connected, port
    preset_changed        = Signal(object, bool, str, int)  # Preset, dirty, bank, slot_1
    error_occurred        = Signal(str)
    notification_received = Signal(list)
    reorder_done          = Signal()
    preset_names_changed  = Signal(list)               # list[str | None], indices 0-98
    preset_name_progress  = Signal(int, int, str)       # done, total, label
    system_params_changed = Signal(dict)               # {name: int}
    factory_names_changed = Signal(list)               # list[str | None], indices 0-98
    operation_progress    = Signal(int, int, str)      # done, total, label
    operation_done        = Signal(str)                # status message

    # Internal queued signals — fired from the transport reader thread,
    # consumed on the worker thread.
    _refresh_needed  = Signal()
    _device_lost     = Signal(str)   # error message

    def __init__(self):
        super().__init__()
        self._device: Device | None = None
        self._db = EffectsDB()
        self._user_preset_names: list = []
        self._refresh_needed.connect(self.refresh_preset)
        self._device_lost.connect(self._on_device_lost)

    # ---------------------------------------------------------- connection

    @Slot(str)
    def connect_device(self, port: str):
        self._safe_disconnect()
        try:
            dev = Device(port=port or None)
            dev.on_notification(self._on_notification)
            dev.on_disconnect(lambda exc: self._device_lost.emit(str(exc)))
            dev.connect()
            self._device = dev
            self.connection_changed.emit(True, port or "auto")
            self._emit_preset()
            self._fetch_system_params()
            self._fetch_preset_names()
            self._fetch_factory_names()
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            self.connection_changed.emit(False, "")

    @Slot()
    def disconnect_device(self):
        was_connected = self._device is not None
        self._safe_disconnect()
        self._user_preset_names = []
        if was_connected:
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
            if raw < 99:
                self._device.save_to_user_slot(raw)
                self._emit_preset()
            else:
                self.error_occurred.emit("Active preset is a factory preset — use Save As")
        self._run(_do)

    @Slot(int, str)
    def save_preset_as(self, index: int, name: str):
        def _do():
            original_name = None
            if not name:
                try:
                    original_name = self._device.get_active_preset().name
                except Exception:
                    pass

            if name:
                self._device.save_and_rename(index, name)
            else:
                self._device.save_to_user_slot(index)

            self._device.load_user_preset(index)
            self._emit_preset()
            stored = name if name else (original_name or "")
            if self._user_preset_names and 0 <= index < len(self._user_preset_names):
                self._user_preset_names[index] = stored
                self.preset_names_changed.emit(list(self._user_preset_names))
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
        def _do():
            self._device.set_model(slot, model_id)
            self._emit_preset()
        self._run(_do)

    @Slot(int)
    def delete_effect(self, slot: int):
        def _do():
            self._device.delete_effect(slot)
            self._emit_preset()
        self._run(_do)

    @Slot(int, str)
    def add_effect(self, slot: int, address: str):
        slot_data = self._db.build_slot_data(address)
        if slot_data is None:
            self.error_occurred.emit(f"Unknown effect: {address!r}")
            return
        def _do():
            try:
                self._device.add_effect(slot, slot_data)
            except Exception as exc:
                self.error_occurred.emit(str(exc))
            # Always refresh: device may create the slot even when it sends a nack
            self._emit_preset()
        self._run(_do)

    @Slot(int, str, list)
    def replace_effect(self, slot: int, address: str, restore_order: list):
        """Cross-category slot change: delete old effect, add new one, restore chain order."""
        slot_data = self._db.build_slot_data(address)
        if slot_data is None:
            self.error_occurred.emit(f"Unknown effect: {address!r}")
            return
        def _do():
            try:
                self._device.delete_effect(slot)
            except Exception as exc:
                self.error_occurred.emit(str(exc))
            try:
                self._device.add_effect(slot, slot_data)
            except Exception as exc:
                self.error_occurred.emit(str(exc))
            if restore_order:
                try:
                    self._device.reorder_chain(restore_order)
                except Exception as exc:
                    self.error_occurred.emit(str(exc))
            # Always refresh regardless of nacks
            self._emit_preset()
        self._run(_do)

    @Slot(list)
    def reorder_chain(self, order: list):
        self._run(lambda: self._device.reorder_chain(order))
        self.reorder_done.emit()

    @Slot(str)
    def import_preset(self, path: str):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            data = raw.get("preset", raw) if isinstance(raw, dict) else raw
            preset = Preset.from_json(data)
        except Exception as exc:
            self.error_occurred.emit(f"Cannot read file: {exc}")
            return
        def _do():
            self._device.send_preset(preset)
            self._emit_preset()
        self._run(_do)

    @Slot(str)
    def export_preset(self, path: str):
        def _do():
            preset = self._device.get_active_preset()
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(preset.to_json(), f, indent=2, ensure_ascii=False)
            except OSError as exc:
                self.error_occurred.emit(f"Cannot write file: {exc}")
        self._run(_do)

    # ---------------------------------------------------------- backup / restore

    @Slot(str)
    def backup_device(self, path: str):
        def _do():
            label = "Backing up presets…"
            self.operation_progress.emit(0, NUM_PRESETS, label)
            presets = self._device.export_user_bank(
                progress=lambda d, t: self.operation_progress.emit(d, t, label)
            )
            parts = []
            count = 0
            for p in presets:
                if p is not None:
                    parts.append(json.dumps(p.to_json(), indent=3, ensure_ascii=False))
                    count += 1
                else:
                    parts.append("")
            content = "\n##".join(parts) + "\n##"
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
            except OSError as exc:
                self.error_occurred.emit(f"Cannot write file: {exc}")
                return
            self.operation_progress.emit(NUM_PRESETS, NUM_PRESETS, label)
            self.operation_done.emit(f"Backed up {count} presets.")
        self._run(_do)

    @Slot(str)
    def restore_device(self, path: str):
        def _do():
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
            except OSError as exc:
                self.error_occurred.emit(f"Cannot read file: {exc}")
                return
            parts = re.split(r'^##', content, flags=re.MULTILINE)
            presets_map: dict = {}
            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                try:
                    presets_map[i] = Preset.from_json(json.loads(part))
                except Exception:
                    pass
            presets = [presets_map.get(i) for i in range(NUM_PRESETS)]
            total = sum(1 for p in presets if p is not None)
            label = "Restoring presets…"
            self.operation_progress.emit(0, total, label)
            self._device.restore_user_bank(
                presets,
                progress=lambda d, t: self.operation_progress.emit(d, t, label)
            )
            self.operation_progress.emit(total, total, label)
            self.operation_done.emit(f"Restored {total} presets.")
            self._emit_preset()
            self._fetch_preset_names()
            self._fetch_factory_names()
        self._run(_do)

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

    @Slot(str, int, str, int, int, bool)
    def assign_expression(self, ctrl: str, slot: int, param: str,
                          min_val: int, max_val: int, flat: bool):
        if slot < 0:
            self._run(lambda: self._device.clear_ctrl(ctrl))
        else:
            self._run(lambda: self._device.assign_expression(
                ctrl, slot, param, min_val, max_val, flat=flat))

    @Slot(int, str, int, int, int, int, bool)
    def assign_lfo(self, slot: int, param: str,
                   min_val: int, max_val: int, speed: int, waveform: int, flat: bool):
        if slot < 0:
            self._run(lambda: self._device.clear_ctrl("lfo1"))
        else:
            self._run(lambda: self._device.assign_lfo(
                slot, param, min_val, max_val, speed, waveform, flat=flat))

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

    @Slot(str, int)
    def set_system_param(self, name: str, value: int):
        self._run(lambda: self._device.set_system_param(name, value))

    def _fetch_system_params(self):
        if not self._device:
            return
        try:
            params = self._device.get_system_params()
            self.system_params_changed.emit(params)
        except Exception:
            pass

    def _fetch_factory_names(self):
        if not self._device:
            return
        _TOTAL = 198
        _LABEL = "Loading factory preset list…"
        try:
            self.preset_name_progress.emit(99, _TOTAL, _LABEL)
            names = self._device.factory_preset_names(
                progress=lambda done, total: self.preset_name_progress.emit(
                    done + 99, _TOTAL, _LABEL)
            )
            self.factory_names_changed.emit(names)
        except Exception as exc:
            self.preset_name_progress.emit(_TOTAL, _TOTAL, _LABEL)
            self.error_occurred.emit(str(exc))

    def _fetch_preset_names(self):
        if not self._device:
            return
        _TOTAL = 198
        _LABEL = "Loading user preset list…"
        try:
            self.preset_name_progress.emit(0, _TOTAL, _LABEL)
            names = self._device.user_preset_names(
                progress=lambda done, total: self.preset_name_progress.emit(
                    done, _TOTAL, _LABEL)
            )
            self._user_preset_names = names
            self.preset_names_changed.emit(names)
        except Exception as exc:
            self.preset_name_progress.emit(_TOTAL, _TOTAL, _LABEL)
            self.error_occurred.emit(str(exc))

    @Slot(str)
    def _on_device_lost(self, msg: str):
        self._device = None
        self._user_preset_names = []
        self.error_occurred.emit(msg)
        self.connection_changed.emit(False, "")

    def _safe_disconnect(self):
        if self._device:
            try:
                self._device.disconnect()
            except Exception:
                pass
            self._device = None
