"""High-level API for the RP360XP.

Typical usage:

    from rp360xp import Device

    with Device() as dev:
        names = dev.user_preset_names()
        preset = dev.get_active_preset()
        print(preset.name)
        preset.slots[2].params["DRIVE"] = 80
        dev.set_param(2, "DRIVE", 80)
        dev.save_preset(slot=2)
"""

from __future__ import annotations

import json
import logging
import math
from typing import Optional

from .model import Preset
from .protocol import Protocol
from .transport import Transport, SEND_CHUNK

log = logging.getLogger(__name__)

BANK_USER    = "user"
BANK_FACTORY = "factory"
NUM_PRESETS  = 99   # 0-based slots 0..98 = presets 1..99


class DeviceError(Exception):
    pass


class Device:
    def __init__(self, port: Optional[str] = None):
        self._port = port
        self._transport = Transport()
        self._protocol = Protocol(self._transport)
        self._protocol.on_notification(self._on_notification)
        self._notification_handlers: list = []
        self._disconnect_handlers: list = []
        self._transport.on_error(self._on_transport_error)

    # ----------------------------------------------------------------- context

    def __enter__(self) -> Device:
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()

    # ------------------------------------------------------------------ connect

    def connect(self) -> None:
        """Connect to the RP360XP and open a Nexus-compatible session."""
        self._transport.connect(self._port)
        self._handshake()

    def disconnect(self) -> None:
        """Close the session and disconnect."""
        try:
            self._protocol.send_command("sbs", value=0)
        except Exception:
            pass
        self._transport.disconnect()

    def _handshake(self) -> None:
        """Replicate the Nexus startup sequence."""
        self._protocol.send_command("rp", path="STATE")
        self._protocol.send_command("rp", path="VERSION")
        self._protocol.send_command("sbs", value=1)
        self._protocol.send_command("rp", path="system/SYNC")

    # ------------------------------------------------------------ preset names

    def user_preset_names(self, progress=None) -> list[Optional[str]]:
        """Return list of 99 user preset names (index 0..98). None = empty slot.

        progress(done, total) is an optional callback called after each name is read.
        """
        return self._preset_names(BANK_USER, progress)

    def factory_preset_names(self, progress=None) -> list[Optional[str]]:
        """Return list of 99 factory preset names (index 0..98)."""
        return self._preset_names(BANK_FACTORY, progress)

    def _preset_names(self, bank: str, progress=None) -> list[Optional[str]]:
        names = []
        for i in range(NUM_PRESETS):
            name = self._protocol.send_command("rp", path=f"banks/{bank}/{i}/name")
            names.append(name)
            if progress:
                progress(i + 1, NUM_PRESETS)
        return names

    # ------------------------------------------------------------ preset read

    def get_active_preset(self) -> Preset:
        """Read and return the currently active (edit-buffer) preset."""
        data = self._protocol.send_command("rc", path="preset")
        return Preset.from_json(data["preset"] if "preset" in data else data)

    def get_user_preset(self, index: int) -> Preset:
        """Read user preset by 0-based index (0 = preset #1)."""
        return self._get_preset(BANK_USER, index)

    def get_factory_preset(self, index: int) -> Preset:
        """Read factory preset by 0-based index."""
        return self._get_preset(BANK_FACTORY, index)

    def _get_preset(self, bank: str, index: int) -> Preset:
        _check_index(index)
        data = self._protocol.send_command("rc", path=f"banks/{bank}/{index}")
        return Preset.from_json(data["preset"] if "preset" in data else data)

    # ---------------------------------------------------------- preset select

    def load_user_preset(self, index: int) -> Preset:
        """Load user preset into the edit buffer and return it."""
        _check_index(index)
        self._protocol.send_command("mc", path=f"banks/{BANK_USER}/{index}", value="preset")
        return self.get_active_preset()

    def load_factory_preset(self, index: int) -> Preset:
        """Load factory preset into the edit buffer and return it."""
        _check_index(index)
        self._protocol.send_command("mc", path=f"banks/{BANK_FACTORY}/{index}", value="preset")
        return self.get_active_preset()

    # ----------------------------------------------------------- param editing

    def set_param(self, slot: int, param: str, value: int, *, flat: bool = False) -> None:
        """Set a single effect parameter in the edit buffer.

        flat=True for slots without an fx subdict (e.g. the VOLUME slot).
        """
        path = f"preset/fxc/{slot}/{param}" if flat else f"preset/fxc/{slot}/fx/{param}"
        self._protocol.send_command("sp", path=path, value=value)

    def set_enable(self, slot: int, enabled: bool) -> None:
        """Enable or disable an effect slot."""
        self._protocol.send_command(
            "sp",
            path=f"preset/fxc/{slot}/ENABLE",
            value=int(enabled),
        )

    def set_model(self, slot: int, model: str) -> None:
        """Change the effect model in a slot (e.g. 'dist.SCREAMER')."""
        self._protocol.send_command(
            "ssc",
            path=f"preset/fxc/{slot}",
            value={"fx": {"name": model}},
        )

    def set_preset_level(self, level: int) -> None:
        """Set the global output level of the current preset (0-99)."""
        self._protocol.send_command("sp", path="preset/PRS LEVL", value=level)

    def reorder_chain(self, order: list[int]) -> None:
        """Reorder the effect chain.

        order must be a permutation of the slot indices currently occupied in the
        preset — typically all 10 (0-9) for a full preset, fewer if some slots are
        empty.  The device physically reassigns effects to new slot indices and
        automatically updates all ctrl LNK paths to follow the moved effects.

        Example (full preset, put slot 9 first):
            [9, 0, 1, 2, 3, 4, 5, 6, 7, 8]
        """
        self._protocol.send_command("shc", path="preset/fxc", value=order)

    def delete_effect(self, slot: int) -> None:
        """Remove the effect from a slot (leaves slot empty)."""
        self._protocol.send_command("dc", path=f"preset/fxc/{slot}")

    def add_effect(self, slot: int, slot_data: dict) -> None:
        """Write a full slot definition into the edit buffer.

        slot_data must be a complete slot dict as the device expects, e.g.:
          {"ENABLE": 1, "fx": {"GAIN": 50, "name": "dist.SCREAMER"}}
        or for flat slots (EQ):
          {"ENABLE": 1, "HIGH BW": 1, ..., "name": "eq.EQ"}
        """
        self._protocol.send_command("ssc", path="preset/fxc", value={str(slot): slot_data})

    # ---------------------------------------------------------- controllers

    # Toggle-type controls (assigned to a slot's ENABLE)
    STOMP_CONTROLS = ("ctrlA", "ctrlB", "ctrlC", "ctrlVSw")
    # Continuous controls (assigned to an effect parameter with a sweep range)
    EXPRESSION_CONTROLS = ("treadle", "altTreadle")
    # All seven controller names
    ALL_CONTROLS = STOMP_CONTROLS + EXPRESSION_CONTROLS + ("lfo1",)

    def assign_stomp(self, ctrl: str, slot: int) -> None:
        """Assign a toggle control to switch a slot's ENABLE.

        ctrl must be one of 'ctrlA', 'ctrlB', 'ctrlC', 'ctrlVSw'.
        ctrlVSw is the toe switch at the end of the expression pedal travel.
        """
        if ctrl not in self.STOMP_CONTROLS:
            raise ValueError(f"ctrl must be one of {self.STOMP_CONTROLS}, got {ctrl!r}")
        self._protocol.send_command(
            "ssc",
            path="preset/ctrls",
            value={ctrl: {"LNK": f"../../fxc/{slot}/ENABLE"}},
        )

    def assign_expression(self, ctrl: str, slot: int, param: str,
                          min_val: int = 0, max_val: int = 99,
                          *, flat: bool = False) -> None:
        """Assign an expression pedal to an effect parameter.

        ctrl must be 'treadle' or 'altTreadle'.
        min_val/max_val define the pedal sweep mapped to the parameter range.
        flat=True for slots without an fx subdict (vol, eq).
        """
        if ctrl not in self.EXPRESSION_CONTROLS:
            raise ValueError(
                f"ctrl must be one of {self.EXPRESSION_CONTROLS}, got {ctrl!r}"
            )
        lnk = f"../../fxc/{slot}/{param}" if flat else f"../../fxc/{slot}/fx/{param}"
        self._protocol.send_command(
            "ssc",
            path="preset/ctrls",
            value={ctrl: {"LNK": lnk, "MIN": min_val, "MAX": max_val}},
        )

    def assign_lfo(self, slot: int, param: str,
                   min_val: int = 0, max_val: int = 99,
                   speed: int = 74, waveform: int = 0,
                   *, flat: bool = False) -> None:
        """Assign lfo1 to an effect parameter.

        min_val/max_val define the LFO amplitude mapped to the parameter range.
        speed: 0-185  (0 = 0.05 Hz, 74 ≈ 0.79 Hz, 138 ≈ 5.30 Hz, 185 = 10 Hz)
        waveform: 0=TRIANGLE  1=SINE  2=SQUARE
        flat=True for slots without an fx subdict (vol, eq).
        """
        if not (0 <= speed <= 185):
            raise ValueError(f"speed must be 0-185, got {speed}")
        if waveform not in (0, 1, 2):
            raise ValueError(
                f"waveform must be 0 (TRIANGLE), 1 (SINE) or 2 (SQUARE), got {waveform}"
            )
        lnk = f"../../fxc/{slot}/{param}" if flat else f"../../fxc/{slot}/fx/{param}"
        self._protocol.send_command(
            "ssc",
            path="preset/ctrls",
            value={"lfo1": {
                "LNK": lnk, "MIN": min_val, "MAX": max_val,
                "SPEED": speed, "WAVEFORM": waveform,
            }},
        )

    def set_ctrl_field(self, ctrl: str, field: str, value) -> None:
        """Set a single numeric field on a controller (MIN, MAX, SPEED, WAVEFORM)."""
        if ctrl not in self.ALL_CONTROLS:
            raise ValueError(f"ctrl must be one of {self.ALL_CONTROLS}, got {ctrl!r}")
        self._protocol.send_command("sp", path=f"preset/ctrls/{ctrl}/{field}", value=value)

    def clear_ctrl(self, ctrl: str) -> None:
        """Remove the assignment for any controller (all seven names accepted)."""
        if ctrl not in self.ALL_CONTROLS:
            raise ValueError(f"ctrl must be one of {self.ALL_CONTROLS}, got {ctrl!r}")
        self._protocol.send_command("sp", path=f"preset/ctrls/{ctrl}/LNK", value="")

    def clear_stomp(self, ctrl: str) -> None:
        """Remove the assignment for a toggle control (ctrlA/B/C/ctrlVSw)."""
        if ctrl not in self.STOMP_CONTROLS:
            raise ValueError(f"ctrl must be one of {self.STOMP_CONTROLS}, got {ctrl!r}")
        self.clear_ctrl(ctrl)

    def clear_all_stomps(self) -> None:
        """Remove all toggle control assignments (A, B, C and VSw)."""
        for ctrl in self.STOMP_CONTROLS:
            self.clear_ctrl(ctrl)

    # ----------------------------------------------------------- preset write

    def send_preset(self, preset: Preset) -> None:
        """Write a complete preset into the device's edit buffer."""
        payload = {"preset": preset.to_json()}
        self._protocol.send_command("ssc", path="", value=payload)

    def rename_preset(self, name: str) -> None:
        """Rename the current edit-buffer preset (does not save)."""
        self._protocol.send_command("sp", path="preset/name", value=name)

    def save_to_user_slot(self, index: int) -> None:
        """Save the edit-buffer preset to a user slot (0-based)."""
        _check_index(index)
        self._protocol.send_command(
            "mc", path="preset", value=f"banks/{BANK_USER}/{index}"
        )

    def save_and_rename(self, index: int, name: str) -> None:
        """Rename then save to a user slot (Nexus 'store new')."""
        self.rename_preset(name)
        self.save_to_user_slot(index)

    # --------------------------------------------------------------- backup

    def export_user_bank(self, progress=None) -> list[Preset]:
        """Read all 99 user presets. Returns list indexed 0..98."""
        presets = []
        for i in range(NUM_PRESETS):
            try:
                presets.append(self.get_user_preset(i))
            except Exception as exc:
                log.warning("Could not read user preset %d: %s", i, exc)
                presets.append(None)
            if progress:
                progress(i + 1, NUM_PRESETS)
        return presets

    def restore_user_bank(self, presets: list,
                          progress=None) -> int:
        """Restore user presets one slot at a time.

        Mirrors the Nexus restore protocol: one ssc command per preset sent
        directly to banks/user with a single-slot payload {"N": preset_data}.

        presets is a list of up to 99 Preset-or-None items (index = slot 0-based).
        None entries are skipped (slot left unchanged on the device).

        progress(done, total) is an optional callback called after each ack.
        Returns the number of presets written.
        """
        to_write = [(i, p) for i, p in enumerate(presets[:NUM_PRESETS]) if p is not None]
        for done, (i, p) in enumerate(to_write):
            self._protocol.send_command(
                "ssc", path="banks/user", value={str(i): p.to_json()},
                timeout=15.0,
            )
            if progress:
                progress(done + 1, len(to_write))
        return len(to_write)

    def restore_user_bank_bulk(self, presets: list,
                               timeout: float = 300.0,
                               progress=None) -> int:
        """Restore user presets via a single bulk ssc to banks/user.

        Sends all presets in one JSON payload, mirroring what Nexus attempts
        during a full-bank restore.  The device may nack or disconnect depending
        on firmware version — use restore_user_bank() for the reliable fallback.

        progress(done_fragments, total_fragments) is called after each transport
        fragment is acknowledged, giving visibility into the transfer while the
        device processes the full payload.

        Returns the number of presets in the payload.
        """
        payload = {
            str(i): p.to_json()
            for i, p in enumerate(presets[:NUM_PRESETS])
            if p is not None
        }
        encoded = json.dumps(
            ["ssc", 0, "banks/user", payload],
            separators=(",", ":"),
        ).encode("utf-8")
        total_fragments = math.ceil(len(encoded) / SEND_CHUNK)
        log.info(
            "Bulk restore: %d presets, payload %d bytes, %d fragments",
            len(payload), len(encoded), total_fragments,
        )
        self._protocol.send_command(
            "ssc", path="banks/user", value=payload,
            timeout=timeout,
            on_progress=progress,
        )
        return len(payload)

    # --------------------------------------------------- dirty / state queries

    # ---------------------------------------------------------- system params

    SYSTEM_PARAMS = (
        "FSWMODE", "EXTFSWMODE", "LOOPERPOS",
        "STEREO", "OUTPUTSW", "USB REC", "USB PBKQ",
    )

    def get_system_params(self) -> dict:
        """Read the writable system parameters. Returns {name: int}."""
        result = {}
        for p in self.SYSTEM_PARAMS:
            try:
                result[p] = int(self._protocol.send_command("rp", path=f"system/{p}"))
            except Exception:
                pass
        return result

    def set_system_param(self, name: str, value: int) -> None:
        """Write one system parameter."""
        self._protocol.send_command("sp", path=f"system/{name}", value=value)

    # ---------------------------------------------------------- master volume

    def get_master_vol(self) -> int:
        """Return the current master output volume (0-99)."""
        return int(self._protocol.send_command("rp", path="system/MASTERVOL"))

    def set_master_vol(self, value: int) -> None:
        """Set the master output volume (0-99).

        The device also sends np system/MASTERVOL notifications when the
        physical master volume knob is turned, so this path is bidirectional.
        """
        if not (0 <= value <= 99):
            raise ValueError(f"Master volume must be 0-99, got {value}")
        self._protocol.send_command("sp", path="system/MASTERVOL", value=value)

    # --------------------------------------------------- dirty / state queries

    def is_preset_dirty(self) -> bool:
        """Return True if the edit buffer has unsaved changes."""
        return bool(self._protocol.send_command("rp", path="system/PRESETDIRTY"))

    def last_preset_index(self) -> int:
        """Return the raw LAST PRES value from the device.

        Encoding: 0-98 = user preset (0-based), 100-198 = factory preset (0-based).
        Use last_preset_info() for a decoded (bank, index) tuple.
        """
        return int(self._protocol.send_command("rp", path="system/LAST PRES"))

    def last_preset_info(self) -> tuple[str, int]:
        """Return (bank, index) for the last active preset.

        bank is 'user' or 'factory'; index is 0-based.
        Encoding: user 0-98 → LAST PRES 0-98; factory 0-98 → LAST PRES 99-197.
        """
        raw = self.last_preset_index()
        if raw >= 99:
            return BANK_FACTORY, raw - 99
        return BANK_USER, raw

    # ---------------------------------------------------------- notifications

    def on_notification(self, handler) -> None:
        """Register a callback for device events.

        handler(msg: list) is called with the raw parsed JSON array for 'np' and
        'cm' messages (footswitch presses, expression pedal, preset changes).
        """
        self._notification_handlers.append(handler)

    def on_disconnect(self, handler) -> None:
        """Register a callback invoked when the serial connection is lost unexpectedly."""
        self._disconnect_handlers.append(handler)

    def _on_transport_error(self, exc: Exception) -> None:
        for h in self._disconnect_handlers:
            try:
                h(exc)
            except Exception:
                log.exception("Disconnect handler raised")

    def _on_notification(self, msg: list) -> None:
        for h in self._notification_handlers:
            try:
                h(msg)
            except Exception:
                log.exception("Notification handler raised")


# ------------------------------------------------------------------ helpers

def _check_index(index: int) -> None:
    if not (0 <= index < NUM_PRESETS):
        raise ValueError(f"Preset index must be 0..{NUM_PRESETS - 1}, got {index}")
