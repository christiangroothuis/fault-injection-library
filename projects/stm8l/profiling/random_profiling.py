#!/usr/bin/env python3

import argparse
import logging
import random
import sys
import time
from dotenv import load_dotenv

from findus import Database, PicoGlitcher

SUCCESS_PIN = 20
EXPECTED_PIN = 21


class DerivedPicoGlitcher(PicoGlitcher):
    def init(self, *args, **kwargs):
        super().init(*args, **kwargs)

        self.pico_glitcher.pyb.exec_raw_no_follow(
            "import machine\n"
            f"success_pin = machine.Pin({SUCCESS_PIN}, machine.Pin.IN, machine.Pin.PULL_DOWN)\n"
            f"expected_pin = machine.Pin({EXPECTED_PIN}, machine.Pin.IN, machine.Pin.PULL_DOWN)\n"
        )

    def read_success_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(success_pin.value()))\n")
        return bool(int(out[0].strip()))

    def read_expected_flag(self) -> bool:
        out = self.pico_glitcher.pyb.exec_raw(f"print(int(expected_pin.value()))\n")
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

        self.glitcher = DerivedPicoGlitcher()
        self.glitcher.init(port=args.rpico, enable_vtarget=False)
        self.glitcher.change_config_and_reset("mux_vinit", "3.3")
        self.glitcher.init(port=args.rpico, enable_vtarget=False)

        self.glitcher.rising_edge_trigger()

        self.glitcher.set_multiplexing()
        self.glitcher.power_cycle_reset(0.01)

        self.db = Database(sys.argv, resume=args.resume, nostore=args.no_store)
        self.start_time = int(time.time())

    def run(self):
        s_length = self.args.length[0]
        e_length = self.args.length[1]
        s_delay = self.args.delay[0]
        e_delay = self.args.delay[1]

        exp_id = 0

        while True:
            if exp_id % 1000 == 0:
                self.glitcher.power_cycle_reset(1)

            delay = random.randint(s_delay, e_delay)
            length = random.randint(s_length, e_length)

            mul_config = {"t1": length, "v1": "1.8"}
            self.glitcher.arm_multiplexing(delay, mul_config)

            self.glitcher.reset(0.01)

            try:
                self.glitcher.block(timeout=1)

                time.sleep(0.001)

                success = self.glitcher.read_success_flag()
                reset = self.glitcher.read_expected_flag()
                print(f"success={success}, reset={reset}")

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
            self.db.insert(exp_id, delay, length, color, state)
            speed = self.glitcher.get_speed(self.start_time, exp_id)
            experiment_base_id = self.db.get_base_experiments_count()
            print(
                self.glitcher.colorize(
                    f"[+] Experiment {exp_id}\t{experiment_base_id}\t({speed})\t{delay:>{len(str(e_delay))}}\t{length}\t{color}\t{state}",
                    color,
                )
            )
            exp_id += 1


if __name__ == "__main__":
    load_dotenv()

    p = argparse.ArgumentParser(
        description="STM8L single-glitch via external trigger + success pin"
    )
    p.add_argument("--rpico", default="/dev/ttyUSB1", help="PicoGlitcher serial port")
    p.add_argument(
        "--delay", nargs=2, type=int, required=True, help="Glitch offset range (ns)"
    )
    p.add_argument(
        "--length",
        nargs=2,
        type=int,
        required=True,
        help="Glitch pulse width range (ns)",
    )
    p.add_argument(
        "--reset-hold", type=float, default=0.01, help="Target reset hold time (s)"
    )
    p.add_argument(
        "--block-timeout",
        type=float,
        default=1.0,
        help="Timeout waiting for glitch (s)",
    )
    p.add_argument(
        "--trigger-input",
        default="default",
        help="The trigger input to use (default, alt, ext1, ext2). The inputs ext1 and ext2 require the PicoGlitcher v2.",
    )
    p.add_argument("--resume", action="store_true", help="Resume previous database run")
    p.add_argument(
        "--no-store", action="store_true", help="Do not write results to the database"
    )
    args = p.parse_args()

    try:
        Main(args).run()
    except KeyboardInterrupt:
        print("Interrupted, exiting.")
        sys.exit(1)
