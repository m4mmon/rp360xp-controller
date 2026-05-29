"""Serial transport layer for the RP360XP.

Handles:
- Port discovery (by USB VID:PID 1210:0032)
- Packet framing (small and large formats)
- Checksum calculation and verification
- Background read thread
- Automatic ACK for device packets (CHAN=B)

Packet formats (spec §2):

  Small  (pkt[1] != 0x00):
    55 [LEN_LO] [LEN_HI] [CHAN] [SEQ_HI] [SEQ_LO] [00] [UNK] [payload…] [CKSUM]
    total = LEN + 3

  Large  (pkt[1] == 0x00):
    55 [00] [LEN_HI] [LEN_LO] [00] [CHAN] [SEQ_HI] [SEQ_LO] [00] [00] [payload…] [CKSUM]
    total = LEN + 5

  CHAN values:
    0x43 'C'  host → device command
    0x42 'B'  device → host data / notification
    0x41 'A'  device → host ACK (of a host command)
    0x02      host → device ACK (of a device packet)

  CKSUM = (256 - sum(pkt[1:-1])) & 0xFF
"""

from __future__ import annotations

import logging
import math
import queue
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

USB_VID = 0x1210
USB_PID = 0x0032
BAUD_RATE = 115200

CHAN_CMD  = 0x43   # 'C'  host → device command
CHAN_DATA = 0x42   # 'B'  device → host
CHAN_DACK = 0x41   # 'A'  device ACK of host cmd
CHAN_HACK = 0x02   #      host ACK of device packet

# Maximum payload bytes per small outgoing packet (empirically 249, matching Nexus)
SEND_CHUNK = 249


@dataclass
class Packet:
    chan: int
    seq: int
    payload: bytes


class TransportError(Exception):
    pass


