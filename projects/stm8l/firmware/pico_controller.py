#!/usr/bin/env python3

from projects.stm8l.firmware import proto
import time, struct, sys
import serial, serial.tools.list_ports
from findus import Pyboard, PyboardError
import argparse


MODULE = "glitcher"  # fixed module name on device
REMOTE_GLITCHER = "glitcher.py"  # fixed remote path on device
REMOTE_PROTO = "proto.py"  # fixed remote path on device
CLASS = "Glitcher"  # fixed class with a main(self) method
GLITCHER_FILE = "projects/stm8l/firmware/glitcher.py"
PROTO_FILE = "projects/stm8l/firmware/proto.py"


class PicoController:
    def __init__(self, port):
        self.port = port
        self.pb = None
        self.ser = None

        self.connect()
        self.open_data_channel()

    def connect(self):
        self.pb = Pyboard(self.port)
        self.pb.enter_raw_repl(soft_reset=False)

        print(f"[control] uploading {GLITCHER_FILE} -> {REMOTE_GLITCHER}")
        self.pb.fs_put(GLITCHER_FILE, REMOTE_GLITCHER)
        print(f"[control] uploading {PROTO_FILE} -> {REMOTE_PROTO}")
        self.pb.fs_put(PROTO_FILE, REMOTE_PROTO)

        self.pb.exec(b"import uos as os; os.dupterm(None,0)\n")

        print(f"[control] starting {MODULE}.{CLASS}().main() in background…")
        start_code = (
            "import sys,_thread\n"
            "sys.path.insert(0,'/')\n"
            f"import {MODULE} as _m\n"
            f"_obj=getattr(_m,'{CLASS}')()\n"
            "_thread.start_new_thread(_obj.main,())\n"
            "print('STARTED')\n"
        )
        out = self.pb.exec(start_code).decode(errors="ignore")
        if "STARTED" not in out:
            raise RuntimeError(f"device did not confirm start, got: {out!r}")
        
        print("[control] worker started; releasing REPL/port")
        # disable REPL to free up the port
        # self.pb.exit_raw_repl()
        time.sleep(0.1)
        self.pb.close()
        print("[control] closed Pyboard")
        self.pb = None

    def open_data_channel(self):
        self.ser = serial.Serial(self.port, 115200, timeout=0.2)
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        self.ser.reset_output_buffer()

        return self.ser

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None
        if self.pb:
            self.pb.close()
            self.pb = None

    def __del__(self):
        time.sleep(0.1)
        if self.ser:
            self.ser.write(proto.frame(proto.CMD["QUIT"]))
        time.sleep(0.1)
        self.close()

    def arm_double_multiplexing(self, delay1: int, length1: int, v1: str, delay2: int, length2: int, v2: str):
        frame = proto.frame(
            proto.CMD["ARM_DOUBLE_MULTIPLEXING"],
            delay1,
            length1,
            proto.voltage_map[v1],
            delay2,
            length2,
            proto.voltage_map[v2],
        )
        print(frame)

        self.ser.write(frame)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Pico controller for glitcher")
    p.add_argument(
        "--port",
        "-p",
        required=True,
        help="serial port of the Pico (default: first one found)",
    )

    args = p.parse_args()
    glitcher = PicoController(args.port)
    glitcher.ser.write(b"\r\x02")
    glitcher.ser.write(b"\r\x02")
    glitcher.ser.write(b"\r\x02")

    while True:
        glitcher.ser.write(b"\r\x10")
        glitcher.arm_double_multiplexing(1000, 200, "VCC", 2000, 300, "VCC")
        print(glitcher.ser.readline())
        # glitcher.arm_double_multiplexing(1000, 200, "1.8", 2000, 300, "GND")
        print(glitcher.ser.readline())
        print(glitcher.ser.readline())
        print(glitcher.ser.readline())
