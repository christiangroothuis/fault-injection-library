#!/usr/bin/env python3

import argparse
import os
import random
import sys
import time
import numpy as np

from dotenv import load_dotenv

from findus import Database, PicoGlitcher
from ..utils.psu import PS3005D
from ..utils.pushover import send_pushover_notification

SUCCESS_PIN = 20
RESET_PIN = 21


class ProfilingGlitcher(PicoGlitcher):
    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)

        self.pico_glitcher.pyb.exec_raw_no_follow(
            "import machine\n"
            f"success_pin = machine.Pin({SUCCESS_PIN}, machine.Pin.IN, machine.Pin.PULL_DOWN)\n"
            f"reset_pin = machine.Pin({RESET_PIN}, machine.Pin.IN, machine.Pin.PULL_DOWN)\n"
        )

    def read_success_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(success_pin.value()))\n")
        return bool(int(out[0].strip()))

    def read_reset_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(reset_pin.value()))\n")
        return bool(int(out[0].strip()))

    def classify(self, state: bytes) -> str:
        color = "C"
        if b"expected" in state:
            color = "G"
        elif b"timeout" in state:
            color = "Y"
        elif b"success" in state:
            color = "R"
        return color


class Main:
    def __init__(self, args):
        self.args = args
        self.parameters = {
            # shared glitch params 
            # TODO: test with library multiplexing on chip 2
            # "s_length": 0,
            # "e_length": 200,
            # "length_step": 4,
            # "s_delay": 1876,
            # "e_delay": 1892,
            # "delay_step": 16,
            # "s_voltage": 0.20,
            # "e_voltage": 2.20,
            # "voltage_step": 0.01,
            # "n_glitches": 500,
            # clean glitch extended params
            # "s_length": 204,
            # "e_length": 300,
            # "length_step": 4,
            # "s_delay": 1876,
            # "e_delay": 1892,
            # "delay_step": 16,
            # "s_voltage": 1.22,
            # "e_voltage": 2.20,
            # "voltage_step": 0.01,
            # "n_glitches": 500,

            "length_step": 4,
            "s_delay": 1876,
            "e_delay": 1892,
            "voltage_step": 0.01,
            "n_glitches": 500,
        }

        self.glitcher = ProfilingGlitcher()
        self.glitcher.init(port=args.rpico, enable_vtarget=False)
        self.glitcher.change_config_and_reset("mux_vinit", "3.3")
        self.glitcher.init(port=args.rpico, enable_vtarget=False)

        self.glitcher.rising_edge_trigger()
        self.glitcher.set_multiplexing()

        self.glitcher.power_cycle_reset(0.01)

        self.db = Database(
            sys.argv + [f"{k}={v}" for k, v in self.parameters.items()],
            resume=args.resume,
            nostore=args.no_store,
            column_names=["voltage", "delay", "length"],
        )
        self.start_time = int(time.time())
        self.psu = PS3005D(port=args.psu)

    def run(self):
        exp_id = 0
        self.psu.set_voltage(0.0)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        for voltage in np.arange(0.0, 2.2, 0.01):
            self.psu.set_voltage(voltage)
            time.sleep(0.1)

            delay = 2000

            for length in np.arange(0, 2**14 - 1, 12):
                self.glitcher.arm_multiplexing(delay, {"t1": int(length), "v1": "VI1"})
                self.glitcher.reset(50e-6)
                success = False
                state = b"expected"

                try:
                    self.glitcher.block(timeout=0.1)
                    if self.glitcher.read_reset_flag():
                        break
                except TimeoutError:
                    print("[-] Timeout received in block(). Continuing.")
                    self.glitcher.power_cycle_reset(0.2)
                    time.sleep(0.01)
                    state = b"timeout"

                color = self.glitcher.classify(state)
                    
                speed = self.glitcher.get_speed(self.start_time, exp_id)
                experiment_base_id = self.db.get_base_experiments_count()
                exp_id += 1

            print(voltage, length)


if __name__ == "__main__":
    load_dotenv()

    p = argparse.ArgumentParser(
        description="STM8L single-glitch via external trigger + success pin"
    )
    p.add_argument(
        "--rpico",
        default="/dev/ttyUSB1",
        required=True,
        help="PicoGlitcher serial port",
    )
    p.add_argument(
        "--psu", default="/dev/ttyUSB0", required=True, help="PSU serial port"
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    p.add_argument("--ic", required=True, help="IC number")
    p.add_argument("--bor", help="Brownout reset voltage enabled")
    p.add_argument("--shape", choices=["clean", "ringing"], default="clean", help="Glitch shape")
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