class Transport:
    def __init__(self):
        self._port: Optional[serial.Serial] = None
        self._buf = bytearray()
        self._read_thread: Optional[threading.Thread] = None
        self._running = False
        self._rx_queue: queue.Queue[Packet] = queue.Queue()
        self._write_lock = threading.Lock()
        self._host_seq = 0
        self._dack_pending: dict[int, threading.Event] = {}
        self._error_handler: Optional[Callable] = None
        self._dack_lock = threading.Lock()

    # ------------------------------------------------------------------ public

    def on_error(self, handler: Callable[[Exception], None]) -> None:
        """Register a callback invoked when the read thread dies (serial error)."""
        self._error_handler = handler

    @staticmethod
    def find_port() -> Optional[str]:
        """Return the serial port path for the RP360XP, or None if not found."""
        for p in serial.tools.list_ports.comports():
            if p.vid == USB_VID and p.pid == USB_PID:
                return p.device
        # Fallback: match by description string
        for p in serial.tools.list_ports.comports():
            desc = (p.description or "").upper()
            hwid = (p.hwid or "").upper()
            if "RP360" in desc or "RP360" in hwid or "1210:0032" in hwid:
                return p.device
        return None

    def connect(self, port: Optional[str] = None) -> None:
        """Open the serial port and start the read thread."""
        if port is None:
            port = self.find_port()
        if port is None:
            raise TransportError("RP360XP not found — check USB connection")
        log.info("Connecting to %s", port)
        self._port = serial.Serial(port, BAUD_RATE, timeout=0.1)
        self._running = True
        self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._read_thread.start()

    def disconnect(self) -> None:
        """Stop the read thread and close the serial port."""
        self._running = False
        if self._read_thread:
            self._read_thread.join(timeout=2.0)
        if self._port and self._port.is_open:
            self._port.close()
        self._port = None
        log.info("Disconnected")

    def send(self, payload: bytes, on_progress=None) -> None:
        """Frame payload as one or more small packets, waiting for DACK between each.

        on_progress(done, total) is called after each fragment is acknowledged,
        where done and total are fragment counts.
        """
        total = math.ceil(len(payload) / SEND_CHUNK) if payload else 1
        done = 0
        for chunk in _split(payload, SEND_CHUNK):
            event = threading.Event()
            with self._dack_lock:
                seq = self._next_seq()
                self._dack_pending[seq] = event
            pkt = self._build_small(CHAN_CMD, seq, chunk)
            self._write(pkt)
            if not event.wait(timeout=5.0):
                with self._dack_lock:
                    self._dack_pending.pop(seq, None)
                log.warning("No DACK received for SEQ=%d, continuing", seq)
            done += 1
            if on_progress:
                on_progress(done, total)

    def recv(self, timeout: float = 2.0) -> Optional[Packet]:
        """Return the next packet from the device, or None on timeout."""
        try:
            return self._rx_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    # --------------------------------------------------------------- internals

    def _next_seq(self) -> int:
        seq = self._host_seq
        self._host_seq = (self._host_seq + 1) & 0xFFFF
        return seq

    def _write(self, data: bytes) -> None:
        with self._write_lock:
            self._port.write(data)

    def _read_loop(self) -> None:
        while self._running:
            try:
                waiting = self._port.in_waiting
                data = self._port.read(waiting if waiting > 0 else 1)
                if data:
                    self._buf.extend(data)
                    self._parse_buffer()
            except serial.SerialException as exc:
                log.error("Serial read error: %s", exc)
                if self._error_handler:
                    self._error_handler(exc)
                break

    def _parse_buffer(self) -> None:
        while self._buf:
            # Sync: find start byte
            if self._buf[0] != 0x55:
                log.debug("Discarding stray byte 0x%02x", self._buf[0])
                del self._buf[0]
                continue

            if len(self._buf) < 3:
                break

            if self._buf[1] != 0x00:
                # Small packet: 2-byte LE LEN
                pkt_len = self._buf[1] | (self._buf[2] << 8)
                total = pkt_len + 3
            else:
                # Large packet: 2-byte BE LEN at positions [2:4]
                if len(self._buf) < 4:
                    break
                pkt_len = (self._buf[2] << 8) | self._buf[3]
                total = pkt_len + 5

            if len(self._buf) < total:
                break   # incomplete packet, wait for more bytes

            pkt_bytes = bytes(self._buf[:total])
            del self._buf[:total]

            if not _verify_checksum(pkt_bytes):
                log.warning("Checksum mismatch, discarding packet")
                continue

            self._dispatch(pkt_bytes)

    def _dispatch(self, pkt_bytes: bytes) -> None:
        if pkt_bytes[1] != 0x00:
            chan = pkt_bytes[3]
            seq  = (pkt_bytes[4] << 8) | pkt_bytes[5]
            payload = pkt_bytes[8:-1]
        else:
            chan = pkt_bytes[5]
            seq  = (pkt_bytes[6] << 8) | pkt_bytes[7]
            payload = pkt_bytes[10:-1]

        log.debug("← CHAN=0x%02x SEQ=%d len=%d", chan, seq, len(payload))

        if chan == CHAN_DATA:
            # Auto-ACK every device data packet
            self._write(self._build_ack(seq))
            self._rx_queue.put(Packet(chan=chan, seq=seq, payload=payload))
        elif chan == CHAN_DACK:
            with self._dack_lock:
                event = self._dack_pending.pop(seq, None)
            if event:
                event.set()
        else:
            log.debug("Ignoring packet with unexpected CHAN=0x%02x", chan)

    # -------------------------------------------- packet builders

    @staticmethod
    def _build_small(chan: int, seq: int, payload: bytes) -> bytes:
        body = bytes([chan, (seq >> 8) & 0xFF, seq & 0xFF, 0x00, 0x00]) + payload
        pkt_len = len(body) + 1   # +1 for checksum
        header = bytes([0x55, pkt_len & 0xFF, (pkt_len >> 8) & 0xFF])
        raw = header + body
        return raw + bytes([_checksum(raw)])

    @staticmethod
    def _build_ack(device_seq: int) -> bytes:
        body = bytes([CHAN_HACK,
                      (device_seq >> 8) & 0xFF, device_seq & 0xFF,
                      0x00, CHAN_DATA])
        pkt_len = len(body) + 1   # +1 for checksum
        header = bytes([0x55, pkt_len & 0xFF, (pkt_len >> 8) & 0xFF])
        raw = header + body
        return raw + bytes([_checksum(raw)])


# ------------------------------------------------------------------ helpers

def _checksum(pkt: bytes) -> int:
    """Compute CKSUM = (256 - sum(pkt[1:])) & 0xFF  (excludes pkt[0]=0x55)."""
    return (256 - sum(pkt[1:])) & 0xFF


def _verify_checksum(pkt: bytes) -> bool:
    return pkt[-1] == (256 - sum(pkt[1:-1])) & 0xFF


def _split(data: bytes, size: int):
    for i in range(0, len(data), size):
        yield data[i:i + size]
