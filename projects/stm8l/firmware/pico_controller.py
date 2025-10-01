#!/usr/bin/env python3

import time, struct, sys
import serial, serial.tools.list_ports
from findus import Pyboard, PyboardError
import argparse


MODULE = "glitcher"  # fixed module name on device
REMOTE = "glitcher.py"  # fixed remote path on device
CLASS = "Glitcher"  # fixed class with a main(self) method
LOCAL = "glitcher.py"  # local file to upload


class PicoController:
    def __init__(self, port=None):
        self.port = port
        self.pb = None
        self.ser = None

    def connect(self):
        print(f"[control] opening {self.port} via Pyboard…")
        self.pb = Pyboard(self.port)
        self.pb.enter_raw_repl(soft_reset=True)
        print(f"[control] uploading {LOCAL} -> {REMOTE}")
        self.pb.fs_put(LOCAL, REMOTE)
        print(f"[control] starting {MODULE}.{CLASS}().main() in background…")
        start_code = (
            "import sys,_thread\n"
            "sys.path.insert(0,'/')\n"
            f"import {MODULE} as _m\n"
            f"_obj=getattr(_m,'{CLASS}')()\n"
            "_thread.start_new_thread(_obj.main,())\n"
            "print('STARTED')\n"
        )
        out = self.pb.exec_(start_code).decode(errors="ignore")
        if "STARTED" not in out:
            raise RuntimeError(f"device did not confirm start, got: {out!r}")
        print("[control] worker started; releasing REPL/port")
        self.pb.close()
        self.pb = None

    def open_data_channel(self):
        self.ser = serial.Serial(self.port, 115200, timeout=0.2)
        time.sleep(0.3)  # let USB CDC settle
        return self.ser

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None
        if self.pb:
            self.pb.close()
            self.pb = None

    def send_frame(self, cmd: int, payload: bytes = b""):
        self.ser.write(bytes((cmd,)) + struct.pack("<H", len(payload)) + payload)

    def recv_frame(self):
        # header
        hdr = self.ser.read(3)
        while len(hdr) < 3:
            more = self.ser.read(3 - len(hdr))
            if not more:
                continue
            hdr += more
        cmd = hdr[0]
        ln = hdr[1] | (hdr[2] << 8)
        # payload
        data = self.ser.read(ln)
        while len(data) < ln:
            more = self.ser.read(ln - len(data))
            if not more:
                continue
            data += more
        return cmd, data


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pico controller for glitcher")
    p.add_argument(
        "--port",
        "-p",
        default=None,
        help="serial port of the Pico (default: first one found)",
    )

    args = p.parse_args()
    glitcher = PicoController(args.port)

    try:
        glitcher.connect()
    except PyboardError as e:
        print("PyboardError:", e)
        sys.exit(1)

    ser = glitcher.open_data_channel()
    print("[data] ready for fast byte I/O (send_frame/recv_frame)")
    # --- quick demo (remove if not needed) ---
    glitcher.send_frame(1, b"\x01"); print("→", glitcher.recv_frame())
