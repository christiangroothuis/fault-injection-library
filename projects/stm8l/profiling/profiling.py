#!/usr/bin/env python3

import argparse
import random
import sys
import time
import numpy as np

from dotenv import load_dotenv

from findus import Database, PicoGlitcher
from ..utils.psu import PS3005D

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
            "s_length": 4,
            "length_step": 4,
            "s_delay": 1875,
            "e_delay": 1900,
            "s_voltage": 0.98,
            "e_voltage": 1.60,
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

        self.psu.set_voltage(self.parameters["s_voltage"])
        time.sleep(0.1)
        self.psu.set_current_limit(0.2)
        time.sleep(0.1)
        self.psu.turn_on()
        time.sleep(0.1)

        for voltage in np.arange(
            self.parameters["s_voltage"],
            self.parameters["e_voltage"],
            self.parameters["voltage_step"],
        ):
            voltage = 1.19
            print(f"Setting PSU voltage to {voltage:.2f} V")
            self.psu.set_voltage(voltage)
            time.sleep(0.1)

            length_band = self.parameters["length_step"] * 4
            estimated_optimal_length = round(32 * voltage / 4) * 4

            for length in np.arange(
                min(
                    max(
                        estimated_optimal_length - length_band,
                        self.parameters["s_length"],
                    ),
                    12,
                ),
                min(estimated_optimal_length + length_band + self.parameters["length_step"], 32),
                self.parameters["length_step"],
            ):
                # length = int(length)
                length = 24
                for _ in range(self.parameters["n_glitches"]):
                    delay = random.randint(
                        self.parameters["s_delay"], self.parameters["e_delay"]
                    )
                    mul_config = {"t1": length, "v1": "VI1"}
                    self.glitcher.arm_multiplexing(delay, mul_config)
                    self.glitcher.reset(200e-6)  # reset for 50us
                    success = False

                    try:
                        self.glitcher.block(timeout=0.1)
                        time.sleep(60e-6)
                        success = self.glitcher.read_success_flag()
                        reset = self.glitcher.read_reset_flag()

                        if success:
                            state = b"success"
                        elif reset:
                            state = b"reset"
                        else:
                            state = b"expected"
                    except:
                        print("[-] Timeout received in block(). Continuing.")
                        self.glitcher.power_cycle_reset(0.2)
                        time.sleep(0.2)
                        state = b"timeout"

                    color = self.glitcher.classify(state)
                    if success:
                        self.db.insert(
                            exp_id, voltage * 100, delay, length, color, state
                        )
                    speed = self.glitcher.get_speed(self.start_time, exp_id)
                    experiment_base_id = self.db.get_base_experiments_count()
                    print(
                        self.glitcher.colorize(
                            f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{voltage:.2f}\t{delay:>{len(str(self.parameters['e_delay']))}}\t{length}\t{color}\t{state}",
                            color,
                        )
                    )
                    exp_id += 1


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
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
